import re
import os
import json
import signal
import hashlib
import threading
import traceback

from ansible import constants as C
from ansible.playbook import Playbook
from ansible.playbook.task import Task
from ansible.playbook.play import Play
from ansible.inventory.host import Host
from ansible.utils.color import stringc
from ansible.utils.display import Display
from ansible.playbook.handler import Handler
from ansible.utils.fqcn import add_internal_fqcns
from ansible.plugins.callback import CallbackBase
from ansible.executor.stats import AggregateStats
from ansible.executor.task_result import TaskResult
from ansible.playbook.included_file import IncludedFile

from ansible_collections.unity.general.plugins.plugin_utils.hostlist import format_hostnames

display = Display()


def _hash_object_dirty(x) -> str:
    "for non json-serializable objects, just casts to string."
    json_bytes = json.dumps(x, sort_keys=True, default=str).encode("utf8")
    return hashlib.md5(json_bytes).hexdigest()


# TODO does this work?
def _anonymize_dict(identifiers: list[str], _input: dict) -> dict:
    """
    replace all identifiers with "ANONYMOUS" in string leaf nodes of dict tree
    """
    replace_me = "(" + "|".join([re.escape(y) for y in identifiers]) + ")"

    def anonymize_or_recurse_or_nothing(x):
        if isinstance(x, str):
            return re.sub(replace_me, "ANONYMOUS", x, flags=re.IGNORECASE)
        elif isinstance(x, list):
            return [anonymize_or_recurse_or_nothing(e) for e in x]
        elif isinstance(x, dict):
            return {k: anonymize_or_recurse_or_nothing(v) for k, v in x.items()}
        return x

    return anonymize_or_recurse_or_nothing(_input)


class ResultID:
    """
    normally I prefer to just use dictionaries but having a type makes it easier for variable names
    """

    def __init__(self, hostname: str, item: str | None):
        self.hostname = hostname
        self.item = item

    def __str__(self):
        if self.item:
            return f"{self.hostname}(item={self.item})"
        return self.hostname


class WarningID:
    """
    normally I prefer to just use dictionaries but having a type makes it easier for variable names
    """

    def __init__(self, hostname: str, item: str | None, index: int):
        self.hostname = hostname
        self.item = item
        self.index = index

    def __str__(self):
        if self.item:
            return f"{self.hostname}(item={self.item})[{self.index}]"
        return f"{self.hostname}[{self.index}]"


class ExceptionID(WarningID):
    pass


def result_ids2str(result_ids: list[ResultID], multiline: bool = None):
    """
    builds a list of hosts for each item
    then, groups items with identical lists of hosts
    if multiline isn't explicitly set to False, it may be automatically enabled
    """
    item2hostnames = {}
    for result_id in result_ids:
        item2hostnames.setdefault(result_id.item, set()).add(result_id.hostname)
    hostnames_str2items = {}
    for item, hostnames in item2hostnames.items():
        hostnames_str = ",".join(sorted(list(hostnames)))
        hostnames_str2items.setdefault(hostnames_str, []).append(item)
    output = []
    for hostnames_str, items in hostnames_str2items.items():
        if not any(items):
            output.append(hostnames_str)
        else:
            output.append(f"{hostnames_str}: items={json.dumps(items, sort_keys=True)}")
    if multiline or (multiline is None and sum(len(x) for x in output) > 100):
        return "\n".join(output)
    return ", ".join(output)


class DedupeCallback(CallbackBase):
    """
    Callback plugin that reduces output size by culling redundant output.
    * at the end of the task, print the list of hosts that returned each status.
    * each result is "anonymized", so that hostname and item differences are ignored for deduping.
      each result is broken up into four parts: diffs, warnings, exceptions, and "stripped result".
      "stripped result" is just the result dict minus diffs, warnings, and exceptions.
      duplicate diffs, warnings, exceptions, and stripped results are grouped so that unnecessary
      output can be avoided. each can be printed immediately or at the end of task.
    * each result is given a "status", which can be one of:
      ok changed unreachable failed skipped ignored interrupted running
    * since information might not be printed immediately, SIGINT is trapped to display results
      before ansible exits.
    * If a result hash changed=true but no diff, a \"no diff\" message is used as the diff
    * when using the `--step` option in `ansible-playbook`, output from the just-completed task
      is not printed until the start of the next task, which is not natural.
    * only the linear and debug strategies are allowed.
    * async tasks are not allowed.
    * if a task is skipped and its result has a "skipped_reason" and its result doesn't have
      a "msg", then the skipped reason becomes the msg.
    """

    def __sigint_handler(self, signum, frame):
        """
        make sure the user knows which runners were interrupted
        since they might be blocking the playbook and might need to be excluded
        """
        _id = f"pid={os.getpid()} thread={threading.get_ident()} self={self}"
        _id_hash = hashlib.md5(_id.encode()).hexdigest()[:5]
        display.v(f"[{_id_hash}] = SIGINT caught!")
        display.v(f"[{_id_hash}] = {_id}")
        display.v(f"[{_id_hash}] stack trace: {traceback.format_stack()}")
        try:
            display.v(f"[{_id_hash}] acquiring sigint handler lock...")
            self.__sigint_handler_lock.acquire()
            display.v(f"[{_id_hash}] sigint handler lock acquired.")
            if self.__sigint_handler_run:
                display.warning(
                    f"[{_id_hash}] caught multiple SIGINT, sending SIGKILL to PID {os.getpid()}. Use -v for more information."
                )
                os.kill(os.getpid(), signal.SIGKILL)
            if not self.first_task_started:
                display.v(
                    f"[{_id_hash}]: first task not yet started, skipping special sigint logic..."
                )
                return
            if os.getpid() != self.pid_where_sigint_trapped:
                display.v(
                    f"[{_id_hash}]: pid != {self.pid_where_sigint_trapped}, skipping special sigint logic..."
                )
                return
            self.__sigint_handler_run = True
            for hostname in self.running_hosts:
                self._register_result({}, ResultID(hostname, None), "interrupted")
            del self.running_hosts
            self.running_hosts = set()
            self.__maybe_task_end()
        finally:
            display.v(f"[{_id_hash}] releasing sigint handler lock...")
            self.__sigint_handler_lock.release()
            display.v(f"[{_id_hash}] executing original sigint handler...")
            self.original_sigint_handler(signum, frame)

    def __init__(self):
        super(DedupeCallback, self).__init__()
        self.task_name = None
        self.task_is_loop = None
        self.task_end_done = None
        self.running_hosts = None
        self.status2result_ids = None
        self.exception2exception_ids = None
        self.warning2warning_ids = None
        self.diff_hash2result_ids = None
        self.diff_hash2diff = None
        self.result_stripped_hash2result_ids = None
        self.result_stripped_hash2result_stripped = None
        self.result_stripped_hash2status = None
        # the above data is set/reset at the start of each task
        # don't try to access above data before the 1st task has started
        self.first_task_started = False
        self.pid_where_sigint_trapped = os.getpid()
        self.__sigint_handler_lock = threading.RLock()
        self.__sigint_handler_run = False

        self.original_sigint_handler = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, self.__sigint_handler)

    def __task_start(self, task: Task):
        self.__maybe_task_end()
        self.task_name = task.get_name()
        self.task_is_loop = bool(task.loop)
        self.task_end_done = False
        del self.running_hosts
        self.running_hosts = set()
        del self.status2result_ids
        self.status2result_ids = {
            "ok": [],
            "changed": [],
            "unreachable": [],
            "failed": [],
            "skipped": [],
            "ignored": [],
            "interrupted": [],
        }
        del self.exception2exception_ids
        self.exception2exception_ids = {}
        del self.warning2warning_ids
        self.warning2warning_ids = {}
        del self.diff_hash2result_ids
        self.diff_hash2result_ids = {}
        del self.diff_hash2diff
        self.diff_hash2diff = {}
        del self.result_stripped_hash2result_ids
        self.result_stripped_hash2result_ids = {}
        del self.result_stripped_hash2result_stripped
        self.result_stripped_hash2result_stripped = {}
        del self.result_stripped_hash2status
        self.result_stripped_hash2status = {}
        if not self.first_task_started:
            self.first_task_started = True

    def __runner_start(self, host: Host, task: Task):
        hostname = host.get_name()
        if not task.loop:
            self.running_hosts.add(hostname)
        self.__update_status_totals()

    def __maybe_task_end(self):
        """
        The ansible callback API does not have any notion of task end.
        I thought I could detect this by keeping a number of running_hosts, incrementing on
        v2_runner_start and decrementing on v2_runner_*, but this has false positives:
        there can be times when the number of running runners is 0 but more runners will still
        be spawned in the future.
        I thought I could detect this by comparing the number of unique hostnames of completed
        runners against `ansible_play_hosts_all`, but this won't work for skipped tasks because
        there will never be any completed runners.
        To make up for this, I call this function multiple times later and make sure it only
        runs once.
        """
        if (not self.first_task_started) or self.task_end_done:
            return
        self.task_end_done = True
        self.__update_status_totals()

        sorted_diffs_and_groupings = []
        sorted_diff_hash2result_ids = dict(
            sorted(self.diff_hash2result_ids.items(), key=lambda x: len(x[1]))
        )
        for diff_hash, grouping in sorted_diff_hash2result_ids.items():
            diff = self.diff_hash2diff[diff_hash]
            sorted_diffs_and_groupings.append((diff, grouping))

        sorted_results_stripped_and_groupings = []
        status2msg2result_ids = {}
        sorted_result_stripped_hash2result_ids = dict(
            sorted(self.result_stripped_hash2result_ids.items(), key=lambda x: len(x[1]))
        )
        for result_stripped_hash, result_ids in sorted_result_stripped_hash2result_ids.items():
            result_stripped = self.result_stripped_hash2result_stripped[result_stripped_hash]
            sorted_results_stripped_and_groupings.append((result_stripped, result_ids))
            msg = result_stripped.get("msg", None)
            status = self.result_stripped_hash2status[result_stripped_hash]
            status2msg2result_ids.setdefault(status, {}).setdefault(msg, []).extend(result_ids)
        self.deduped_task_end(
            status2msg2result_ids,
            sorted_results_stripped_and_groupings,
            sorted_diffs_and_groupings,
            self.warning2warning_ids,
            self.exception2exception_ids,
        )

    def _register_result(self, result: dict, result_id: ResultID, status: str) -> list[ResultID]:
        "returns resultIDs of duplicates, not comparing diffs/exceptions/warnings"
        result_stripped_dupes = []
        result_stripped = {k: v for k, v in result.items() if k not in ["exceptions", "warnings"]}
        result_stripped_hash = _hash_object_dirty(
            _anonymize_dict([result_id.hostname, str(result_id.item)], result_stripped)
        )
        if result_stripped_hash in self.result_stripped_hash2result_ids:
            result_stripped_dupes = self.result_stripped_hash2result_ids[result_stripped_hash]
            self.result_stripped_hash2result_ids[result_stripped_hash].append(result_id)
        else:
            self.result_stripped_hash2result_ids[result_stripped_hash] = [result_id]
            self.result_stripped_hash2result_stripped[result_stripped_hash] = result_stripped
            self.result_stripped_hash2status[result_stripped_hash] = status
        self.status2result_ids[status].append(result_id)
        return result_stripped_dupes

    def __runner_or_runner_item_end(self, result: TaskResult, status: str):
        hostname = CallbackBase.host_label(result)
        item = self._get_item_label(result._result)
        result_id = ResultID(hostname, item)
        warning2dupes = {}
        exception2dupes = {}
        # prompte "skipped_reason" to "msg" so that user can see
        if (
            status == "skipped"
            and "msg" not in result._result
            and "skipped_reason" in result._result
        ):
            result._result["msg"] = result._result["skipped_reason"]
        result_stripped_dupes = self._register_result(
            result._result, ResultID(hostname, item), status
        )
        for i, exception in enumerate(result._result.get("exceptions", [])):
            exception_id = ExceptionID(hostname, item, i)
            self.exception2exception_ids.setdefault(exception, []).append(exception_id)
        for i, warning in enumerate(result._result.get("warnings", [])):
            warning_id = WarningID(hostname, item, i)
            self.warning2warning_ids.setdefault(warning, []).append(warning_id)
        if result._result.get("changed", False):
            diff_or_diffs = result._result.get("diff", [])
            if not isinstance(diff_or_diffs, list):
                diffs = [diff_or_diffs]
            else:
                diffs = diff_or_diffs
            diffs = [x for x in diffs if x]
            if len(diffs) == 0:
                diffs = [
                    {
                        "prepared": stringc(
                            "task reports changed=true but does not report any diff.",
                            C.COLOR_CHANGED,
                        )
                    }
                ]
            for diff in diffs:
                diff_no_headers = {
                    k: v for k, v in diff.items() if k not in ["before_header", "after_header"]
                }
                diff_no_headers = _anonymize_dict([hostname, str(item)], diff_no_headers)
                diff_hash = _hash_object_dirty(diff_no_headers)
                self.diff_hash2result_ids.setdefault(diff_hash, []).append(result_id)
                self.diff_hash2diff[diff_hash] = diff
        self.deduped_result(result, status, result_id, result_stripped_dupes)
        for (warning_id, warning), dupes in warning2dupes.items():
            self.deduped_warning(warning, warning_id, dupe_of=dupes)
        for (exception_id, exception), dupes in exception2dupes.items():
            self.deduped_exception(exception, exception_id, dupe_of=dupes)

        if not self.task_is_loop:
            try:
                self.running_hosts.remove(hostname)
            except KeyError:
                display.warning(
                    f"a runner has completed for host '{hostname}' but this host is not known to have any running runners!"
                )
        self.__update_status_totals()

    def __update_status_totals(self):
        status_totals = {
            status: len(result_ids) for status, result_ids in self.status2result_ids.items()
        }
        # I have to work around this edge case because _runner_on_completed removes hostname
        # from the running_hosts list, and the same host can't be removed multiple times.
        # if I knew the length of the loop I could add the same host multiple times so that
        # it could be removed multiple times, but I don't because the loop variable has not
        # been evaluated.
        if self.task_is_loop:
            status_totals["running"] = "?"
        else:
            status_totals["running"] = len(self.running_hosts)
        self.deduped_update_status_totals(status_totals)

    def __play_start(self, play: Play):
        strategy_fqcn = add_internal_fqcns([play.strategy])[0]
        if not strategy_fqcn in add_internal_fqcns(("linear", "debug")):
            raise RuntimeError(
                f'Unsupported strategy: "{play.strategy}". Supported strategies are "linear" and "debug".'
            )

    def __check_diff_always(self) -> None:
        if not C.DIFF_ALWAYS:
            self._display.warning(
                "DIFF_ALWAYS is not enabled. It is highly recommended that you enable it!"
                + " The whole point of using the deduped_callback API is to make diff information manageable."
            )

    # V2 API #######################################################################################
    def v2_on_any(self, *args, **kwargs):
        self.deduped_on_any(*args, **kwargs)

    def v2_runner_on_start(self, host: Host, task: Task) -> None:
        self.__runner_start(host, task)
        self.deduped_runner_on_start(host, task)

    def v2_runner_on_unreachable(self, result: TaskResult) -> None:
        self.__runner_or_runner_item_end(result, "unreachable")

    def v2_runner_on_skipped(self, result: TaskResult) -> None:
        self.__runner_or_runner_item_end(result, "skipped")

    def v2_runner_item_on_skipped(self, result: TaskResult) -> None:
        self.__runner_or_runner_item_end(result, "skipped")

    def v2_runner_on_ok(self, result: TaskResult) -> None:
        if result._result.get("changed", False):
            self.__runner_or_runner_item_end(result, "changed")
        else:
            self.__runner_or_runner_item_end(result, "ok")

    def v2_runner_item_on_ok(self, result: TaskResult) -> None:
        if result._result.get("changed", False):
            self.__runner_or_runner_item_end(result, "changed")
        else:
            self.__runner_or_runner_item_end(result, "ok")

    def v2_runner_on_failed(self, result: TaskResult, ignore_errors=False) -> None:
        if ignore_errors:
            self.__runner_or_runner_item_end(result, "ignored")
        else:
            self.__runner_or_runner_item_end(result, "failed")

    def v2_runner_item_on_failed(self, result: TaskResult) -> None:
        self.__runner_or_runner_item_end(result, "failed")

    def v2_runner_retry(self, result: TaskResult) -> None:
        self.deduped_runner_retry(result)

    def v2_on_file_diff(self, result) -> None:
        # I need to replace empty diffs with a "no diff" message, and this is not called
        # for empty diffs. instead I handle diffs during __runner_or_runner_item_end
        pass

    def v2_playbook_on_task_start(self, task: Task, is_conditional) -> None:
        self.__task_start(task)
        self.deduped_playbook_on_task_start(task, is_conditional)

    def v2_playbook_on_cleanup_task_start(self, task: Task) -> None:
        self.__task_start(task)
        self.deduped_playbook_on_cleanup_task_start(task)

    def v2_playbook_on_handler_task_start(self, task: Task) -> None:
        self.__task_start(task)
        self.deduped_playbook_on_handler_task_start(task)

    def v2_playbook_on_play_start(self, play: Play) -> None:
        self.__maybe_task_end()  # weird edge case
        self.__play_start(play)
        self.deduped_playbook_on_play_start(play)

    def v2_playbook_on_start(self, playbook: Playbook) -> None:
        self.__check_diff_always()
        self.deduped_playbook_on_start(playbook)

    def v2_playbook_on_notify(self, handler: Handler, host: Host) -> None:
        self.deduped_playbook_on_notify(handler, host)

    def v2_playbook_on_import_for_host(self, result: TaskResult, imported_file) -> None:
        self.deduped_playbook_on_import_for_host(result, imported_file)

    def v2_playbook_on_not_import_for_host(self, result: TaskResult, missing_file) -> None:
        self.deduped_playbook_on_not_import_for_host(result, missing_file)

    def v2_playbook_on_include(self, included_file: IncludedFile) -> None:
        self.deduped_playbook_on_include(included_file)

    def v2_playbook_on_no_hosts_matched(self) -> None:
        self.deduped_playbook_on_no_hosts_matched()

    def v2_playbook_on_no_hosts_remaining(self) -> None:
        self.deduped_playbook_on_no_hosts_remaining()

    def v2_playbook_on_vars_prompt(
        self,
        varname,
        private=True,
        prompt=None,
        encrypt=None,
        confirm=False,
        salt_size=None,
        salt=None,
        default=None,
        unsafe=None,
    ) -> None:
        self.deduped_playbook_on_vars_prompt(
            varname, private, prompt, encrypt, confirm, salt_size, salt, default, unsafe
        )

    def v2_playbook_on_stats(self, stats: AggregateStats) -> None:
        self.__maybe_task_end()  # normally done at task_start(), but there will be no next task
        self.deduped_playbook_on_stats(stats)

    # I'm too lazy to test these and I don't use async so I'm just going to cut support
    def v2_runner_on_async_poll(self, *args, **kwargs) -> None:
        raise NotImplementedError("dedupe_callback does not support async!")

    def v2_runner_on_async_ok(self, *args, **kwargs) -> None:
        raise NotImplementedError("dedupe_callback does not support async!")

    def v2_runner_on_async_failed(self, *args, **kwargs) -> None:
        raise NotImplementedError("dedupe_callback does not support async!")

    # DEDUPED API ##################################################################################
    def deduped_on_any(self, *args, **kwargs) -> None:
        "see ansible.plugins.callback.CallbackBase.v2_on_any"
        pass

    def deduped_playbook_on_start(self, playbook: Playbook) -> None:
        "see ansible.plugins.callback.CallbackBase.v2_playbook_on_start"
        pass

    def deduped_playbook_on_play_start(self, play: Play) -> None:
        "see ansible.plugins.callback.CallbackBase.v2_playbook_on_play_start"
        pass

    def deduped_playbook_on_task_start(self, task: Task, is_conditional) -> None:
        "see ansible.plugins.callback.CallbackBase.v2_playbook_on_task_start"
        pass

    def deduped_playbook_on_cleanup_task_start(self, task: Task) -> None:
        "see ansible.plugins.callback.CallbackBase.v2_playbook_on_cleanup_task_start"
        pass

    def deduped_playbook_on_handler_task_start(self, task: Task) -> None:
        "see ansible.plugins.callback.CallbackBase.v2_playbook_on_handler_task_start"
        pass

    def deduped_runner_on_start(self, host: Host, task: Task) -> None:
        "see ansible.plugins.callback.CallbackBase.v2_runner_on_start"
        pass

    def deduped_playbook_on_stats(self, stats: AggregateStats) -> None:
        "see ansible.plugins.callback.CallbackBase.v2_playbook_on_stats"
        pass

    def deduped_runner_retry(self, result: TaskResult) -> None:
        "see ansible.plugins.callback.CallbackBase.v2_runner_retry"
        pass

    def deduped_playbook_on_notify(self, handler: Handler, host: Host) -> None:
        "see ansible.plugins.callback.CallbackBase.v2_playbook_on_notify"
        pass

    def deduped_playbook_on_import_for_host(self, result: TaskResult, imported_file) -> None:
        "see ansible.plugins.callback.CallbackBase.v2_playbook_on_import_for_host"
        pass

    def deduped_playbook_on_not_import_for_host(self, result: TaskResult, missing_file) -> None:
        "see ansible.plugins.callback.CallbackBase.v2_playbook_on_not_import_for_host"
        pass

    def deduped_playbook_on_include(self, included_file: IncludedFile) -> None:
        "see ansible.plugins.callback.CallbackBase.v2_playbook_on_include"
        pass

    def deduped_playbook_on_no_hosts_matched(self) -> None:
        "see ansible.plugins.callback.CallbackBase.v2_playbook_on_no_hosts_matched"
        pass

    def deduped_playbook_on_no_hosts_remaining(self) -> None:
        "see ansible.plugins.callback.CallbackBase.v2_playbook_on_no_hosts_remaining"
        pass

    def deduped_playbook_on_vars_prompt(
        self,
        varname,
        private=True,
        prompt=None,
        encrypt=None,
        confirm=False,
        salt_size=None,
        salt=None,
        default=None,
        unsafe=None,
    ) -> None:
        "see ansible.plugins.callback.CallbackBase.v2_playbook_on_vars_prompt"
        pass

    def deduped_update_status_totals(self, status_totals: dict[str, str]) -> None:
        """
        status_totals: dictionary from status to a string representing the total number of runners
        or runner items that have that status. the total is usually digits, but it will have
        the value "?" when using a loop. possible values for status are:
        ok changed unreachable failed skipped ignored interrupted running
        """
        pass

    def deduped_result(
        self, result: TaskResult, status: str, result_id: ResultID, dupe_of_stripped: list[ResultID]
    ) -> None:
        """
        use this if you need to print results immediately rather than waiting until end of task
        possible values for status are:
        ok changed unreachable failed skipped ignored interrupted
        hostnames, items, diffs, warnings, and exceptions are all ignored in dupe_of_stripped.
        """
        pass

    def deduped_diff(self, diff: dict, result_id: ResultID, dupe_of: list[ResultID]):
        """
        use this if you need to print diffs immediately rather than waiting until end of task
        hostnames and items are ignored when checking for dupes/groupings
        """
        pass

    def deduped_warning(
        self, warning: str, warning_id: WarningID, dupe_of: list[WarningID]
    ) -> None:
        """
        use this if you need to print warnings immediately rather than waiting until end of task
        hostnames and items are ignored when checking for dupes/groupings
        """
        pass

    def deduped_exception(
        self, exception: str, exception_id: ExceptionID, dupe_of: list[ExceptionID]
    ) -> None:
        """
        use this if you need to print exceptions immediately rather than waiting until end of task
        hostnames and items are ignored when checking for dupes/groupings
        """
        pass

    def deduped_task_end(
        self,
        status2msg2result_ids: dict[str, dict[(str | None), list[ResultID]]],
        sorted_results_stripped_and_groupings: list[tuple[dict, list[ResultID]]],
        sorted_diffs_and_groupings: list[tuple[dict, list[ResultID]]],
        warning2warning_ids: dict[str, list[WarningID]],
        exception2exception_ids: dict[str, list[ExceptionID]],
    ) -> None:
        """
        status2msg2result_ids: dict from status to dict of message to list of hostnames.
        possible values for status are:
        ok changed unreachable failed skipped ignored interrupted running
        not sorted.

        sorted_results_stripped_and_groupings: list of tuples where the first element of each tuple
        is a stripped result dict. a stripped result dict is a result dict without diffs, warnings,
        or exceptions. the second element in each tuple is a list of ResultIDs that produced
        that result. hostnames and items are ignored when grouping ResultIDs. the list of tuples
        is sorted such that the largest groupings are last. a "stripped result" is a result dict

        sorted_diffs_and_groupings: list of tuples where the first element of each tuple is a
        diff dict. the second element in each tuple is a list of ResultIDs that produced
        that result. hostnames and items are ignored when grouping ResultIDs. the list of tuples
        is sorted such that the largest groupings are last. these are only the diffs from
        results where changed==True.

        warning2warning_ids: dict from strings to WarningIDs

        exception2exception_ids: dict from strings to WarningIDs

        hostnames and items are ignored for finding dupes/groupings.
        """
        pass

import re
import os
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

from ansible_collections.unity.general.plugins.plugin_utils.beartype import beartype

VALID_STATUSES = [
    "ok",
    "changed",
    "unreachable",
    "failed",
    "skipped",
    "ignored",
    "interrupted",
    "running",
]

SURROGATE_DIFF = {
    "prepared": stringc("task reports changed=true but does not report any diff.", C.COLOR_CHANGED)
}

display = Display()


@beartype
def _anonymize_dict(identifiers: list[str], _input: dict) -> dict:
    """
    replace all identifiers with "ANONYMOUS" in string leaf nodes of dict tree
    """
    replace_me = "(" + "|".join([re.escape(y) for y in identifiers]) + ")"

    def anonymize_or_recurse_or_nothing(x):
        if isinstance(x, str):
            return re.sub(replace_me, "ANONYMOUS", x, flags=re.IGNORECASE)
        if isinstance(x, list):
            return [anonymize_or_recurse_or_nothing(e) for e in x]
        if isinstance(x, dict):
            return {k: anonymize_or_recurse_or_nothing(v) for k, v in x.items()}
        return x

    return anonymize_or_recurse_or_nothing(_input)


class ResultID:
    """
    normally I prefer to just use dictionaries but having a type makes it easier for variable names
    """

    @beartype
    def __init__(self, hostname: str, item: object):
        assert isinstance(hostname, str)
        self.hostname = hostname
        self.item = item

    @beartype
    def __str__(self):
        if self.item:
            return f"{self.hostname} (item={self.item})"
        return self.hostname


class ExceptionID(ResultID):
    "there can be only 1 exception per result"


class WarningID:
    """
    normally I prefer to just use dictionaries but having a type makes it easier for variable names
    there can be multiple warnings per result so there must also be an index
    """

    @beartype
    def __init__(self, hostname: str, item: object, index: int):
        self.hostname = hostname
        self.item = item
        self.index = index

    @beartype
    def __str__(self):
        if self.item:
            return f"{self.hostname} (item={self.item})[{self.index}]"
        return f"{self.hostname}[{self.index}]"


class DeprecationID(WarningID):
    pass


class DiffID(WarningID):
    pass


class ResultGist(dict):
    """
    information about a result which is necessary for stdout callback, but not so much information
    that results can't be deduped
    """

    @beartype
    def __init__(
        self,
        status: str,
        message: str | None,
        invocation: dict,
        is_verbose: bool,
        task_path: str,
        task_action: str,
    ):
        super().__init__()
        self["status"] = status
        self["message"] = message
        self["invocation"] = invocation
        self["is_verbose"] = is_verbose
        self["task_path"] = task_path
        self["task_action"] = task_action


class Grouper:
    @beartype
    def __init__(self, id_type):
        self._id_type = id_type
        self._preprocessed_values = []
        self.values_1st_match = []
        self.ids = []

    @beartype
    def add(self, _id, value, preprocessed_value=None) -> list[object]:
        "returns list of dupes"
        assert isinstance(_id, self._id_type), f"expected {self._id_type}, got {type(_id)}"
        if preprocessed_value is None:
            preprocessed_value = value
        for i, group_preprocessed_value in enumerate(self._preprocessed_values):
            if preprocessed_value == group_preprocessed_value:
                dupes = self.ids[i].copy()
                self.ids[i].append(_id)
                return dupes
        self._preprocessed_values.append(preprocessed_value)
        self.values_1st_match.append(value)
        self.ids.append([_id])
        return []

    @beartype
    def export(self) -> list[tuple[object, list[object]]]:
        "each tuple has value on left and list of ids on right"
        output = []
        for i, value in enumerate(self.values_1st_match):
            ids = self.ids[i]
            output.append((value, ids))
        return output


class DedupeCallback(CallbackBase):
    """
    Callback plugin that reduces output size by culling redundant output.
    * at the end of the task, print the list of hosts that returned each status.
    * each result is "anonymized", so that hostname and item differences are ignored for deduping.
      each result is broken up into five parts: diffs, warnings, exceptions, deprecations, and
      "stripped result". "stripped result" is just the remainder with the other parts removed.
      duplicate diffs, warnings, exceptions, and stripped results are grouped so that unnecessary
      output can be avoided. each can be printed immediately or at the end of task.
    * each result is given a "status", see dedupe_callback.VALID_STATUSES
    * since information might not be printed immediately, SIGINT is trapped to display results
      before ansible exits.
    * If a result hash changed=true but no diff, a \"no diff\" message is used as the diff
    * when using the `--step` option in `ansible-playbook`, output from the just-completed task
      is not printed until the start of the next task, which is not natural.
    * only the linear and debug strategies are allowed.
    * async tasks are not allowed.
    * if a task is skipped and its result has a "skipped_reason" and its result doesn't have
      a "msg", then the skipped reason becomes the msg.
    * if a task is changed and its result has a "msg", then a new diff is added to the result
      containing that message. This means that at the end of task, you can safely skip over
      all changed results.
    * if you find that loop items are taking up too much space on screen, that means that you should
      be setting the label with `loop_control`
    """

    @beartype
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
                fake_result_id = ResultID(hostname, None)
                self.result_id2status[fake_result_id] = "interrupted"
                self.status2result_ids["interrupted"].append(fake_result_id)
            del self.running_hosts
            self.running_hosts = set()
            self.__maybe_task_end()
        finally:
            display.v(f"[{_id_hash}] releasing sigint handler lock...")
            self.__sigint_handler_lock.release()
            display.v(f"[{_id_hash}] executing original sigint handler...")
            self.original_sigint_handler(signum, frame)

    @beartype
    def __init__(self):
        super(DedupeCallback, self).__init__()
        self.task_name = None
        self.task_is_loop = None
        self.task_end_done = None
        self.running_hosts = None
        self.status2result_ids = None
        self.result_id2status = None
        self.warning_grouper = None
        self.exception_grouper = None
        self.deprecation_grouper = None
        self.diff_grouper = None
        self.result_gist_grouper = None
        self.result_stripped_status = None
        # the above data is set/reset at the start of each task
        # don't try to access above data before the 1st task has started
        self.first_task_started = False
        self.pid_where_sigint_trapped = os.getpid()
        self.__sigint_handler_lock = threading.RLock()
        self.__sigint_handler_run = False

        self.original_sigint_handler = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, self.__sigint_handler)

    @beartype
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
        del self.result_id2status
        self.result_id2status = {}
        del (
            self.warning_grouper,
            self.exception_grouper,
            self.deprecation_grouper,
            self.diff_grouper,
            self.result_gist_grouper,
        )
        self.warning_grouper = Grouper(WarningID)
        self.exception_grouper = Grouper(ExceptionID)
        self.deprecation_grouper = Grouper(DeprecationID)
        self.diff_grouper = Grouper(DiffID)
        self.result_gist_grouper = Grouper(ResultID)
        if not self.first_task_started:
            self.first_task_started = True

    @beartype
    def __runner_start(self, host: Host, task: Task):
        hostname = host.get_name()
        if not task.loop:
            # TODO when deletgated, this should be "delegator -> delegatee"
            self.running_hosts.add(hostname)
        self.__update_status_totals()

    @beartype
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
        self.deduped_task_end(
            self.result_gist_grouper.export(),
            self.diff_grouper.export(),
        )
        self.__update_status_totals()

    @beartype
    def __process_result(self, result: TaskResult, status: str):
        hostname = CallbackBase.host_label(result)
        item = result._result.get("item", None)
        item_label = str(self._get_item_label(result._result))
        result_id = ResultID(hostname, item)

        # prompte "skipped_reason" to "msg" so that user can see
        if (
            status == "skipped"
            and "msg" not in result._result
            and "skipped_reason" in result._result
        ):
            result._result["msg"] = result._result["skipped_reason"]

        gist = ResultGist(
            status,
            result._result.get("msg", None),
            result._result.get("invocation", {}),
            self._run_is_verbose(result),
            result._task.get_path(),
            result._task.action,
        )
        anon_gist = _anonymize_dict([hostname, str(item_label)], gist)
        gist_dupes = self.result_gist_grouper.add(result_id, gist, preprocessed_value=anon_gist)

        for i, warning in enumerate(result._result.get("warnings", [])):
            warning_id = WarningID(hostname, item, i)
            dupe_of = self.warning_grouper.add(warning_id, warning)  # TODO anonymize
            self.deduped_warning(warning, warning_id, dupe_of)
        for i, deprecation in enumerate(result._result.get("deprecations", [])):
            deprecation_id = DeprecationID(hostname, item, i)
            dupe_of = self.deprecation_grouper.add(deprecation_id, deprecation)  # TODO anonymize
            self.deduped_deprecation(deprecation, deprecation_id, dupe_of)
        if exception := result._result.get("exception", None):
            exception_id = ExceptionID(hostname, item)
            dupe_of = self.exception_grouper.add(exception_id, exception)  # TODO anonymize
            self.deduped_exception(exception, exception_id, dupe_of)

        if result._result.get("changed", False):
            diff_or_diffs = result._result.get("diff", [])
            if not isinstance(diff_or_diffs, list):
                diffs = [diff_or_diffs]
            else:
                diffs = diff_or_diffs
            diffs = [x for x in diffs if x]
            # convert result message to a diff
            if msg := result._result.get("msg", None):
                diffs.append({"prepared": msg.strip()})
            if len(diffs) == 0:
                diffs.append(SURROGATE_DIFF.copy())
            for i, diff in enumerate(diffs):
                diff_no_headers = {
                    k: v for k, v in diff.items() if k not in ["before_header", "after_header"]
                }
                anon_diff = _anonymize_dict([hostname, item_label], diff_no_headers)
                self.diff_grouper.add(DiffID(hostname, item, i), diff, preprocessed_value=anon_diff)

        if not self.task_is_loop:
            try:
                self.running_hosts.remove(hostname)
            except KeyError:
                self._display.warning(
                    f"a runner has completed for host '{hostname}' but this host is not known to have any running runners!"
                )
        self.result_id2status[result_id] = status
        self.status2result_ids[status].append(result_id)
        stripped_result_dict = {
            k: v
            for k, v in result._result.items()
            if k not in ["exception", "warnings", "deprecations"]
        }
        self.deduped_result(result_id, stripped_result_dict, gist, gist_dupes)
        self.__update_status_totals()

    @beartype
    def __update_status_totals(self):
        status_totals = {
            status: str(len(result_ids)) for status, result_ids in self.status2result_ids.items()
        }
        # I have to work around this edge case because _runner_on_completed removes hostname
        # from the running_hosts list, and the same host can't be removed multiple times.
        # if I knew the length of the loop I could add the same host multiple times so that
        # it could be removed multiple times, but I don't because the loop variable has not
        # been evaluated.
        if self.task_is_loop:
            status_totals["running"] = "?"
        else:
            status_totals["running"] = str(len(self.running_hosts))
        self.deduped_update_status_totals(status_totals)

    @beartype
    def __play_start(self, play: Play):
        strategy_fqcn = add_internal_fqcns([play.strategy])[0]
        if not strategy_fqcn in add_internal_fqcns(("linear", "debug")):
            raise RuntimeError(
                f'Unsupported strategy: "{play.strategy}". Supported strategies are "linear" and "debug".'
            )

    @beartype
    def __check_diff_always(self) -> None:
        if not C.DIFF_ALWAYS:
            self._display.warning(
                "DIFF_ALWAYS is not enabled. It is highly recommended that you enable it!"
                + " The whole point of using the deduped_callback API is to make diff information manageable."
            )

    # V2 API #######################################################################################
    def v2_on_any(self, *args, **kwargs):
        self.deduped_on_any(*args, **kwargs)

    @beartype
    def v2_runner_on_start(self, host: Host, task: Task) -> None:
        self.__runner_start(host, task)
        self.deduped_runner_on_start(host, task)

    @beartype
    def v2_runner_on_unreachable(self, result: TaskResult) -> None:
        self.__process_result(result, "unreachable")

    @beartype
    def v2_runner_on_skipped(self, result: TaskResult) -> None:
        self.__process_result(result, "skipped")

    @beartype
    def v2_runner_item_on_skipped(self, result: TaskResult) -> None:
        self.__process_result(result, "skipped")

    @beartype
    def v2_runner_on_ok(self, result: TaskResult) -> None:
        if result._result.get("changed", False):
            self.__process_result(result, "changed")
        else:
            self.__process_result(result, "ok")

    @beartype
    def v2_runner_item_on_ok(self, result: TaskResult) -> None:
        if result._result.get("changed", False):
            self.__process_result(result, "changed")
        else:
            self.__process_result(result, "ok")

    @beartype
    def v2_runner_on_failed(self, result: TaskResult, ignore_errors=False) -> None:
        if ignore_errors:
            self.__process_result(result, "ignored")
        else:
            self.__process_result(result, "failed")

    @beartype
    def v2_runner_item_on_failed(self, result: TaskResult) -> None:
        self.__process_result(result, "failed")

    @beartype
    def v2_runner_retry(self, result: TaskResult) -> None:
        self.deduped_runner_retry(result)

    @beartype
    def v2_on_file_diff(self, result) -> None:
        # I need to replace empty diffs with a "no diff" message, and this is not called
        # for empty diffs. instead I handle diffs during __process_result
        pass

    @beartype
    def v2_playbook_on_task_start(self, task: Task, is_conditional) -> None:
        self.__task_start(task)
        self.deduped_playbook_on_task_start(task, is_conditional)

    @beartype
    def v2_playbook_on_cleanup_task_start(self, task: Task) -> None:
        self.__task_start(task)
        self.deduped_playbook_on_cleanup_task_start(task)

    @beartype
    def v2_playbook_on_handler_task_start(self, task: Task) -> None:
        self.__task_start(task)
        self.deduped_playbook_on_handler_task_start(task)

    @beartype
    def v2_playbook_on_play_start(self, play: Play) -> None:
        self.__maybe_task_end()  # weird edge case
        self.__play_start(play)
        self.deduped_playbook_on_play_start(play)

    @beartype
    def v2_playbook_on_start(self, playbook: Playbook) -> None:
        self.__check_diff_always()
        self.deduped_playbook_on_start(playbook)

    @beartype
    def v2_playbook_on_notify(self, handler: Handler, host: Host) -> None:
        self.deduped_playbook_on_notify(handler, host)

    @beartype
    def v2_playbook_on_import_for_host(self, result: TaskResult, imported_file) -> None:
        self.deduped_playbook_on_import_for_host(result, imported_file)

    @beartype
    def v2_playbook_on_not_import_for_host(self, result: TaskResult, missing_file) -> None:
        self.deduped_playbook_on_not_import_for_host(result, missing_file)

    @beartype
    def v2_playbook_on_include(self, included_file: IncludedFile) -> None:
        self.deduped_playbook_on_include(included_file)

    @beartype
    def v2_playbook_on_no_hosts_matched(self) -> None:
        self.deduped_playbook_on_no_hosts_matched()

    @beartype
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

    @beartype
    def deduped_playbook_on_start(self, playbook: Playbook) -> None:
        "see ansible.plugins.callback.CallbackBase.v2_playbook_on_start"

    @beartype
    def deduped_playbook_on_play_start(self, play: Play) -> None:
        "see ansible.plugins.callback.CallbackBase.v2_playbook_on_play_start"

    @beartype
    def deduped_playbook_on_task_start(self, task: Task, is_conditional) -> None:
        "see ansible.plugins.callback.CallbackBase.v2_playbook_on_task_start"

    @beartype
    def deduped_playbook_on_cleanup_task_start(self, task: Task) -> None:
        "see ansible.plugins.callback.CallbackBase.v2_playbook_on_cleanup_task_start"

    @beartype
    def deduped_playbook_on_handler_task_start(self, task: Task) -> None:
        "see ansible.plugins.callback.CallbackBase.v2_playbook_on_handler_task_start"

    @beartype
    def deduped_runner_on_start(self, host: Host, task: Task) -> None:
        "see ansible.plugins.callback.CallbackBase.v2_runner_on_start"

    @beartype
    def deduped_playbook_on_stats(self, stats: AggregateStats) -> None:
        "see ansible.plugins.callback.CallbackBase.v2_playbook_on_stats"

    @beartype
    def deduped_runner_retry(self, result: TaskResult) -> None:
        "see ansible.plugins.callback.CallbackBase.v2_runner_retry"

    @beartype
    def deduped_playbook_on_notify(self, handler: Handler, host: Host) -> None:
        "see ansible.plugins.callback.CallbackBase.v2_playbook_on_notify"

    @beartype
    def deduped_playbook_on_import_for_host(self, result: TaskResult, imported_file) -> None:
        "see ansible.plugins.callback.CallbackBase.v2_playbook_on_import_for_host"

    @beartype
    def deduped_playbook_on_not_import_for_host(self, result: TaskResult, missing_file) -> None:
        "see ansible.plugins.callback.CallbackBase.v2_playbook_on_not_import_for_host"

    @beartype
    def deduped_playbook_on_include(self, included_file: IncludedFile) -> None:
        "see ansible.plugins.callback.CallbackBase.v2_playbook_on_include"

    @beartype
    def deduped_playbook_on_no_hosts_matched(self) -> None:
        "see ansible.plugins.callback.CallbackBase.v2_playbook_on_no_hosts_matched"

    @beartype
    def deduped_playbook_on_no_hosts_remaining(self) -> None:
        "see ansible.plugins.callback.CallbackBase.v2_playbook_on_no_hosts_remaining"

    @beartype
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

    @beartype
    def deduped_update_status_totals(self, status_totals: dict[str, str]) -> None:
        """
        status_totals: dictionary from status to a string representing the total number of runners
        or runner items that have that status. the total is usually digits, but it will have
        the value "?" when using a loop. see dedupe_callback.VALID_STATUSES
        """

    @beartype
    def deduped_result(
        self,
        result_id: ResultID,
        stripped_result_dict: dict,
        result_gist: ResultGist,
        gist_dupes: list[ResultID],
    ) -> None:
        """
        this encompasses all the v2 functions for "runner" and "runner item" statuses
        see ansible.plugins.callback.CallbackBase.v2_playbook_on_ok

        stripped_result_dict: the normal result dict minus warnings, deprecations, and exception

        result_gist: contains relevant information about the result which can't be derived from
        the result dict

        gist_dupes: a list of ResultIDs that have an identical gist
        """

    @beartype
    def deduped_diff(self, diff: dict, result_id: ResultID, dupe_of: list[ResultID]):
        """
        use this if you need to print diffs immediately rather than waiting until end of task
        hostnames and items are ignored when checking for dupes/groupings
        """

    @beartype
    def deduped_exception(
        self, exception: object, exception_id: ExceptionID, dupe_of: list[ExceptionID]
    ) -> None:
        """
        use this if you need to print exceptions immediately rather than waiting until end of task
        hostnames and items are ignored when checking for dupes/groupings
        """

    @beartype
    def deduped_warning(
        self, warning: str, warning_id: WarningID, dupe_of: list[WarningID]
    ) -> None:
        """
        use this if you need to print warnings immediately rather than waiting until end of task
        hostnames and items are ignored when checking for dupes/groupings
        """

    @beartype
    def deduped_deprecation(
        self, deprecation: dict, deprecation_id: DeprecationID, dupe_of: list[DeprecationID]
    ) -> None:
        """
        use this if you need to print deprecations immediately rather than waiting until end of task
        hostnames and items are ignored when checking for dupes/groupings
        """

    @beartype
    def deduped_task_end(
        self,
        result_gists_and_groupings: list[tuple[ResultGist, list[ResultID]]],
        diffs_and_groupings: list[tuple[dict, list[DiffID]]],
    ) -> None:
        """
        results_stripped_info_and_groupings: list of tuples where the first element of each tuple
        is a stripped result dict. a stripped result dict is a result dict without diffs, warnings,
        or exceptions. the second element in each tuple is a ResultInfo. the third element in each
        tuple is a list of ResultIDs that produced that result. hostnames and items are ignored
        when grouping ResultIDs.

        diffs_and_groupings: list of tuples where the first element of each tuple is a
        diff dict. the second element in each tuple is a list of ResultIDs that produced
        that result. hostnames and items are ignored when grouping ResultIDs. these are only
        the diffs from results where changed==True.

        hostnames and items are ignored for finding dupes/groupings.
        """

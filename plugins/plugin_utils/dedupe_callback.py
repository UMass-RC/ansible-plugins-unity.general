import re
import os
import json
import signal
import hashlib

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
from ansible.module_utils.common.text.converters import to_text

display = Display()


def _hash_object(x) -> str:
    json_bytes = json.dumps(x, sort_keys=True).encode("utf8")
    return hashlib.md5(json_bytes).hexdigest()


def _anonymize_result(hostname: str, result: dict) -> dict:
    """
    remove the "item" key from result
    remove hostname from any string result.values() or string result.values().values()
    if result has an item, remove that item from any string result.values() or string result.values().values()
    case insensitive
    """

    def anonymize_if_string(x: str) -> str:
        if "item" in result:
            replace_me = rf"\b({re.escape(hostname)}|{re.escape(to_text(result["item"]))})\b"
        else:
            replace_me = rf"\b{re.escape(hostname)}\b"
        if not isinstance(x, str):
            display.debug(
                f'unable to anonymize, not a string: "{to_text(val)}" of type "{type(val)}"'
            )
            return x
        return re.sub(replace_me, "ANONYMOUS", x, flags=re.IGNORECASE)

    anonymous_result = {}
    for key, val in result.items():
        if key == "item":
            continue
        if isinstance(val, dict):
            anonymous_result[key] = {k: anonymize_if_string(v) for k, v in val.items()}
        else:
            anonymous_result[key] = anonymize_if_string(val)
    return anonymous_result


class DedupeCallback(CallbackBase):
    """
    Callback plugin that reduces output size by culling redundant output.
    * at the end of the task, print the list of hosts that returned each status.
    * for the \"changed\" status, group any identical diffs and print the list of hosts which
      generated that diff. If a runner returns changed=true but no diff, a \"no diff\" message
      is used as the diff. Effectively, diff mode is always on.
    * identical errors are not printed multiple times. Instead, errors following the first printed
      will say \"same as <previous hostname>\". The errors are also anonymized so that they can
      be grouped even when the hostname is part of the error.
    * since we are collecting diffs and waiting to display them until the end of the task,
      in the event of an interrupt, mark all currently running runners as completed
      with the \"interrupted\" status. Then print the end-of-task summary as normal,
      then call the normal SIGINT handler and terminate ansible.
      with this plugin it is now easy to find out which hosts are hanging up your playbook.
      sometimes Ansible will actually ignore this interrupt and continue running, and you just
      have to send it again.
    * when using the `--step` option in `ansible-playbook`, output from the just-completed task
      is not printed until the start of the next task, which is not natural.
    * only the linear and debug strategies are allowed.
    """

    def __sigint_handler(self, signum, frame):
        """
        make sure the user knows which runners were interrupted
        since they might be blocking the playbook and might need to be excluded
        """
        # only the original parent process, no children
        if os.getpid() == self.pid_where_sigint_trapped and self.first_task_started:
            for hostname in self.running_hosts:
                self.status2hostnames["interrupted"].append(hostname)
            self.__maybe_task_end()
        # execute normal interrupt signal handler
        self.original_sigint_handler(signum, frame)

    def __init__(self):
        super(DedupeCallback, self).__init__()
        self.task_name = None
        self.status2hostnames = None
        self.running_hosts = None
        self.diff_hash2hostnames = None
        self.diff_hash2diff = None
        self.task_is_loop = None
        self.results_printed = None
        self.task_end_done = None
        # the above data is set/reset at the start of each task
        # don't try to access above data before the 1st task has started
        self.first_task_started = False

        self.original_sigint_handler = signal.getsignal(signal.SIGINT)
        self.pid_where_sigint_trapped = os.getpid()
        signal.signal(signal.SIGINT, self.__sigint_handler)

    def __task_start(self, task: Task):
        self.__maybe_task_end()
        if not self.first_task_started:
            self.first_task_started = True
        self.task_name = task.get_name()
        del self.status2hostnames
        self.status2hostnames = {
            "ok": [],
            "changed": [],
            "unreachable": [],
            "failed": [],
            "skipped": [],
            "ignored": [],
            "interrupted": [],
        }
        del self.running_hosts
        self.running_hosts = set()
        del self.diff_hash2hostnames
        self.diff_hash2hostnames = {}
        del self.diff_hash2diff
        self.diff_hash2diff = {}
        self.task_is_loop = bool(task.loop)
        del self.results_printed
        self.results_printed = {}
        self.task_end_done = False

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
        # sort the diff groupings such that the biggest groupings (most hostnames) go last
        sorted_diffs_and_hostnames = []
        sorted_diff_hash2hostnames = dict(
            sorted(self.diff_hash2hostnames.items(), key=lambda x: len(x[1]))
        )
        for diff_hash, hostnames in sorted_diff_hash2hostnames.items():
            diff = self.diff_hash2diff[diff_hash]
            sorted_diffs_and_hostnames.append((diff, hostnames))
        self.deduped_task_end(sorted_diffs_and_hostnames, self.status2hostnames)

    def __duplicate_result_of(self, result: dict, anonymous_result: dict) -> str | None:
        """
        return value is either a hostname or "{hostname} (item={item})" or None
        """
        for hostname, host_results_printed in self.results_printed.items():
            for printed_result, printed_anonymous_result in host_results_printed:
                if (result == printed_result) or (anonymous_result == printed_anonymous_result):
                    if "item" in printed_result:
                        # TODO use _get_item_label?
                        return f"{hostname} (item={printed_result["item"]})"
                    else:
                        return hostname
        return None

    def __runner_or_runner_item_end(self, result: TaskResult, status: str):
        hostname = result._host.get_name()
        anonymous_result = _anonymize_result(hostname, result._result)
        duplicate_of = self.__duplicate_result_of(result._result, anonymous_result)
        self.__register_result_diff(result)
        self.deduped_runner_or_runner_item_end(result, status, duplicate_of)
        self.results_printed.setdefault(hostname, []).append([result._result, anonymous_result])
        if not self.task_is_loop:
            try:
                self.running_hosts.remove(hostname)
            except KeyError:
                display.warning(
                    f"a runner has completed for host '{hostname}' but this host is not known to have any running runners!"
                )
        self.status2hostnames[status].append(hostname)
        self.__update_status_totals()

    def __register_result_diff(self, result: TaskResult):
        hostname = result._host.get_name()
        if not result._result.get("changed", False):
            return
        diffs = result._result.get("diff", None)
        if not diffs:
            diffs = [
                {
                    "prepared": stringc(
                        "task reports changed=true but does not report any diff.",
                        C.COLOR_CHANGED,
                    )
                }
            ]
        if not isinstance(diffs, list):
            diffs = [diffs]
        for diff in diffs:
            diff_no_headers = {
                k: v for k, v in diff.items() if k not in ["before_header", "after_header"]
            }
            diff_hash = _hash_object(diff_no_headers)
            self.diff_hash2hostnames.setdefault(diff_hash, []).append(hostname)
            self.diff_hash2diff[diff_hash] = diff

    def __update_status_totals(self):
        status_totals = {
            status: len(hostnames) for status, hostnames in self.status2hostnames.items()
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

    def v2_playbook_on_vars_prompt(self, **kwargs) -> None:
        self.deduped_playbook_on_vars_prompt(**kwargs)

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

    def deduped_playbook_on_vars_prompt(self, varname, **kwargs) -> None:
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

    def deduped_runner_or_runner_item_end(
        self, result: TaskResult, status: str, dupe_of: str | None
    ) -> None:
        """
        this is called when a runner or runner item finishes. possible values for status are:
        ok changed unreachable failed skipped ignored interrupted
        if this same result has already been returned by another runner for this task, then
        dupe_of will be the hostname of that runner.
        hostnames are ignored when checking if another host has made the same result.
        """
        pass

    def deduped_task_end(
        self,
        sorted_diffs_and_hostnames: list[tuple[dict, list[str]]],
        status2hostnames: dict[str, list[str]],
    ) -> None:
        """
        sorted_diffs_and_hostnames: list of tuples where the first element of each tuple is a
        diff dict. the second element in each tuple is a list of hostnames. the list of tuples
        is sorted such that the largest lists of hostnames are last. these are only the diffs from
        results where changed==True.

        status2hostnames: dict from status to list of hostnames. possible values for status are:
        ok changed unreachable failed skipped ignored interrupted running
        not sorted.
        """
        pass

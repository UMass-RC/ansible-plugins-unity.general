import re
import os
import json
import signal
import hashlib

from ansible import constants as C
from ansible.playbook.task import Task
from ansible.playbook.play import Play
from ansible.inventory.host import Host
from ansible.utils.color import stringc
from ansible.utils.display import Display
from ansible.executor.stats import AggregateStats
from ansible.executor.task_result import TaskResult
from ansible.module_utils.common.text.converters import to_text
from ansible.plugins.callback.default import CallbackModule as DefaultCallback

DOCUMENTATION = r"""
  name: dedupe
  short_description: remove duplicate output
  version_added: 0.1.0
  description: |
    Callback plugin that reduces output size by culling redundant output.
    * rather than showing each task-host-status on one line, display the total of number of hosts
      with each status all on one line and update that same line using carriage return.
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
    * if at least one item in a loop returns a failure, the result for the loop as whole will be
      truncated to just 'msg' and 'item_statuses'. This avoids dumping out all of the data for every
      item in the loop. 'item_statuses' is a simple overview of all the items.
  author: Simon Leary
  extends_documentation_fragment:
    default_callback
"""

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

    def anonymize_string(x: str) -> str:
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
            anonymous_result[key] = {k: anonymize_string(v) for k, v in val.items()}
        else:
            anonymous_result[key] = anonymize_string(val)
    return anonymous_result


class CallbackModule(DefaultCallback):
    CALLBACK_VERSION = 1.0
    CALLBACK_NAME = "dedupe"

    def _sigint_handler(self, signum, frame):
        """
        make sure the user knows which runners were interrupted
        since they might be blocking the playbook and might need to be excluded
        """
        # only the original parent process, no children
        if os.getpid() == self.pid_where_sigint_trapped:
            for hostname in self.running_hosts:
                self.status2hostnames["interrupted"].append(hostname)
            self._maybe_task_end()
        # execute normal interrupt signal handler
        self.original_sigint_handler(signum, frame)

    def __init__(self):
        super(CallbackModule, self).__init__()
        self.task_name = None
        self.status2hostnames = None
        self.running_hosts = None
        self.diff_hash2hostnames = None
        self.diff_hash2diff = None
        self.task_is_loop = None
        self.results_printed = None
        self.task_end_done = None
        self.hostname2loop_item_statuses = None
        # the above data is set/reset at the start of each task
        # don't try to access above data before the 1st task has started
        self.first_task_started = False

        self.original_sigint_handler = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, self._sigint_handler)
        self.pid_where_sigint_trapped = os.getpid()

    def v2_playbook_on_task_start(self, task: Task, is_conditional):
        self._task_start(task, "TASK")

    def v2_playbook_on_cleanup_task_start(self, task: Task):
        self._task_start(task, "CLEANUP TASK")

    def v2_playbook_on_handler_task_start(self, task: Task):
        self._task_start(task, "RUNNING HANDLER")

    def _task_start(self, task: Task, prefix: str):
        self._maybe_task_end()
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
        del self.hostname2loop_item_statuses
        self.hostname2loop_item_statuses = {}
        self.task_end_done = False
        self.deduped_task_start(task, prefix)

    def v2_runner_on_start(self, host: Host, task: Task):
        hostname = host.get_name()
        if not task.loop:
            self.running_hosts.add(hostname)
        self._display_status_totals()

    def _maybe_task_end(self):
        """
        The ansible callback API does not have any notion of task end.
        I thought I could detect this by keeping a number of running__hosts, incrementing on
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
        self._display_status_totals()
        # sort the diff groupings such that the biggest groupings (most hostnames) go last
        sorted_diffs_and_hostnames = []
        sorted_diff_hash2hostnames = dict(
            sorted(self.diff_hash2hostnames.items(), key=lambda x: len(x[1]))
        )
        for diff_hash, hostnames in sorted_diff_hash2hostnames.items():
            diff = self.diff_hash2diff[diff_hash]
            sorted_diffs_and_hostnames.append((diff, hostnames))
        self.deduped_task_end(sorted_diffs_and_hostnames, self.status2hostnames)

    def _duplicate_result_of(self, result: dict, anonymous_result: dict) -> str | None:
        """
        return value is either a hostname or "{hostname} (item={item})" or None
        """
        for hostname, host_results_printed in self.results_printed.items():
            for printed_result, printed_anonymous_result in host_results_printed:
                if (result == printed_result) or (anonymous_result == printed_anonymous_result):
                    if "item" in printed_result:
                        return f"{hostname} (item={printed_result["item"]})"
                    else:
                        return hostname
        return None

    def _runner_on_completed(self, result: TaskResult, status: str):
        display.v(f"{status}: {json.dumps(result._result)}")
        hostname = result._host.get_name()
        anonymous_result = _anonymize_result(hostname, result._result)
        duplicate_of = self._duplicate_result_of(result._result, anonymous_result)
        if (
            self.task_is_loop
            and "item" not in result._result
            and "failed" in self.hostname2loop_item_statuses.get(hostname, {}).values()
        ):
            display.debug(
                f"task result truncated to just 'msg' (and 'item_statuses' added) since one of the loop items already reported an error"
            )
            result._result = {
                "msg": result._result["msg"],
                "item_statuses": self.hostname2loop_item_statuses[hostname],
            }
        self.deduped_runner_end(result, status, duplicate_of)
        self.results_printed.setdefault(hostname, []).append([result._result, anonymous_result])
        if "item" in result._result:
            item_str = to_text(result._result["item"])
            self.hostname2loop_item_statuses.setdefault(hostname, {})[item_str] = status
        if not self.task_is_loop:
            try:
                self.running_hosts.remove(hostname)
            except KeyError:
                display.warning(
                    f"a runner has completed for host '{hostname}' but this host is not known to have any running runners!"
                )
        self.status2hostnames[status].append(hostname)
        self._display_status_totals()

    def v2_runner_on_ok(self, result: TaskResult):
        hostname = result._host.get_name()
        if result._result.get("changed", False):
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
            self._runner_on_completed(result, "changed")
        else:
            self._runner_on_completed(result, "ok")

    def v2_runner_on_failed(self, result: TaskResult, ignore_errors=False):
        if ignore_errors:
            self._runner_on_completed(result, "ignored")
        else:
            self._runner_on_completed(result, "failed")

    def v2_runner_on_unreachable(self, result: TaskResult):
        self._runner_on_completed(result, "unreachable")

    def v2_runner_on_skipped(self, result: TaskResult):
        self._runner_on_completed(result, "skipped")

    def v2_on_file_diff(self, result: TaskResult):
        pass  # diffs handled during `v2_runner_on_ok`

    # treat loop items the same as regular tasks
    def v2_runner_item_on_skipped(self, result: TaskResult):
        return self.v2_runner_on_skipped(result)

    def v2_runner_item_on_ok(self, result: TaskResult):
        return self.v2_runner_on_ok(result)

    def v2_runner_item_on_failed(self, result: TaskResult):
        return self.v2_runner_on_failed(result)

    def v2_playbook_on_stats(self, stats: AggregateStats):
        self._maybe_task_end()  # normally done at task_start(), but there will be no next task
        self.deduped_playbook_stats(stats)

    def v2_playbook_on_play_start(self, play: Play):
        self._maybe_task_end()  # weird edge case
        self.deduped_play_start(play)

    def _display_status_totals(self):
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
        self.deduped_display_status_totals(status_totals)

    # implement these yourself!
    def deduped_display_status_totals(self, status_totals: dict[str, str]):
        """
        status_totals: dictionary from status to a string representing the total number of runners
        or runner items that have that status. the total is usually digits, but it will have
        the value "?" when using a loop. possible values for status are:
        ok changed unreachable failed skipped ignored interrupted running
        """
        pass

    def deduped_runner_end(self, result: TaskResult, status: str, dupe_of: str | None):
        """
        this is called when a runner or runner item finishes. possible values for status are:
        ok changed unreachable failed skipped ignored interrupted
        if this same result has already been returned by another runner for this task, then
        dupe_of will be the hostname of that runner.
        hostnames are ignored when checking if another host has made the same result.
        """
        pass

    def deduped_play_start(self, play: Play):
        """
        this is called when a play starts. the default ansible callback plugin does this:
        `self._display.banner(f"{play.get_name()}")` with an optional suffix "[CHECK MODE]"
        """
        pass

    def deduped_task_start(self, task: Task, prefix: str):
        """
        this is called when a task starts. the default ansible callback plugin does this:
        self._display.banner(f"{prefix} {task.get_name()}")
        """
        pass

    def deduped_task_end(
        self,
        sorted_diffs_and_hostnames: list[tuple[dict, list[str]]],
        status2hostnames: dict[str, list[str]],
    ):
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

    def deduped_playbook_stats(self, stats: AggregateStats):
        """
        this is called at the end of an ansible playbook.
        """
        pass

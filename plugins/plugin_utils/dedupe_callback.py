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
from ansible.executor.stats import AggregateStats
from ansible.executor.task_result import TaskResult
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
      truncated to just the 'msg' property. This avoids dumping out all of the data for every
      item in the loop.
  author: Simon Leary
  extends_documentation_fragment:
    default_callback
"""


def _hash_object(x) -> str:
    json_bytes = json.dumps(x, sort_keys=True).encode("utf8")
    return hashlib.md5(json_bytes).hexdigest()


def _remove_word_from_values(word: str, x: dict) -> dict:
    output = {}
    for key, val in x.items():
        if isinstance(val, str):
            output[key] = re.sub(rf"\b{re.escape(word)}\b", "", val)
        else:
            output[key] = val
    return output


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
        self.status2hostnames = {}
        self.running_hosts = []
        self.diff_hash2hostnames = {}
        self.diff_hash2diff = {}
        self.unknown_loop_size = None
        self.results_printed = {}
        self.task_item_failure_already_reported = False
        self.task_end_done = None

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
        self.running_hosts = []
        del self.diff_hash2hostnames
        self.diff_hash2hostnames = {}
        del self.diff_hash2diff
        self.diff_hash2diff = {}
        self.unknown_loop_size = False
        del self.results_printed
        self.results_printed = {}
        del self.task_item_failure_already_reported
        self.task_item_failure_already_reported = False
        self.task_end_done = False
        self.deduped_task_start(task, prefix)

    def v2_runner_on_start(self, host: Host, task: Task):
        hostname = host.get_name()
        # task.loop is still literal and has not been evaluated/expanded yet
        if task.loop:
            self.unknown_loop_size = True
        else:
            self.running_hosts.append(hostname)
        self._update_status_totals()

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
        if self.task_end_done:
            return
        self.task_end_done = True
        self._update_status_totals()
        # sort the diff groupings such that the biggest groupings (most hostnames) go last
        sorted_diffs_and_hostnames = []
        sorted_diff_hash2hostnames = dict(
            sorted(self.diff_hash2hostnames.items(), key=lambda x: len(x[1]))
        )
        for diff_hash, hostnames in sorted_diff_hash2hostnames.items():
            diff = self.diff_hash2diff[diff_hash]
            sorted_diffs_and_hostnames.append((diff, hostnames))
        self.deduped_task_end(sorted_diffs_and_hostnames, self.status2hostnames)

    def _host_with_already_printed_result(self, result: dict, anonymous_result: dict) -> str | None:
        for hostname, (past_result, past_anon_result) in self.results_printed.items():
            if result == past_result or anonymous_result == past_anon_result:
                return hostname
        return None

    def _runner_on_completed(self, result: TaskResult, status: str):
        hostname = result._host.get_name()
        anonymous_result = _remove_word_from_values(hostname, result._result)
        already_printed_host = self._host_with_already_printed_result(
            result._result, anonymous_result
        )
        if "item" not in result._result and self.task_item_failure_already_reported:
            self._display.debug(
                f"entire-loop result truncated to just 'msg' since one of the loop items already reported an error: {json.dumps(result._result)}"
            )
            result._result = {"msg": result._result["msg"]}
        self.deduped_runner_end(result, status, already_printed_host)
        self.results_printed[hostname] = [result._result, anonymous_result]
        if status == "failed" and "item" in result._result:
            self.task_item_failure_already_reported = True
        if not self.unknown_loop_size:
            try:
                self.running_hosts.remove(hostname)
            except KeyError:
                self._display.warning(
                    f"a runner has completed for host '{hostname}' but this host is not known to have any running runners!"
                )
        self.status2hostnames[status].append(hostname)
        self._update_status_totals()

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

    def _update_status_totals(self):
        status_totals = {
            status: len(hostnames) for status, hostnames in self.status2hostnames.items()
        }
        if self.unknown_loop_size:
            status_totals["running"] = "?"
        else:
            status_totals["running"] = len(self.running_hosts)
        self.deduped_update_status_totals(status_totals)

    # implement these yourself!
    def deduped_update_status_totals(self, status_totals: dict[str, str]):
        """
        status_totals: dictionary from status to a string representing the total number of hostnames
        that returned that status. the total is usually digits, but it will have the value "?" when
        using a loop. possible values for status are:
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

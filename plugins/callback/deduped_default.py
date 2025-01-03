import os
import datetime

from ansible import context
from ansible import constants as C
from ansible.playbook import Playbook
from ansible.playbook.task import Task
from ansible.playbook.play import Play
from ansible.inventory.host import Host
from ansible.playbook.handler import Handler
from ansible.executor.stats import AggregateStats
from ansible.executor.task_result import TaskResult
from ansible.utils.color import colorize, hostcolor
from ansible.playbook.included_file import IncludedFile

from ansible_collections.unity.general.plugins.plugin_utils.hostlist import format_hostnames
from ansible_collections.unity.general.plugins.plugin_utils.dedupe_callback import DedupeCallback
from ansible_collections.unity.general.plugins.plugin_utils.format_diff_callback import (
    FormatDiffCallback,
)


DOCUMENTATION = r"""
  name: deduped_default
  type: stdout
  short_description: similar to ansible.builtin.default but using the unity.general.deduped callback
  version_added: 0.1.0
  description: |
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
    * if at least one item in a loop returns a failure, the result for the loop as whole will be
      truncated to just 'msg' and 'item_statuses'. This avoids dumping out all of the data for every
      item in the loop. 'item_statuses' is a simple overview of all the items.
    * only the linear and debug strategies are allowed.
    * check mode markers are always enabled
    * errors are never printed to stderr
    * task paths are never printed
    * custom stats are not supported
  requirements:
  - whitelist in configuration
  options:
    result_format:
      default: yaml
    pretty_results:
      default: true
  author: Simon Leary
  extends_documentation_fragment:
    - result_format_callback
    - unity.general.format_diff
"""

_STATUS_COLORS = {
    "changed": C.COLOR_CHANGED,
    "failed": C.COLOR_ERROR,
    "ignored": C.COLOR_WARN,
    "interrupted": C.COLOR_ERROR,
    "ok": C.COLOR_OK,
    "running": "normal",
    "skipped": C.COLOR_SKIP,
    "unreachable": C.COLOR_UNREACHABLE,
}

STATUSES_PRINT_IMMEDIATELY = ["failed", "unreachable"]


class CallbackModule(DedupeCallback, FormatDiffCallback):
    CALLBACK_VERSION = 1.0
    CALLBACK_TYPE = "stdout"
    CALLBACK_NAME = "unity.general.deduped_default"
    CALLBACK_NEEDS_WHITELIST = True

    def _task_start(self, task: Task, prefix: str) -> None:
        args = ""
        if not task.no_log and C.DISPLAY_ARGS_TO_STDOUT:
            args = ", ".join("%s=%s" % a for a in task.args.items())
            args = " %s" % args
        if task.check_mode:
            checkmsg = " [CHECK MODE]"
        else:
            checkmsg = ""
        self._display.banner("%s [%s%s]%s" % (prefix, task.get_name().strip(), args, checkmsg))

    def deduped_update_status_totals(self, status_totals: dict[str, str]):
        pass

    def deduped_runner_or_runner_item_end(
        self, result: TaskResult, status: str, dupe_of: str | None
    ):
        # ansible.builtin.debug sets verbose
        if not (self._run_is_verbose(result) or status in STATUSES_PRINT_IMMEDIATELY):
            return
        self._clean_results(result._result, result._task.action)
        self._handle_exception(result._result)
        self._handle_warnings(result._result)
        if "item" in result._result:
            item = f" (item={self._get_item_label(result._result)})"
        else:
            item = ""
        header = f"[{self.host_label(result)}]: {status.upper()}{item} =>"
        if dupe_of is not None:
            msg = f'{header} same result as "{dupe_of}"'
        else:
            msg = f"{header}{self._dump_results(result._result, indent=2)}"
        self._display.display(
            msg,
            color=_STATUS_COLORS[status],
        )

    def deduped_playbook_on_play_start(self, play: Play):
        play_name = play.get_name().strip()
        if play.check_mode:
            self._display.banner(f"PLAY [{play_name}] [CHECK MODE]")
        else:
            self._display.banner(f"PLAY [{play_name}]")

    def deduped_task_end(
        self,
        sorted_diffs_and_hostnames: list[dict, list[str]],
        status2hostnames: dict[str, list[str]],
    ):
        self._display.display("\n")
        for diff, hostnames in sorted_diffs_and_hostnames:
            self._display.display(self._get_diff(diff))
            self._display.display(f"changed: {format_hostnames(hostnames)}", color=C.COLOR_CHANGED)
        for status, hostnames in status2hostnames.items():
            if status == "changed":
                continue  # we already did this
            if len(hostnames) == 0:
                continue
            color = _STATUS_COLORS[status]
            self._display.display(f"{status}: {format_hostnames(hostnames)}", color=color)
        elapsed = datetime.datetime.now() - self.task_start_time
        self.task_start_time = None
        self._display.display(f"elapsed: {elapsed.total_seconds()} seconds")

    def deduped_playbook_on_stats(self, stats: AggregateStats):
        self._display.banner("PLAY RECAP")

        hosts = sorted(stats.processed.keys())
        for h in hosts:
            t = stats.summarize(h)

            self._display.display(
                "%s : %s %s %s %s %s %s %s"
                % (
                    hostcolor(h, t),
                    colorize("ok", t["ok"], C.COLOR_OK),
                    colorize("changed", t["changed"], C.COLOR_CHANGED),
                    colorize("unreachable", t["unreachable"], C.COLOR_UNREACHABLE),
                    colorize("failed", t["failures"], C.COLOR_ERROR),
                    colorize("skipped", t["skipped"], C.COLOR_SKIP),
                    colorize("rescued", t["rescued"], C.COLOR_OK),
                    colorize("ignored", t["ignored"], C.COLOR_WARN),
                ),
                screen_only=True,
            )

            self._display.display(
                "%s : %s %s %s %s %s %s %s"
                % (
                    hostcolor(h, t, False),
                    colorize("ok", t["ok"], None),
                    colorize("changed", t["changed"], None),
                    colorize("unreachable", t["unreachable"], None),
                    colorize("failed", t["failures"], None),
                    colorize("skipped", t["skipped"], None),
                    colorize("rescued", t["rescued"], None),
                    colorize("ignored", t["ignored"], None),
                ),
                log_only=True,
            )

        self._display.display("", screen_only=True)

    def deduped_playbook_on_start(self, playbook: Playbook) -> None:
        if context.CLIARGS["check"]:
            checkmsg = " [DRY RUN]"
        else:
            checkmsg = ""
        self._display.banner(f"PLAYBOOK{checkmsg}: {os.path.basename(playbook._file_name)}")
        delme = True

    def deduped_playbook_on_task_start(self, task: Task, is_conditional) -> None:
        self._task_start(task, "TASK")
        self.task_start_time = datetime.datetime.now()

    def deduped_playbook_on_cleanup_task_start(self, task: Task) -> None:
        self._task_start(task, "CLEANUP TASK")

    def deduped_playbook_on_handler_task_start(self, task: Task) -> None:
        self._task_start(task, "RUNNING HANDLER")

    def deduped_runner_retry(self, result: TaskResult) -> None:
        task_name = result.task_name or result._task
        host_label = self.host_label(result)
        msg = "FAILED - RETRYING: [%s]: %s (%d retries left)." % (
            host_label,
            task_name,
            result._result["retries"] - result._result["attempts"],
        )
        if self._run_is_verbose(result, verbosity=2):
            msg += "Result was: %s" % self._dump_results(result._result)
        self._display.display(msg, color=C.COLOR_DEBUG)

    def deduped_playbook_on_notify(self, handler: Handler, host: Host) -> None:
        if self._display.verbosity > 1:
            self._display.display(
                "NOTIFIED HANDLER %s for %s" % (handler.get_name(), host),
                color=C.COLOR_VERBOSE,
                screen_only=True,
            )

    def deduped_playbook_on_include(self, included_file: IncludedFile) -> None:
        msg = "included: %s for %s" % (
            included_file._filename,
            ", ".join([h.name for h in included_file._hosts]),
        )
        label = self._get_item_label(included_file._vars)
        if label:
            msg += " => (item=%s)" % label
        self._display.display(msg, color=C.COLOR_INCLUDED)

    def deduped_playbook_on_no_hosts_matched(self) -> None:
        self._display.display("skipping: no hosts matched", color=C.COLOR_SKIP)

    def deduped_playbook_on_no_hosts_remaining(self) -> None:
        self._display.banner("NO MORE HOSTS LEFT")

import os
import shutil
import datetime

from ansible import context
from ansible import constants as C
from ansible.playbook import Playbook
from ansible.playbook.task import Task
from ansible.playbook.play import Play
from ansible.inventory.host import Host
from ansible.utils.color import stringc
from ansible.playbook.handler import Handler
from ansible.executor.stats import AggregateStats
from ansible.executor.task_result import TaskResult
from ansible.playbook.included_file import IncludedFile
from ansible.utils.color import stringc, colorize, hostcolor

from ansible_collections.unity.general.plugins.plugin_utils.yaml import yaml_dump
from ansible_collections.unity.general.plugins.plugin_utils.hostlist import format_hostnames
from ansible_collections.unity.general.plugins.plugin_utils.format_diff import format_diff
from ansible_collections.unity.general.plugins.plugin_utils.cleanup_result import cleanup_result
from ansible_collections.unity.general.plugins.plugin_utils.dedupe_callback import DedupeCallback


DOCUMENTATION = r"""
  name: clush
  type: stdout
  short_description: inspired by Clustershell
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
    * if the ClusterShell python library is available, it will be used to \"fold\" any lists
      of hosts. Else, every hostname will be printed comma-delimited.
    * when using loops, this plugin does not display the number of running runners, since the
      loop variable has not yet been templated before it is passed to this plugin.
    * the default ansible callback displays delegated tasks in the form \"delegator -> delegatee\",
      but this callback plugin will only show the delegator.
    * tracebacks are now printed in full no matter what verbosity.
    * when using the `--step` option in `ansible-playbook`, output from the just-completed task
      is not printed until the start of the next task, which is not natural.
    * if at least one item in a loop returns a failure, the result for the loop as whole will be
      truncated to just the 'msg' property. This avoids dumping out all of the data for every
      item in the loop.
    * since we use yaml block for multiline strings, stderr_lines / stdout_lines are deleted
      if stderr/stdout exist, respectively.
  author: Simon Leary
  extends_documentation_fragment:
    - default_callback
    - unity.general.format_diff
    - unity.general.ramdisk_cache
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
# if a runner returns a result with "msg", print only "msg" rather than the full result dictionary
STATUSES_PRINT_MSG_ONLY = ["ok", "changed", "unreachable", "skipped", "ignored"]


def _indent(prepend, text):
    return prepend + text.replace("\n", "\n" + prepend)


def _tty_width() -> int:
    output, _ = shutil.get_terminal_size()
    return output


class CallbackModule(DedupeCallback):
    CALLBACK_VERSION = 2.0
    CALLBACK_TYPE = "stdout"
    CALLBACK_NAME = "clush"

    # https://github.com/ansible/ansible/pull/84496
    def get_options(self):
        return self._plugin_options

    def _clear_line(self):
        self._display.display(f"\r{' ' * _tty_width()}\r", newline=False)

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
        components = []
        for status, total in status_totals.items():
            color = _STATUS_COLORS[status]
            if total == 0:
                continue
            components.append((f"{status}={total}", color))

        # build a new list of components which, when printed, will not exceed the tty width
        at_least_one_component_stripped = False
        component_delimiter = "  "
        components_stripped = []
        components_stripped_length = 0
        tty_width = _tty_width()
        for component, color in components:
            if (components_stripped_length + len(component)) > tty_width:
                at_least_one_component_stripped = True
                break
            components_stripped.append((component, color))
            components_stripped_length += len(component) + len(component_delimiter)
        if len(components_stripped) > 0:
            # there's one trailing delimiter accounted for, remove it
            components_stripped_length -= len(component_delimiter)

        if components_stripped_length < tty_width:
            num_trailing_spaces = tty_width - components_stripped_length
        else:
            num_trailing_spaces = 0

        output = component_delimiter.join(
            [stringc(component, color) for component, color in components_stripped]
        )
        output += " " * num_trailing_spaces
        # add an arrow with white background to indicate that content was removed (`less -S`)
        if at_least_one_component_stripped:
            output = output[:-1] + "\033[30;47m>\033[0m"
        output += "\r"
        self._display.display(output, newline=False)

    def deduped_runner_or_runner_item_end(
        self, result: TaskResult, status: str, dupe_of: str | None
    ):
        hostname = result._host.get_name()
        self._handle_exception(result._result)
        self._handle_warnings(result._result)
        # ansible.builtin.debug sets verbose
        if not (self._run_is_verbose(result) or status in STATUSES_PRINT_IMMEDIATELY):
            return
        if "item" in result._result:
            header = (
                f"[{hostname}]: {status.upper()} (item={self._get_item_label(result._result)}) =>"
            )
        else:
            header = f"[{hostname}]: {status.upper()} =>"
        if dupe_of is not None:
            msg = f'{header} same result as "{dupe_of}"'
        # if msg is the only key, or msg is present and status is one of STATUSES_PRINT_MSG_ONLY
        elif "msg" in result._result and (
            status in STATUSES_PRINT_MSG_ONLY or len(result._result.keys()) == 1
        ):
            msg = f"{header} {result._result['msg']}"
        else:
            cleanup_result(result._result)
            msg = f"{header}\n{_indent(" ", yaml_dump(result._result))}"
        self._clear_line()
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
            self._display.display(format_diff(self._get_diff(diff), self.get_options()))
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
            checkmsg = "[DRY RUN]"
        else:
            checkmsg = ""
        self._display.banner(f"PLAYBOOK {checkmsg}: {os.path.basename(playbook._file_name)}")

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

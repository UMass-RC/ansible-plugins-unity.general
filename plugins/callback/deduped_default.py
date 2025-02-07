import copy
import datetime

from ansible import constants as C
from ansible.playbook import Playbook
from ansible.playbook.task import Task
from ansible.playbook.play import Play
from ansible.inventory.host import Host
from ansible.playbook.handler import Handler
from ansible.executor.stats import AggregateStats
from ansible.executor.task_result import TaskResult
from ansible.playbook.included_file import IncludedFile
from ansible.plugins.callback.default import CallbackModule as DefaultCallback

from ansible_collections.unity.general.plugins.plugin_utils.dedupe_callback import (
    DedupeCallback,
    ResultID,
    WarningID,
    ExceptionID,
    result_ids2str,
)
from ansible_collections.unity.general.plugins.plugin_utils.format_diff_callback import (
    FormatDiffCallback,
)


DOCUMENTATION = r"""
  name: deduped_default
  type: stdout
  short_description: similar to ansible.builtin.default but using the unity.general.deduped callback
  version_added: 2.18.1
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
    * async tasks are not allowed.
    * if a task is skipped and its result has a "skipped_reason" and its result doesn't have
      a "msg", then the skipped reason becomes the msg.
  requirements:
  - whitelist in configuration
  author: Simon Leary
  extends_documentation_fragment:
    - unity.general.default_callback_default_options
    - default_callback
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

STATUSES_PRINT_IMMEDIATELY = ["failed", "ignored", "unreachable"]


def _indent(_input: str, num_spaces=2) -> str:
    return (" " * num_spaces) + _input.replace("\n", ("\n" + (" " * num_spaces)))


def _truncate_width(_input: str, max_width: int) -> str:
    output = []
    for line in _input.splitlines():
        if len(line) > max_width:
            output.append(line[:max_width])
        else:
            output.append(line)
    return "\n".join(output)


def _format_status_result_ids_msg(
    status: str,
    result_ids: list[ResultID],
    msg: str = None,
    count=True,
    preferred_max_width: int | None = None,
    truncated_msg_min_len: int = 10,
    multiline=None,
):
    """
    8 possible output formats:
      - {status}: {result_ids}
      - {status}:({count}) {result_ids}
      - {status}: {result_ids} => {msg}
      - {status}:({count}) {result_ids} => {msg}
      - |
        {status}:
          {result_ids}
      - |
        {status}:({count})
          {result_ids}
      - |
        {status}:
          {result_ids}
        msg:
          {msg}
      - |
        {status}:({count})
          {result_ids}
        msg:
          {msg}
    output format is decided by whether:
      - `count` is enabled
      - `msg` is None
      - `result_ids2str(result_ids)` contains a newline or `multiline` is enabled

    `preferred_max_width` and `truncated_msg_min_length` determine how the width is truncated.
    for the single-line outputs, `msg` is truncated so that the entire output has fewer than
    `preferred_max_width` characters, but `msg` will be truncated to no fewer than
    `truncated_msg_min_len` characters. for multi-line outputs, only the message lines are
    truncated, even if the {result_ids} lines go past `preferred_max_width`.
    `truncated_msg_min_len` is ignored when truncating multi-line output, since the msg is not
    prefixed by any other data.

    `multiline` is passed along to `result_ids2str`. it can be set to either False or True to
    force output to be on one line or on muliple lines, respectively.
    """
    result_ids_str = result_ids2str(result_ids, multiline=multiline)
    count_str = f"({len(result_ids)})" if count else ""
    if "\n" in result_ids_str:
        if not msg:
            return f"{status}:{count_str}\n{_indent(result_ids_str)}"
        return "\n".join(
            [
                f"{status}:",
                f"  hosts/items {count_str}:",
                _indent(result_ids_str, num_spaces=4),
                "  msg:",
                _truncate_width(_indent(msg, num_spaces=4), max_width=preferred_max_width),
            ]
        )
    else:
        if not msg:
            return f"{status}:{count_str} {result_ids_str}"
        prefix = f"{status}:{count_str} {result_ids_str} => "
        if preferred_max_width is None:
            return prefix + msg
        truncated_msg_len = max([preferred_max_width - len(prefix), truncated_msg_min_len])
        return prefix + msg[:truncated_msg_len]


class CallbackModule(DedupeCallback, FormatDiffCallback, DefaultCallback):
    CALLBACK_VERSION = 1.0
    CALLBACK_TYPE = "stdout"
    CALLBACK_NAME = "unity.general.deduped_default"
    CALLBACK_NEEDS_WHITELIST = True

    def __task_start(self, task):
        self.task_start_time = datetime.datetime.now()
        # DefaultCallback.v2_playbook_on_task_start won't print the banner if this condition is met
        # I want the banner to always print at task start, so I just print it when I know that
        # DefaultCallback.v2_playbook_on_task_start won't print it
        # this must come after or else it will break self._last_task_name
        if not all([self.get_option("display_skipped_hosts"), self.get_option("display_ok_hosts")]):
            self._print_task_banner(task)

    def deduped_update_status_totals(self, status_totals: dict[str, str]):
        pass

    def deduped_result(
        self, result: TaskResult, status: str, result_id: ResultID, dupe_of: list[ResultID]
    ):
        if not (
            self._run_is_verbose(result)  # ansible.builtin.debug sets verbose
            or (status in STATUSES_PRINT_IMMEDIATELY)
            or (status == "ok" and self.get_option("display_ok_hosts"))
            or (status == "skipped" and self.get_option("display_skipped_hosts"))
        ):
            return
        my_result_dict = copy.deepcopy(result._result)
        self._clean_results(my_result_dict, result._task.action)
        # warnings, exceptions have been moved to deduped_warning, deduped_exception
        my_result_dict = {
            k: v for k, v in my_result_dict.items() if k not in ["warnings", "exceptions"]
        }
        if "results" in my_result_dict and not self._run_is_verbose(result):
            del my_result_dict["results"]
        # header = f"{status}: {result_id} =>"
        if len(dupe_of) > 0:
            msg = f"same result (not including diff) as {dupe_of[0]}"
        else:
            msg = self._dump_results(my_result_dict, indent=2)
        if status == "failed" and self.get_option("show_task_path_on_failure"):
            self._print_task_path(result._task)
        self._display.display(
            _format_status_result_ids_msg(status, [result_id], msg, count=False),
            color=_STATUS_COLORS[status],
            stderr=(status == "failed" and self.get_option("display_failed_stderr")),
        )

    def deduped_warning(
        self, warning: str, warning_id: WarningID, dupe_of: list[WarningID]
    ) -> None:
        if len(dupe_of) > 0:
            warning = f"same warning as {dupe_of[0]}"
        else:
            warning = f"{warning_id}: {warning}"
        self._handle_warnings({"warnings": [warning]})

    def deduped_exception(
        self, exception: str, exception_id: ExceptionID, dupe_of: list[ExceptionID]
    ) -> None:
        if len(dupe_of) > 0:
            exception = f"same exception as {dupe_of[0]}"
        else:
            exception = f"{exception_id}: {exception}"
        self._handle_exceptions({"exceptions": [exception]})

    def deduped_task_end(
        self,
        status2msg2result_ids: dict[str, list[ResultID]],
        sorted_results_stripped_and_groupings: list[tuple[dict, list[ResultID]]],
        sorted_diffs_and_groupings: list[tuple[dict, list[ResultID]]],
        warning2warning_ids: dict[str, list[WarningID]],
        exception2exception_ids: dict[str, list[ExceptionID]],
    ):
        for diff, result_ids in sorted_diffs_and_groupings:
            self._display.display(self._get_diff(diff))
            self._display.display(
                _format_status_result_ids_msg("changed", result_ids, count=False),
                color=C.COLOR_CHANGED,
            )
        for status, msg2result_ids in status2msg2result_ids.items():
            if len(msg2result_ids) == 0:  # nothing to do
                continue
            color = _STATUS_COLORS[status]
            for msg, result_ids in msg2result_ids.items():
                self._display.display(
                    _format_status_result_ids_msg(status, result_ids, msg, preferred_max_width=80),
                    color=color,
                )
        elapsed = datetime.datetime.now() - self.task_start_time
        self.task_start_time = None
        self._display.display(f"elapsed: {elapsed.total_seconds()} seconds")

    def deduped_playbook_on_play_start(self, play: Play):
        DefaultCallback.v2_playbook_on_play_start(self, play)

    def deduped_playbook_on_stats(self, stats: AggregateStats):
        DefaultCallback.v2_playbook_on_stats(self, stats)

    def deduped_playbook_on_start(self, playbook: Playbook):
        DefaultCallback.v2_playbook_on_start(self, playbook)

    def deduped_playbook_on_task_start(self, task: Task, is_conditional):
        DefaultCallback.v2_playbook_on_task_start(self, task, is_conditional)
        self.__task_start(task)

    def deduped_playbook_on_cleanup_task_start(self, task: Task):
        DefaultCallback.v2_playbook_on_cleanup_task_start(self, task)
        self.__task_start(task)

    def deduped_playbook_on_handler_task_start(self, task: Task):
        DefaultCallback.v2_playbook_on_handler_task_start(self, task)
        self.__task_start(task)

    def deduped_runner_on_start(self, host: Host, task: Task):
        DefaultCallback.v2_runner_on_start(self, host, task)

    def deduped_runner_retry(self, result: TaskResult):
        DefaultCallback.v2_runner_retry(self, result)

    def deduped_playbook_on_notify(self, handler: Handler, host: Host):
        DefaultCallback.v2_playbook_on_notify(self, handler, host)

    def deduped_playbook_on_include(self, included_file: IncludedFile):
        DefaultCallback.v2_playbook_on_include(self, included_file)

    def deduped_playbook_on_no_hosts_matched(self):
        DefaultCallback.v2_playbook_on_no_hosts_matched(self)

    def deduped_playbook_on_no_hosts_remaining(self):
        DefaultCallback.v2_playbook_on_no_hosts_remaining(self)

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
    ):
        DefaultCallback.v2_playbook_on_vars_prompt(
            self, varname, private, prompt, encrypt, confirm, salt_size, salt, default, unsafe
        )

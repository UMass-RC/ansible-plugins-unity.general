import os
import sys
import copy
import json
import hashlib
import datetime
import textwrap

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

from ansible_collections.unity.general.plugins.plugin_utils.beartype import beartype
from ansible_collections.unity.general.plugins.plugin_utils.hostlist import format_hostnames
from ansible_collections.unity.general.plugins.plugin_utils.dedupe_callback import (
    DedupeCallback,
    ResultID,
    DiffID,
    WarningID,
    ExceptionID,
    DeprecationID,
)
from ansible_collections.unity.general.plugins.plugin_utils.format_diff_callback import (
    FormatDiffCallback,
)
from ansible_collections.unity.general.plugins.plugin_utils.options_fixed_callback import (
    OptionsFixedCallback,
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
    * if a task is changed and its result has a "msg", then a new diff is added to the result
      containing that message. This means that at the end of task, you can safely skip over
      all changed results from `status2msg2result_ids`.
    * if you find that loop items are taking up too much space on screen, that means that you should
      be setting the label with `loop_control`
  requirements:
  - whitelist in configuration
  author: Simon Leary
  extends_documentation_fragment:
    - unity.general.deduped_default_callback
    - default_callback
    - result_format_callback # defines result_format, pretty_results options
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


def _hash_object_dirty(x) -> str:
    "for non json-serializable objects, just casts to string."
    json_bytes = json.dumps(x, sort_keys=True, default=str).encode("utf8")
    return hashlib.md5(json_bytes).hexdigest()


@beartype
class CallbackModule(DedupeCallback, FormatDiffCallback, OptionsFixedCallback, DefaultCallback):
    CALLBACK_VERSION = 1.0
    CALLBACK_TYPE = "stdout"
    CALLBACK_NAME = "unity.general.deduped_default"
    CALLBACK_NEEDS_WHITELIST = True

    @beartype
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.textwrapper = textwrap.TextWrapper(replace_whitespace=False)

    @beartype
    def __task_start(self, task):
        self.task_start_time = datetime.datetime.now()
        # DefaultCallback.v2_playbook_on_task_start won't print the banner if this condition is met
        # I want the banner to always print at task start, so I just print it when I know that
        # DefaultCallback.v2_playbook_on_task_start won't print it
        # this must come after or else it will break self._last_task_name
        if not all([self.get_option("display_skipped_hosts"), self.get_option("display_ok_hosts")]):
            self._print_task_banner(task)

    @beartype
    def _indent_and_maybe_wrap(self, x: str, width: int = None, indent="  "):
        if not (self.get_option("wrap_text") and sys.stdout.isatty()):
            return textwrap.indent(x, prefix=indent)
        if width is None:
            self.textwrapper.width = os.get_terminal_size().columns
        else:
            self.textwrapper.width = width
        self.textwrapper.initial_indent = indent
        self.textwrapper.subsequent_indent = indent
        output_chunks = (
            []
        )  # with replace_whitespace=False, wrapper cannot properly indent newlines in input
        for line in x.splitlines():
            output_chunks.append("\n".join(self.textwrapper.wrap(line)))
        return "\n".join(output_chunks)

    @beartype
    def result_ids2str(
        self,
        result_ids: list[ResultID],
        multiline: bool | None = None,
        preferred_max_width: int | None = None,
    ):
        """
        builds a list of hosts for each item
        then, groups items with identical lists of hosts
        if multiline isn't explicitly set to False, it may be automatically enabled
        """
        if preferred_max_width is None and sys.stdout.isatty():
            preferred_max_width = os.get_terminal_size().columns  # default 80 if not a tty
        item_hash2hostnames = {}
        item_hash2item = {}
        for result_id in result_ids:
            item_hash = _hash_object_dirty(result_id.item)
            item_hash2item[item_hash] = result_id.item
            item_hash2hostnames.setdefault(item_hash, set()).add(result_id.hostname)
        hostnames_str2items = {}
        for item_hash, hostnames in item_hash2hostnames.items():
            item = item_hash2item[item_hash]
            hostnames_str = format_hostnames(hostnames)
            hostnames_str2items.setdefault(hostnames_str, []).append(item)
        output_groupings = []
        for hostnames_str, items in hostnames_str2items.items():
            # dont want: foo,bar (items=["foo", None])
            # want: foo,bar; foo,bar(item="foo")
            if None in items:
                items.remove(None)
                output_groupings.append(hostnames_str)
            if len(items) == 1:
                output_groupings.append(f"{hostnames_str} (item={items[0]})")
            elif len(items) > 1:
                output_groupings.append(
                    f"{hostnames_str} (items={json.dumps(items, sort_keys=True, default=str)})"
                )  # dirty serialize
        oneline_output = "; ".join(output_groupings)
        if (
            multiline is None
            and preferred_max_width is not None
            and len(oneline_output) > preferred_max_width
        ):
            multiline = True
        if multiline:
            return "\n".join(output_groupings)
        return oneline_output

    @beartype
    def format_status_result_ids_msg(
        self,
        status: str,
        result_ids: list[ResultID],
        msg: str | None = None,
        preferred_max_width: int | None = None,
        multiline=None,
        do_format_msg=True,
    ):
        """
        4 possible output formats:
          - {status}: {result_ids}
          - {status}: {result_ids} => {msg}
          - |
            {status}:
              {result_ids}
          - |
            {status}:
              {result_ids} =>
                {msg}
        output format is decided by whether:
          - `msg` is truey/falsey
          - `result_ids2str(result_ids)` contains a newline or `multiline` is enabled

        `multiline` is passed along to `result_ids2str`. it can be set to either False or True to
        force output to be on one line or on muliple lines, respectively.
        """
        if preferred_max_width is None and sys.stdout.isatty():
            preferred_max_width = os.get_terminal_size().columns
        if len(result_ids) == 1:
            result_ids_str = str(result_ids[0])
        else:
            result_ids_str = self.result_ids2str(
                result_ids, multiline=multiline, preferred_max_width=preferred_max_width
            )
        if msg:
            one_line_output = f"{status}: {result_ids_str} => {msg}"
        else:
            one_line_output = f"{status}: {result_ids_str}"
        if (
            multiline is None
            and preferred_max_width is not None
            and len(one_line_output) > preferred_max_width
        ):
            multiline = True
        if not multiline:
            return one_line_output
        result_ids_str_wrapped = self._indent_and_maybe_wrap(
            result_ids_str, indent="  ", width=preferred_max_width
        )
        if not msg:
            return f"{status}:\n{result_ids_str_wrapped}"
        if not do_format_msg:
            return f"{status}:\n{result_ids_str_wrapped} =>{msg}"
        msg_wrapped = self._indent_and_maybe_wrap(msg, indent="    ", width=preferred_max_width)
        return f"{status}:\n{result_ids_str_wrapped} =>\n{msg_wrapped}"

    @beartype
    def deduped_update_status_totals(self, status_totals: dict[str, str]):
        pass

    @beartype
    def deduped_result(
        self, result: TaskResult, status: str, result_id: ResultID, dupe_of_stripped: list[ResultID]
    ) -> None:
        if not (
            self._run_is_verbose(result)  # ansible.builtin.debug sets verbose
            or (status in STATUSES_PRINT_IMMEDIATELY)
            or (status == "ok" and self.get_option("display_ok_hosts"))
            or (status == "skipped" and self.get_option("display_skipped_hosts"))
        ):
            return
        my_result_dict = copy.deepcopy(result._result)
        self._clean_results(my_result_dict, result._task.action)
        # warnings, exceptions, deprecations have been moved to their own functions
        my_result_dict = {
            k: v
            for k, v in my_result_dict.items()
            if k not in ["warnings", "exceptions", "deprecations"]
        }
        if "results" in my_result_dict and not self._run_is_verbose(result):
            del my_result_dict["results"]
        if status == "failed" and self.get_option("show_task_path_on_failure"):
            self._print_task_path(result._task)
        if len(dupe_of_stripped) > 0:
            msg = f"same result (not including diff) as {dupe_of_stripped[0]}"
            output = self.format_status_result_ids_msg(status, [result_id], msg=msg)
        else:
            output = self.format_status_result_ids_msg(
                status,
                [result_id],
                msg=self._dump_results(my_result_dict, indent=2),
                do_format_msg=False,  # _dump_results already has leading newline, indentation
            )
        self._display.display(
            output,
            color=_STATUS_COLORS[status],
            stderr=(status == "failed" and self.get_option("display_failed_stderr")),
        )

    @beartype
    def deduped_warning(
        self, warning: object, warning_id: WarningID, dupe_of: list[WarningID]
    ) -> None:
        if len(dupe_of) > 0:
            warning = f"same warning as {dupe_of[0]}"
        else:
            warning = f"{warning_id}: {warning}"
        self._handle_warnings({"warnings": [warning]})

    @beartype
    def deduped_exception(
        self, exception: str, exception_id: ExceptionID, dupe_of: list[ExceptionID]
    ) -> None:
        if len(dupe_of) > 0:
            exception = f"same exception as {dupe_of[0]}"
        else:
            exception = f"{exception_id}: {exception}"
        self._handle_exception({"exception": [exception]})

    @beartype
    def deduped_deprecation(
        self, deprecation: dict, deprecation_id: DeprecationID, dupe_of: list[DeprecationID]
    ) -> None:
        if len(dupe_of) > 0:
            self._display.warning(f"{deprecation_id}: same deprecation as {dupe_of[0]}")
        else:
            new_deprecation = deprecation.copy()
            new_deprecation["msg"] = f"{deprecation_id}: " + new_deprecation.get("msg", "")
            self._handle_warnings({"deprecations": [new_deprecation]})

    @beartype
    def deduped_task_end(
        self,
        status2msg2result_ids: dict[str, dict[(str | None), list[ResultID]]],
        results_stripped_and_groupings: list[tuple[dict, list[ResultID]]],
        diffs_and_groupings: list[tuple[dict, list[DiffID]]],
        warnings_and_groupings: list[tuple[object, list[WarningID]]],
        exceptions_and_groupings: list[tuple[object, list[ExceptionID]]],
        deprecations_and_groupings: list[tuple[object, list[DeprecationID]]],
    ):
        # Largest groupings last
        sorted_diffs_and_groupings = sorted(diffs_and_groupings, key=lambda x: len(x[1]))
        for diff, diff_ids in sorted_diffs_and_groupings:
            # convert DiffID to ResultID, discarding index
            result_ids = [ResultID(x.hostname, x.item) for x in diff_ids]
            self._display.display(self._get_diff(diff))
            self._display.display(
                self.format_status_result_ids_msg("changed", result_ids),
                color=C.COLOR_CHANGED,
            )
        for status, msg2result_ids in status2msg2result_ids.items():
            if len(msg2result_ids) == 0:  # nothing to do
                continue
            if status == "changed":
                continue
            color = _STATUS_COLORS[status]
            for msg, result_ids in msg2result_ids.items():
                self._display.display(
                    self.format_status_result_ids_msg(status, result_ids, msg=msg),
                    color=color,
                )
        elapsed = datetime.datetime.now() - self.task_start_time
        self.task_start_time = None
        self._display.display(f"elapsed: {elapsed.total_seconds()} seconds")

    @beartype
    def deduped_playbook_on_play_start(self, play: Play):
        DefaultCallback.v2_playbook_on_play_start(self, play)

    @beartype
    def deduped_playbook_on_stats(self, stats: AggregateStats):
        DefaultCallback.v2_playbook_on_stats(self, stats)

    @beartype
    def deduped_playbook_on_start(self, playbook: Playbook):
        DefaultCallback.v2_playbook_on_start(self, playbook)

    @beartype
    def deduped_playbook_on_task_start(self, task: Task, is_conditional):
        DefaultCallback.v2_playbook_on_task_start(self, task, is_conditional)
        self.__task_start(task)

    @beartype
    def deduped_playbook_on_cleanup_task_start(self, task: Task):
        DefaultCallback.v2_playbook_on_cleanup_task_start(self, task)
        self.__task_start(task)

    @beartype
    def deduped_playbook_on_handler_task_start(self, task: Task):
        DefaultCallback.v2_playbook_on_handler_task_start(self, task)
        self.__task_start(task)

    @beartype
    def deduped_runner_on_start(self, host: Host, task: Task):
        DefaultCallback.v2_runner_on_start(self, host, task)

    @beartype
    def deduped_runner_retry(self, result: TaskResult):
        DefaultCallback.v2_runner_retry(self, result)

    @beartype
    def deduped_playbook_on_notify(self, handler: Handler, host: Host):
        DefaultCallback.v2_playbook_on_notify(self, handler, host)

    @beartype
    def deduped_playbook_on_include(self, included_file: IncludedFile):
        DefaultCallback.v2_playbook_on_include(self, included_file)

    @beartype
    def deduped_playbook_on_no_hosts_matched(self):
        DefaultCallback.v2_playbook_on_no_hosts_matched(self)

    @beartype
    def deduped_playbook_on_no_hosts_remaining(self):
        DefaultCallback.v2_playbook_on_no_hosts_remaining(self)

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
    ):
        DefaultCallback.v2_playbook_on_vars_prompt(
            self, varname, private, prompt, encrypt, confirm, salt_size, salt, default, unsafe
        )

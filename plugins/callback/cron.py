from ansible import constants as C
from ansible.playbook.task import Task
from ansible.playbook.play import Play
from ansible.executor.stats import AggregateStats
from ansible.executor.task_result import TaskResult
from ansible_collections.unity.general.plugins.plugin_utils.dedupe_callback import DedupeCallback
from ansible_collections.unity.general.plugins.plugin_utils.hostlist import format_hostnames
from ansible_collections.unity.general.plugins.plugin_utils.yaml import yaml_dump
from ansible_collections.unity.general.plugins.plugin_utils.cleanup_result import cleanup_result

DOCUMENTATION = r"""
  name: cron
  type: stdout
  short_description: suitable for a cron job, with deduped output and pretty YAML
  version_added: 0.1.0
  description: |
    Callback plugin that reduces output size by culling redundant output.
    * no color
    * only warnings, changes, failures, and exceptions are printed
    * task/play banners are withheld until something needs to be printed
    * results are not printed right away unless verbose mode or result has errors. when they are
      printed, they are formatted nicely with yaml
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
  options:
    ignore_unreachable:
      default: false
      type: bool
      ini:
        - section: unity.general.cron
          key: ignore_unreachable
      env:
        - name: CRON_IGNORE_UNREACHABLE


  author: Simon Leary
  extends_documentation_fragment:
    default_callback
"""

# ignore_unreachable option takes precedence over this
STATUSES_PRINT_IMMEDIATELY = ["failed", "unreachable"]


def _indent(prepend, text):
    return prepend + text.replace("\n", "\n" + prepend)


def _banner(x, banner_len=80) -> str:
    return x + " " * (banner_len - len(x))


class CallbackModule(DedupeCallback):
    CALLBACK_VERSION = 1.0
    CALLBACK_TYPE = "stdout"
    CALLBACK_NAME = "cron"

    def __init__(self):
        super(CallbackModule, self).__init__()
        self._display_buffer = []

    # https://github.com/ansible/ansible/pull/84496
    def get_options(self):
        return self._plugin_options

    def _flush_display_buffer(self):
        if self._display_buffer:
            self._display.display("\n".join(self._display_buffer))
            del self._display_buffer
            self._display_buffer = []

    def deduped_display_status_totals(self, status_totals: dict[str, str]):
        pass

    def _display_warnings_deprecations_exceptions(self, result: TaskResult) -> None:
        # TODO don't display duplicate warnings/deprecations/exceptions
        if C.ACTION_WARNINGS:
            if "warnings" in result and result["warnings"]:
                for warning in result["warnings"]:
                    self._display.warning(yaml_dump(warning))
                del result["warnings"]
        if "exception" in result:
            msg = f"An exception occurred during task execution.\n{yaml_dump(result['exception'])}"
            self._display.display(msg, stderr=self.get_option("display_failed_stderr"))
            del result["exception"]

    def deduped_runner_end(self, result: TaskResult, status: str, dupe_of: str | None):
        hostname = result._host.get_name()
        self._display_warnings_deprecations_exceptions(result._result)
        if not (self._run_is_verbose(result) or status in STATUSES_PRINT_IMMEDIATELY):
            return
        if status == "unreachable" and self.get_option("ignore_unreachable"):
            return
        if dupe_of is not None:
            msg = f'[{hostname}]: {status.upper()} => same result as "{dupe_of}"'
        else:
            cleanup_result(result._result)
            msg = f"[{hostname}]: {status.upper()} =>\n{_indent("  ", yaml_dump(result._result))}"
        self._flush_display_buffer()
        self._display.display(msg)

    def deduped_play_start(self, play: Play):
        play_name = play.get_name().strip()
        if play.check_mode:
            self._display_buffer.append(_banner(f"PLAY [{play_name}] [CHECK MODE]"))
        else:
            self._display_buffer.append(_banner(f"PLAY [{play_name}]"))

    def deduped_task_start(self, task: Task, prefix: str):
        self._display_buffer.append(_banner(f"{prefix} [{task.get_name().strip()}] "))

    def deduped_task_end(
        self,
        sorted_diffs_and_hostnames: list[dict, list[str]],
        status2hostnames: dict[str, list[str]],
    ):
        self._flush_display_buffer()
        for diff, hostnames in sorted_diffs_and_hostnames:
            self._display.display(format_result_diff(diff, self.get_options()))
            self._display.display(f"changed: {format_hostnames(hostnames)}")

    def deduped_playbook_stats(self, stats: AggregateStats):
        pass

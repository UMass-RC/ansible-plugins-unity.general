import sys
import yaml

from ansible import constants as C
from ansible.playbook.task import Task
from ansible.playbook.play import Play
from ansible.executor.stats import AggregateStats
from ansible.vars.clean import strip_internal_keys
from ansible.executor.task_result import TaskResult
from ansible.parsing.yaml.dumper import AnsibleDumper
from ansible_collections.unity.general.plugins.callback.dedupe import (
    CallbackModule as DedupeCallback,
)

try:
    from ClusterShell.NodeSet import NodeSet

    DO_NODESET = True

except ImportError:
    print("unable to import clustershell. hostname lists will not be folded.", file=sys.stderr)

    DO_NODESET = False


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
  author: Simon Leary
  extends_documentation_fragment:
    default_callback
"""

STATUSES_PRINT_IMMEDIATELY = ["failed"]


def _indent(prepend, text):
    return prepend + text.replace("\n", "\n" + prepend)


def _format_hostnames(hosts) -> str:
    if DO_NODESET:
        return str(NodeSet.fromlist(sorted(list(hosts))))
    else:
        return ",".join(sorted(list(hosts)))


# from http://stackoverflow.com/a/15423007/115478
def _should_use_block(value):
    """Returns true if string should be in block format"""
    for c in "\u000a\u000d\u001c\u001d\u001e\u0085\u2028\u2029":
        if c in value:
            return True
    return False


# stolen from community.general.yaml callback plugin
class HumanReadableYamlDumper(AnsibleDumper):
    def represent_scalar(self, tag, value, style=None):
        """Uses block style for multi-line strings"""
        if style is None:
            if _should_use_block(value):
                style = "|"
            else:
                style = self.default_style
        node = yaml.representer.ScalarNode(tag, value, style=style)
        if self.alias_key is not None:
            self.represented_objects[self.alias_key] = node
        return node


def _yaml_dump(x):
    return yaml.dump(
        x,
        allow_unicode=True,
        width=1000,
        Dumper=HumanReadableYamlDumper,
        default_flow_style=False,
    )


def _banner(x, banner_len=80) -> str:
    return x + " " * (banner_len - len(x))


class CallbackModule(DedupeCallback):
    CALLBACK_VERSION = 1.0
    CALLBACK_TYPE = "stdout"
    CALLBACK_NAME = "cron"

    def __init__(self):
        super(CallbackModule, self).__init__()
        self._display_buffer = []

    def _flush_display_buffer(self):
        if self._display_buffer:
            self._display.display("\n".join(self._display_buffer))
            del self._display_buffer
            self._display_buffer = []

    def deduped_update_status_totals(self, status_totals: dict[str, str]):
        pass

    def _display_warnings_deprecations_exceptions(self, result: TaskResult) -> None:
        # TODO don't display duplicate warnings/deprecations/exceptions
        if C.ACTION_WARNINGS:
            if "warnings" in result and result["warnings"]:
                for warning in result["warnings"]:
                    self._display.warning(_yaml_dump(warning))
        if "exception" in result:
            msg = f"An exception occurred during task execution.\n{_yaml_dump(result['exception'])}"
            self._display.display(msg, stderr=self.get_option("display_failed_stderr"))

    def deduped_runner_end(self, result: TaskResult, status: str, dupe_of: str | None):
        hostname = result._host.get_name()
        self._display_warnings_deprecations_exceptions(result._result)
        if not (self._run_is_verbose(result) or status in STATUSES_PRINT_IMMEDIATELY):
            return
        strip_internal_keys(result._result)  # this must come after _run_is_verbose()
        if "invocation" in result._result:
            del result._result["invocation"]
        if dupe_of is not None:
            msg = f'[{hostname}]: {status.upper()} => same result as "{dupe_of}"'
        else:
            # since we use block for multiline, no need for list of lines
            if "stdout" in result._result and "stdout_lines" in result._result:
                self._display.debug(
                    f"removing stdout_lines since stdout exists: {result._result["stdout_lines"]}"
                )
                result._result.pop("stdout_lines")
            if "stderr" in result._result and "stderr_lines" in result._result:
                self._display.debug(
                    f"removing stderr_lines since stderr exists: {result._result["stderr_lines"]}"
                )
                result._result.pop("stderr_lines")
            msg = f"[{hostname}]: {status.upper()} =>\n{_indent("  ", _yaml_dump(result._result))}"
        self._flush_display_buffer()
        self._display.display(msg, stderr=self.get_option("display_failed_stderr"))

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
        for diff, hostnames in sorted_diffs_and_hostnames:
            self._flush_display_buffer()
            self._display.display(self._get_diff(diff))
            self._display.display(f"changed: {_format_hostnames(hostnames)}")

    def deduped_playbook_stats(self, stats: AggregateStats):
        pass
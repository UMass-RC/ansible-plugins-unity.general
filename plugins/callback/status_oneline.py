import shutil

from ansible import constants as C
from ansible.utils.color import stringc
from ansible_collections.unity.general.plugins.callback.deduped_default import (
    CallbackModule as DedupedDefaultCallback,
)
from ansible_collections.unity.general.plugins.plugin_utils.beartype import beartype

DOCUMENTATION = r"""
  name: status_oneline
  type: stdout
  short_description: displays the status of all runners on one line
  version_added: 2.18.1
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
    * only the linear and debug strategies are allowed.
    * async tasks are not allowed.
    * the time elapsed for each task is also printed
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


@beartype
def _tty_width() -> int:
    output, _ = shutil.get_terminal_size()
    return output


@beartype
class CallbackModule(DedupedDefaultCallback):
    CALLBACK_VERSION = 1.0
    CALLBACK_TYPE = "stdout"
    CALLBACK_NAME = "status_oneline"

    @beartype
    def __init__(self):
        super(CallbackModule, self).__init__()

    @beartype
    def _clear_line(self):
        self._display.display(f"\r{' ' * _tty_width()}\r", newline=False)

    @beartype
    def deduped_update_status_totals(self, status_totals: dict[str, str], final=False):
        components = []
        statuses_to_ignore = []
        if not self.get_option("display_ok_hosts"):
            statuses_to_ignore.append("ok")
        if not self.get_option("display_skipped_hosts"):
            statuses_to_ignore.append("skipped")
        for status, total in status_totals.items():
            if status in statuses_to_ignore:
                continue
            color = _STATUS_COLORS[status]
            if total == "0":
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
        output += "\n" if final else "\r"
        self._display.display(output, newline=False)

    @beartype
    def deduped_result(self, *args, **kwargs):
        self._clear_line()  # destroy last status line
        DedupedDefaultCallback.deduped_result(self, *args, **kwargs)

    @beartype
    def deduped_task_end(self, *args, **kwargs):
        self._clear_line()  # destroy last status line
        DedupedDefaultCallback.deduped_task_end(self, *args, **kwargs)

from ansible_collections.unity.general.plugins.callback.deduped_default import (
    CallbackModule as DedupedDefaultCallback,
)
from ansible_collections.unity.general.plugins.plugin_utils.buffered_callback import (
    BufferedCallback,
)

DOCUMENTATION = r"""
  name: cron
  type: stdout
  short_description: suitable for a cron job
  version_added: 0.1.0
  description: |
    Callback plugin that reduces output size by culling redundant output.
    * nothing is printed unless one of the results is changed or failed
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
  requirements:
    - whitelist in configuration
  options:
    ignore_unreachable:
      type: bool
      default: false
      ini:
        - section: callback_cron
          key: ignore_unreachable
      env:
        - name: CALLBACK_CRON_IGNORE_UNREACHABLE
    result_format:
      default: yaml
    pretty_results:
      default: true
  author: Simon Leary
  extends_documentation_fragment:
    - unity.general.default_callback_default_options
    - default_callback
    - result_format_callback
    - unity.general.format_diff
"""

STATUSES_DO_PRINT = ["changed", "failed"]


class CallbackModule(DedupedDefaultCallback, BufferedCallback):
    CALLBACK_VERSION = 2.0
    CALLBACK_TYPE = "stdout"
    CALLBACK_NAME = "unity.general.cron"
    CALLBACK_NEEDS_WHITELIST = True

    def __init__(self):
        super(CallbackModule, self).__init__()
        self.do_print = False

    def deduped_runner_or_runner_item_end(self, result, status, dupe_of):
        if status in STATUSES_DO_PRINT or (
            status == "unreachable" and (self.get_option("ignore_unreachable") is False)
        ):
            self.do_print = True
        super(CallbackModule, self).deduped_runner_or_runner_item_end(result, status, dupe_of)

    def deduped_playbook_on_stats(self, stats):
        super().deduped_playbook_on_stats(stats)
        if self.do_print:
            self.display_buffer()

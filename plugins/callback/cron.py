import shutil
import subprocess

from ansible_collections.unity.general.plugins.callback.deduped_default import (
    CallbackModule as DedupedDefaultCallback,
)
from ansible_collections.unity.general.plugins.plugin_utils.bitwarden_redact import bitwarden_redact
from ansible_collections.unity.general.plugins.plugin_utils.buffered_callback import (
    BufferedCallback,
)
from ansible_collections.unity.general.plugins.plugin_utils.color import decolorize
from ansible_collections.unity.general.plugins.plugin_utils.dedupe_callback import (
    VALID_STATUSES,
    DiffID,
    ResultGist,
    ResultID,
)

DOCUMENTATION = r"""
  name: cron
  type: notification
  short_description: No output if nothing interesting happened. HTML output for cron email.
  version_added: 2.18.1
  description: |
    * ANSI text is converted to HTML using aha
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
  requirements:
    - whitelist in configuration
    - aha
  options:
    redact_bitwarden:
      description: check bitwarden cache file for secrets and remove them from task results
      type: bool
      default: false
      ini:
        - section: callback_cron
          key: redact_bitwarden
      env:
        - name: CALLBACK_CRON_REDACT_BITWARDEN
    statuses_enable_print:
      description: |
        if any task result has any of these statuses, output will be printed.
        see plugins.plugin_utils.dedupe_callback.VALID_STATUSES
      type: list
      elements: str
      default:
        - changed
        - failed
        - unreachable
      ini:
        - section: callback_cron
          key: statuses_enable_print
      env:
        - name: CALLBACK_CRON_STATUSES_ENABLE_PRINT
    warning_enable_print:
      description: if enabled, any task result warnings will cause output to be printed.
      type: bool
      default: true
      ini:
        - section: callback_cron
          key: warning_enable_print
      env:
        - name: CALLBACK_CRON_WARNING_ENABLE_PRINT
    exception_enable_print:
      description: if enabled, any task result exceptions will cause output to be printed.
      type: bool
      default: true
      ini:
        - section: callback_cron
          key: exception_enable_print
      env:
        - name: CALLBACK_CRON_EXCEPTION_ENABLE_PRINT
    deprecation_enable_print:
      description: if enabled, any task result deprecation warnings will cause output to be printed.
      type: bool
      default: true
      ini:
        - section: callback_cron
          key: deprecation_enable_print
      env:
        - name: CALLBACK_CRON_DEPRECATION_ENABLE_PRINT
  author: Simon Leary
  extends_documentation_fragment:
    - unity.general.deduped_default_callback
    - default_callback
    - result_format_callback # defines result_format, pretty_results options
    - unity.general.format_diff
    - unity.general.ramdisk_cache
"""


class CallbackModule(DedupedDefaultCallback, BufferedCallback):
    CALLBACK_VERSION = 4.0
    CALLBACK_TYPE = "stdout"
    CALLBACK_NAME = "unity.general.cron"
    CALLBACK_NEEDS_WHITELIST = True

    def __init__(self):
        super().__init__()
        self._do_print = False
        self.set_options()
        statuses_enable_print = self.get_option("statuses_enable_print")
        invalid_statuses = [x for x in statuses_enable_print if x not in VALID_STATUSES]
        assert len(invalid_statuses) == 0, (
            f"invalid statuses in `statuses_enable_print`: {invalid_statuses}"
        )

    # https://github.com/ansible/ansible/pull/84496
    def get_options(self):
        return self._plugin_options

    def deduped_result(
        self,
        result_id: ResultID,
        stripped_result_dict: dict,
        result_gist: ResultGist,
        gist_dupes: list[ResultID],
    ) -> None:
        if self.get_option("redact_bitwarden"):
            stripped_result_dict = bitwarden_redact(stripped_result_dict, self.get_options())
            result_gist = ResultGist(**bitwarden_redact(result_gist, self.get_options()))
        return super().deduped_result(result_id, stripped_result_dict, result_gist, gist_dupes)

    def deduped_task_end(
        self,
        result_gists_and_groupings: list[tuple[ResultGist, list[ResultID]]],
        diffs_and_groupings: list[tuple[dict, list[DiffID]]],
        interrupted: list[ResultID],
    ) -> None:
        statuses = {gist["status"] for gist, _ in result_gists_and_groupings}
        if any(x in self.get_option("statuses_enable_print") for x in statuses):
            self._do_print = True
        return super().deduped_task_end(
            result_gists_and_groupings, diffs_and_groupings, interrupted
        )

    def deduped_playbook_on_end(self):
        if not self._do_print:
            return
        if not self._display.buffer:
            self._real_display.warning("cron: no playbook output to print!")
            return
        if shutil.which("aha") is None:
            self._real_display.warning("cron: aha not found!")
            html = f"<html><body><pre>{decolorize(self._display.buffer)}</pre></body></html>"
        else:
            aha_proc = subprocess.Popen(
                ["aha", "--black"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            html, _ = aha_proc.communicate(input=self._display.buffer)
        self._real_display.display(html)

import re

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

import re

from ansible.playbook.task import Task
from ansible.playbook.play import Play
from ansible.inventory.host import Host
from ansible.utils.display import Display
from ansible.executor.stats import AggregateStats
from ansible.executor.task_result import TaskResult
from ansible.module_utils.common.text.converters import to_text

from ansible.plugins.callback.default import CallbackModule
from ansible_collections.unity.general.plugins.plugin_utils.slack_report_cache import (
    get_report_lines,
    flush_report_lines,
)


display = Display()

DOCUMENTATION = r"""
  name: slack
  type: notification
  short_description: send results to slack
  version_added: 0.1.0
  description: TODO
  requirements:
      - whitelist in configuration
      - slack-sdk (python library)
  options:
    bot_user_oauth_token:
      required: true
      description: bot user oauth token
      env:
        - name: CALLBACK_SLACK_BOT_USER_OAUTH_TOKEN
      ini:
        - section: callback_slack
          key: bot_user_oauth_token
    channel_id:
      required: true
      description: 'slack channel ID. example: "702HMQCE5NQ"'
      env:
        - name: CALLBACK_SLACK_CHANNEL_ID
      ini:
        - section: callback_slack
          key: channel_id
    redact_bitwarden:
      description: check bitwarden cache file for secrets and remove them from task results
      type: bool
      default: false
      ini:
      - section: callback_slack
        key: redact_bitwarden
      env:
      - name: CALLBACK_SLACK_REDACT_BITWARDEN
  author: Simon Leary
  extends_documentation_fragment:
  - default_callback
  - unity.general.ramdisk_cache
"""

# https://stackoverflow.com/a/14693789/18696276
ANSI_COLOR_REGEX = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


class CallbackModule(CallbackModule):
    CALLBACK_VERSION = 2.0
    CALLBACK_TYPE = "notification"
    CALLBACK_NAME = "unity.general.slack"
    CALLBACK_NEEDS_WHITELIST = True

    def __init__(self):
        super(CallbackModule, self).__init__()
        # defined in set_options()
        self._web_client = self.channel_id = self.bot_user_oauth_token = None
        self._text_buffer = []

    # https://github.com/ansible/ansible/pull/84496
    def get_options(self):
        return self._plugin_options

    def set_options(self, task_keys=None, var_options=None, direct=None):
        super(CallbackModule, self).set_options(
            task_keys=task_keys, var_options=var_options, direct=direct
        )
        self.bot_user_oauth_token = self.get_option("bot_user_oauth_token")
        self.channel_id = self.get_option("channel_id")
        if self.bot_user_oauth_token is None:
            self._disabled = True
            display.warning(
                "bot user oauth token was not provided. this can be provided using the `SLACK_BOT_USER_OAUTH_TOKEN` environment variable."
            )
        else:
            self._web_client = WebClient(token=self.bot_user_oauth_token)

    def v2_playbook_on_stats(self, stats: AggregateStats):
        report_lines = get_report_lines(self.get_options())
        flush_report_lines(self.get_options())
        if not report_lines:
            display.warning(f"slack: no report lines found!")
            return
        report = "\n".join(report_lines)
        try:
            self._web_client.chat_postMessage(channel=self.channel_id, text=report)
        except SlackApiError as e:
            display.warning(f"failed to send message to slack! {to_text(e)}\n")

    def v2_playbook_on_task_start(self, task: Task, is_conditional):
        pass

    def v2_playbook_on_cleanup_task_start(self, task: Task):
        pass

    def v2_playbook_on_handler_task_start(self, task: Task):
        pass

    def v2_runner_on_start(self, host: Host, task: Task):
        pass

    def v2_runner_on_ok(self, result: TaskResult):
        pass

    def v2_runner_on_failed(self, result: TaskResult, ignore_errors=False):
        pass

    def v2_runner_on_unreachable(self, result: TaskResult):
        pass

    def v2_runner_on_skipped(self, result: TaskResult):
        pass

    def v2_on_file_diff(self, result: TaskResult):
        pass

    def v2_runner_item_on_skipped(self, result: TaskResult):
        pass

    def v2_runner_item_on_ok(self, result: TaskResult):
        pass

    def v2_runner_item_on_failed(self, result: TaskResult):
        pass

    def v2_playbook_on_play_start(self, play: Play):
        pass

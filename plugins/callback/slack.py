import traceback

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from ansible.utils.display import Display
from ansible.plugins.callback import CallbackBase
from ansible.module_utils.common.text.converters import to_text

from ansible_collections.unity.general.plugins.plugin_utils import slack_report_cache


display = Display()

DOCUMENTATION = r"""
  name: slack
  type: notification
  short_description: send results to slack
  version_added: 2.18.1
  description: TODO
  requirements:
      - whitelist in configuration
      - L(slack-sdk,https://pypi.org/project/slack-sdk/)
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
  author: Simon Leary
  extends_documentation_fragment:
  - unity.general.ramdisk_cache
"""


class CallbackModule(CallbackBase):
    CALLBACK_VERSION = 2.0
    CALLBACK_TYPE = "notification"
    CALLBACK_NAME = "unity.general.slack"
    CALLBACK_NEEDS_WHITELIST = True

    def __init__(self):
        super(CallbackModule, self).__init__()
        self._text_buffer = []

    # https://github.com/ansible/ansible/pull/84496
    def get_options(self):
        return self._plugin_options

    def _get_report(self) -> str:
        report_lines = slack_report_cache.get_lines(self.get_options())
        slack_report_cache.flush(self.get_options())
        return "\n".join(report_lines)

    def _send_report(self, report: str) -> None:
        try:
            web_client = WebClient(token=self.get_option("bot_user_oauth_token"))
            web_client.chat_postMessage(channel=self.get_option("channel_id"), text=report)
        except SlackApiError as e:
            display.vvv(traceback.format_exc())
            display.warning(f"slack: failed to send report! {to_text(e)}\n")

    def v2_playbook_on_start(self, _):
        # this can happen when last playbook was interrupted
        old_report = self._get_report()
        if old_report:
            display.warning("slack: found old unsent report in cache. sending now...")
        self._send_report(old_report)

    def v2_playbook_on_stats(self, _):
        report = self._get_report()
        if not report:
            display.warning("slack: no report lines found!")
            return
        self._send_report(report)

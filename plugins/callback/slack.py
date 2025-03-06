from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from ansible.utils.display import Display
from ansible.executor.stats import AggregateStats
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

    def v2_plabook_on_stats(self) -> None:
        report_lines = slack_report_cache.get_lines(self.get_options())
        slack_report_cache.flush(self.get_options())
        if not report_lines:
            display.warning(f"slack: no report lines found!")
            return
        report = "\n".join(report_lines)
        try:
            web_client = WebClient(token=self.get_option("bot_user_oauth_token"))
            web_client.chat_postMessage(channel=self.get_option("channel_id"), text=report)
        except SlackApiError as e:
            display.warning(f"failed to send report to slack! {to_text(e)}\n")

import os
import re
import sys
import socket
import shutil
import traceback
import subprocess
from io import BytesIO
from datetime import datetime, timezone
from requests.exceptions import SSLError

import requests

from ansible.playbook import Playbook
from ansible.playbook.play import Play
from ansible.errors import AnsibleError
from ansible.executor.task_result import TaskResult
from ansible_collections.unity.general.plugins.plugin_utils import slack_report_cache
from ansible_collections.unity.general.plugins.plugin_utils.bitwarden_redact import (
    bitwarden_redact,
)
from ansible_collections.unity.general.plugins.callback.deduped_default import (
    CallbackModule as DedupedDefaultCallback,
    ResultID,
    ResultGist,
)
from ansible_collections.unity.general.plugins.plugin_utils.buffered_callback import (
    BufferedCallback,
)

DOCUMENTATION = r"""
  name: http_post
  type: notification
  short_description: upload HTMl formatted log to HTTP server
  version_added: 2.18.1
  description: |
    * ANSI text is converted to HTML using aha
    * nothing is printed unless one of the results is changed or failed
    * at the end of the task, print the list of hosts that returned each status.
    * for the \"changed\" status, group any identical diffs and print the list of hosts which
      generated that diff. If a runner returns changed=true but no diff, a \"no diff\" message
      is used as the diff. Effectively, diff mode is always on.
    * identical errors are not printed multiple times. Instead, errors following the first printed
      will say \"same as <previous hostname>\". The errors are also anonymized so that they can
      be grouped even when the hostname is part of the error.
    * since we are collecting diffs and waiting to self._display them until the end of the task,
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
    - L(aha,https://github.com/theZiz/aha)
    - L(requests,https://pypi.org/project/requests/)
    - L(slack-sdk,https://pypi.org/project/slack-sdk/) (optional)
    - HTTPS web server that allows file upload
  options:
    enable:
      description:
      type: bool
      default: true
      env:
        - name: DO_POST
    upload_url:
      description: URL to upload the log to
      type: str
      required: true
      ini:
        - section: callback_http_post
          key: upload_url
      env:
        - name: CALLBACK_HTTP_POST_URL
    upload_check_mode:
      description: whether to upload when playbook was run in check mode
      type: bool
      default: false
      ini:
        - section: callback_http_post
          key: upload_check_mode
      env:
        - name: CALLBACK_HTTP_POST_UPLOAD_CHECK_MODE
    redact_bitwarden:
      description: check bitwarden cache file for secrets and remove them from task results
      type: bool
      default: false
      ini:
        - section: callback_http_post
          key: redact_bitwarden
      env:
        - name: CALLBACK_HTTP_POST_REDACT_BITWARDEN
    upload_filename:
      description: |
        Python format string that makes the uploaded file name.
        example: "{timestamp}-{playbook_name}-{username}-{hostname}.log.html"
        the only variables that can be expanded are: timestamp playbook_name username hostname
        timestamp is float number of seconds since unix epoch.
      type: str
      required: true
      ini:
        - section: callback_http_post
          key: upload_filename
      env:
        - name: CALLBACK_HTTP_POST_UPLOAD_FILENAME
    download_url:
      description: |
        Python format string that makes the download URL for the uploaded file.
        example: "https://foobar/{filename}"
        The unity.general.slack callback plugin must also be enabled.
        the only variable that can be expanded is `filename`.
      type: str
      ini:
        - section: callback_http_post
          key: download_url
      env:
        - name: CALLBACK_HTTP_POST_DOWNLOAD_URL
    slack_message:
      description: |
        Python format string that makes a message for slack. the unity.general.slack callback
        plugin is required for this to be useful.
        example: "Ansible HTML log uploaded: {download_url}".
        the only variable that can be expanded is `download_url`.
      type: str
      ini:
        - section: callback_http_post
          key: slack_message
      env:
        - name: CALLBACK_HTTP_POST_SLACK_MESSAGE
    slack_bot_user_oauth_token:
      description: bot user oauth token
      env:
        - name: CALLBACK_HTTP_POST_SLACK_BOT_USER_OAUTH_TOKEN
      ini:
        - section: callback_http_post
          key: slack_bot_user_oauth_token
    slack_channel_id:
      description: 'slack channel ID. example: "702HMQCE5NQ"'
      env:
        - name: CALLBACK_HTTP_POST_SLACK_CHANNEL_ID
      ini:
        - section: callback_http_post
          key: slack_channel_id
    result_format:
      default: yaml
    pretty_results:
      default: true
  author: Simon Leary
  extends_documentation_fragment:
    - unity.general.deduped_default_callback
    - default_callback
    - result_format_callback # defines result_format, pretty_results options
    - unity.general.format_diff
    - unity.general.ramdisk_cache
"""

# https://stackoverflow.com/a/14693789/18696276
ANSI_REGEX = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


class CallbackModule(DedupedDefaultCallback, BufferedCallback):
    CALLBACK_VERSION = 3.0
    CALLBACK_TYPE = "notification"
    CALLBACK_NAME = "unity.general.http_post"
    CALLBACK_NEEDS_WHITELIST = True

    def _send_slack_message(self, msg: str) -> None:
        from slack_sdk import WebClient
        from slack_sdk.errors import SlackApiError

        token = self.get_option("slack_bot_user_oauth_token")
        channel_id = self.get_option("slack_channel_id")
        assert (
            token is not None
        ), "slack_bot_user_oauth_token option is required when slack_message option is defined"
        assert (
            channel_id is not None
        ), "slack_channel_id option is required when slack_message option is defined"
        try:
            web_client = WebClient(token=token)
            web_client.chat_postMessage(channel=channel_id, text=msg)
        except SlackApiError as e:
            self._real_display.vvv(traceback.format_exc())
            self._real_display.warning(
                f"slack: failed to send message!\nerror: {e}\nmessage: {msg}"
            )

    def __init__(self):
        super(CallbackModule, self).__init__()
        self._playbook_name = None
        self._all_plays_check_mode = True

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

    def deduped_playbook_on_end(self):
        if not self.get_option("enable"):
            return
        if not self._display.buffer:
            self._real_display.warning(
                "http_post: log not uploaded because there is nothing to upload."
            )
            return
        if self._all_plays_check_mode and not self.get_option("upload_check_mode"):
            self._real_display.warning(
                "http_post: log not uploaded because all plays were run in check mode. this can be forced using the 'upload_check_mode' option."
            )
            return
        filename = self.get_option("upload_filename").format(
            timestamp=datetime.now(timezone.utc).timestamp(),
            playbook_name=self._playbook_name,
            username=os.getlogin(),
            hostname=socket.gethostname().split(".", 1)[0],
        )
        if shutil.which("aha") is None:
            self._real_display.warning("http_post: aha not found!")
            html_bytes = (
                "<html><body><pre>"
                + re.sub(ANSI_REGEX, "", self._display.buffer)
                + "</pre></body></html>"
            ).encode()
        else:
            aha_proc = subprocess.Popen(
                ["aha", "--black"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            # TODO is utf8 okay?
            html_bytes, _ = aha_proc.communicate(input=bytes(self._display.buffer, "utf8"))
        upload_url = self.get_option("upload_url")
        self._real_display.v(f'http_post: uploading... file "{filename}" to "{upload_url}"')
        try:
            response = requests.post(
                upload_url,
                files={"file": (filename, BytesIO(html_bytes), "text/html")},
            )
        except SSLError as e:
            if "SSLCertVerificationError" in str(e):
                raise type(e)(
                    'http_post: failed to verify SSL certificate of "%s". You might want to set REQUESTS_CA_BUNDLE=/path/to/root-ca-cert in your .envrc using direnv. %s'
                    % (self.get_option("upload_url"), str(e))
                ).with_traceback(sys.exc_info()[2])
            else:
                raise
        if response.status_code != 200:
            self._real_display.v(f'response text: "{response.text}"')
            raise AnsibleError(
                f'http_post: status_code={response.status_code}, reason="{response.reason}"\nUse -v to see response text.'
            )
        self._real_display.v("http_post: done.")
        if download_url := self.get_option("download_url"):
            download_url = download_url.format(filename=filename)
            self._real_display.display(f'http_post: download_url: "{download_url}".')
        if slack_message := self.get_option("slack_message"):
            msg = slack_message.format(download_url=download_url)
            self._send_slack_message(msg)

    def deduped_playbook_on_start(self, playbook: Playbook) -> None:
        super(CallbackModule, self).deduped_playbook_on_start(playbook)
        self._playbook_name = os.path.basename(playbook._file_name)

    def deduped_playbook_on_play_start(self, play: Play) -> None:
        super(CallbackModule, self).deduped_playbook_on_play_start(play)
        self._all_plays_check_mode &= bool(play.check_mode)

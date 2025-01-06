import os
import sys
import socket
import requests
import subprocess
from io import BytesIO
from datetime import datetime, timezone
from requests.exceptions import SSLError

from ansible.playbook import Playbook
from ansible.executor.task_result import TaskResult
from ansible_collections.unity.general.plugins.plugin_utils import slack_report_cache
from ansible_collections.unity.general.plugins.plugin_utils.bitwarden_redact import bitwarden_redact
from ansible_collections.unity.general.plugins.callback.deduped_default import (
    CallbackModule as DedupedDefaultCallback,
)
from ansible_collections.unity.general.plugins.plugin_utils.buffered_callback import (
    BufferedCallback,
)

DOCUMENTATION = r"""
  name: http_post
  type: notification
  short_description: upload HTMl formatted log to HTTP server
  version_added: 0.1.0
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
  requirements:
    - whitelist in configuration
    - aha
    - HTTPS web server that allows file upload
  options:
    upload_url:
      description: URL to upload the log to
      type: str
      required: true
      ini:
        - section: callback_http_post
          key: upload_url
      env:
        - name: CALLBACK_HTTP_POST_URL
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
    result_format:
      default: yaml
    pretty_results:
      default: true
  author: Simon Leary
  extends_documentation_fragment:
    - unity.general.default_callback_default_options
    - default_callback
    - unity.general.format_diff
    - unity.general.ramdisk_cache
"""


class CallbackModule(DedupedDefaultCallback, BufferedCallback):
    CALLBACK_VERSION = 3.0
    CALLBACK_TYPE = "notification"
    CALLBACK_NAME = "unity.general.http_post"
    CALLBACK_NEEDS_WHITELIST = True

    def __init__(self):
        super(CallbackModule, self).__init__()
        self._playbook_name = None

    # https://github.com/ansible/ansible/pull/84496
    def get_options(self):
        return self._plugin_options

    def has_option(self, x):
        return x in self._plugin_options and self._plugin_options[x] is not None

    def deduped_runner_or_runner_item_end(self, result: TaskResult, status: str, dupe_of: str):
        if self.get_option("redact_bitwarden"):
            result._result = bitwarden_redact(result._result, self.get_options())
        return super().deduped_runner_or_runner_item_end(result, status, dupe_of)

    def deduped_playbook_on_stats(self, stats):
        super(CallbackModule, self).deduped_playbook_on_stats(stats)
        if not self._display.buffer:
            self._display.warning("http_post: log not uploaded because there is nothing to upload.")
            return
        filename = self.get_option("upload_filename").format(
            timestamp=datetime.now(timezone.utc).timestamp(),
            playbook_name=self._playbook_name,
            username=os.getlogin(),
            hostname=socket.gethostname().split(".", 1)[0],
        )
        aha_proc = subprocess.Popen(
            ["aha", "--black"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        # TODO is utf8 okay?
        html_bytes, _ = aha_proc.communicate(input=bytes(self._display.buffer, "utf8"))
        self._real_display.v("http_post: uploading...")
        try:
            response = requests.post(
                self.get_option("upload_url"),
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
        response.raise_for_status()
        self._real_display.v("http_post: done.")
        if self.has_option("download_url"):
            download_url = self.get_option("download_url").format(filename=filename)
            self._real_display.display(f'http_post: download_url: "{download_url}".')
        else:
            download_url = None
        if self.has_option("slack_message"):
            msg = self.get_option("slack_message").format(download_url=download_url)
            slack_report_cache.add_line(msg, self.get_options())

    def deduped_playbook_on_start(self, playbook: Playbook) -> None:
        super(CallbackModule, self).deduped_playbook_on_start(playbook)
        self._playbook_name = os.path.basename(playbook._file_name)

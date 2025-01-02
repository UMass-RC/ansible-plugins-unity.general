import os
import sys
import socket
import requests
import subprocess
from io import BytesIO
from requests.exceptions import SSLError
from datetime import datetime, timezone

from ansible.playbook.task import Task
from ansible.playbook.play import Play
from ansible.utils.display import Display
from ansible.executor.stats import AggregateStats
from ansible.executor.task_result import TaskResult

from ansible_collections.unity.general.plugins.plugin_utils.yaml import yaml_dump
from ansible_collections.unity.general.plugins.plugin_utils.hostlist import format_hostnames
from ansible_collections.unity.general.plugins.plugin_utils.diff_callback import DiffCallback
from ansible_collections.unity.general.plugins.plugin_utils.cleanup_result import cleanup_result
from ansible_collections.unity.general.plugins.plugin_utils.dedupe_callback import DedupeCallback
from ansible_collections.unity.general.plugins.plugin_utils.bitwarden_redact import bitwarden_redact
from ansible_collections.unity.general.plugins.plugin_utils.slack_report_cache import (
    add_report_line,
)

display = Display()

DOCUMENTATION = r"""
  name: http_post
  type: notification
  short_description: upload an HTML file formatted similarly to a terminal
  version_added: 0.1.0
  description: |
    * results are not printed unless they have errors. when they are printed, they are
      formatted nicely with yaml.
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
    * if the ClusterShell python library is available, it will be used to \"fold\" any lists
      of hosts. Else, every hostname will be printed comma-delimited.
    * the default ansible callback displays delegated tasks in the form \"delegator -> delegatee\",
      but this callback plugin will only show the delegator.
    * when using the `--step` option in `ansible-playbook`, output from the just-completed task
      is not printed until the start of the next task, which is not natural.
    * verbose task results (including those from ansible.builtin.debug) are not printed.
  requirements:
      - whitelist in configuration
      - aha
      - HTTPS web server that allows file upload
  options:
    post_url:
      description: URL to upload the log to
      type: str
      required: true
      ini:
      - section: callback_http_post
        key: url
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
    link_for_slack:
      description: |
        Python format string that makes the download URL for the uploaded file.
        example: "https://foobar/{filename}"
        The unity.general.slack callback plugin must also be enabled.
      type: str
      ini:
      - section: callback_http_post
        key: link_for_slack
      env:
      - name: CALLBACK_HTTP_POST_LINK_FOR_SLACK

  author: Simon Leary
  extends_documentation_fragment:
  - default_callback
  - unity.general.diff_callback
  - unity.general.ramdisk_cache
"""

STATUSES_PRINT_IMMEDIATELY = ["failed", "unreachable"]
# if a runner returns a result with "msg", print only "msg" rather than the full result dictionary
STATUSES_PRINT_MSG_ONLY = ["ok", "changed", "unreachable", "skipped", "ignored"]


def _indent(prepend, text):
    return prepend + text.replace("\n", "\n" + prepend)


def _banner(x, banner_len=80) -> str:
    return x + ("*" * (banner_len - len(x)))


class CallbackModule(DedupeCallback, DiffCallback):
    CALLBACK_VERSION = 2.0
    CALLBACK_TYPE = "notification"
    CALLBACK_NAME = "unity.general.http_post"
    CALLBACK_NEEDS_WHITELIST = True

    def __init__(self):
        super(CallbackModule, self).__init__()
        self._always_check_mode = True
        # defined in v2_playbook_on_start
        self._playbook_name = None
        # defined in set_options()
        self._web_client = self.channel_id = self.bot_user_oauth_token = None

        self._text_buffer = []

    # https://github.com/ansible/ansible/pull/84496
    def get_options(self):
        return self._plugin_options

    def has_option(self, x):
        return x in self._plugin_options and self._plugin_options[x] is not None

    def v2_playbook_on_start(self, playbook):
        self._playbook_name = os.path.basename(playbook._file_name)

    def deduped_display_status_totals(self, status_totals: dict[str, str]):
        pass

    def deduped_runner_end(self, result: TaskResult, status: str, dupe_of: str | None):
        hostname = result._host.get_name()
        if status not in STATUSES_PRINT_IMMEDIATELY:
            return
        if self.get_option("redact_bitwarden"):
            result._result = bitwarden_redact(result._result, self.get_options())
        if dupe_of is not None:
            msg = f'[{hostname}]: {status.upper()} => same result as "{dupe_of}"'
        # if msg is the only key, or msg is present and status is one of STATUSES_PRINT_MSG_ONLY
        elif "msg" in result._result and (
            status in STATUSES_PRINT_MSG_ONLY or len(result._result.keys()) == 1
        ):
            msg = f"[{hostname}]: {status.upper()} => {result._result['msg']}"
        else:
            cleanup_result(result._result)
            msg = f"[{hostname}]: {status.upper()} =>\n{_indent("  ", yaml_dump(result._result))}"
        self._text_buffer.append(msg)

    def deduped_play_start(self, play: Play):
        play_name = play.get_name().strip()
        if play.check_mode:
            self._text_buffer.append(_banner(f"PLAY [{play_name}] [CHECK MODE]"))
        else:
            self._always_check_mode = False
            self._text_buffer.append(_banner(f"PLAY [{play_name}]"))

    def deduped_task_start(self, task: Task, prefix: str):
        self._text_buffer.append(_banner(f"{prefix} [{task.get_name().strip()}]"))

    def deduped_task_end(
        self,
        sorted_diffs_and_hostnames: list[dict, list[str]],
        status2hostnames: dict[str, list[str]],
    ):
        if self.get_option("redact_bitwarden"):
            sorted_diffs_and_hostnames = [
                (bitwarden_redact(diff, self.get_options()), hostname)
                for diff, hostname in sorted_diffs_and_hostnames
            ]
        for diff, hostnames in sorted_diffs_and_hostnames:
            for x in ["before", "after"]:
                if x in diff and not isinstance(x, str):
                    diff[x] = self._serialize_diff(diff[x])
                self._text_buffer.append(self._get_diff(diff).strip())
            self._text_buffer.append(f"changed: {format_hostnames(hostnames)}")
        for status, hostnames in status2hostnames.items():
            if status == "changed":
                continue  # we already did this
            if len(hostnames) == 0:
                continue
            self._text_buffer.append(f"{status}: {format_hostnames(hostnames)}")
        self._text_buffer.append("")

    def upload_buffer(self):
        if not self._text_buffer:
            display.v("http_post: log not uploaded because there is nothing to upload.")
            return
        if self._always_check_mode:
            display.v("http_post: log not uploaded because all tasks were in check mode.")
            return
        content = "\n".join(self._text_buffer)
        filename = "%s-%s-%s-%s.log" % (
            datetime.now(timezone.utc).timestamp(),
            self._playbook_name,
            os.getlogin(),
            socket.gethostname().split(".", 1)[0],
        )
        # filename = f"{datetime.now(timezone.utc).timestamp()}-{self._playbook_name}-{os.getlogin()}-{socket.gethostname().split(".", 1)[0]}.log"
        aha_proc = subprocess.Popen(
            ["aha", "--black"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        html_bytes, _ = aha_proc.communicate(input=bytes(content, "utf8"))
        try:
            response = requests.post(
                self.get_option("post_url"),
                files={"file": (filename, BytesIO(html_bytes), "text/html")},
            )
        except SSLError as e:
            if "SSLCertVerificationError" in str(e):
                raise type(e)(
                    'http_post: failed to verify SSL certificate of "%s". You might want to set REQUESTS_CA_BUNDLE=/path/to/root-ca-cert in your .envrc using direnv. %s'
                    % (self.get_option("post_url"), str(e))
                ).with_traceback(sys.exc_info()[2])
            else:
                raise
        response.raise_for_status()
        if self.has_option("link_for_slack"):
            link = self.get_option("link_for_slack").format(filename=filename)
            add_report_line(f"ansible HTML log: {link}", self.get_options())

    def deduped_playbook_stats(self, stats: AggregateStats):
        self.upload_buffer()

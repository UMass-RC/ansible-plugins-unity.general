import os
import re
import sys
import json
import atexit
import socket
import shutil
import subprocess

from ansible.module_utils.common.text.converters import to_text
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from ansible import constants as C
from ansible.utils.color import stringc
from ansible.playbook.task import Task
from ansible.playbook.play import Play
from ansible.executor.stats import AggregateStats
from ansible.executor.task_result import TaskResult
from ansible_collections.unity.general.plugins.plugin_utils.dedupe_callback import (
    CallbackModule as DedupeCallback,
)
from ansible_collections.unity.general.plugins.plugin_utils.yaml import yaml_dump
from ansible_collections.unity.general.plugins.plugin_utils.diff import (
    format_result_diff,
)
from ansible_collections.unity.general.plugins.plugin_utils.diff import format_result_diff
from ansible_collections.unity.general.plugins.plugin_utils.hostlist import format_hostnames
from ansible_collections.unity.general.plugins.plugin_utils.cleanup_result import cleanup_result

if shutil.which("aha") is not None:
    DO_AHA = True
else:
    print(
        "unable to find the `aha` command. output will be diff formatted rather than html.",
        file=sys.stderr,
    )
    DO_AHA = False

DOCUMENTATION = r"""
  name: slack
  type: notification
  short_description: send results to slack
  version_added: 0.1.0
  description: |
    Callback plugin that reduces output size by culling redundant output.
    * results are not printed right away unless verbose mode or result has errors. when they are
      printed, they are formatted nicely with yaml
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
  requirements:
      - whitelist in configuration
      - slack-sdk (python library)
  options:
    bot_user_oauth_token:
      required: true
      description: bot user oauth token
      env:
        - name: SLACK_BOT_USER_OAUTH_TOKEN
      ini:
        - section: callback_slack
          key: bot_user_oauth_token
    channel_id:
      required: true
      description: Slack room to post in.
      env:
        - name: SLACK_CHANNEL
      ini:
        - section: callback_slack
          key: channel_id
  author: Simon Leary
  extends_documentation_fragment:
    default_callback
"""

# https://stackoverflow.com/a/14693789/18696276
ANSI_COLOR_REGEX = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")

STATUSES_PRINT_IMMEDIATELY = ["failed", "unreachable"]
# if a runner returns a result with "msg", print only "msg" rather than the full result dictionary
STATUSES_PRINT_MSG_ONLY = ["ok", "changed", "unreachable", "skipped", "ignored"]

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


def _indent(prepend, text):
    return prepend + text.replace("\n", "\n" + prepend)


def _banner(x, banner_len=80) -> str:
    return x + ("*" * (banner_len - len(x)))


class CallbackModule(DedupeCallback):
    CALLBACK_VERSION = 2.0
    CALLBACK_TYPE = "notification"
    CALLBACK_NAME = "unity.general.slack"
    CALLBACK_NEEDS_WHITELIST = True

    def __init__(self):
        super(CallbackModule, self).__init__()
        self.disabled = False
        self._always_check_mode = True
        self.username = os.getlogin()
        self.hostname = socket.gethostname().split(".", 1)[0]
        # defined in v2_playbook_on_start
        self.playbook_name = None
        # defined in set_options()
        self.web_client = self.channel_id = self.bot_user_oauth_token = None

        self._text_buffer = []
        atexit.register(self.send_buffer_to_slack)

    def set_options(self, task_keys=None, var_options=None, direct=None):
        super(CallbackModule, self).set_options(
            task_keys=task_keys, var_options=var_options, direct=direct
        )
        self.bot_user_oauth_token = self.get_option("bot_user_oauth_token")
        self.channel_id = self.get_option("channel_id")
        if self.bot_user_oauth_token is None:
            self.disabled = True
            self._display.warning(
                "bot user oauth token was not provided. this can be provided using the `SLACK_BOT_USER_OAUTH_TOKEN` environment variable."
            )
        else:
            self.web_client = WebClient(token=self.bot_user_oauth_token)

    def v2_playbook_on_start(self, playbook):
        self.playbook_name = os.path.basename(playbook._file_name)

    def deduped_display_status_totals(self, status_totals: dict[str, str]):
        pass

    def deduped_runner_end(self, result: TaskResult, status: str, dupe_of: str | None):
        hostname = result._host.get_name()
        if not (self._run_is_verbose(result) or status in STATUSES_PRINT_IMMEDIATELY):
            return
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
        if self.disabled:
            return
        play_name = play.get_name().strip()
        if play.check_mode:
            self._text_buffer.append(_banner(f"PLAY [{play_name}] [CHECK MODE]"))
        else:
            self._always_check_mode = False
            self._text_buffer.append(_banner(f"PLAY [{play_name}]"))

    def deduped_task_start(self, task: Task, prefix: str):
        if self.disabled:
            return
        self._text_buffer.append(_banner(f"{prefix} [{task.get_name().strip()}]"))

    def deduped_task_end(
        self,
        sorted_diffs_and_hostnames: list[dict, list[str]],
        status2hostnames: dict[str, list[str]],
    ):
        if self.disabled:
            return
        for diff, hostnames in sorted_diffs_and_hostnames:
            self._text_buffer.append(format_result_diff(diff))
            self._text_buffer.append(
                stringc(f"changed: {format_hostnames(hostnames)}", _STATUS_COLORS["changed"])
            )
        for status, hostnames in status2hostnames.items():
            if status == "changed":
                continue  # we already did this
            if len(hostnames) == 0:
                continue
            self._text_buffer.append(
                stringc(f"{status}: {format_hostnames(hostnames)}", _STATUS_COLORS[status])
            )
        self._text_buffer.append("")

    def send_buffer_to_slack(self):
        if self.disabled:
            return
        if not self._text_buffer:
            return
        try:
            if self._always_check_mode:
                filename = f"{self.playbook_name}-checkmode-{self.username}-{self.hostname}.log"
            else:
                filename = f"{self.playbook_name}-{self.username}-{self.hostname}.log"
            monochrome_output = ANSI_COLOR_REGEX.sub("", "\n".join(self._text_buffer))

            if DO_AHA:
                filename += ".html"
                snippet_type = "html"
                aha_proc = subprocess.run(
                    ["aha", "--black"],
                    input=("\n".join(self._text_buffer)).encode(),
                    capture_output=True,
                )
                content = aha_proc.stdout
            else:
                filename += ".diff"
                snippet_type = "diff"
                content = monochrome_output
            kwargs = dict(
                filename=filename,
                content=content,
                snippet_type=snippet_type,
                channel=self.channel_id,
                title="",
                initial_comment=f"",
            )
            response = self.web_client.files_upload_v2(**kwargs)
        except SlackApiError as e:
            msg = f"failed to send message to slack! {to_text(e)}\n{json.dumps(kwargs, indent=4)}"
            for line in msg.splitlines():
                self._display.warning(line)
        del self._text_buffer
        self._text_buffer = []

    def deduped_playbook_stats(self, stats: AggregateStats):
        self.send_buffer_to_slack()

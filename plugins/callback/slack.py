import os
import re
import sys
import time
import json
import socket
import tempfile

# from ansible import context
from ansible.module_utils.common.text.converters import to_text
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from slack_sdk.web import SlackResponse

from ansible.playbook.task import Task
from ansible.playbook.play import Play
from ansible.executor.stats import AggregateStats
from ansible.executor.task_result import TaskResult
from ansible.parsing.yaml.dumper import AnsibleDumper
from ansible_collections.unity.general.plugins.callback.dedupe import (
    CallbackModule as DedupeCallback,
)

try:
    from ClusterShell.NodeSet import NodeSet

    DO_NODESET = True

except ImportError:
    print("unable to import clustershell. hostname lists will not be folded.", file=sys.stderr)

    DO_NODESET = False

DOCUMENTATION = r"""
  name: slack
  type: notification
  short_description: send results to slack
  version_added: 0.1.0
  description: |
    Callback plugin that reduces output size by culling redundant output.
    * task results are discarded except for "status" and "diff"
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
    channel:
      default: "#ansible"
      description: Slack room to post in.
      env:
        - name: SLACK_CHANNEL
      ini:
        - section: callback_slack
          key: channel
  author: Simon Leary
  extends_documentation_fragment:
    default_callback
"""

# https://stackoverflow.com/a/14693789/18696276
ANSI_COLOR_REGEX = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def format_hostnames(hosts) -> str:
    if DO_NODESET:
        return str(NodeSet.fromlist(sorted(list(hosts))))
    else:
        return ",".join(sorted(list(hosts)))


class CallbackModule(DedupeCallback):
    CALLBACK_VERSION = 2.0
    CALLBACK_TYPE = "notification"
    CALLBACK_NAME = "unity.general.slack"
    CALLBACK_NEEDS_WHITELIST = True

    def __init__(self):
        super(CallbackModule, self).__init__()
        self._text_buffer = []
        self.disabled = False
        self.username = os.getlogin()
        self.hostname = socket.gethostname()
        # defined in v2_playbook_on_start
        self.playbook_name = None
        # defined in set_options()
        self.web_client = self.channel = self.bot_user_oauth_token = None

    def set_options(self, task_keys=None, var_options=None, direct=None):
        super(CallbackModule, self).set_options(
            task_keys=task_keys, var_options=var_options, direct=direct
        )
        self.bot_user_oauth_token = self.get_option("bot_user_oauth_token")
        self.channel = self.get_option("channel")
        if self.bot_user_oauth_token is None:
            self.disabled = True
            self._display.warning(
                "bot user oauth token was not provided. this can be provided using the `SLACK_BOT_USER_OAUTH_TOKEN` environment variable."
            )
        else:
            self.web_client = WebClient(token=self.bot_user_oauth_token)

    def v2_playbook_on_start(self, playbook):
        self.playbook_name = os.path.basename(playbook._file_name)

    def deduped_update_status_totals(self, status_totals: dict[str, str]):
        pass

    def deduped_runner_end(self, result: TaskResult, status: str, dupe_of: str | None):
        pass

    def deduped_play_start(self, play: Play):
        if self.disabled:
            return
        play_name = play.get_name().strip()
        if play.check_mode:
            self._text_buffer.append(f"PLAY [{play_name}] [CHECK MODE]")
        else:
            self._text_buffer.append(f"PLAY [{play_name}]")

    def deduped_task_start(self, task: Task, prefix: str):
        if self.disabled:
            return
        self._text_buffer.append(f"{prefix} [{task.get_name().strip()}] ")

    def deduped_task_end(
        self,
        sorted_diffs_and_hostnames: list[dict, list[str]],
        status2hostnames: dict[str, list[str]],
    ):
        if self.disabled:
            return
        for diff, hostnames in sorted_diffs_and_hostnames:
            self._text_buffer.append(self._get_diff(diff))
            self._text_buffer.append(f"changed: {format_hostnames(hostnames)}")
        for status, hostnames in status2hostnames.items():
            if status == "changed":
                continue  # we already did this
            if len(hostnames) == 0:
                continue
            self._text_buffer.append(f"{status}: {format_hostnames(hostnames)}")
        self._text_buffer.append("")

    def deduped_playbook_stats(self, stats: AggregateStats):
        """
        send the whole buffer to slack, ignoring the playbook stats
        """
        if self.disabled:
            return
        if not self._text_buffer:
            return
        try:
            kwargs = dict(
                filename=f"{self.playbook_name}-{self.username}-{self.hostname}.log",
                content=ANSI_COLOR_REGEX.sub("", "\n".join(self._text_buffer)),
                snippet_type="diff",
                channel=self.channel,
                title="",
                initial_comment=f"",
            )
            response = self.web_client.files_upload_v2(**kwargs)
        except SlackApiError as e:
            for (
                line
            ) in f"failed to send message to slack! {to_text(e)}\n{json.dumps(kwargs, indent=4)}".splitlines():
                self._display.warning(line)

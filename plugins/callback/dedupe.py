import re
import os
import sys
import json
import shutil
import signal
import hashlib
import datetime
import threading
import subprocess

import yaml

from ansible import constants as C
from ansible.playbook.task import Task
from ansible.playbook.play import Play
from ansible.inventory.host import Host
from ansible.utils.color import stringc
from ansible.executor.stats import AggregateStats
from ansible.vars.clean import strip_internal_keys
from ansible.executor.task_result import TaskResult
from ansible.parsing.yaml.dumper import AnsibleDumper
from ansible.plugins.callback.default import CallbackModule as DefaultCallback

try:
    from ClusterShell.NodeSet import NodeSet

    DO_NODESET = True

except ImportError:
    print("unable to import clustershell. hostname lists will not be folded.", file=sys.stderr)

    DO_NODESET = False

if shutil.which("diffr"):
    DO_DIFFR = True
else:
    print("unable to locate the diffr command. diffs will not be highlighted.", file=sys.stderr)
    DO_DIFFR = False


DOCUMENTATION = r"""
  name: clush
  type: stdout
  short_description: inspired by Clustershell
  version_added: 0.1.0
  description: |
    Callback plugin that reduces output size by culling redundant output.
    * rather than showing each task-host-status on one line, display the total of number of hosts
      with each status all on one line and update that same line using carriage return.
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
    * the `diff` command is required.
    * Linux is required, because it the path /dev/fd/X to get diff output into memory.
    * if the `diffr` command is available, it is used to highlight diffs.
    * when using loops, this plugin does not display the number of running runners, since the
      loop variable has not yet been templated before it is passed to this plugin.
    * the default ansible callback displays delegated tasks in the form \"delegator -> delegatee\",
      but this callback plugin will only show the delegator.
    * tracebacks are now printed in full no matter what verbosity.
    * when using the `--step` option in `ansible-playbook`, output from the just-completed task
      is not printed until the start of the next task, which is not natural.
  author: Simon Leary
  extends_documentation_fragment:
    default_callback
"""

_STATUS_COLORS = {
    "ok": C.COLOR_OK,
    "changed": C.COLOR_CHANGED,
    "unreachable": C.COLOR_UNREACHABLE,
    "failed": C.COLOR_ERROR,
    "skipped": C.COLOR_SKIP,
    "ignored": C.COLOR_WARN,
    "interrupted": C.COLOR_ERROR,
}

STATUSES_PRINT_IMMEDIATELY = ["failed", "unreachable"]
# if a runner returns a result with "msg", print only "msg" rather than the full result dictionary
STATUSES_PRINT_MSG_ONLY = ["ok", "changed", "unreachable", "skipped", "ignored"]


def format_hostnames(hosts) -> str:
    if DO_NODESET:
        return str(NodeSet.fromlist(sorted(list(hosts))))
    else:
        return ",".join(sorted(list(hosts)))


def _tty_width() -> int:
    output, _ = shutil.get_terminal_size()
    return output


def _hash_object(x) -> str:
    json_bytes = json.dumps(x, sort_keys=True).encode("utf8")
    return hashlib.md5(json_bytes).hexdigest()


# from http://stackoverflow.com/a/15423007/115478
def should_use_block(value):
    """Returns true if string should be in block format"""
    for c in "\u000a\u000d\u001c\u001d\u001e\u0085\u2028\u2029":
        if c in value:
            return True
    return False


# stolen from community.general.yaml callback plugin
class HumanReadableYamlDumper(AnsibleDumper):
    def represent_scalar(self, tag, value, style=None):
        """Uses block style for multi-line strings"""
        if style is None:
            if should_use_block(value):
                style = "|"
            else:
                style = self.default_style
        node = yaml.representer.ScalarNode(tag, value, style=style)
        if self.alias_key is not None:
            self.represented_objects[self.alias_key] = node
        return node


def _yaml_dump(x):
    return yaml.dump(
        x,
        allow_unicode=True,
        width=1000,
        Dumper=HumanReadableYamlDumper,
        default_flow_style=False,
    )


def _remove_word_from_values(word: str, x: dict) -> dict:
    output = {}
    for key, val in x.items():
        if isinstance(val, str):
            output[key] = re.sub(rf"\b{re.escape(word)}\b", "", val)
        else:
            output[key] = val
    return output


class CallbackModule(DefaultCallback):
    CALLBACK_VERSION = 1.0
    CALLBACK_TYPE = "stdout"
    CALLBACK_NAME = "clush"

    def _sigint_handler(self, signum, frame):
        # only the original parent process, no children
        if os.getpid() == self.pid_where_sigint_trapped:
            for hostname in self.running_hosts:
                self.status2hostnames["interrupted"].append(hostname)
            self._task_end()
        # execute normal interrupt signal handler
        self.original_sigint_handler(signum, frame)

    def __init__(self):
        super(CallbackModule, self).__init__()
        self.task_name = None
        self.status2hostnames = {}
        self.running_hosts = []
        self.total_hosts = None
        self.diff_hash2hostnames = {}
        self.diff_hash2diff = {}
        self.print_play_name = None
        self.unknown_loop_size = None
        self.task_start_time = None
        self.results_printed = {}
        self.task_item_failure_already_reported = False
        self._reset_current_task_stats()

        self.original_sigint_handler = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, self._sigint_handler)
        self.pid_where_sigint_trapped = os.getpid()

    def _reset_current_task_stats(self):
        del self.task_name
        self.task_name = None
        del self.status2hostnames
        self.status2hostnames = {
            "running": [],
            "ok": [],
            "changed": [],
            "unreachable": [],
            "failed": [],
            "skipped": [],
            "ignored": [],
            "interrupted": [],
        }
        del self.running_hosts
        self.running_hosts = []
        del self.diff_hash2hostnames
        self.diff_hash2hostnames = {}
        del self.diff_hash2diff
        self.diff_hash2diff = {}
        self.unknown_loop_size = False
        self.task_start_time = None
        del self.results_printed
        self.results_printed = {}
        del self.task_item_failure_already_reported
        self.task_item_failure_already_reported = False

    def _task_start(self, task: Task, prefix: str):
        self._task_end()  # previous task, if any exists
        self.task_name = task.get_name().strip()
        self._display.banner(f"{prefix} [{self.task_name}] ")
        self._print_status_totals_oneline()
        self.task_start_time = datetime.datetime.now()

    def v2_playbook_on_task_start(self, task: Task, is_conditional):
        self._task_start(task, "TASK")

    def v2_playbook_on_cleanup_task_start(self, task: Task):
        self._task_start(task, "CLEANUP TASK")

    def v2_playbook_on_handler_task_start(self, task: Task):
        self._task_start(task, "RUNNING HANDLER")

    def v2_runner_on_start(self, host: Host, task: Task):
        hostname = host.get_name()
        # task.loop is still literal and has not been evaluated/expanded yet
        if task.loop:
            self.unknown_loop_size = True
        else:
            self.running_hosts.append(hostname)
        self._print_status_totals_oneline()

    def _clear_line(self):
        self._display.display(f"\r{' ' * _tty_width()}\r", newline=False)

    def _print_task_results(self):
        # sort the diff groupings such that the biggest groupings go last
        sorted_diff_hash2hostnames = dict(
            sorted(self.diff_hash2hostnames.items(), key=lambda x: len(x[1]))
        )
        for diff_hash, hostnames in sorted_diff_hash2hostnames.items():
            diff = self.diff_hash2diff[diff_hash]
            self._display.display(self._format_result_diff(diff))
            self._display.display(f"changed: {format_hostnames(hostnames)}", color=C.COLOR_CHANGED)
        for status, color in _STATUS_COLORS.items():
            if status == "changed":
                continue  # we already did this
            hostnames = self.status2hostnames[status]
            if len(hostnames) == 0:
                continue
            self._display.display(f"{status}: {format_hostnames(hostnames)}", color=color)
        elapsed = datetime.datetime.now() - self.task_start_time
        self._display.display(f"elapsed: {elapsed.total_seconds()} seconds")

    def _print_status_totals_oneline(self):
        components = []
        if self.unknown_loop_size:
            components.append(("running=?", "normal"))
        elif len(self.running_hosts) > 0:
            components.append((f"running={len(self.running_hosts)}", "normal"))
        for status, color in _STATUS_COLORS.items():
            count = len(self.status2hostnames[status])
            if count == 0:
                continue
            components.append((f"{status}={count}", color))

        # build a new list of components which, when printed, will not exceed the tty width
        at_least_one_component_stripped = False
        component_delimiter = "  "
        components_stripped = []
        components_stripped_length = 0
        tty_width = _tty_width()
        for component, color in components:
            if (components_stripped_length + len(component)) > tty_width:
                at_least_one_component_stripped = True
                break
            components_stripped.append((component, color))
            components_stripped_length += len(component) + len(component_delimiter)
        if len(components_stripped) > 0:
            # there's one trailing delimiter accounted for, remove it
            components_stripped_length -= len(component_delimiter)

        if components_stripped_length < tty_width:
            num_trailing_spaces = tty_width - components_stripped_length
        else:
            num_trailing_spaces = 0

        output = component_delimiter.join(
            [stringc(component, color) for component, color in components_stripped]
        )
        output += " " * num_trailing_spaces
        # add an arrow with white background to indicate that content was removed (`less -S`)
        if at_least_one_component_stripped:
            output = output[:-1] + "\033[30;47m>\033[0m"
        output += "\r"
        self._display.display(output, newline=False)

    def _task_end(self):
        """
        print task results and set the stage for the next task results
        I have not found any reliable way to detect the end of a task via this callback API,
        so this function is called in multiple places in an attempt to catch all the edge cases
        """
        # already been run
        if self.task_name == None:
            return
        self._print_status_totals_oneline()
        self._display.display("")  # preserve oneline
        self._print_task_results()
        self._reset_current_task_stats()

    def _display_warnings_deprecations_exceptions(self, result: TaskResult) -> None:
        # TODO don't display duplicate warnings/deprecations/exceptions
        if C.ACTION_WARNINGS:
            if "warnings" in result and result["warnings"]:
                for warning in result["warnings"]:
                    self._display.warning(_yaml_dump(warning))
            if "deprecations" in result and result["deprecations"]:
                for deprecation in result["deprecations"]:
                    self._display.deprecated(**deprecation)
        if "exception" in result:
            msg = f"An exception occurred during task execution.\n{_yaml_dump(result['exception'])}"
            self._display.display(
                msg, color=C.COLOR_ERROR, stderr=self.get_option("display_failed_stderr")
            )

    def _host_with_already_printed_result(self, result: dict, anonymous_result: dict) -> str:
        for hostname, (past_result, past_anon_result) in self.results_printed.items():
            if result == past_result or anonymous_result == past_anon_result:
                return hostname
        return None

    def _runner_on_completed(self, result: TaskResult, status: str):
        hostname = result._host.get_name()
        self._display_warnings_deprecations_exceptions(result._result)
        # ansible.builtin.debug sets verbose
        if self._run_is_verbose(result) or status in STATUSES_PRINT_IMMEDIATELY:
            strip_internal_keys(result._result)  # this must come after _run_is_verbose()
            if "invocation" in result._result:
                del result._result["invocation"]
            anonymous_result = _remove_word_from_values(hostname, result._result)
            if already_printed_host := self._host_with_already_printed_result(
                result._result, anonymous_result
            ):
                msg = f'[{hostname}]: {status.upper()} => same result as "{already_printed_host}"'
            # if msg is the only key, or msg is present and status is one of STATUSES_PRINT_MSG_ONLY
            elif "msg" in result._result and (
                status in STATUSES_PRINT_MSG_ONLY
                or len(result._result.keys()) == 1
                or self.task_item_failure_already_reported
            ):
                msg = f"[{hostname}]: {status.upper()} => {result._result['msg']}"
            else:
                msg = f"[{hostname}]: {status.upper()} =>\n{_yaml_dump(result._result)}"
            self.results_printed[hostname] = [result._result, anonymous_result]
            self._clear_line()
            self._display.display(
                msg,
                color=_STATUS_COLORS[status],
                stderr=self.get_option("display_failed_stderr"),
            )
        if status == "failed" and "item" in result._result:
            self.task_item_failure_already_reported = True
        if not self.unknown_loop_size:
            self.running_hosts.remove(hostname)
        self.status2hostnames[status].append(hostname)
        self._print_status_totals_oneline()

    def v2_runner_on_ok(self, result: TaskResult):
        hostname = result._host.get_name()
        if result._result.get("changed", False):
            diffs = result._result.get("diff", None)
            if not diffs:
                diffs = [
                    {
                        "prepared": stringc(
                            "task reports changed=true but does not report any diff.",
                            C.COLOR_CHANGED,
                        )
                    }
                ]
            if not isinstance(diffs, list):
                diffs = [diffs]
            for diff in diffs:
                diff_no_headers = {
                    k: v for k, v in diff.items() if k not in ["before_header", "after_header"]
                }
                diff_hash = _hash_object(diff_no_headers)
                self.diff_hash2hostnames.setdefault(diff_hash, []).append(hostname)
                self.diff_hash2diff[diff_hash] = diff
            self._runner_on_completed(result, "changed")
        else:
            self._runner_on_completed(result, "ok")

    def v2_runner_on_failed(self, result: TaskResult, ignore_errors=False):
        if ignore_errors:
            self._runner_on_completed(result, "ignored")
        else:
            self._runner_on_completed(result, "failed")

    def v2_runner_on_unreachable(self, result: TaskResult):
        self._runner_on_completed(result, "unreachable")

    def v2_runner_on_skipped(self, result: TaskResult):
        self._runner_on_completed(result, "skipped")

    def v2_on_file_diff(self, result: TaskResult):
        pass  # diffs handled during `v2_runner_on_ok`

    # treat loop items the same as regular tasks
    def v2_runner_item_on_skipped(self, result: TaskResult):
        return self.v2_runner_on_skipped(result)

    def v2_runner_item_on_ok(self, result: TaskResult):
        return self.v2_runner_on_ok(result)

    def v2_runner_item_on_failed(self, result: TaskResult):
        return self.v2_runner_on_failed(result)

    def v2_playbook_on_stats(self, stats: AggregateStats):
        self._task_end()  # normally done at task_start(), but there will be no next task
        super().v2_playbook_on_stats(stats)

    def v2_playbook_on_play_start(self, play: Play):
        self._task_end()  # weird edge case
        play_name = play.get_name().strip()
        if play.check_mode:
            self._display.banner(f"PLAY [{play_name}] [CHECK MODE]")
        else:
            self._display.banner(f"PLAY [{play_name}]")

    def _format_result_diff(self, diff: dict) -> str:
        if "prepared" in diff:
            return diff["prepared"]
        if "src_binary" in diff:
            return stringc("diff skipped: source file appears to be binary\n", C.COLOR_CHANGED)
        if "dst_binary" in diff:
            return stringc("diff skipped: destination file appears to be binary\n", C.COLOR_CHANGED)
        if "src_larger" in diff:
            return stringc(
                f"diff skipped: source file size is greater than {diff['src_larger']}\n",
                C.COLOR_CHANGED,
            )
        if "dst_larger" in diff:
            return stringc(
                f"diff skipped: destination file size is greater than {diff['dst_larger']}\n",
                C.COLOR_CHANGED,
            )
        output = ""
        if "before" in diff and "after" in diff:
            # Format complex structures into 'files'
            for x in ["before", "after"]:
                if not isinstance(diff[x], str):
                    diff[x] = self._serialize_diff(diff[x])
                elif diff[x] is None:
                    diff[x] = ""
            if diff["before"] == diff["after"]:
                return stringc(
                    "diff skipped: before and after are equal\n",
                    C.COLOR_CHANGED,
                )
            if "before_header" in diff or "after_header" in diff:
                output += stringc(
                    f"\"{diff.get('before_header', None)}\" -> \"{diff.get('after_header', None)}\"\n",
                    C.COLOR_CHANGED,
                )
            before_read_fd, before_write_fd = os.pipe()
            after_read_fd, after_write_fd = os.pipe()
            diff_proc = subprocess.Popen(
                [
                    "diff",
                    "-u",
                    "--color=always",
                    f"/dev/fd/{before_read_fd}",
                    f"/dev/fd/{after_read_fd}",
                ],
                pass_fds=[before_read_fd, after_read_fd],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )

            def write_and_close(fd, data):
                os.write(fd, data)
                os.close(fd)

            before_write_thread = threading.Thread(
                target=write_and_close, args=(before_write_fd, diff["before"].encode())
            )
            after_write_thread = threading.Thread(
                target=write_and_close, args=(after_write_fd, diff["after"].encode())
            )
            before_write_thread.start()
            after_write_thread.start()
            before_write_thread.join()
            after_write_thread.join()
            diff_output, _ = diff_proc.communicate()
            if DO_DIFFR:
                diffr_proc = subprocess.Popen(
                    "diffr", stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
                )
                diffr_output, _ = diffr_proc.communicate(input=diff_output)
                output += diffr_output.decode()
            else:
                output += diff_output.decode()
        return output

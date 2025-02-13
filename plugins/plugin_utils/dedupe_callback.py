import re
import sys
import os
import json
import signal
import hashlib
import threading
import traceback
import textwrap

from ansible import constants as C
from ansible.playbook import Playbook
from ansible.playbook.task import Task
from ansible.playbook.play import Play
from ansible.inventory.host import Host
from ansible.utils.color import stringc
from ansible.utils.display import Display
from ansible.playbook.handler import Handler
from ansible.utils.fqcn import add_internal_fqcns
from ansible.plugins.callback import CallbackBase
from ansible.executor.stats import AggregateStats
from ansible.executor.task_result import TaskResult
from ansible.playbook.included_file import IncludedFile

from ansible_collections.unity.general.plugins.plugin_utils.hostlist import format_hostnames

VALID_STATUSES = [
    "ok",
    "changed",
    "unreachable",
    "failed",
    "skipped",
    "ignored",
    "interrupted",
    "running",
]

display = Display()
textwrapper = textwrap.TextWrapper(replace_whitespace=False)


def _indent_and_maybe_wrap(x, plugin_options: dict, width: int = None, indent="  "):
    if not (plugin_options["wrap_text"] and sys.stdout.isatty()):
        return textwrap.indent(x, prefix=indent)
    if width is None:
        textwrapper.width = os.get_terminal_size().columns
    else:
        textwrapper.width = width
    textwrapper.initial_indent = indent
    textwrapper.subsequent_indent = indent
    output_chunks = (
        []
    )  # with replace_whitespace=False, wrapper cannot properly indent newlines in input
    for line in x.splitlines():
        output_chunks.append("\n".join(textwrapper.wrap(line)))
    return "\n".join(output_chunks)


def _hash_object_dirty(x) -> str:
    "for non json-serializable objects, just casts to string."
    json_bytes = json.dumps(x, sort_keys=True, default=str).encode("utf8")
    return hashlib.md5(json_bytes).hexdigest()


# TODO does this work?
def _anonymize_dict(identifiers: list[str], _input: dict) -> dict:
    """
    replace all identifiers with "ANONYMOUS" in string leaf nodes of dict tree
    """
    replace_me = "(" + "|".join([re.escape(y) for y in identifiers]) + ")"

    def anonymize_or_recurse_or_nothing(x):
        if isinstance(x, str):
            return re.sub(replace_me, "ANONYMOUS", x, flags=re.IGNORECASE)
        elif isinstance(x, list):
            return [anonymize_or_recurse_or_nothing(e) for e in x]
        elif isinstance(x, dict):
            return {k: anonymize_or_recurse_or_nothing(v) for k, v in x.items()}
        return x

    return anonymize_or_recurse_or_nothing(_input)


class ResultID:
    """
    normally I prefer to just use dictionaries but having a type makes it easier for variable names
    """

    def __init__(self, hostname: str, item: object):
        assert isinstance(hostname, str)
        self.hostname = hostname
        self.item = item

    def __str__(self):
        if self.item:
            return f"{self.hostname} (item={self.item})"
        return self.hostname


class ExceptionID(ResultID):
    "there can be only 1 exception per result"


class WarningID:
    """
    normally I prefer to just use dictionaries but having a type makes it easier for variable names
    there can be multiple warnings per result so there must also be an index
    """

    def __init__(self, hostname: str, item: object, index: int):
        assert isinstance(hostname, str)
        assert isinstance(index, int)
        self.hostname = hostname
        self.item = item
        self.index = index

    def __str__(self):
        if self.item:
            return f"{self.hostname} (item={self.item})[{self.index}]"
        return f"{self.hostname}[{self.index}]"


class DeprecationID(WarningID):
    pass


class DiffID(WarningID):
    pass


def result_ids2str(
    result_ids: list[ResultID],
    plugin_options: dict,
    multiline: bool | None = None,
    preferred_max_width: int | None = None,
):
    """
    builds a list of hosts for each item
    then, groups items with identical lists of hosts
    if multiline isn't explicitly set to False, it may be automatically enabled

    `plugin_options` is the result from AnsiblePlugin.get_options()
    your plugin must extend the unity.general.wrap_text documentation fragment
    """
    if preferred_max_width is None and sys.stdout.isatty():
        preferred_max_width = os.get_terminal_size().columns  # default 80 if not a tty
    item_hash2hostnames = {}
    item_hash2item = {}
    for result_id in result_ids:
        item_hash = _hash_object_dirty(result_id.item)
        item_hash2item[item_hash] = result_id.item
        item_hash2hostnames.setdefault(item_hash, set()).add(result_id.hostname)
    hostnames_str2items = {}
    for item_hash, hostnames in item_hash2hostnames.items():
        item = item_hash2item[item_hash]
        hostnames_str = format_hostnames(hostnames)
        hostnames_str2items.setdefault(hostnames_str, []).append(item)
    output_groupings = []
    for hostnames_str, items in hostnames_str2items.items():
        # dont want: foo,bar (items=["foo", None])
        # want: foo,bar; foo,bar(item="foo")
        if None in items:
            items.remove(None)
            output_groupings.append(hostnames_str)
        if len(items) == 1:
            output_groupings.append(f"{hostnames_str} (item={items[0]})")
        elif len(items) > 1:
            output_groupings.append(
                f"{hostnames_str} (items={json.dumps(items, sort_keys=True, default=str)})"
            )  # dirty serialize
    oneline_output = "; ".join(output_groupings)
    if (
        multiline is None
        and preferred_max_width is not None
        and len(oneline_output) > preferred_max_width
    ):
        multiline = True
    if multiline:
        return "\n".join(output_groupings)
    return oneline_output


def format_status_result_ids_msg(
    status: str,
    result_ids: list[ResultID],
    plugin_options: dict,
    msg: str = None,
    preferred_max_width: int | None = None,
    multiline=None,
    do_format_msg=True,
):
    """
    4 possible output formats:
      - {status}: {result_ids}
      - {status}: {result_ids} => {msg}
      - |
        {status}:
          {result_ids}
      - |
        {status}:
          {result_ids} =>
            {msg}
    output format is decided by whether:
      - `msg` is truey/falsey
      - `result_ids2str(result_ids)` contains a newline or `multiline` is enabled

    `multiline` is passed along to `result_ids2str`. it can be set to either False or True to
    force output to be on one line or on muliple lines, respectively.

    `plugin_options` is the result from AnsiblePlugin.get_options()
    your plugin must extend the unity.general.wrap_text documentation fragment
    """
    if preferred_max_width is None and sys.stdout.isatty():
        preferred_max_width = os.get_terminal_size().columns
    if len(result_ids) == 1:
        result_ids_str = str(result_ids[0])
    else:
        result_ids_str = result_ids2str(
            result_ids, plugin_options, multiline=multiline, preferred_max_width=preferred_max_width
        )
    if msg:
        one_line_output = f"{status}: {result_ids_str} => {msg}"
    else:
        one_line_output = f"{status}: {result_ids_str}"
    if (
        multiline is None
        and preferred_max_width is not None
        and len(one_line_output) > preferred_max_width
    ):
        multiline = True
    if not multiline:
        return one_line_output
    result_ids_str_wrapped = _indent_and_maybe_wrap(
        result_ids_str, plugin_options, indent="  ", width=preferred_max_width
    )
    if not msg:
        return f"{status}:\n{result_ids_str_wrapped}"
    if not do_format_msg:
        return f"{status}:\n{result_ids_str_wrapped} =>{msg}"
    msg_wrapped = _indent_and_maybe_wrap(
        msg, plugin_options, indent="    ", width=preferred_max_width
    )
    return f"{status}:\n{result_ids_str_wrapped} =>\n{msg_wrapped}"


class Grouper:
    def __init__(self, id_type):
        self._id_type = id_type
        self._preprocessed_values = []
        self.values_1st_match = []
        self.ids = []

    def add(self, _id, value, preprocessed_value=None) -> list[object]:
        "returns list of dupes"
        assert isinstance(_id, self._id_type), f"expected {self._id_type}, got {type(_id)}"
        if preprocessed_value is None:
            preprocessed_value = value
        for i, group_preprocessed_value in enumerate(self._preprocessed_values):
            if preprocessed_value == group_preprocessed_value:
                dupes = self.ids[i].copy()
                self.ids[i].append(_id)
                return dupes
        self._preprocessed_values.append(preprocessed_value)
        self.values_1st_match.append(value)
        self.ids.append([_id])
        return []

    def export(self) -> list[tuple[object, list[object]]]:
        "each tuple has value on left and list of ids on right"
        output = []
        for i, value in enumerate(self.values_1st_match):
            ids = self.ids[i]
            output.append((value, ids))
        return output


class DedupeCallback(CallbackBase):
    """
    Callback plugin that reduces output size by culling redundant output.
    * at the end of the task, print the list of hosts that returned each status.
    * each result is "anonymized", so that hostname and item differences are ignored for deduping.
      each result is broken up into five parts: diffs, warnings, exceptions, deprecations, and
      "stripped result". "stripped result" is just the remainder with the other parts removed.
      duplicate diffs, warnings, exceptions, and stripped results are grouped so that unnecessary
      output can be avoided. each can be printed immediately or at the end of task.
    * each result is given a "status", see dedupe_callback.VALID_STATUSES
    * since information might not be printed immediately, SIGINT is trapped to display results
      before ansible exits.
    * If a result hash changed=true but no diff, a \"no diff\" message is used as the diff
    * when using the `--step` option in `ansible-playbook`, output from the just-completed task
      is not printed until the start of the next task, which is not natural.
    * only the linear and debug strategies are allowed.
    * async tasks are not allowed.
    * if a task is skipped and its result has a "skipped_reason" and its result doesn't have
      a "msg", then the skipped reason becomes the msg.
    * if a task is changed and its result has a "msg", then a new diff is added to the result
      containing that message. This means that at the end of task, you can safely skip over
      all changed results from `status2msg2result_ids`.
    * if you find that loop items are taking up too much space on screen, that means that you should
      be setting the label with `loop_control`
    """

    def __sigint_handler(self, signum, frame):
        """
        make sure the user knows which runners were interrupted
        since they might be blocking the playbook and might need to be excluded
        """
        _id = f"pid={os.getpid()} thread={threading.get_ident()} self={self}"
        _id_hash = hashlib.md5(_id.encode()).hexdigest()[:5]
        display.v(f"[{_id_hash}] = SIGINT caught!")
        display.v(f"[{_id_hash}] = {_id}")
        display.v(f"[{_id_hash}] stack trace: {traceback.format_stack()}")
        try:
            display.v(f"[{_id_hash}] acquiring sigint handler lock...")
            self.__sigint_handler_lock.acquire()
            display.v(f"[{_id_hash}] sigint handler lock acquired.")
            if self.__sigint_handler_run:
                display.warning(
                    f"[{_id_hash}] caught multiple SIGINT, sending SIGKILL to PID {os.getpid()}. Use -v for more information."
                )
                os.kill(os.getpid(), signal.SIGKILL)
            if not self.first_task_started:
                display.v(
                    f"[{_id_hash}]: first task not yet started, skipping special sigint logic..."
                )
                return
            if os.getpid() != self.pid_where_sigint_trapped:
                display.v(
                    f"[{_id_hash}]: pid != {self.pid_where_sigint_trapped}, skipping special sigint logic..."
                )
                return
            self.__sigint_handler_run = True
            for hostname in self.running_hosts:
                self.__runner_or_runner_item_end_dict({}, ResultID(hostname, None), "interrupted")
            del self.running_hosts
            self.running_hosts = set()
            self.__maybe_task_end()
        finally:
            display.v(f"[{_id_hash}] releasing sigint handler lock...")
            self.__sigint_handler_lock.release()
            display.v(f"[{_id_hash}] executing original sigint handler...")
            self.original_sigint_handler(signum, frame)

    def __init__(self):
        super(DedupeCallback, self).__init__()
        self.task_name = None
        self.task_is_loop = None
        self.task_end_done = None
        self.running_hosts = None
        self.status2result_ids = None
        self.result_id2status = None
        self.warning_grouper = None
        self.exception_grouper = None
        self.deprecation_grouper = None
        self.diff_grouper = None
        self.result_stripped_grouper = None
        self.result_stripped_status = None
        # the above data is set/reset at the start of each task
        # don't try to access above data before the 1st task has started
        self.first_task_started = False
        self.pid_where_sigint_trapped = os.getpid()
        self.__sigint_handler_lock = threading.RLock()
        self.__sigint_handler_run = False

        self.original_sigint_handler = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, self.__sigint_handler)

    def __task_start(self, task: Task):
        self.__maybe_task_end()
        self.task_name = task.get_name()
        self.task_is_loop = bool(task.loop)
        self.task_end_done = False
        del self.running_hosts
        self.running_hosts = set()
        del self.status2result_ids
        self.status2result_ids = {
            "ok": [],
            "changed": [],
            "unreachable": [],
            "failed": [],
            "skipped": [],
            "ignored": [],
            "interrupted": [],
        }
        del self.result_id2status
        self.result_id2status = {}
        del (
            self.warning_grouper,
            self.exception_grouper,
            self.deprecation_grouper,
            self.diff_grouper,
            self.result_stripped_grouper,
        )
        self.warning_grouper = Grouper(WarningID)
        self.exception_grouper = Grouper(ExceptionID)
        self.deprecation_grouper = Grouper(DeprecationID)
        self.diff_grouper = Grouper(DiffID)
        self.result_stripped_grouper = Grouper(ResultID)
        if not self.first_task_started:
            self.first_task_started = True

    def __runner_start(self, host: Host, task: Task):
        hostname = host.get_name()
        if not task.loop:
            self.running_hosts.add(hostname)
        self.__update_status_totals()

    def __maybe_task_end(self):
        """
        The ansible callback API does not have any notion of task end.
        I thought I could detect this by keeping a number of running_hosts, incrementing on
        v2_runner_start and decrementing on v2_runner_*, but this has false positives:
        there can be times when the number of running runners is 0 but more runners will still
        be spawned in the future.
        I thought I could detect this by comparing the number of unique hostnames of completed
        runners against `ansible_play_hosts_all`, but this won't work for skipped tasks because
        there will never be any completed runners.
        To make up for this, I call this function multiple times later and make sure it only
        runs once.
        """
        if (not self.first_task_started) or self.task_end_done:
            return
        self.task_end_done = True
        self.__update_status_totals()

        status2msg2result_ids = {}
        for i, (result_stripped, grouping) in enumerate(self.result_stripped_grouper.export()):
            status = self.result_id2status[grouping[0]]
            status2msg2result_ids.setdefault(status, {}).setdefault(
                result_stripped.get("msg", None), []
            ).extend(grouping)

        self.deduped_task_end(
            status2msg2result_ids,
            self.result_stripped_grouper.export(),
            self.diff_grouper.export(),
            self.warning_grouper.export(),
            self.exception_grouper.export(),
            self.deprecation_grouper.export(),
        )

    def __runner_or_runner_item_end_dict(
        self, result: dict, result_id: ResultID, status: str
    ) -> list[ResultID]:
        hostname = result_id.hostname
        item = result_id.item
        # prompte "skipped_reason" to "msg" so that user can see
        if status == "skipped" and "msg" not in result and "skipped_reason" in result:
            result["msg"] = result["skipped_reason"]
        result_stripped = {
            k: v for k, v in result.items() if k not in ["exception", "warnings", "deprecations"]
        }
        anon_result_stripped = _anonymize_dict([str(hostname), str(item)], result_stripped)
        result_stripped_dupes = self.result_stripped_grouper.add(
            result_id, result_stripped, preprocessed_value=anon_result_stripped
        )

        for i, warning in enumerate(result.get("warnings", [])):
            warning_id = WarningID(hostname, item, i)
            dupe_of = self.warning_grouper.add(warning_id, warning)
            self.deduped_warning(warning, warning_id, dupe_of)
        for i, deprecation in enumerate(result.get("deprecations", [])):
            deprecation_id = DeprecationID(hostname, item, i)
            dupe_of = self.deprecation_grouper.add(deprecation_id, deprecation)
            self.deduped_deprecation(deprecation, deprecation_id, dupe_of)
        if exception := result.get("exception", None):
            exception_id = ExceptionID(hostname, item)
            dupe_of = self.exception_grouper.add(exception_id, exception)
            self.deduped_exception(exception, exception_id, dupe_of)

        if result.get("changed", False):
            diff_or_diffs = result.get("diff", [])
            if not isinstance(diff_or_diffs, list):
                diffs = [diff_or_diffs]
            else:
                diffs = diff_or_diffs
            diffs = [x for x in diffs if x]
            if msg := result.get("msg", None):
                diffs.append({"prepared": msg.strip()})
            if len(diffs) == 0:
                diffs = [
                    {
                        "prepared": stringc(
                            "task reports changed=true but does not report any diff.",
                            C.COLOR_CHANGED,
                        )
                    }
                ]
            for i, diff in enumerate(diffs):
                diff_no_headers = {
                    k: v for k, v in diff.items() if k not in ["before_header", "after_header"]
                }
                anon_diff = _anonymize_dict([hostname, str(item)], diff_no_headers)
                self.diff_grouper.add(DiffID(hostname, item, i), diff, preprocessed_value=anon_diff)

        if not self.task_is_loop:
            try:
                self.running_hosts.remove(hostname)
            except KeyError:
                self._display.warning(
                    f"a runner has completed for host '{hostname}' but this host is not known to have any running runners!"
                )
        self.__update_status_totals()

        self.result_id2status[result_id] = status
        self.status2result_ids[status].append(result_id)

        return result_stripped_dupes

    def __runner_or_runner_item_end(self, result: TaskResult, status: str):
        hostname = CallbackBase.host_label(result)
        item = self._get_item_label(result._result)
        result_id = ResultID(hostname, item)
        result_stripped_dupes = self.__runner_or_runner_item_end_dict(
            result._result, result_id, status
        )
        self.deduped_result(result, status, result_id, result_stripped_dupes)
        self.__update_status_totals()

    def __update_status_totals(self):
        status_totals = {
            status: len(result_ids) for status, result_ids in self.status2result_ids.items()
        }
        # I have to work around this edge case because _runner_on_completed removes hostname
        # from the running_hosts list, and the same host can't be removed multiple times.
        # if I knew the length of the loop I could add the same host multiple times so that
        # it could be removed multiple times, but I don't because the loop variable has not
        # been evaluated.
        if self.task_is_loop:
            status_totals["running"] = "?"
        else:
            status_totals["running"] = len(self.running_hosts)
        self.deduped_update_status_totals(status_totals)

    def __play_start(self, play: Play):
        strategy_fqcn = add_internal_fqcns([play.strategy])[0]
        if not strategy_fqcn in add_internal_fqcns(("linear", "debug")):
            raise RuntimeError(
                f'Unsupported strategy: "{play.strategy}". Supported strategies are "linear" and "debug".'
            )

    def __check_diff_always(self) -> None:
        if not C.DIFF_ALWAYS:
            self._display.warning(
                "DIFF_ALWAYS is not enabled. It is highly recommended that you enable it!"
                + " The whole point of using the deduped_callback API is to make diff information manageable."
            )

    # V2 API #######################################################################################
    def v2_on_any(self, *args, **kwargs):
        self.deduped_on_any(*args, **kwargs)

    def v2_runner_on_start(self, host: Host, task: Task) -> None:
        self.__runner_start(host, task)
        self.deduped_runner_on_start(host, task)

    def v2_runner_on_unreachable(self, result: TaskResult) -> None:
        self.__runner_or_runner_item_end(result, "unreachable")

    def v2_runner_on_skipped(self, result: TaskResult) -> None:
        self.__runner_or_runner_item_end(result, "skipped")

    def v2_runner_item_on_skipped(self, result: TaskResult) -> None:
        self.__runner_or_runner_item_end(result, "skipped")

    def v2_runner_on_ok(self, result: TaskResult) -> None:
        if result._result.get("changed", False):
            self.__runner_or_runner_item_end(result, "changed")
        else:
            self.__runner_or_runner_item_end(result, "ok")

    def v2_runner_item_on_ok(self, result: TaskResult) -> None:
        if result._result.get("changed", False):
            self.__runner_or_runner_item_end(result, "changed")
        else:
            self.__runner_or_runner_item_end(result, "ok")

    def v2_runner_on_failed(self, result: TaskResult, ignore_errors=False) -> None:
        if ignore_errors:
            self.__runner_or_runner_item_end(result, "ignored")
        else:
            self.__runner_or_runner_item_end(result, "failed")

    def v2_runner_item_on_failed(self, result: TaskResult) -> None:
        self.__runner_or_runner_item_end(result, "failed")

    def v2_runner_retry(self, result: TaskResult) -> None:
        self.deduped_runner_retry(result)

    def v2_on_file_diff(self, result) -> None:
        # I need to replace empty diffs with a "no diff" message, and this is not called
        # for empty diffs. instead I handle diffs during __runner_or_runner_item_end
        pass

    def v2_playbook_on_task_start(self, task: Task, is_conditional) -> None:
        self.__task_start(task)
        self.deduped_playbook_on_task_start(task, is_conditional)

    def v2_playbook_on_cleanup_task_start(self, task: Task) -> None:
        self.__task_start(task)
        self.deduped_playbook_on_cleanup_task_start(task)

    def v2_playbook_on_handler_task_start(self, task: Task) -> None:
        self.__task_start(task)
        self.deduped_playbook_on_handler_task_start(task)

    def v2_playbook_on_play_start(self, play: Play) -> None:
        self.__maybe_task_end()  # weird edge case
        self.__play_start(play)
        self.deduped_playbook_on_play_start(play)

    def v2_playbook_on_start(self, playbook: Playbook) -> None:
        self.__check_diff_always()
        self.deduped_playbook_on_start(playbook)

    def v2_playbook_on_notify(self, handler: Handler, host: Host) -> None:
        self.deduped_playbook_on_notify(handler, host)

    def v2_playbook_on_import_for_host(self, result: TaskResult, imported_file) -> None:
        self.deduped_playbook_on_import_for_host(result, imported_file)

    def v2_playbook_on_not_import_for_host(self, result: TaskResult, missing_file) -> None:
        self.deduped_playbook_on_not_import_for_host(result, missing_file)

    def v2_playbook_on_include(self, included_file: IncludedFile) -> None:
        self.deduped_playbook_on_include(included_file)

    def v2_playbook_on_no_hosts_matched(self) -> None:
        self.deduped_playbook_on_no_hosts_matched()

    def v2_playbook_on_no_hosts_remaining(self) -> None:
        self.deduped_playbook_on_no_hosts_remaining()

    def v2_playbook_on_vars_prompt(
        self,
        varname,
        private=True,
        prompt=None,
        encrypt=None,
        confirm=False,
        salt_size=None,
        salt=None,
        default=None,
        unsafe=None,
    ) -> None:
        self.deduped_playbook_on_vars_prompt(
            varname, private, prompt, encrypt, confirm, salt_size, salt, default, unsafe
        )

    def v2_playbook_on_stats(self, stats: AggregateStats) -> None:
        self.__maybe_task_end()  # normally done at task_start(), but there will be no next task
        self.deduped_playbook_on_stats(stats)

    # I'm too lazy to test these and I don't use async so I'm just going to cut support
    def v2_runner_on_async_poll(self, *args, **kwargs) -> None:
        raise NotImplementedError("dedupe_callback does not support async!")

    def v2_runner_on_async_ok(self, *args, **kwargs) -> None:
        raise NotImplementedError("dedupe_callback does not support async!")

    def v2_runner_on_async_failed(self, *args, **kwargs) -> None:
        raise NotImplementedError("dedupe_callback does not support async!")

    # DEDUPED API ##################################################################################
    def deduped_on_any(self, *args, **kwargs) -> None:
        "see ansible.plugins.callback.CallbackBase.v2_on_any"
        pass

    def deduped_playbook_on_start(self, playbook: Playbook) -> None:
        "see ansible.plugins.callback.CallbackBase.v2_playbook_on_start"
        pass

    def deduped_playbook_on_play_start(self, play: Play) -> None:
        "see ansible.plugins.callback.CallbackBase.v2_playbook_on_play_start"
        pass

    def deduped_playbook_on_task_start(self, task: Task, is_conditional) -> None:
        "see ansible.plugins.callback.CallbackBase.v2_playbook_on_task_start"
        pass

    def deduped_playbook_on_cleanup_task_start(self, task: Task) -> None:
        "see ansible.plugins.callback.CallbackBase.v2_playbook_on_cleanup_task_start"
        pass

    def deduped_playbook_on_handler_task_start(self, task: Task) -> None:
        "see ansible.plugins.callback.CallbackBase.v2_playbook_on_handler_task_start"
        pass

    def deduped_runner_on_start(self, host: Host, task: Task) -> None:
        "see ansible.plugins.callback.CallbackBase.v2_runner_on_start"
        pass

    def deduped_playbook_on_stats(self, stats: AggregateStats) -> None:
        "see ansible.plugins.callback.CallbackBase.v2_playbook_on_stats"
        pass

    def deduped_runner_retry(self, result: TaskResult) -> None:
        "see ansible.plugins.callback.CallbackBase.v2_runner_retry"
        pass

    def deduped_playbook_on_notify(self, handler: Handler, host: Host) -> None:
        "see ansible.plugins.callback.CallbackBase.v2_playbook_on_notify"
        pass

    def deduped_playbook_on_import_for_host(self, result: TaskResult, imported_file) -> None:
        "see ansible.plugins.callback.CallbackBase.v2_playbook_on_import_for_host"
        pass

    def deduped_playbook_on_not_import_for_host(self, result: TaskResult, missing_file) -> None:
        "see ansible.plugins.callback.CallbackBase.v2_playbook_on_not_import_for_host"
        pass

    def deduped_playbook_on_include(self, included_file: IncludedFile) -> None:
        "see ansible.plugins.callback.CallbackBase.v2_playbook_on_include"
        pass

    def deduped_playbook_on_no_hosts_matched(self) -> None:
        "see ansible.plugins.callback.CallbackBase.v2_playbook_on_no_hosts_matched"
        pass

    def deduped_playbook_on_no_hosts_remaining(self) -> None:
        "see ansible.plugins.callback.CallbackBase.v2_playbook_on_no_hosts_remaining"
        pass

    def deduped_playbook_on_vars_prompt(
        self,
        varname,
        private=True,
        prompt=None,
        encrypt=None,
        confirm=False,
        salt_size=None,
        salt=None,
        default=None,
        unsafe=None,
    ) -> None:
        "see ansible.plugins.callback.CallbackBase.v2_playbook_on_vars_prompt"
        pass

    def deduped_update_status_totals(self, status_totals: dict[str, str]) -> None:
        """
        status_totals: dictionary from status to a string representing the total number of runners
        or runner items that have that status. the total is usually digits, but it will have
        the value "?" when using a loop. see dedupe_callback.VALID_STATUSES
        """
        pass

    def deduped_result(
        self, result: TaskResult, status: str, result_id: ResultID, dupe_of_stripped: list[ResultID]
    ) -> None:
        """
        use this if you need to print results immediately rather than waiting until end of task
        see dedupe_callback.VALID_STATUSES
        hostnames, items, diffs, warnings, deprecations, and exceptions are all ignored
        when checking for dupes.
        """
        pass

    def deduped_diff(self, diff: dict, result_id: ResultID, dupe_of: list[ResultID]):
        """
        use this if you need to print diffs immediately rather than waiting until end of task
        hostnames and items are ignored when checking for dupes/groupings
        """
        pass

    def deduped_exception(
        self, exception: object, exception_id: ExceptionID, dupe_of: list[ExceptionID]
    ) -> None:
        """
        use this if you need to print exceptions immediately rather than waiting until end of task
        hostnames and items are ignored when checking for dupes/groupings
        """
        pass

    def deduped_warning(
        self, warning: object, warning_id: WarningID, dupe_of: list[WarningID]
    ) -> None:
        """
        use this if you need to print warnings immediately rather than waiting until end of task
        hostnames and items are ignored when checking for dupes/groupings
        """
        pass

    def deduped_deprecation(
        self, deprecation: object, deprecation_id: DeprecationID, dupe_of: list[DeprecationID]
    ) -> None:
        """
        use this if you need to print deprecations immediately rather than waiting until end of task
        hostnames and items are ignored when checking for dupes/groupings
        """
        pass

    def deduped_task_end(
        self,
        status2msg2result_ids: dict[str, dict[(str | None), list[ResultID]]],
        results_stripped_and_groupings: list[tuple[dict, list[ResultID]]],
        diffs_and_groupings: list[tuple[dict, list[DiffID]]],
        warnings_and_groupings: list[tuple[object, list[WarningID]]],
        exceptions_and_groupings: list[tuple[object, list[ExceptionID]]],
        deprecations_and_groupings: list[tuple[object, list[DeprecationID]]],
    ) -> None:
        """
        status2msg2result_ids: dict from status to dict of message to list of hostnames.
        see dedupe_callback.VALID_STATUSES

        results_stripped_and_groupings: list of tuples where the first element of each tuple
        is a stripped result dict. a stripped result dict is a result dict without diffs, warnings,
        or exceptions. the second element in each tuple is a list of ResultIDs that produced
        that result. hostnames and items are ignored when grouping ResultIDs.

        diffs_and_groupings: list of tuples where the first element of each tuple is a
        diff dict. the second element in each tuple is a list of ResultIDs that produced
        that result. hostnames and items are ignored when grouping ResultIDs. these are only
        the diffs from results where changed==True.

        hostnames and items are ignored for finding dupes/groupings.
        """
        pass

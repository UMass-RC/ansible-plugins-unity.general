import os
import json
import shutil
import datetime
import threading
import subprocess

from ansible import constants as C
from ansible.utils.color import stringc
from ansible.utils.display import Display
from ansible.plugins.callback.default import CallbackModule

from ansible_collections.unity.general.plugins.plugin_utils.ramdisk_cache import (
    get_cache_path,
    lock_cache_open_file,
    unlock_cache_close_file,
)

"""
to use these utilities in a plugin, you must extend the following doc fragments:
  - unity.general.diff
  - unity.general.ramdisk_cache (if using diff_redact_bitwarden)
"""

display = Display()

if shutil.which("diffr"):
    DO_DIFFR = True
else:
    display.warning("unable to locate the `diffr` command. diffs will not be highlighted.")
    DO_DIFFR = False


def _get_bitwarden_secrets(plugin_options: dict):
    """
    plugin_options is the result from AnsiblePlugin.get_options()
    """
    bitwarden_cache_path = get_cache_path("bitwarden", plugin_options)
    cache_file = lock_cache_open_file(bitwarden_cache_path, plugin_options)
    try:
        bitwarden_cache = json.load(cache_file)
    except json.JSONDecodeError as e:
        display.debug(f"assuming bitwarden cache is empty due to json decode error: {str(e)}")
        return []
    secrets = []
    for value in bitwarden_cache.values():
        if isinstance(value, list):
            secrets += value
        else:
            secrets.append(value)
    unlock_cache_close_file(cache_file)
    return [x.strip() for x in secrets]


def _redact_bitwarden_secrets(content: str, plugin_options: dict) -> str:
    """
    plugin_options is the result from AnsiblePlugin.get_options()
    """
    num_secrets_redacted = 0
    start_time = datetime.datetime.now()
    for secret in _get_bitwarden_secrets(plugin_options):
        if secret in content:
            content = content.replace(secret, "REDACTED")
            num_secrets_redacted += 1
    seconds_elapsed = (datetime.datetime.now() - start_time).total_seconds()
    display.v(
        f"slack: it took {seconds_elapsed:.1f} seconds to remove {num_secrets_redacted} secrets from the output buffer."
    )
    return content


def format_result_diff(diff: dict, plugin_options: dict) -> str:
    """
    plugin_options is the result from AnsiblePlugin.get_options()
    """
    output = ""
    if "before_header" in diff or "after_header" in diff:
        output += stringc(
            f"\"{diff.get('before_header', None)}\" -> \"{diff.get('after_header', None)}\"\n",
            C.COLOR_CHANGED,
        )
    if "prepared" in diff:
        output += diff["prepared"]
        return output
    if "src_binary" in diff:
        output += stringc("diff skipped: source file appears to be binary\n", C.COLOR_CHANGED)
        return output
    if "dst_binary" in diff:
        output += stringc("diff skipped: destination file appears to be binary\n", C.COLOR_CHANGED)
        return output
    if "src_larger" in diff:
        output += stringc(
            f"diff skipped: source file size is greater than {diff['src_larger']}\n",
            C.COLOR_CHANGED,
        )
        return output
    if "dst_larger" in diff:
        output += stringc(
            f"diff skipped: destination file size is greater than {diff['dst_larger']}\n",
            C.COLOR_CHANGED,
        )
        return output
    output = ""
    if "before" in diff and "after" in diff:
        # Format complex structures into 'files'
        for x in ["before", "after"]:
            if not isinstance(diff[x], str):
                callback_obj = CallbackModule()
                diff[x] = callback_obj._serialize_diff(diff[x])
            if diff[x] is None:
                diff[x] = ""
        if diff["before"] == diff["after"]:
            return stringc(
                "diff skipped: before and after are equal\n",
                C.COLOR_CHANGED,
            )
        if plugin_options.get("diff_redact_bitwarden", False) is True:
            for x in ["before", "after"]:
                diff[x] = _redact_bitwarden_secrets(diff[x], plugin_options)
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

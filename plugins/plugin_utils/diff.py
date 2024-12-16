import os
import sys
import shutil
import threading
import subprocess

from ansible import constants as C
from ansible.utils.color import stringc
from ansible.plugins.callback.default import CallbackModule

if shutil.which("diffr"):
    DO_DIFFR = True
else:
    print("unable to locate the diffr command. diffs will not be highlighted.", file=sys.stderr)
    DO_DIFFR = False


def format_result_diff(diff: dict) -> str:
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
            elif diff[x] is None:
                diff[x] = ""
        if diff["before"] == diff["after"]:
            return stringc(
                "diff skipped: before and after are equal\n",
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

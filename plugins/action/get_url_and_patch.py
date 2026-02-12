from __future__ import absolute_import, division, print_function

__metaclass__ = type

import json
import os
import re
import subprocess
import tempfile
import urllib.request

from ansible.plugins.action import ActionBase
from ansible.utils.display import Display

DOCUMENTATION = r"""
---
module: get_url_and_patch
short_description: Download and apply a patch to a file
description:
  - Downloads a file using get_url and applies a patch to it with the patch module and then copies it to dest
options:
  url:
    description: URL of the patch file to download.
    required: true
    type: str
  patch:
    description: Path to the patch file on the controller
    required: true
    type: str
  dest:
    description: Path to the file on the remote host where the patched file is written.
    required: true
    type: str
requirements:
  - patch command in PATH
author:
  - Simon Leary
"""

display = Display()


def _assert_single_file_patch(patch_contents):
    num_dashdashdash = len(re.findall(r"^---", patch_contents, re.MULTILINE))
    num_plusplusplus = len(re.findall(r"^\+\+\+", patch_contents, re.MULTILINE))
    if num_dashdashdash != num_plusplusplus:
        raise RuntimeError(
            f"patch has {num_dashdashdash} '---' headers, but {num_plusplusplus} '+++' headers!"
        )
    if num_dashdashdash != 1:
        raise RuntimeError(f"expected exactly 1 file in patch, found {num_dashdashdash}")


class ActionModule(ActionBase):
    def run(self, tmp=None, task_vars=None):
        result = super(ActionModule, self).run(tmp, task_vars)
        if task_vars is None:
            task_vars = {}
        if not (url := self._task.args.get("url")):
            result.update(failed=True, msg="url is required")
            return result
        if not (patch_path := self._task.args.get("patch")):
            result.update(failed=True, msg="patch is required")
            return result
        if not (dest := self._task.args.get("dest")):
            result.update(failed=True, msg="dest is required")
            return result
        tempfile_fd, tempfile_path = None, None
        try:
            tempfile_fd, tempfile_path = tempfile.mkstemp()
            with urllib.request.urlopen(url) as response:
                os.write(tempfile_fd, response.read())
            with open(patch_path, "r") as patch_f:
                patch_contents = patch_f.read()
                _assert_single_file_patch(patch_contents)
                patch_f.seek(0)
                patch_command = [
                    "patch",
                    "--batch",  # noninteractive
                    "--forward",  # do not automatically try reversed patch on failure
                    tempfile_path,
                ]
                completed_process = subprocess.run(
                    patch_command, stdin=patch_f, capture_output=True, check=False
                )
                result.update(patch_cmd=patch_command, patch_rc=completed_process.returncode)
                try:
                    result.update(
                        patch_stdout_lines=completed_process.stdout.decode("utf8").split("\n"),
                        patch_stderr_lines=completed_process.stderr.decode("utf8").split("\n"),
                    )
                except UnicodeDecodeError:
                    result.update(
                        failed=True, msg="failed to utf8 decode stdout or stderr from `patch`"
                    )
                    return result
                if completed_process.returncode != 0:
                    result.update(failed=True, msg="`patch` failed!")
                    return result
            copy_task = self._task.copy()
            del copy_task.args
            copy_task.args = {"src": tempfile_path, "dest": dest}
            copy_action_plugin = self._shared_loader_obj.action_loader.get(
                "ansible.builtin.copy",
                task=copy_task,
                connection=self._connection,
                play_context=self._play_context,
                loader=self._loader,
                templar=self._templar,
                shared_loader_obj=self._shared_loader_obj,
            )
            copy_res = copy_action_plugin.run(task_vars=task_vars)
            display.v(json.dumps(copy_res))
            result.update(copy_res)
            return result
        except Exception as e:
            result.update(failed=True, msg=f"Exception: {type(e)=} {str(e)=}")
            return result
        finally:
            if tempfile_fd is not None:
                os.close(tempfile_fd)
                os.unlink(tempfile_path)

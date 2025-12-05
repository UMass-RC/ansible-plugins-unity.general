from __future__ import absolute_import, division, print_function

__metaclass__ = type

import json
import os
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

EXAMPLES = r"""
- name: Download a patch and apply it
  get_url_and_patch:
    url: "https://example.com/patches/fix-123.diff"
    patch: /tmp/config.patch
    dest: /etc/myapp/config.conf

# specify where to download the patch on the remote host
- name: Download to a specific path and apply
  get_url_and_patch:
    url: "https://example.com/patches/fix-123.diff"
    patch: /tmp/file.patch
    dest: /srv/myproject/file.txt
"""

display = Display()


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
                subprocess.run(
                    ["patch", "--batch", tempfile_path],  # --batch means non interactive
                    stdin=patch_f,
                    capture_output=True,
                    check=True,
                )
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
        except subprocess.CalledProcessError as e:
            result.update(
                failed=True,
                msg=f"CalledProcessError: {e.returncode=} {e.cmd=} {e.stdout=} {e.stderr=}",
            )
            return result
        except Exception as e:
            result.update(failed=True, msg=f"Exception: {type(e)=} {str(e)=}")
            return result
        finally:
            if tempfile_fd is not None:
                os.close(tempfile_fd)
                os.unlink(tempfile_path)

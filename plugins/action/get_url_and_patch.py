from __future__ import absolute_import, division, print_function

__metaclass__ = type

import json

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

RETURN = r"""
changed:
  description: ""
  type: bool
failed:
  description: ""
  type: bool
module_results:
  description: list of dicts of module results
  type: list
"""

display = Display()


def _update_result_from_modules(result: dict):
    result["failed"] = any([x["result"].get("failed", False) for x in result["module_results"]])
    result["changed"] = False
    for result_wrapper in result["module_results"]:
        if result_wrapper["name"] == "patch" and result_wrapper["result"].get("changed", False):
            result["changed"] = True
    module_outcomes = []
    for result_wrapper in result["module_results"]:
        outcome = "failed" if result_wrapper["result"].get("failed", False) else "succeeded"
        module_outcomes.append(f"{result_wrapper['name']} {outcome}")
    result["msg"] = ", ".join(module_outcomes)
    display.v(json.dumps(result))


class ActionModule(ActionBase):
    def run(self, tmp=None, task_vars=None):
        result = super(ActionModule, self).run(tmp, task_vars)
        result["module_results"] = []
        _update_result_from_modules(result)
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
        result.update(url=url, dest=dest)

        original_check_mode = self._task.check_mode
        self._task.check_mode = False

        # TEMPFILE 1 ###############################################################################
        tempfile_url_res = self._execute_module(
            module_name="ansible.builtin.tempfile",
            task_vars=task_vars,
            module_args={},
        )
        result["module_results"].append(
            {"name": "tempfile (for URL download)", "result": tempfile_url_res}
        )
        _update_result_from_modules(result)
        if tempfile_url_res.get("failed", False):
            return result
        tempfile_url_path = tempfile_url_res["path"]

        # TEMPFILE 2 ###############################################################################
        tempfile_patch_res = self._execute_module(
            module_name="ansible.builtin.tempfile",
            task_vars=task_vars,
            module_args={},
        )
        result["module_results"].append(
            {"name": "tempfile (for patch working copy)", "result": tempfile_patch_res}
        )
        _update_result_from_modules(result)
        if tempfile_patch_res.get("failed", False):
            return result
        tempfile_patch_path = tempfile_patch_res["path"]

        # GET_URL ##################################################################################
        get_url_res = self._execute_module(
            module_name="ansible.builtin.get_url",
            module_args={"url": url, "dest": tempfile_patch_path, "_ansible_check_mode": False},
            task_vars=task_vars,
        )
        result["module_results"].append({"name": "get_url", "result": get_url_res})
        _update_result_from_modules(result)
        if get_url_res.get("failed", False):
            return result
        self._transfer_file(patch_path, tempfile_patch_path)
        self._fixup_perms2([tempfile_patch_path])

        # PATCH ####################################################################################
        patch_res = self._execute_module(
            module_name="ansible.posix.patch",
            module_args={
                "src": tempfile_patch_path,
                "dest": tempfile_url_path,
                "_ansible_check_mode": False,
            },
            task_vars=task_vars,
        )
        result["module_results"].append({"name": "patch", "result": patch_res})
        _update_result_from_modules(result)
        # don't fail yet, delete tempfiles first
        # if patch_res.get("failed", False):
        #     return result

        # REMOVE TEMPFILE 1 ########################################################################
        file_rm_url_res = self._execute_module(
            module_name="ansible.builtin.file",
            module_args={
                "path": tempfile_url_path,
                "state": "absent",
                "_ansible_check_mode": False,
            },
            task_vars=task_vars,
        )
        result["module_results"].append(
            {"name": "file (remove tempfile for URL download)", "result": file_rm_url_res}
        )
        _update_result_from_modules(result)
        if file_rm_url_res.get("failed", False):
            return result

        # REMOVE TEMPFILE 2 ########################################################################
        file_rm_patch_res = self._execute_module(
            module_name="ansible.builtin.file",
            module_args={
                "path": tempfile_url_path,
                "state": "absent",
                "_ansible_check_mode": False,
            },
            task_vars=task_vars,
        )
        result["module_results"].append(
            {"name": "file (remove tempfile for patch working copy)", "result": file_rm_patch_res}
        )
        _update_result_from_modules(result)
        if file_rm_patch_res.get("failed", False):
            return result
        # now that tempfiles are delted we can fail from this before copy
        if patch_res.get("failed", False):
            return result

        # COPY #####################################################################################
        copy_task = self._task.copy()
        copy_task.check_mode = original_check_mode
        del copy_task.args
        copy_task.args = {"src": tempfile_url_path, "dest": dest, "remote_src": True}
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
        result["module_results"].append({"name": "copy", "result": copy_res})
        _update_result_from_modules(result)

        return result

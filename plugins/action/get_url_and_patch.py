from __future__ import absolute_import, division, print_function

__metaclass__ = type


from ansible.plugins.action import ActionBase

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
  description: whether any step changed something
  type: bool
tempfile_res:
  description: result dict returned from the tempfile module
  type: dict
get_url_res:
  description: result dict returned from the get_url module
  type: dict
patch_res:
  description: result dict returned from the patch module
  type: dict
file_rm_res:
  description: result dict returned from the file module where tempfile is deleted
  type: dict
"""


def _update_result_from_modules(result: dict):
    tempfile_failed = result.get("tempfile_res", {}).get("failed", False)
    get_url_failed = result.get("get_url_res", {}).get("failed", False)
    patch_failed = result.get("patch_res", {}).get("failed", False)
    file_rm_failed = result.get("file_rm_res", {}).get("failed", False)
    copy_failed = result.get("copy_res", {}).get("failed", False)
    if tempfile_failed:
        result["msg"] = "tempfile failed"
    if get_url_failed:
        result["msg"] = "tempfile succeeded and then get_url failed"
    if (not patch_failed) and (file_rm_failed):
        result["msg"] = (
            "tempfile, get_url, and patch succeeded and then file (removing tempfile) failed"
        )
    if (patch_failed) and (not file_rm_failed):
        result["msg"] = (
            "tempfile and get_url succeeded, and then patch failed, and then file (removing tempfile) succeeded"
        )
    if (patch_failed) and (file_rm_failed):
        result["msg"] = (
            "tempfile and get_url succeeded, and then patch failed, and file (removing tempfile) failed"
        )
    if copy_failed:
        result["msg"] = (
            "tempfile, get_url, patch, file (removing tempfile) succeeded, and then copy failed"
        )
    result["failed"] = any(
        [tempfile_failed, get_url_failed, patch_failed, file_rm_failed, copy_failed]
    )
    result["changed"] = result.get("patch_res", {}).get("changed", False)


class ActionModule(ActionBase):
    def run(self, tmp=None, task_vars=None):
        result = super(ActionModule, self).run(tmp, task_vars)
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

        tempfile_res = self._execute_module(
            module_name="ansible.builtin.tempfile",
            task_vars=task_vars,
            module_args={"_ansible_check_mode": False},
        )
        result.update(tempfile_patch_res=tempfile_res)
        _update_result_from_modules(result)
        if result["failed"]:
            return result
        tempfile_path = tempfile_res["path"]

        get_url_res = self._execute_module(
            module_name="ansible.builtin.get_url",
            module_args={"url": url, "dest": tempfile_path, "_ansible_check_mode": False},
            task_vars=task_vars,
        )
        result.update(get_url_res=get_url_res)
        _update_result_from_modules(result)
        if result["failed"]:
            return result

        patch_res = self._execute_module(
            module_name="ansible.posix.patch",
            module_args={"src": tempfile_path, "dest": dest, "_ansible_check_mode": False},
            task_vars=task_vars,
        )
        result.update(patch_res=patch_res)
        _update_result_from_modules(result)
        # if result["failed"]:
        #     return result

        file_rm_res = self._execute_module(
            module_name="ansible.builtin.file",
            module_args={
                "path": tempfile_path,
                "state": "absent",
                "_ansible_check_mode": False,
            },
            task_vars=task_vars,
        )
        result.update(file_rm_res=file_rm_res)
        _update_result_from_modules(result)
        if result["failed"]:
            return result

        patch_res = self._execute_module(
            module_name="ansible.builtin.copy",
            module_args={"src": tempfile_path, "dest": dest},
            task_vars=task_vars,
        )
        result.update(patch_res=patch_res)
        _update_result_from_modules(result)

        return result

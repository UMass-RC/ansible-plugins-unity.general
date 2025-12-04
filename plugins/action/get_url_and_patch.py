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
  description: ""
  type: bool
failed:
  description: ""
  type: bool
module_results:
  description: list of dicts of module results
  type: list
"""


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
    result["msg"] = "\n".join(module_outcomes)


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

        tempfile_url_res = self._execute_module(
            module_name="ansible.builtin.tempfile",
            task_vars=task_vars,
            module_args={},
            # "remote module (ansible.builtin.tempfile) does not support check mode"
            # module_args={"_ansible_check_mode": False},
        )
        result["module_results"].append(
            {"name": "tempfile (for URL download)", "result": tempfile_url_res}
        )
        _update_result_from_modules(result)
        if tempfile_url_res.get("failed", False):
            return result
        tempfile_url_path = tempfile_url_res["path"]

        tempfile_patch_res = self._execute_module(
            module_name="ansible.builtin.tempfile",
            task_vars=task_vars,
            module_args={},
            # "remote module (ansible.builtin.tempfile) does not support check mode"
            # module_args={"_ansible_check_mode": False},
        )
        result["module_results"].append(
            {"name": "tempfile (for patch working copy)", "result": tempfile_patch_res}
        )
        _update_result_from_modules(result)
        if tempfile_patch_res.get("failed", False):
            return result
        tempfile_patch_path = tempfile_patch_res["path"]

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
        self._fixup_perms2(tempfile_patch_path)

        patch_res = self._execute_module(
            module_name="ansible.posix.patch",
            module_args={"src": tempfile_url_path, "dest": dest, "_ansible_check_mode": False},
            task_vars=task_vars,
        )
        result["module_results"].append({"name": "patch", "result": patch_res})
        _update_result_from_modules(result)
        # if patch_res.get("failed", False):
        #     return result

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

        copy_res = self._execute_module(
            module_name="ansible.builtin.copy",
            module_args={"src": tempfile_url_path, "dest": dest},
            task_vars=task_vars,
        )
        result["module_results"].append({"name": "copy", "result": copy_res})
        _update_result_from_modules(result)

        return result

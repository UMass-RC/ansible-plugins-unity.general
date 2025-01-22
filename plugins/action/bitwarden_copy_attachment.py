import re
import traceback

from ansible.errors import AnsibleError
from ansible.utils.display import Display
from ansible.plugins.action import ActionBase

from ansible_collections.unity.general.plugins.plugin_utils.action import failed

display = Display()


class ActionModule(ActionBase):
    """
    see the stub module for documentation / options
    """

    def run(self, tmp=None, task_vars=None):
        if not isinstance(self._task.args["mode"], str):
            return failed('mode must be a string! example: "0755"')
        if not re.fullmatch(r"0[0-7]{3}", self._task.args["mode"]):
            return failed('mode is not valid! example: "0755"')

        try:
            attachment_download_path = self._templar._lookup(
                "unity.general.bitwarden_attachment_download",
                item_name=self._task.args["item_name"],
                attachment_filename=self._task.args["attachment_filename"],
            )
        except AnsibleError as e:
            display.v(traceback.format_exception(e))
            return failed(f"Error fetching attachment: {str(e)}")

        module_args = {
            k: v
            for k, v in self._task.args.items()
            if k not in ["item_name", "attachment_filename"]
        }
        module_args["src"] = attachment_download_path

        result = self._execute_module(
            module_name="ansible.legacy.copy",
            module_args=module_args,
            task_vars=task_vars,
        )
        result["_ansible_no_log"] = True
        return result

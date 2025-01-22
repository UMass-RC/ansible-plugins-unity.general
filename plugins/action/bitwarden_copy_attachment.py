from ansible.errors import AnsibleError
from ansible.plugins.action import ActionBase

from ansible_collections.unity.general.plugins.plugin_utils.action import validate_args, failed


class ActionModule(ActionBase):
    def run(self, tmp=None, task_vars=None):
        argument_errors = validate_args(
            self._task.args,
            required=["item_name", "attachment_filename", "dest", "owner", "group", "mode"],
        )
        if len(argument_errors) > 0:
            return failed("\n".join(argument_errors))
        item_name = self._task.args["item_name"]
        attachment_filename = self._task.args["attachment_filename"]
        dest = self._task.args["dest"]
        owner = self._task.args["owner"]
        group = self._task.args["group"]
        mode = self._task.args["mode"]

        try:
            attachment_download_path = self._templar._lookup(
                "unity.general.bitwarden_attachment_download",
                item_name=item_name,
                attachment_filename=attachment_filename,
            )
        except AnsibleError as e:
            return failed(f"Error fetching attachment: {str(e)}")

        result = self._execute_module(
            module_name="ansible.legacy.copy",
            module_args={
                "src": attachment_download_path,
                "dest": dest,
                "owner": owner,
                "group": group,
                "mode": mode,
            },
            task_vars=task_vars,
        )
        result["_ansible_no_log"] = True
        return result

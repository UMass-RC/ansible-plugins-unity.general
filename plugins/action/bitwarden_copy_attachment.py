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
        # TODO this doesn't need to run on remote host
        validate_args_result = self._execute_module(
            module_name="unity.general.bitwarden_copy_attachment",
            module_args=self._task.args,
            tmp=tmp,
            task_vars=task_vars,
        )
        if validate_args_result["failed"]:
            return validate_args_result
        params = validate_args_result["params"]

        try:
            lookup_kwargs = {
                k: v
                for k, v in params.items()
                if k in ["item_name", "attachment_filename", "collection_id"]
            }
            attachment_download_path = self._templar._lookup(
                "unity.general.bitwarden_attachment_download", **lookup_kwargs
            )
        except AnsibleError as e:
            display.v(traceback.format_exception(e))
            return failed(f"Error fetching attachment: {str(e)}")

        copy_task = self._task.copy()
        del copy_task.args
        copy_task.args = {
            k: v
            for k, v in params.items()
            if k not in ["item_name", "attachment_filename", "collection_id", "enable_logging"]
        }
        copy_task.args["src"] = attachment_download_path
        copy_action_plugin = self._shared_loader_obj.action_loader.get(
            "unity.copy_multi_diff.copy",
            task=copy_task,
            connection=self._connection,
            play_context=self._play_context,
            loader=self._loader,
            templar=self._templar,
            shared_loader_obj=self._shared_loader_obj,
        )
        result = copy_action_plugin.run(task_vars=task_vars)
        if not params["enable_logging"]:
            result["_ansible_no_log"] = True
        return result

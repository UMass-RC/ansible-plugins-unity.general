from ansible.plugins.callback import CallbackBase


class OptionsFixedCallback(CallbackBase):
    # https://github.com/ansible/ansible/pull/84496
    def get_options(self):
        return self._plugin_options

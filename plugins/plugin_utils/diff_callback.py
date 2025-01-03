import subprocess

from ansible.utils.display import Display
from ansible.plugins.callback import CallbackBase

display = Display()


class DiffCallbackBase(CallbackBase):
    """
    adds a wrapper around the default CallbackModule._get_diff
    allows diff formatting piped through a shell command

    your plugin must extend the unity.general.diff_callback documentation fragment
    """

    def _get_diff(self, diff_or_diffs: dict | list[dict]) -> str:
        output = super(CallbackBase, self)._get_diff(diff_or_diffs)
        formatter = self.get_option("diff_formatter")
        if formatter != "NONE":
            formatter_proc = subprocess.Popen(
                formatter,
                shell=True,
                text=True,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            try:
                output, _ = formatter_proc.communicate(input=output)
            except subprocess.CalledProcessError as e:
                display.warning(f'DiffCallback: diff formatter "{formatter}" failed! {e}')
        return output

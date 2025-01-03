import re
import subprocess

from ansible.utils.display import Display
from ansible.plugins.callback import CallbackBase

display = Display()

ANSI_REGEX = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


class FormatDiffCallback(CallbackBase):
    def _get_diff(self, diff_or_diffs: dict | list[dict]) -> str:
        """
        your CallbackModule must extend the unity.general.format_diff documentation fragment
        """
        normal_diff = re.sub(
            ANSI_REGEX, "", super(FormatDiffCallback, self)._get_diff(diff_or_diffs)
        )
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
                output, _ = formatter_proc.communicate(input=normal_diff)
            except subprocess.CalledProcessError as e:
                display.warning(f'diff formatter "{formatter}" failed! {e}')
                return normal_diff
        return output

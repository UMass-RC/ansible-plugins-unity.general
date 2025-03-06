import re
import shutil
import subprocess
import tempfile
import shlex

from ansible.utils.display import Display
from ansible.plugins.callback import CallbackBase

display = Display()

ANSI_REGEX = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


class FormatDiffCallback(CallbackBase):
    def _get_diff(self, diff_or_diffs: dict | list[dict]) -> str:
        """
        your CallbackModule must extend the unity.general.format_diff documentation fragment
        """
        normal_diff = super(FormatDiffCallback, self)._get_diff(diff_or_diffs)
        formatter = self.get_option("diff_formatter")
        formatter_argv_0 = shlex.split(formatter)[0]
        if formatter == "NONE":
            return normal_diff
        if shutil.which(formatter_argv_0) is None:
            display.warning(f'diff formatter "{formatter}" not found')
            return normal_diff

        monochrome_diff = re.sub(ANSI_REGEX, "", normal_diff)
        # Popen.communicate() and subprocess.run() were having deadlock issues
        with tempfile.TemporaryFile(mode="w+") as tmp_in:
            with tempfile.TemporaryFile(mode="w+") as tmp_out:
                tmp_in.write(monochrome_diff)
                tmp_in.seek(0)
                with subprocess.Popen(
                    formatter,
                    shell=True,
                    text=True,
                    stdin=tmp_in,
                    stdout=tmp_out,
                    stderr=subprocess.STDOUT,
                ) as formatter_proc:
                    try:
                        formatter_proc.wait()
                        tmp_out.seek(0)
                        output = tmp_out.read()
                    except subprocess.CalledProcessError as e:
                        display.warning(f'diff formatter "{formatter}" failed! {e}')
                        return normal_diff
                    if formatter_proc.returncode != 0:
                        display.warning(
                            f'diff formatter "{formatter}" returned nonzero exit code {formatter_proc.returncode}.\n{output}'
                        )
                        return normal_diff
                    return output.strip()

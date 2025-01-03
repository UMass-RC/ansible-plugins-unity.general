import re
import subprocess

from ansible.utils.display import Display

display = Display()

ANSI_REGEX = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def format_diff(unified_diff: str, plugin_options: dict) -> str:
    """
    plugin_options is the result from AnsiblePlugin.get_options()
    your plugin must extend the unity.general.format_diff documentation fragment
    """
    unified_diff = re.sub(ANSI_REGEX, "", unified_diff)
    formatter = plugin_options.get("diff_formatter", "NONE")
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
            output, _ = formatter_proc.communicate(input=unified_diff)
        except subprocess.CalledProcessError as e:
            display.warning(f'diff formatter "{formatter}" failed! {e}')
            return unified_diff
    return output

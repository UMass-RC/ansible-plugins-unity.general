from ansible.utils.display import Display
from ansible.vars.clean import strip_internal_keys

display = Display()


def cleanup_result(result: dict) -> None:
    strip_internal_keys(result)
    if "invocation" in result:
        display.debug(f"deleting result invocation: {result["invocation"]}")
        del result["invocation"]
    # since we use block for multiline, no need for list of lines
    if "stdout" in result and "stdout_lines" in result:
        display.debug(f"removing stdout_lines since stdout exists: {result["stdout_lines"]}")
        result.pop("stdout_lines")
    if "stderr" in result and "stderr_lines" in result:
        display.debug(f"removing stderr_lines since stderr exists: {result["stderr_lines"]}")
        result.pop("stderr_lines")

from ansible.errors import AnsibleFilterError, AnsibleFilterTypeError


def str2bool(x: str) -> bool:
    if not isinstance(x, str):
        raise AnsibleFilterTypeError(f"string is required. given: {type(x)}")
    truey_strings = ["yes", "true", "y", "on"]
    falsey_strings = ["no", "false", "n", "off"]
    if x.lower() in truey_strings:
        return True
    if x.lower() in falsey_strings:
        return False
    raise AnsibleFilterError(
        f"ambiguous input. examples: {list(zip(truey_strings, falsey_strings))}"
    )


class FilterModule(object):
    def filters(self):
        return {
            "str2bool": str2bool,
        }

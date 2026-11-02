from ansible.errors import AnsibleFilterError
from ansible.vars.hostvars import HostVars, HostVarsVars


def dict_get[T](
    _dict: dict | HostVars | HostVarsVars, key: str, expected_type: type[T], name: str
) -> T:
    try:
        value = _dict[key]
    except KeyError as e:
        raise AnsibleFilterError(f'key "{key}" not found in "{name}"') from e
    if not isinstance(value, expected_type):
        raise AnsibleFilterError(
            f'key "{key}" in "{name}" is {type(value).__name__}, expected {expected_type.__name__}'
        )
    return value


def dict_get_deep[T](
    _dict: dict | HostVars | HostVarsVars,
    keys: list[str],
    expected_type: type[T],
    name: str,
) -> T:
    cursor = _dict
    for key in keys:
        name += f'["{key}"]'
        try:
            cursor = cursor[key]
        except KeyError as e:
            raise AnsibleFilterError(f'key "{key}" not found in "{name}"') from e
    if not isinstance(cursor, expected_type):
        raise AnsibleFilterError(
            f'"{name}" is {type(cursor).__name__}, expected {expected_type.__name__}'
        )
    return cursor

from ansible.module_utils.basic import AnsibleModule


def all_elements_equal(x: list) -> bool:
    if len(x) < 2:
        return True
    first_elem = x[0]
    for elem in x[1:]:
        if elem != first_elem:
            return False
    return True


def _check_output(argv: list[str], _module: AnsibleModule, timeout_sec=0) -> str:
    _, stdout, _ = _module.run_command(["timeout", "-v", str(timeout_sec)] + argv, check_rc=True)
    return stdout

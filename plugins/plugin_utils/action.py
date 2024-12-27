def validate_args(
    args: dict[str, object],
    optional: list[str] = [],
    required: list[str] = [],
    types: dict[str, list[object]] = {},
    allow_extra_args=False,
) -> list[str]:
    """
    returns a list of strings describing errors with the arguments.
    if the list is empty then the arguments are valid.
    """
    errors = []
    required_args_not_found = set(required) - set(args.keys())
    if len(required_args_not_found) > 0:
        errors.append(f"required args not found: {sorted(list(required_args_not_found))}")
    wrong_types = {k: v for k, v in args.items() if k in types and type(v) not in types[k]}
    if len(wrong_types) > 0:
        for key, val in wrong_types.items():
            expected_type = types[key]
            errors.append(
                f"expected type '{expected_type}' for arg '{key}', instead got '{type(val)}'"
            )
    if not allow_extra_args:
        extra_args = set(args.keys()) - set(required) - set(optional)
        if len(extra_args) > 0:
            errors.append(f"unsupported arguments: {sorted(list(extra_args))}")
    return errors


def failed(msg):
    return {"failed": True, "msg": msg}

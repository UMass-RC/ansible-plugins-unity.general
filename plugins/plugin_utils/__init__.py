import re


def action_failed(msg):
    return {"failed": True, "msg": msg}


# https://stackoverflow.com/a/14693789/18696276
ANSI_REGEX = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def decolorize(x: str) -> str:
    return re.sub(ANSI_REGEX, "", x)

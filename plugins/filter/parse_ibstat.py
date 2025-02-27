import re
from ansible.errors import AnsibleFilterError


def parse_ibstat(_input: str) -> list[dict]:
    output = []
    for i, line in enumerate(_input.splitlines()):
        if not line.strip():
            continue
        if match := re.match(r"^CA '(.*)'$", line):
            ca_name = match.groups(1)[0]
            output.append({"CA name": ca_name, "ports": []})
        elif match := re.match(r"^\tPort (\d+):$", line):
            port_num = int(match.groups(1)[0])
            output[-1]["ports"].append({"port number": port_num})
        # line starts with exactly one tab
        elif match := re.match(r"^\t[^\t]", line):
            key, val = [x.strip() for x in line.split(":")]
            output[-1][key] = val
        # line starts with exactly two tabs
        elif match := re.match(r"^\t{2}[^\t]", line):
            key, val = [x.strip() for x in line.split(":")]
            output[-1]["ports"][-1][key] = val
        else:
            raise AnsibleFilterError(f"unable to parse line {i}. current output: {output}")
    return output


class FilterModule(object):
    def filters(self):
        return {"parse_ibstat": parse_ibstat}

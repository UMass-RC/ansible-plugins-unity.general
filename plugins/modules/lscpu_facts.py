DOCUMENTATION = r"""
name: lscpu_facts
short_description: contains the output from the lscpu command
description: ""
platform:
  - linux
author: Simon Leary <simon.leary42@proton.me>
version_added: 2.18.1
"""

RETURN = r"""
lscpu:
    description: ''
    type: dict
    returned: always
    sample: {}
"""

import re
import subprocess
from ansible.module_utils.basic import AnsibleModule


def main():
    module = AnsibleModule(argument_spec={})
    lscpu = {}
    lscpu_out = subprocess.check_output("lscpu", text=True)
    for line in lscpu_out.splitlines():
        try:
            k, v = re.fullmatch(r"(.*?):\s+(.*)", line).group(1, 2)
            lscpu[k] = v
        except AttributeError:
            module.fail_json(msg="failed to parse output from lscpu")
    module.exit_json(ansible_facts={"lscpu": lscpu})


if __name__ == "__main__":
    main()

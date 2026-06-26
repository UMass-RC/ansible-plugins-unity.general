#!/usr/bin/python

DOCUMENTATION = r"""
name: gpu_model_facts
short_description: facts module that finds the nvidia GPU model name
description: ""
requirements:
  - clinfo
author: Simon Leary <simon.leary42@proton.me>
version_added: 2.19
"""

RETURN = r"""
gpu_model:
  description: GPU model name
  type: string
  returned: always
  sample: "a100"
"""

import re
from ansible.module_utils.basic import AnsibleModule
from ansible_collections.unity.general.plugins.module_utils.common import (
    _check_output,
    all_elements_equal,
    translate_nvidia_gpu_model_name,
)


def get_gpu_model(_module) -> str:
    clinfo_out = _check_output(["clinfo"], _module, timeout_sec=2)
    gpu_lines = [x for x in clinfo_out.splitlines() if x.strip().startswith("Device Name")]
    gpu_models = [re.sub(r"^\s*Device Name\s+(.*?)\s*$", r"\1", x) for x in gpu_lines]
    assert all_elements_equal(gpu_models)
    return translate_nvidia_gpu_model_name(gpu_models[0])


def main():
    _module = AnsibleModule(argument_spec={}, supports_check_mode=True)
    _module.exit_json(
        ansible_facts={
            "gpu_model": get_gpu_model(_module),
        }
    )


if __name__ == "__main__":
    main()

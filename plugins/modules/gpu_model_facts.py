#!/usr/bin/python

DOCUMENTATION = r"""
name: gpu_model_facts
short_description: facts module that finds the nvidia GPU model name
description: ""
requirements:
  - jc
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

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.unity.general.plugins.module_utils.common import (
    get_gpu_model_and_count,
)


def main():
    _module = AnsibleModule(argument_spec={}, supports_check_mode=True)
    _module.exit_json(
        ansible_facts={
            "gpu_model": get_gpu_model_and_count(_module)[0],
        }
    )


if __name__ == "__main__":
    main()

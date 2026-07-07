#!/usr/bin/python

DOCUMENTATION = r"""
name: slurm_gres_and_gpu_features_facts
short_description: facts module that finds Slurm Gres and GPU related features
description: ""
requirements:
  - nvidia-smi
  - jc
author: Simon Leary <simon.leary42@proton.me>
version_added: 2.18.1
"""

RETURN = r"""
slurm_gres:
  description: https://slurm.schedmd.com/gres.html
  type: string
  returned: always
  sample: "gpu:a100:4"
slurm_gpu_features:
  description: list of slurm features
  type: list
  elements: string
  returned: always
  sample:
    - a100
    - fp64
    - sm_90
    - nvlink
    - nvswitch
    - vram8
    - vram16
    - vram24
    - vram40
"""

import re

from ansible.module_utils.basic import AnsibleModule

from ansible_collections.unity.general.plugins.module_utils.archspec import check_requirements
from ansible_collections.unity.general.plugins.module_utils.common import (
    _check_output,
    get_gpu_model_and_count,
    all_elements_equal,
)

# 8 -> "vram8". GB, not GiB. a node with vram12 will inherit vram11, vram8, ...
VRAM_FEATURES = [8, 11, 12, 16, 23, 32, 40, 48, 80, 102, 143]
# this should include all of the nvidia compute capability versions present in the cluster
# 7.0 -> "sm70". a node with sm_90 will inherit sm_89, sm_87, ...
INCLUDE_NV_CC = [5.2, 6.1, 7.0, 7.5, 8.0, 8.6, 8.7, 8.9, 9.0]
NV_SMI_TIMEOUT_SEC = 10
FEATURE_INCLUDE_WHEN = {
    "a100-80g": {
        "all_of": ["a100", "vram80"],
    },
    "a100-40g": {
        "all_of": ["a100", "vram40"],
        "none_of": ["vram80"],
    },
    "v100-32g": {
        "all_of": ["v100", "vram32"],
    },
    "v100-16g": {
        "all_of": ["v100", "vram16"],
        "none_of": ["vram32"],
    },
    "fp64": {"any_of": ["gh200", "h100", "a100", "v100"]},
    "bf16": {"any_of": ["gh200", "h100", "a100", "l40s", "a40", "l4"]},
    "gracehopper": {"all_of": ["gh200"]},
    # legacy GPU features, we switched to those used by slurmd nvml autodetect
    "titanx": {"all_of": ["titan_x"]},
    "2080ti": {"all_of": ["2080_ti"]},
    "1080ti": {"all_of": ["1080_ti"]},
    "rtx8000": {"all_of": ["rtx_8000"]},
    "h200": {"all_of": ["h200_nvl"]},
}


def get_cuda_compute_capability_features(cc: float, _module: AnsibleModule) -> set[str]:
    # include older CCs
    ccs = [x for x in INCLUDE_NV_CC if x <= cc]
    if cc not in INCLUDE_NV_CC:
        _module.warn(
            "WARNING: `INCLUDE_NV_CC` is supposed to contain a list of all the nvidia compute "
            + f"capability versions present in the cluster, but my version {cc} is not present!"
        )
        ccs.append(cc)
    features = set()
    for cc in ccs:
        # 5.2 -> "sm_52"
        features.add(f"sm_{str(cc).replace('.', '')}")
    return features


def get_vram_features(
    vram_size_MiB: int, _module: AnsibleModule, vram_wasted_warning_threshold_GB=2
) -> set[str]:
    vram_size_GB = int(vram_size_MiB * 1024 * 1024 / 1000000000)
    qualified_vram_features = {x for x in VRAM_FEATURES if x <= vram_size_GB}
    if (wasted := vram_size_GB - max(qualified_vram_features)) > vram_wasted_warning_threshold_GB:
        _module.warn(
            "largest VRAM feature %s is %s GB smaller than actual VRAM size %s"
            % (max(qualified_vram_features), wasted, vram_size_GB)
        )
    return {f"vram{x}" for x in qualified_vram_features}


def get_nvlink_features(_module: AnsibleModule) -> set[str]:
    nv_smi_out = _check_output(
        ["nvidia-smi", "nvlink", "--status"], _module, timeout_sec=NV_SMI_TIMEOUT_SEC
    )
    for line in nv_smi_out.splitlines():
        if re.match(r"^GPU \d+: ", line):
            continue
        if line == "NVML: Unable to retrieve NVLink information as all links are inActive":
            continue
        if re.fullmatch(r"Link \d+: [\d\.]+ GB/s", line.strip()):
            return {"nvlink"}
        _module.warn(f'unexpected output from "nvidia-smi nvlink --status": "{line}"')
    return set()


def get_gpu_vram_mebibytes_and_compute_capability(_module: AnsibleModule) -> tuple[int, float]:
    nv_smi_out = _check_output(
        [
            "nvidia-smi",
            "--query-gpu=memory.total,compute_cap",
            "--format=csv,noheader",
        ],
        _module,
        timeout_sec=NV_SMI_TIMEOUT_SEC,
    )
    gpu_table = [line.split(",") for line in nv_smi_out.splitlines()]
    if not all(len(row) == 2 for row in gpu_table):
        _module.fail_json(f"unexpected nvidia-smi output: {nv_smi_out}")
    output = []
    for vram_MiB, cc in gpu_table:
        output.append((int(re.sub(r"\s+MiB$", "", vram_MiB)), float(cc)))
    assert all_elements_equal(output)
    return output[0]


def main():
    _module = AnsibleModule(argument_spec={}, supports_check_mode=True)
    gpu_model, gpu_count = get_gpu_model_and_count(_module)
    gres = f"gpu:{gpu_model}:{gpu_count}"
    features = {gpu_model}
    vram_MiB, cc = get_gpu_vram_mebibytes_and_compute_capability(_module)
    features.update(get_cuda_compute_capability_features(cc, _module))
    features.update(get_vram_features(vram_MiB, _module))
    # features.update(get_nvlink_features(_module))
    features.update(check_requirements(FEATURE_INCLUDE_WHEN, features))
    _module.exit_json(
        ansible_facts={
            "slurm_gres": gres,
            "slurm_gpu_features": sorted(list(features)),
        }
    )


if __name__ == "__main__":
    main()

#!/usr/bin/python

DOCUMENTATION = r"""
name: slurm_gres_and_gpu_features_facts
short_description: facts module that finds Slurm Gres and GPU related features
description: ""
requirements:
  - nvidia-smi
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
import shutil
import signal
import subprocess

from ansible.module_utils.basic import AnsibleModule

# ex: vram8, vram11. GB, not GiB
VRAM_FEATURES = [8, 11, 12, 16, 23, 32, 40, 48, 80, 102]
# this should include all of the nvidia compute capability versions present in the cluster
# a node with sm_90 should inherit sm_89, sm_87, ...
INCLUDE_NV_CC = [5.2, 6.1, 7.0, 7.5, 8.0, 8.6, 8.7, 8.9, 9.0]
NV_SMI_TIMEOUT_SEC = 10
# features based on other features
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
}
# this takes precedence over FEATURE_INCLUDE_WHEN
FEATURE_EXCLUDE_WHEN = {}
FEATURE_EXCLUDE_REGEXES = []


def check_requirements(name2requirements: dict[str, list], _list: list) -> set[str]:
    """
    require that certain elements are present or absent from _list
    if requirements are satisfied, add `name` to the output list
    example: [
        "foobar": {
            "all_of": ["foo", "bar"]
            "any_of": ["fu", "ba"],
            "none_of": ["baz"],
        }
    ]
    if "foobar" is an empty dict, it will always be added to output
    """
    output = set()
    for name, requirements in name2requirements.items():
        if "all_of" in requirements:
            if not all(x in _list for x in requirements["all_of"]):
                continue
        if "any_of" in requirements:
            if not any(x in _list for x in requirements["any_of"]):
                continue
        if "none_of" in requirements:
            if any(x in _list for x in requirements["none_of"]):
                continue
        output.add(name)
    return output


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


def translate_model_name(model_name: str) -> str:
    """
    includes duplicates

    model names follow no consistent naming scheme
    `lshw` names and `nvidia-smi` names are equally inconsistent
    here are the names that I know this works for:
        NVIDIA A100-PCIE-40GB
        NVIDIA A100 80GB PCIe
        NVIDIA A100-SXM4-80GB
        NVIDIA A40
        NVIDIA GeForce GTX 1080 Ti
        NVIDIA GeForce GTX TITAN X
        NVIDIA GeForce RTX 2080
        NVIDIA GeForce RTX 2080 Ti
        Quadro RTX 8000
        Tesla M40 24GB
        Tesla V100-PCIE-16GB
        Tesla V100-SXM2-16GB
        Tesla V100-SXM2-32GB
        NVIDIA H100 80GB HBM3
    """
    model_name = model_name.lower()
    model_name = model_name.replace("nvidia", "")
    model_name = model_name.replace("geforce", "")
    model_name = model_name.replace("quadro", "")
    model_name = model_name.replace("tesla", "")
    model_name = model_name.replace("gtx", "")
    model_name = model_name.replace("hbm3", "")
    if "8000" not in model_name:
        model_name = model_name.replace("rtx", "")
    model_name = re.sub(r"\d+gb", "", model_name)
    model_name = model_name.replace("pcie", "")
    model_name = re.sub(r"sxm\d+", "", model_name)
    model_name = model_name.strip("_- ")
    model_name = re.sub(r"\s+", "_", model_name)
    return model_name


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
    vram_size_GB = round(vram_size_MiB * 1024 * 1024 / 1000000000)
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


def get_gpu_table(_module: AnsibleModule) -> list[list]:
    nv_smi_out = _check_output(
        [
            "nvidia-smi",
            "--query-gpu=name,memory.total,compute_cap",
            "--format=csv,noheader",
        ],
        _module,
        timeout_sec=NV_SMI_TIMEOUT_SEC,
    )
    gpu_table = [line.split(",") for line in nv_smi_out.splitlines()]
    if not all(len(row) == 3 for row in gpu_table):
        _module.fail_json(f"unexpected nvidia-smi output: {nv_smi_out}")
    output = []
    for model_name, vram_MiB, cc in gpu_table:
        output.append(
            [
                translate_model_name(model_name.strip()),
                int(re.sub(r"\s+MiB$", "", vram_MiB.strip())),
                float(cc),
            ]
        )
    return output


def main():
    _module = AnsibleModule(argument_spec={})
    gpu_table = get_gpu_table(_module)
    for row in gpu_table[1:]:
        for i, row in enumerate(gpu_table, start=1):
            if row != gpu_table[0]:
                _module.fail_json(msg=f"GPU {i} is different from GPU 0! all GPUs must be the same")
    model_name, vram_MiB, cc = gpu_table[0]
    gres = f"gpu:{model_name}:{len(gpu_table)}"
    features = {model_name}
    features.update(get_cuda_compute_capability_features(cc, _module))
    features.update(get_vram_features(vram_MiB, _module))
    # features.update(get_nvlink_features(_module))
    # add feature aliases
    features.update(check_requirements(FEATURE_INCLUDE_WHEN, features))
    # exclude excluded features
    features_to_remove = set()
    for feature in features:
        for exclude_regex in FEATURE_EXCLUDE_REGEXES:
            if re.match(exclude_regex, feature):
                features_to_remove.add(feature)
    features_to_remove.update(check_requirements(FEATURE_EXCLUDE_WHEN, features))
    features.difference_update(features_to_remove)

    _module.exit_json(
        ansible_facts={
            "slurm_gres": gres,
            "slurm_gpu_features": sorted(list(features)),
        }
    )


if __name__ == "__main__":
    main()

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
import sys
import shutil
import subprocess

from ansible.module_utils.basic import AnsibleModule

# ex: vram8, vram11. GB, not GiB
VRAM_FEATURES = [8, 11, 12, 16, 23, 32, 40, 48, 80]
# this should include all of the nvidia compute capability versions present in the cluster
# a node with sm_90 should inherit sm_89, sm_87, ...
INCLUDE_NV_CC = [5.2, 6.1, 7.0, 7.5, 8.0, 8.6, 8.7, 8.9, 9.0]
BLOCKING_TIMEOUT_SEC = 10
# features based on other features
FEATURE_INCLUDE_WHEN = {
    "a100-80g": {
        "all_of": ["a100", "vram80"],
    },
    "a100-40g": {
        "all_of": ["a100", "vram40"],
        "none_of": ["vram80"],
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
# features that are added based on CPU micro-architecture (uarch)
# for example, an icelake node's uarch list looks like this:
# icelake, cascadelake, cannonlake, skylake_avx512, skylake, x86_64_v4, broadwell, haswell,
# ivybridge, x86_64_v3, sandybridge, westmere, nehalem, core2, x86_64_v2, nocona, x86_64
# icelake is just cascadelake plus a few more extra flags/features/capabilities listed in `lscpu`
# a machine with ppc64le CPUs doesn't seem to list any flags
FEATURE_UARCH_ALIASES = {}


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


def _check_output(argv: list[str]) -> str:
    return subprocess.check_output(argv, text=True, timeout=BLOCKING_TIMEOUT_SEC)


def get_gpu_model_names() -> list[str]:
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
    features = []
    if not shutil.which("nvidia-smi"):
        return features
    nv_smi_out = _check_output(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"])
    for model_name in nv_smi_out.splitlines():
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

        features.append(model_name)
    return features


def get_cuda_compute_capability_features() -> set[str]:
    features = set()
    if not shutil.which("nvidia-smi"):
        return features
    nv_smi_out = _check_output(
        ["nvidia-smi", "--query-gpu=compute_cap", "--format=csv,noheader", "--id=0"]
    )
    cc = float(nv_smi_out)
    # include older CCs
    ccs = [x for x in INCLUDE_NV_CC if x <= cc]
    if cc not in INCLUDE_NV_CC:
        print(
            "WARNING: `INCLUDE_NV_CC` is supposed to contain a list of all the nvidia compute "
            + f"capability versions present in the cluster, but my version {cc} is not present!",
            file=sys.stderr,
        )
        ccs.append(cc)
    for cc in ccs:
        # 5.2 -> "sm_52"
        features.add(f"sm_{str(cc).replace('.', '')}")
    return features


def get_vram_features() -> set[str]:
    if not shutil.which("nvidia-smi"):
        return set()
    nv_smi_out = _check_output(["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader"])
    found_vram_sizes_MiB = []
    for line in [x.strip() for x in nv_smi_out.splitlines()]:
        found_size = int(re.match(r"(\d+) MiB", line).group(1))
        found_vram_sizes_MiB.append(found_size)
    assert all_elements_equal(found_vram_sizes_MiB)
    vram_size_GB = found_vram_sizes_MiB[0] * 1024 * 1024 / 1000000000
    qualified_vram_features = {x for x in VRAM_FEATURES if x <= vram_size_GB}
    return {f"vram{x}" for x in qualified_vram_features}


def get_nvlink_features() -> set[str]:
    if not shutil.which("nvidia-smi"):
        return set()
    nv_smi_out = _check_output(["nvidia-smi", "nvlink", "--status"])
    for line in nv_smi_out.splitlines():
        if re.match(r"^GPU \d+: ", line):
            continue
        if line == "NVML: Unable to retrieve NVLink information as all links are inActive":
            continue
        if re.fullmatch(r"Link \d+: [\d\.]+ GB/s", line.strip()):
            return {"nvlink"}
        print(f'unexpected output from "nvidia-smi nvlink --status": "{line}"', file=sys.stderr)
    return set()


def get_gres() -> str:
    gpu_model_names = get_gpu_model_names()
    if len(gpu_model_names) > 0:
        gpu_model_name_counts = {}
        for gpu_model_name in gpu_model_names:
            if gpu_model_name not in gpu_model_name_counts:
                gpu_model_name_counts[gpu_model_name] = 0
            gpu_model_name_counts[gpu_model_name] += 1
        gres = ""
        for model_name, count in gpu_model_name_counts.items():
            gres += f"gpu:{model_name}:{count},"
        gres = gres.strip(",")
    return gres


def main():
    gres = get_gres()

    features = set()
    feature_collectors = [
        get_gpu_model_names,
        get_cuda_compute_capability_features,
        get_vram_features,
        get_nvlink_features,
    ]

    # run each feature collector and combine their results
    for collector in feature_collectors:
        features.update(collector())
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

    module = AnsibleModule(argument_spec={})
    module.exit_json(
        ansible_facts={"slurm_gres": gres, "slurm_gpu_features": sorted(list(features))}
    )


if __name__ == "__main__":
    main()

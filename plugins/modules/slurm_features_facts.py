#!/usr/bin/python

# archspec hardcoded instead because module dependencies are weird
# requirements:
#   - L(archspec,https://pypi.org/project/archspec/)
DOCUMENTATION = r"""
name: slurm_features_facts
short_description: facts module that finds Slurm features for this machine
description: ""
author: Simon Leary <simon.leary42@proton.me>
version_added: 2.18.1
"""

RETURN = r"""
slurm_features:
    description: list of slurm features
    type: list
    elements: string
    returned: always
    sample:
      - intel
      - x86_64
      - skylake
      - 10gbps
"""

import re
import platform
import subprocess
from ansible.module_utils.basic import AnsibleModule
from ansible_collections.unity.general.plugins.module_utils.archspec import UARCH_DB

BLOCKING_TIMEOUT_SEC = 10
# features based on other features
FEATURE_INCLUDE_WHEN = {
    "arm64": {
        "all_of": ["aarch64"],
    },
}
# this takes precedence over FEATURE_INCLUDE_WHEN
FEATURE_EXCLUDE_WHEN = {}
FEATURE_EXCLUDE_REGEXES = [
    r"armv\d.*",  # this is broken but we don't use armv* anyway
    r"neoverse.*",
]
# features that are added based on CPU micro-architecture (uarch)
# for example, an icelake node's uarch list looks like this:
# icelake, cascadelake, cannonlake, skylake_avx512, skylake, x86_64_v4, broadwell, haswell,
# ivybridge, x86_64_v3, sandybridge, westmere, nehalem, core2, x86_64_v2, nocona, x86_64
# icelake is just cascadelake plus a few more extra flags/features/capabilities listed in `lscpu`
# a machine with ppc64le CPUs doesn't seem to list any flags
FEATURE_UARCH_ALIASES = {"avx512": {"any_of": ["x86_64_v4", "skylake_avx512"]}}
CPU_FAMILY = platform.machine()


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


def _check_output(argv: list[str]) -> str:
    return subprocess.check_output(argv, text=True, timeout=BLOCKING_TIMEOUT_SEC)


def get_link_speed() -> set[str]:
    ip_out = _check_output(["ip", "route", "get", "8.8.8.8"])
    dev = ip_out.split(" ")[4]
    valid_speeds = [10, 25, 40, 100, 200]
    with open(f"/sys/class/net/{dev}/speed") as fd:
        speed = fd.read().strip()
        speed = int(speed) // 1000
        if speed > 1:
            return {f"{x}gbps" for x in valid_speeds if x <= speed}
    return set()


def _get_cpu_vendor_model() -> tuple[str, str]:
    vendor = None
    model = None
    lscpu_out = _check_output(["lscpu"])
    for line in lscpu_out.splitlines():
        if line.startswith("Model name:"):
            _, model = line.split(":", 1)
            model = model.strip()
        if line.startswith("Vendor ID:"):
            _, vendor = line.split(":", 1)
            vendor = vendor.strip()
    return (vendor, model)


def get_cpu_model_features() -> set[str]:
    cpu_vendor, cpu_model = _get_cpu_vendor_model()
    features = set()
    if cpu_vendor is not None:
        if cpu_vendor == "GenuineIntel":
            features.add("intel")
        elif cpu_vendor == "AuthenticAMD":
            features.add("amd")
        # power9 comes from get_uarch_features
    if cpu_model is not None:
        if cpu_model.lower().startswith("intel(r)"):
            model_number_regex = r"\b\d{4,}[a-z]?(?: v\d)?\b"  # examples: "8352y", "2620 v3"
            matches = re.findall(model_number_regex, cpu_model.lower())
            assert (
                len(matches) == 1
            ), f'wrong number of regex matches! cpu_model: "{cpu_model.lower()}", regex: "{model_number_regex}", matches: "{matches}"'
            features.add(f"intel{matches[0].lower().replace(' ', '')}")
        if cpu_model.lower().startswith("amd"):
            model_number_regex = r"\b\d[0-9a-z]{3,}\b"  # examples: "7h12", "1900x", "7955wx"
            matches = re.findall(model_number_regex, cpu_model.lower())
            assert (
                len(matches) == 1
            ), f'wrong number of regex matches! cpu_model: "{cpu_model.lower()}", regex: "{model_number_regex}", matches: "{matches}"'
            features.add(f"amd{matches[0].lower()}")
        if cpu_model == "Neoverse-N1":
            features.add("armn1")
        elif cpu_model == "Neoverse-V2":
            features.add("armn2")
        if "altivec supported" in cpu_model:
            features.add("altivec")
    return features


def _get_uarches() -> list[str]:
    """
    shamelessly ripped off from https://github.com/archspec/archspec
    known bug: aarch64 nodes are given all versions of ARM, despite not being qualified.
    While PowerPC has a "generation" requirement, there is no information given
    on how to enforce these arm versions. And since we don't use those arm versions as
    slurm features on Unity, I ignore the problem
    """
    with open("/proc/cpuinfo", "r", encoding="utf8") as proc_cpuinfo_file:
        cpuinfo = {}
        for line in proc_cpuinfo_file:
            key, separator, value = line.partition(":")
            if separator != ":" and cpuinfo:  # end of first entry
                break
            cpuinfo[key.strip()] = value.strip()
    if "cpu" in cpuinfo and "POWER" in cpuinfo["cpu"]:
        generation_match = re.search(r"POWER(\d+)", cpuinfo["cpu"])
        cpuinfo["generation"] = int(generation_match.group(1))
        cpuinfo["vendor_id"] = "IBM"
    if CPU_FAMILY == "aarch64":
        cpuinfo["vendor_id"] = "ARM"
        cpuinfo["flags"] = cpuinfo["Features"].split()
        del cpuinfo["Features"]
    if "vendor_id" not in cpuinfo:
        cpuinfo["vendor_id"] = "generic"
    if "flags" not in cpuinfo:
        cpuinfo["flags"] = []
    if isinstance(cpuinfo["flags"], str):
        cpuinfo["flags"] = cpuinfo["flags"].split()
    for name, info in UARCH_DB["feature_aliases"].items():
        if "families" in info:
            if not any(x == CPU_FAMILY for x in info["families"]):
                continue
        if "any_of" in info:
            if not any(x in cpuinfo["flags"] for x in info["any_of"]):
                continue
        if "all_of" in info:
            if not all(x in cpuinfo["flags"] for x in info["all_of"]):
                continue
        cpuinfo["flags"].append(name)
    found_uarches = [CPU_FAMILY]
    for name, info in UARCH_DB["microarchitectures"].items():
        # require that any of the "from" uarches are found
        if len(info["from"]) > 0:
            if not any(x in found_uarches for x in info["from"]):
                continue
        # assume "generic" means "allow anything"
        if info["vendor"] != "generic" and not cpuinfo["vendor_id"] == info["vendor"]:
            continue
        if not all(x in cpuinfo["flags"] for x in info["features"]):
            continue
        # ignore empty
        if info["vendor"] == "generic" and info["features"] == [] and info["from"] == []:
            continue
        if "generation" in info and "generation" in cpuinfo:
            if info["generation"] > cpuinfo["generation"]:
                continue
        found_uarches.append(name)
    return found_uarches


def _find_best_uarches(uarches: list[str]) -> list[str]:
    """
    explore up the "from" tree to remove uarches which were only stepping stones to other uarches
    """
    remove_these = set()
    uarch_db2 = UARCH_DB["microarchitectures"]
    for uarch in uarches:
        if "from" not in uarch_db2[uarch] or len(uarch_db2[uarch]["from"]) == 0:
            continue
        queue = uarch_db2[uarch]["from"]
        while len(queue) > 0:
            cursor = queue.pop(0)
            if cursor in remove_these:
                continue
            remove_these.add(cursor)
            if "from" in uarch_db2[cursor] and len(uarch_db2[cursor]["from"]) > 0:
                queue = queue + uarch_db2[cursor]["from"]
    output = uarches
    for remove_uarch in remove_these:
        output = [x for x in output if x != remove_uarch]
    return output


def get_uarch_features() -> set[str]:
    features = set()
    uarches = _get_uarches()
    features.update(_find_best_uarches(uarches))
    if CPU_FAMILY == "x86_64":
        generic_uarches = [x for x in uarches if re.match(r"x86_64_v\d", x)]
        features.update(generic_uarches)
    # add uarch aliases
    features.update(check_requirements(FEATURE_UARCH_ALIASES, uarches))
    return features


def main():
    features = set()
    features.add(CPU_FAMILY)
    feature_collectors = [
        get_cpu_model_features,
        get_uarch_features,
        get_link_speed,
    ]
    for collector in feature_collectors:
        features.update(collector())

    features.update(check_requirements(FEATURE_INCLUDE_WHEN, features))

    features_to_remove = set()
    for feature in features:
        for exclude_regex in FEATURE_EXCLUDE_REGEXES:
            if re.match(exclude_regex, feature):
                features_to_remove.add(feature)
    features_to_remove.update(check_requirements(FEATURE_EXCLUDE_WHEN, features))
    features.difference_update(features_to_remove)

    module = AnsibleModule(argument_spec={})
    module.exit_json(ansible_facts={"slurm_features": sorted(list(features))})


if __name__ == "__main__":
    main()

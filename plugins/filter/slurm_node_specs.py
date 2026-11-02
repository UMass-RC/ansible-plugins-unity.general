#!/usr/bin/env python3
"""
pack slurm NodeName list strings from an unpacked node spec DB
packing is done only for nodes with equal specs
one large dict keyed by NodeName values -> list of dicts each with a NodeName key
use the "-" argument to take yaml from stdin

all node specs of type list will be sorted

ex:
```
cpu001:
  Weight: 10
cpu002:
  Weight: 10
cpu003:
  Weight: 10
```
becomes
```
- NodeName: cpu[001-003]
  Weight: 10
```

####################################################################################################

unpack slurm NodeName list strings from one or more packed node spec DB files
list of dicts each with a NodeName key -> one large dict keyed by NodeName values
use the "-" argument to take yaml from stdin

If the RemoveFeatures spec is defined for a node, each those strings will be removed from the
Features spec, if they exist.
If the RemoveSpecs spec is defined for a node, each of those specs will be removed, if they exist.
The RemoveFeatures and RemoveSpecs specs will always be removed.
Removals are conducted after all files have been combined, which means that DB files can subtract
content from one another.

duplicate/overlapping NodeName lists are allowed.
between files:
    conflicting definitions are allowed. Each file takes precedence over the last.
within a file:
    conflicting definitions are not allowed, except for objects of type list or dict.
    conflicting lists: items in the new list will be added to the old list only if they don't
    already exist in the old list.
    conflicting dictionaries: recursive. no conflicting definitions except for dict/list.

ex:
```
- NodeName: cpu[001-003]
  Weight: 10
  CPUs: 1
- NodeName: cpu002
  RealMemory: 1234
  RemoveSpecs: [CPUs]
```
becomes
```
cpu001:
  Weight: 10
  CPUs: 1
cpu002:
  Weight: 10
  RealMemory: 1234
cpu003:
  Weight: 10
  CPUs: 1
```

####################################################################################################

The following terms are interchangable:
"hostlist expression", "node range expression", "folded node set", "NodeName list string"

"""

import hashlib
import json
import re

from ansible._internal._templating._jinja_common import Marker
from ansible.errors import AnsibleFilterError
from ansible.parsing.yaml.objects import AnsibleUnicode
from ansible.utils.display import Display
from ansible.vars.hostvars import HostVars
from ClusterShell.NodeSet import NodeSet

from ..plugin_utils.filter import dict_get, dict_get_deep

type _str = str | AnsibleUnicode
type _spec_value = _str | int | list[_str]
type NodeSpecsPacked = list[dict[_str, _spec_value]]
type NodeSpecsUnpacked = dict[_str, dict[_str, _spec_value]]

display = Display()

NODE_SPEC_ORDER = [
    "NodeName",
    "Features",
    "Boards",
    "SocketsPerBoard",
    "CoresPerSocket",
    "ThreadsPerCore",
]

MIN_MEMORY_REDUCTION_WARNING_MB = 1000


def _fold_node_set(hostnames: list[str]) -> str:
    """
    ["cpu001", "cpu002", "cpu003", "cpu006"] -> ["cpu[001-003,006]"]
    ["cpu001", "cpu002", "cpu003", "gpu001", "gpu002", "gpu003"] -> "cpu[001-003],gpu[001-003]"
    """
    return str(NodeSet.fromlist(hostnames))


def _get_dict_hash(x) -> str:
    sorted_json_bytes = json.dumps(x, sort_keys=True).encode()
    return hashlib.md5(sorted_json_bytes).hexdigest()


def _group_nodes_equal_specs(node_specs: NodeSpecsUnpacked) -> dict[str, dict]:
    confhash2conf = {}
    confhash2hostnames = {}
    hostnames2conf = {}
    for hostname, specs in node_specs.items():
        confhash = _get_dict_hash(specs)
        confhash2conf[confhash] = specs
        confhash2hostnames.setdefault(confhash, []).append(hostname)
    for confhash, hostnames in confhash2hostnames.items():
        node_set_str = _fold_node_set(hostnames)
        hostnames2conf[node_set_str] = confhash2conf[confhash]
    return hostnames2conf


def _make_string_sortable_numerically(string: str) -> list[tuple[int, int]]:
    """
    each character becomes a tuple of two ints. The first int is either 0,1, or 2
    0 for characters that come before numbers, 1 for numbers, 2 for after numbers
    the second int is the unicode value of the character, or the integer value of the number
    that this character is a part of.
                $         7         8         9         a        ~
    "$789a~" -> [[0, 36], [1, 789], [1, 789], [1, 789], [2, 97], [2, 126]]
    """
    output = []
    # yields each digit run or single non-digit character
    for token in re.findall(r"[0-9]+|[^0-9]", string):
        if token[0] in "0123456789":
            output.extend([(1, int(token))] * len(token))
        else:
            char_int = ord(token)
            output.append((0, char_int) if char_int < ord("0") else (2, char_int))
    return output


def _sort_with_priority(_list: list, _priority_elements: list):
    """
    move priority elements to the front, in the given order
    """

    def sort_key(x):
        # anything in 0 goes to the front, but then within 0 they are sorted
        # using their index in `_priority_elements`
        try:
            return (0, _priority_elements.index(x))
        except ValueError:
            return (1, x)

    return sorted(_list, key=sort_key)


def _dict_sorted_keys_with_priority(_dict: dict, _priority_keys: list) -> dict:
    sorted_keys = _sort_with_priority(list(_dict.keys()), _priority_keys)
    output = {}
    for key in sorted_keys:
        output[key] = _dict[key]
    return output


def pack(node_specs: NodeSpecsUnpacked) -> NodeSpecsPacked:
    _node_specs = {}
    # sort spec values
    # WARNING: I assume that the order doesn't matter for all specs of type list
    for hostname, specs in node_specs.items():
        _node_specs[hostname] = {}
        for spec_name, spec_value in specs.items():
            if isinstance(spec_value, list):
                if all(isinstance(x, str) for x in spec_value):
                    _node_specs[hostname][spec_name] = sorted(
                        spec_value,
                        key=_make_string_sortable_numerically,
                    )
                else:
                    _node_specs[hostname][spec_name] = sorted(spec_value)
            else:
                _node_specs[hostname][spec_name] = spec_value
    # "pack" the specs
    node_specs_packed = []
    for name_list_str, specs in _group_nodes_equal_specs(_node_specs).items():
        specs["NodeName"] = name_list_str
        node_specs_packed.append(specs)
    # sort spec keys
    # _group_nodes_equal_specs does json.dumps(sort_keys=True), but I can't tell json to use
    # my numeric sorting algorithm
    for i, specs in enumerate(node_specs_packed):
        node_specs_packed[i] = _dict_sorted_keys_with_priority(specs, NODE_SPEC_ORDER)
    # sort hostnames
    return sorted(
        node_specs_packed,
        # ignore '[' such that cpu001 doesn't end up sorted after cpu[002-005]
        key=lambda x: _make_string_sortable_numerically(
            re.sub(
                r"\[(\d)",
                r"\1",
                dict_get(x, "NodeName", str, name="node_specs_packed entry"),
            )
        ),
    )


def _unfold_node_set(hostnames: str) -> list[str]:
    """
    "cpu[001-003,006]" -> ["cpu001", "cpu002", "cpu003", "cpu006"]
    "cpu[001-003],gpu[001-003]" -> ["cpu001", "cpu002", "cpu003", "gpu001", "gpu002", "gpu003"]
    """
    return list(NodeSet(hostnames))


def _merge(dict1: dict, dict2: dict, path=None, allow_conflicts=False) -> dict:
    # track the current path during recursion for a good error message
    if path is None:
        path = []
    merged = dict(dict1)  # make a copy of the first dictionary
    for key, value in dict2.items():
        current_path = path + [key]  # Update the current path
        if key not in dict1:
            merged[key] = value
        else:
            # redundant copy: do nothing
            if dict1[key] == value:
                continue
            # dict: recursive call
            if isinstance(value, dict) and isinstance(dict1[key], dict):
                merged[key] = _merge(
                    dict1[key], value, current_path, allow_conflicts=allow_conflicts
                )
            # list: add dict2 list items to dict1 list if not already present
            elif isinstance(value, list) and isinstance(dict1[key], list):
                merged[key] = list(dict1[key]) + [item for item in value if item not in dict1[key]]
            # else: error
            else:
                if allow_conflicts:
                    merged[key] = value
                else:
                    full_path = ".".join(map(str, current_path))
                    msg = f"Conflict at '{full_path}': '{dict1[key]}' vs '{value}'"
                    raise RuntimeError(msg)
    return merged


def _unpack(node_specs: NodeSpecsPacked) -> NodeSpecsUnpacked:
    output = {}
    for specs in node_specs:
        folded_node_set = dict_get(specs, "NodeName", str, name="node_specs entry")
        del specs["NodeName"]
        for hostname in _unfold_node_set(folded_node_set):
            if hostname not in output:
                output[hostname] = specs
            else:
                output[hostname] = _merge(output[hostname], specs, allow_conflicts=False)
    return output


def _do_removals(node_specs: NodeSpecsPacked) -> NodeSpecsPacked:
    output = [x for x in node_specs]  # copy
    for specs in output:
        if "RemoveFeatures" in specs:
            if "Features" in specs:
                for remove_feature in dict_get(
                    specs, "RemoveFeatures", list, name="node_specs entry"
                ):
                    specs["Features"] = [
                        x
                        for x in dict_get(specs, "Features", list, name="node_specs entry")
                        if x != remove_feature
                    ]
            del specs["RemoveFeatures"]
        if "RemoveSpecs" in specs:
            for remove_spec in dict_get(specs, "RemoveSpecs", list, name="node_specs entry"):
                if remove_spec in specs:
                    del specs[remove_spec]
            del specs["RemoveSpecs"]
    return output


def unpack(node_specs: NodeSpecsPacked | list[NodeSpecsPacked]) -> dict[str, dict]:
    if not node_specs:
        return {}
    # type checker can't understand this check but it's solid
    if isinstance(node_specs[0], dict):
        output = _unpack(node_specs)  # type:ignore
    else:
        output = _unpack(node_specs[0])
        for specs in node_specs[1:]:
            output = _merge(output, _unpack(specs))  # type:ignore
    return output


type _mem_iter = list[tuple[int, str]]


def __cluster_memory(sorted_memoryMB_hostname: _mem_iter, max_reduction: int) -> _mem_iter:
    "cluster integers by reducing them by no more than max_reduction"
    # if the entire range can be reduced to equal the lowest number without violating max_reduction
    if sorted_memoryMB_hostname[-1][0] - sorted_memoryMB_hostname[0][0] <= max_reduction:
        new_memoryMB = sorted_memoryMB_hostname[0][0]
        output = []
        reduction2hostnames = {}
        for memoryMB, hostname in sorted_memoryMB_hostname:
            output.append((new_memoryMB, hostname))
            reduction = memoryMB - new_memoryMB
            if reduction != 0:
                reduction2hostnames.setdefault(reduction, []).append(hostname)
        for reduction, hostnames in reduction2hostnames.items():
            if reduction < MIN_MEMORY_REDUCTION_WARNING_MB:
                continue
            reduced_hostnames_folded = _fold_node_set(hostnames)
            display.warning(
                f"{reduced_hostnames_folded} RealMemory reduced by {reduction} MB to match {sorted_memoryMB_hostname[0][1]}"
            )
        return output
    # divide and conquer. split the range at the biggest gap
    # for each element, the corresponding gap is the distance between it and the previous element
    gaps = [-1]
    for i, (memoryMB, _) in enumerate(sorted_memoryMB_hostname[1:], start=1):
        gaps.append(memoryMB - sorted_memoryMB_hostname[i - 1][0])
    # https://stackoverflow.com/a/11825864/18696276
    biggest_gap_index = max(range(len(gaps)), key=gaps.__getitem__)
    left = __cluster_memory(sorted_memoryMB_hostname[:biggest_gap_index], max_reduction)
    right = __cluster_memory(sorted_memoryMB_hostname[biggest_gap_index:], max_reduction)
    return left + right


def _cluster_memory(
    node_specs_mem: NodeSpecsUnpacked,
    node_specs_nomem: NodeSpecsUnpacked,
    min_reduction_MB: int,
    max_reduction_MB: int,
) -> NodeSpecsUnpacked:
    # need to make a copy of node_specs_mem because it's passed by reference
    # and some other code is mucking with it
    output = {k: {"RealMemory": v["RealMemory"]} for k, v in node_specs_mem.items()}
    for grouping in pack(node_specs_nomem):
        sorted_memoryMB_hostname = sorted(
            [
                (
                    dict_get(
                        node_specs_mem[x],
                        "RealMemory",
                        int,
                        name=f'node_specs_mem["{x}"]',
                    ),
                    x,
                )
                for x in _unfold_node_set(
                    dict_get(grouping, "NodeName", str, name="packed node_specs_nomem group")
                )
            ]
        )
        # do reduction now rather than inside _cluster_memory so that it doesn't produce extra
        # warning messages for reducing each node memory by exactly min_reduction_MB
        sorted_memoryMB_hostname = [
            (x[0] - min_reduction_MB, x[1]) for x in sorted_memoryMB_hostname
        ]
        for mem, hostname in __cluster_memory(sorted_memoryMB_hostname, max_reduction_MB):
            output[hostname]["RealMemory"] = mem
    return output


def slurm_node_specs_slim_from_hostvars(
    hostvars: HostVars, hosts: list[str] | None = None
) -> dict[str, dict]:
    "assemble slurm node specs (no RealMemory, no GPU) from hostvars"
    if hostvars is None:
        raise AnsibleFilterError("keyword argument required: hostvars")
    if hosts is None:
        raise AnsibleFilterError("keyword argument required: hosts")
    output = {}
    for hostname in hosts:
        if hostname not in hostvars:
            raise AnsibleFilterError(f'host "{hostname}" not found in host vars!"')
        this_host_vars = hostvars[hostname]
        if isinstance(this_host_vars, Marker):
            raise AnsibleFilterError(f'hostvars["{hostname}"] is unresolved')
        lscpu = dict_get(this_host_vars, "lscpu", dict, name=f'hostvars["{hostname}"]')
        lscpu_name = f'hostvars["{hostname}"]["lscpu"]'
        output[hostname] = {
            "Boards": 1,
            "SocketsPerBoard": int(dict_get(lscpu, "Socket(s)", str, name=lscpu_name)),
            "CoresPerSocket": int(dict_get(lscpu, "Core(s) per socket", str, name=lscpu_name)),
            "ThreadsPerCore": int(dict_get(lscpu, "Thread(s) per core", str, name=lscpu_name)),
            "Features": dict_get(
                this_host_vars, "slurm_features", list, name=f'hostvars["{hostname}"]'
            ),
        }
    return output


def slurm_node_specs_mem_from_hostvars(
    hostvars: HostVars, hosts: list[str] | None = None
) -> dict[str, dict]:
    "assemble slurm node specs (RealMemory only) from hostvars"
    if hostvars is None:
        raise AnsibleFilterError("keyword argument required: hostvars")
    if hosts is None:
        raise AnsibleFilterError("keyword argument required: hosts")
    output = {}
    for hostname in hosts:
        if hostname not in hostvars:
            raise AnsibleFilterError(f'host "{hostname}" not found in host vars!"')
        this_host_vars = hostvars[hostname]
        if isinstance(this_host_vars, Marker):
            raise AnsibleFilterError(f'hostvars["{hostname}"] is unresolved')
        output[hostname] = {
            "RealMemory": dict_get_deep(
                this_host_vars,
                ["ansible_memory_mb", "real", "total"],
                int,
                name=f'hostvars["{hostname}"]',
            )
        }
    return output


def slurm_node_specs_gpu_from_hostvars(
    hostvars: HostVars, hosts: list[str] | None = None
) -> dict[str, dict]:
    "assemble slurm node specs (gres, GPU features only) from hostvars"
    if hostvars is None:
        raise AnsibleFilterError("keyword argument required: hostvars")
    if hosts is None:
        raise AnsibleFilterError("keyword argument required: hosts")
    output: dict[str, dict] = {}
    for hostname in hosts:
        if hostname not in hostvars:
            raise AnsibleFilterError(f'host "{hostname}" not found in host vars!"')
        this_host_vars = hostvars[hostname]
        if isinstance(this_host_vars, Marker):
            raise AnsibleFilterError(f'hostvars["{hostname}"] is unresolved')
        output.setdefault(hostname, {"Features": []})
        output[hostname]["Gres"] = dict_get(
            this_host_vars, "slurm_gres", str, name=f'hostvars["{hostname}"]'
        )
        output[hostname]["Features"].extend(
            dict_get(
                this_host_vars,
                "slurm_gpu_features",
                list,
                name=f'hostvars["{hostname}"]',
            )
        )
    return output


def slurm_node_specs_merge(
    _,
    node_specs_hostfacts_mem: NodeSpecsPacked | list[NodeSpecsPacked] | None = None,
    node_specs_hostfacts_nomem: NodeSpecsPacked | list[NodeSpecsPacked] | None = None,
    node_specs_hardcoded: NodeSpecsPacked | list[NodeSpecsPacked] | None = None,
    min_mem_reduction_MB=100,
    max_mem_reduction_MB=1000,
):
    "assemble slurm node specs from hostvars, also handle RemoveFeatures and RemoveSpecs"
    if node_specs_hostfacts_mem is None:
        raise AnsibleFilterError("keyword argument required: node_specs_hostfacts_mem")
    if node_specs_hostfacts_nomem is None:
        raise AnsibleFilterError("keyword argument required: node_specs_hostfacts_nomem")
    if node_specs_hardcoded is None:
        raise AnsibleFilterError("keyword argument required: node_specs_hardcoded")
    node_specs_hostfacts_mem_unpacked = unpack(node_specs_hostfacts_mem)
    node_specs_hostfacts_nomem_unpacked = unpack(node_specs_hostfacts_nomem)
    node_specs_hardcoded_unpacked = unpack(node_specs_hardcoded)
    mem_clustered = _cluster_memory(
        node_specs_hostfacts_mem_unpacked,
        node_specs_hostfacts_nomem_unpacked,
        min_mem_reduction_MB,
        max_mem_reduction_MB,
    )
    return _do_removals(
        pack(
            _merge(
                _merge(mem_clustered, node_specs_hostfacts_nomem_unpacked),
                node_specs_hardcoded_unpacked,
                allow_conflicts=True,
            )
        )
    )


def slurm_node_specs_feature_groups(node_specs: NodeSpecsPacked) -> dict:
    inventory = {"plugin": "unity.general.groups_simplified_nodeset", "groups": {}}
    for specs in node_specs:
        for feature in dict_get(specs, "Features", list, name="node_specs entry"):
            group_name = f"slurm_nodes_{feature}"
            for forbidden_char in [".", "-"]:
                group_name = group_name.replace(forbidden_char, "_")
            inventory["groups"].setdefault(group_name, {"hosts": []})
            inventory["groups"][group_name]["hosts"].append(specs["NodeName"])
    sorted_groups = {}
    for group_name in sorted(inventory["groups"], key=_make_string_sortable_numerically):
        nodeset = _fold_node_set(inventory["groups"][group_name]["hosts"])
        sorted_groups[group_name] = {"hosts": [nodeset]}
    inventory["groups"] = sorted_groups
    return inventory


def slurm_all_gpu_models(node_specs: NodeSpecsPacked) -> list[str]:
    output = set()
    for specs in node_specs:
        if "Gres" in specs:
            for token in dict_get(specs, "Gres", str, name="node_specs entry").split(","):
                if (_match := re.fullmatch(r"gpu:(.*):\d+", token)) is not None:
                    output.add(_match.group(1))
    return sorted(output)


class FilterModule:
    def filters(self):
        return dict(
            slurm_node_specs_pack=pack,
            slurm_node_specs_unpack=unpack,
            slurm_node_specs_slim_from_hostvars=slurm_node_specs_slim_from_hostvars,
            slurm_node_specs_mem_from_hostvars=slurm_node_specs_mem_from_hostvars,
            slurm_node_specs_gpu_from_hostvars=slurm_node_specs_gpu_from_hostvars,
            slurm_node_specs_merge=slurm_node_specs_merge,
            slurm_node_specs_feature_groups=slurm_node_specs_feature_groups,
            slurm_all_gpu_models=slurm_all_gpu_models,
        )

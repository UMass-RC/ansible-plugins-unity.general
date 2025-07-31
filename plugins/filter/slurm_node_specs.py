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
import re
import copy
import json
import hashlib
import itertools
from ClusterShell.NodeSet import NodeSet

from ansible.vars.hostvars import HostVars, HostVarsVars
from ansible.utils.display import Display
from ansible.errors import AnsibleFilterError
from ansible.parsing.yaml.objects import AnsibleUnicode

from ansible_collections.unity.general.plugins.plugin_utils.beartype import beartype

type _str = (str | AnsibleUnicode)
type NodeSpecs = dict[_str, (_str | int | list[_str])]
type PartitionData = dict[_str, (_str | int | list[_str])]
type NodeSpecsPacked = list[NodeSpecs]
type NodeSpecsUnpacked = dict[_str, NodeSpecs]

display = Display()

NODE_SPEC_ORDER = [
    "NodeName",
    "Features",
    "Boards",
    "SocketsPerBoard",
    "CoresPerSocket",
    "ThreadsPerCore",
]


@beartype
def _fold_node_set(hostnames: list[str]) -> str:
    """
    ["cpu001", "cpu002", "cpu003", "cpu006"] -> ["cpu[001-003,006]"]
    ["cpu001", "cpu002", "cpu003", "gpu001", "gpu002", "gpu003"] -> "cpu[001-003],gpu[001-003]"
    """
    return str(NodeSet.fromlist(hostnames))


@beartype
def _get_dict_hash(x) -> str:
    sorted_json_bytes = json.dumps(x, sort_keys=True).encode()
    return hashlib.md5(sorted_json_bytes).hexdigest()


@beartype
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


@beartype
def _make_string_sortable_numerically(string: str) -> list[tuple[int, int]]:
    """
    each character becomes a tuple of two ints. The first int is either 0,1, or 2
    0 for characters that come before numbers, 1 for numbers, 2 for after numbers
    the second int is the unicode value of the character, or the integer value of the number
    that this character is a part of.
                $         7         8         9         a        ~
    "$789a~" -> [[0, 36], [1, 789], [1, 789], [1, 789], [2, 97], [2, 126]]
    """
    output = [[None, None] for _ in range(len(string))]
    skip_these_indexes = [False] * len(string)
    for i, char in enumerate(string):
        if skip_these_indexes[i]:
            continue
        char_int = ord(char)
        if char_int < ord("0"):
            output[i] = (0, char_int)
        elif str.isdigit(char):
            first_digit_index = i
            last_digit_index = i
            while last_digit_index < len(string) - 1 and str.isdigit(string[last_digit_index + 1]):
                last_digit_index += 1
            this_number = int(string[first_digit_index : last_digit_index + 1])
            for digit_index in range(first_digit_index, last_digit_index + 1):
                skip_these_indexes[digit_index] = True
                output[digit_index] = (1, this_number)
        elif char_int > ord("9"):
            output[i] = (2, char_int)
    return output


@beartype
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


@beartype
def _dict_sorted_keys_with_priority(_dict: dict, _priority_keys: list) -> dict:
    sorted_keys = _sort_with_priority(list(_dict.keys()), _priority_keys)
    output = dict()
    for key in sorted_keys:
        output[key] = _dict[key]
    return output


@beartype
def pack(node_specs: NodeSpecsUnpacked) -> NodeSpecsPacked:
    _node_specs = copy.deepcopy(node_specs)
    # sort spec values
    # WARNING: I assume that the order doesn't matter for all specs of type list
    for hostname, specs in _node_specs.items():
        for spec_name, spec_value in specs.items():
            if isinstance(spec_value, list):
                if all(isinstance(x, str) for x in spec_value):
                    _node_specs[hostname][spec_name] = sorted(
                        _node_specs[hostname][spec_name],
                        key=_make_string_sortable_numerically,
                    )
                else:
                    _node_specs[hostname][spec_name] = sorted(_node_specs[hostname][spec_name])
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
        key=lambda x: _make_string_sortable_numerically(re.sub(r"\[(\d)", r"\1", x["NodeName"])),
    )


@beartype
def _join_if_list(x: list | str) -> str:
    return ",".join(x) if isinstance(x, list) else x


@beartype
def _expand_aliases(hostnames: str, aliases: dict) -> str:
    output = list(NodeSet(_join_if_list(hostnames)))
    for alias_name, alias_value in aliases.items():
        for i, x in enumerate(output):
            if x == alias_name:
                output[i] = _join_if_list(alias_value)
                break
    return ",".join(output)


@beartype
def _unfold_node_set(hostnames: str, aliases: dict | None = None) -> list[str]:
    """
    "cpu[001-003,006]" -> ["cpu001", "cpu002", "cpu003", "cpu006"]
    "cpu[001-003],gpu[001-003]" -> ["cpu001", "cpu002", "cpu003", "gpu001", "gpu002", "gpu003"]
    """
    if aliases is not None:
        hostnames = _expand_aliases(hostnames, aliases)
    return list(NodeSet(hostnames))


@beartype
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
            if dict1[key] == dict2[key]:
                continue
            # dict: recursive call
            if isinstance(value, dict) and isinstance(dict1[key], dict):
                merged[key] = _merge(dict1[key], value, current_path)
            # list: add dict2 list items to dict1 list if not already present
            elif isinstance(value, list) and isinstance(dict1[key], list):
                merged[key] = list(dict1[key]) + [item for item in value if item not in dict1[key]]
            # else: error
            else:
                if allow_conflicts:
                    merged[key] = dict2[key]
                else:
                    full_path = ".".join(map(str, current_path))
                    msg = f"Conflict at '{full_path}': '{dict1[key]}' vs '{dict2[key]}'"
                    raise RuntimeError(msg)
    return merged


@beartype
def _unpack(node_specs: NodeSpecsPacked) -> NodeSpecsUnpacked:
    output = {}
    for specs in node_specs:
        folded_node_set = specs["NodeName"]
        del specs["NodeName"]
        for hostname in _unfold_node_set(folded_node_set):
            these_specs = copy.deepcopy(specs)  # don't want yaml anchors/references
            if hostname not in output:
                output[hostname] = these_specs
            else:
                output[hostname] = _merge(output[hostname], these_specs, allow_conflicts=False)
    return output


@beartype
def _do_removals(node_specs: NodeSpecsUnpacked) -> NodeSpecsUnpacked:
    output = {}
    for hostname, specs in node_specs.items():
        if "RemoveFeatures" in specs:
            if "Features" in specs:
                for remove_feature in specs["RemoveFeatures"]:
                    specs["Features"] = [x for x in specs["Features"] if x != remove_feature]
            del specs["RemoveFeatures"]
        if "RemoveSpecs" in specs:
            for remove_spec in specs["RemoveSpecs"]:
                if remove_spec in specs:
                    del specs[remove_spec]
            del specs["RemoveSpecs"]
        output[hostname] = specs
    return output


# FIXME if node_specs is already unpacked, we get KeyError NodeName, beartype should catch that
@beartype
def unpack(node_specs: NodeSpecsPacked | list[NodeSpecsPacked]) -> dict[str, dict]:
    if not node_specs:
        return {}
    if isinstance(node_specs[0], dict):
        output = _unpack(node_specs)
    else:
        output = _unpack(node_specs[0])
        for specs in node_specs[1:]:
            output = _merge(output, _unpack(specs))
    return _do_removals(output)


type _mem_iter = list[tuple[int, str]] | itertools.chain[tuple[int, str]]


@beartype
def _cluster_memory(sorted_memoryMB_hostname: _mem_iter, max_reduction: int) -> _mem_iter:
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
            if reduction <= 100:
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
    # https://stackoverflow.com/a/1724975/18696276
    return itertools.chain(
        _cluster_memory(sorted_memoryMB_hostname[:biggest_gap_index], max_reduction),
        _cluster_memory(sorted_memoryMB_hostname[biggest_gap_index:], max_reduction),
    )


@beartype
def cluster_mem(
    node_specs_mem: NodeSpecsUnpacked = None,
    node_specs_nomem: NodeSpecsUnpacked = None,
    min_reduction_MB=100,
    max_reduction_MB=1000,
) -> NodeSpecsUnpacked:
    if node_specs_mem is None:
        raise AnsibleFilterError("keyword argument required: node_specs_mem")
    if node_specs_nomem is None:
        raise AnsibleFilterError("keyword argument required: node_specs_nomem")
    output = node_specs_mem.copy()
    for grouping in pack(node_specs_nomem):
        sorted_memoryMB_hostname = sorted(
            [(node_specs_mem[x]["RealMemory"], x) for x in _unfold_node_set(grouping["NodeName"])]
        )
        # do reduction now rather than inside _cluster_memory so that it doesn't produce extra
        # warning messages for reducing each node memory by exactly min_reduction_MB
        sorted_memoryMB_hostname = [
            (x[0] - min_reduction_MB, x[1]) for x in sorted_memoryMB_hostname
        ]
        for mem, hostname in _cluster_memory(sorted_memoryMB_hostname, max_reduction_MB):
            output[hostname]["RealMemory"] = mem
    return output


@beartype
def _dict_get(_dict: dict | HostVars | HostVarsVars, key: str, name="dict") -> object:
    try:
        return _dict[key]
    except KeyError as e:
        raise AnsibleFilterError(f'key "{key}" not found in "{name}"') from e


@beartype
def _dict_get_deep(_dict: dict | HostVars | HostVarsVars, keys: list[str], name="dict") -> object:
    cursor = _dict
    for key in keys:
        name += f'["{key}"]'
        try:
            cursor = cursor[key]
        except KeyError as e:
            raise AnsibleFilterError(f'key "{key}" not found in "{name}"') from e
    return cursor


@beartype
def slurm_node_specs_slim_from_hostvars(
    _, hostvars: HostVars = None, hosts: list[str] = None
) -> dict[str, dict]:
    "assemble slurm node specs (no RealMemory, no GPU) from hostvars"
    if hostvars is None:
        raise AnsibleFilterError("keyword argument required: hostvars")
    if hosts is None:
        raise AnsibleFilterError("keyword argument required: hosts")
    output = {}
    for hostname in hosts:
        if hostname in hostvars:
            lscpu = _dict_get(hostvars[hostname], "lscpu", name=f'hostvars["{hostname}"]')
            lscpu_name = 'hostvars["{hostname}"]["lscpu"]'
            output[hostname] = {
                "Boards": 1,
                "SocketsPerBoard": int(_dict_get(lscpu, "Socket(s)", name=lscpu_name)),
                "CoresPerSocket": int(_dict_get(lscpu, "Core(s) per socket", name=lscpu_name)),
                "ThreadsPerCore": int(_dict_get(lscpu, "Thread(s) per core", name=lscpu_name)),
                "Features": _dict_get(
                    hostvars[hostname], "slurm_features", name=f'hostvars["{hostname}"]'
                ),
            }
    return output


@beartype
def slurm_node_specs_mem_from_hostvars(
    _, hostvars: HostVars = None, hosts: list[str] = None
) -> dict[str, dict]:
    "assemble slurm node specs (RealMemory only) from hostvars"
    if hostvars is None:
        raise AnsibleFilterError("keyword argument required: hostvars")
    if hosts is None:
        raise AnsibleFilterError("keyword argument required: hosts")
    output = {}
    for hostname in hosts:
        if hostname in hostvars:
            output[hostname] = {
                "RealMemory": _dict_get_deep(
                    hostvars[hostname],
                    ["ansible_memory_mb", "real", "total"],
                    name=f'hostvars["{hostname}"]',
                )
            }
    return output


@beartype
def slurm_node_specs_gpu_from_hostvars(
    _, hostvars: HostVars = None, hosts: list[str] = None
) -> dict[str, dict]:
    "assemble slurm node specs (gres, GPU features only) from hostvars"
    if hostvars is None:
        raise AnsibleFilterError("keyword argument required: hostvars")
    if hosts is None:
        raise AnsibleFilterError("keyword argument required: hosts")
    output = {}
    for hostname in hosts:
        if hostname in hostvars:
            output.setdefault(hostname, {"Features": []})
            output[hostname]["Gres"] = _dict_get(
                hostvars[hostname], "slurm_gres", name=f'hostvars["{hostname}"]'
            )
            output[hostname]["Features"].extend(
                _dict_get(hostvars[hostname], "slurm_gpu_features", name=f'hostvars["{hostname}"]')
            )
    return output


@beartype
def build_full_node_specs(
    node_specs_packed_trio: list[NodeSpecsPacked],
    min_memory_reduction_MB: int | None = None,
    max_memory_reduction_MB: int | None = None,
) -> NodeSpecsUnpacked:
    "combine the mem, nomem, and hardcoded node specs, and adjust the mem for better groupings"
    mem, nomem, hardcoded = node_specs_packed_trio
    hardcoded_unpacked = unpack(hardcoded)
    nomem_unpacked = unpack(nomem)
    mem_unpacked = unpack(mem)
    cluster_mem_kwargs = {}
    if min_memory_reduction_MB is not None:
        cluster_mem_kwargs["min_reduction_MB"] = min_memory_reduction_MB
    if max_memory_reduction_MB is not None:
        cluster_mem_kwargs["max_reduction_MB"] = max_memory_reduction_MB
    mem_clustered_unpacked = cluster_mem(mem_unpacked, nomem_unpacked, **cluster_mem_kwargs)
    return _merge(hardcoded_unpacked, _merge(mem_clustered_unpacked, nomem_unpacked))



@beartype
def _unfold_unalias_partition_nodes(
    partitions: list[PartitionData], aliases: dict[_str, _str]
) -> dict[_str, list[_str]]:
    "given partition data and a list of aliases, build a mapping from partition name to node list"
    output = {}
    for partition in partitions:
        output[partition["PartitionName"]] = _unfold_node_set(partition["Nodes"], aliases)
    return output


@beartype
def _get_arch(node_specs: NodeSpecs, valid_arches):
    arch_features = [x for x in node_specs["Features"] if x in valid_arches]
    assert (
        len(arch_features) == 1
    ), f"exactly 1 architecture feature required. {node_specs["Features"]} -> {arch_features}"
    return arch_features[0]


@beartype
def slurm_partitions_group_by_arch(
    partitions: list[PartitionData],
    aliases: dict[_str, _str],
    full_node_specs_unpacked: NodeSpecsUnpacked,
    valid_arches: list[_str],
) -> dict[_str, list[_str]]:
    partition2nodes = _unfold_unalias_partition_nodes(partitions, aliases)
    hostname2arch = {
        hostname: _get_arch(node_specs, valid_arches)
        for hostname, node_specs in full_node_specs_unpacked.items()
    }
    output = {}
    for partition, nodes in partition2nodes.items():
        for hostname in nodes:
            output.setdefault(hostname2arch[hostname], set()).add(partition)
    output = {k: list(v) for k, v in output.items()}
    return output


@beartype
def slurm_mpi_constraints(
    full_node_specs_unpacked: NodeSpecsUnpacked, arch2feature_regex: dict[_str, _str]
) -> dict[_str, _str]:
    "returns a mapping from architecture to constraint"
    valid_arches = list(arch2feature_regex.keys())
    arch2hostnames = {}
    for hostname, node_specs in full_node_specs_unpacked.items():
        arch2hostnames.setdefault(_get_arch(node_specs, valid_arches), []).append(hostname)
    output = {}
    for arch, feature_regex_str in arch2feature_regex.items():
        features = set()
        feature_regex = re.compile(feature_regex_str)
        for hostname in arch2hostnames[arch]:
            this_host_features = full_node_specs_unpacked[hostname]["Features"]
            features.update(set([x for x in this_host_features if feature_regex.match(x)]))
        output[arch] = f"[{"|".join(features)}]"
    return output


class FilterModule:
    def filters(self):
        return dict(
            slurm_node_specs_pack=pack,
            slurm_node_specs_unpack=unpack,
            slurm_build_full_node_specs=build_full_node_specs,
            slurm_node_specs_slim_from_hostvars=slurm_node_specs_slim_from_hostvars,
            slurm_node_specs_mem_from_hostvars=slurm_node_specs_mem_from_hostvars,
            slurm_node_specs_gpu_from_hostvars=slurm_node_specs_gpu_from_hostvars,
            slurm_partitions_group_by_arch=slurm_partitions_group_by_arch,
            slurm_mpi_constraints=slurm_mpi_constraints,
        )

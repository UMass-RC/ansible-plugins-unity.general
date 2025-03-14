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

from ansible.utils.display import Display

display = Display()

NODE_SPEC_ORDER = [
    "NodeName",
    "Features",
    "Boards",
    "SocketsPerBoard",
    "CoresPerSocket",
    "ThreadsPerCore",
]


def _fold_node_set(hostnames: list[str]) -> str:
    """
    ["cpu001", "cpu002", "cpu003", "cpu006"] -> ["cpu[001-003,006]"]
    ["cpu001", "cpu002", "cpu003", "gpu001", "gpu002", "gpu003"] -> "cpu[001-003],gpu[001-003]"
    """
    return str(NodeSet.fromlist(hostnames))


def _get_dict_hash(x) -> str:
    sorted_json_bytes = json.dumps(x, sort_keys=True).encode()
    return hashlib.md5(sorted_json_bytes).hexdigest()


def _group_nodes_equal_specs(node_specs: dict[str, dict]) -> dict[str, dict]:
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
    sorted_keys = _sort_with_priority(_dict.keys(), _priority_keys)
    output = dict()
    for key in sorted_keys:
        output[key] = _dict[key]
    return output


def pack(node_specs: dict[str, dict]) -> list[dict]:
    # sort spec values
    # WARNING: I assume that the order doesn't matter for all specs of type list
    for hostname, specs in node_specs.items():
        for spec_name, spec_value in specs.items():
            if isinstance(spec_value, list):
                if all(isinstance(x, str) for x in spec_value):
                    node_specs[hostname][spec_name] = sorted(
                        node_specs[hostname][spec_name],
                        key=_make_string_sortable_numerically,
                    )
                else:
                    node_specs[hostname][spec_name] = sorted(node_specs[hostname][spec_name])
    # "pack" the specs
    node_specs_packed = []
    for name_list_str, specs in _group_nodes_equal_specs(node_specs).items():
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


def _unpack(node_specs: dict[str, dict]):
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


def _do_removals(node_specs: dict[str, dict]):
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


def unpack(node_specs: list[dict] | list[list[dict]]) -> dict[str, dict]:
    if isinstance(node_specs[0], dict):
        output = _unpack(node_specs)
    else:
        output = _unpack(node_specs[0])
        for specs in node_specs[1:]:
            output = _merge(output, _unpack(specs))
    return _do_removals(output)


def _cluster(sorted_nums_ids: list[tuple[int, str]], max_reduction: int) -> list[tuple[int, str]]:
    "cluster integers by reducing them by no more than max_reduction"
    # if the entire range can be reduced to equal the lowest number without violating max_reduction
    if sorted_nums_ids[-1][0] - sorted_nums_ids[0][0] <= max_reduction:
        new_num = sorted_nums_ids[0][0]
        output = []
        for num, _id in sorted_nums_ids:
            reduction = num - new_num
            if reduction:
                other_ids = [x[1] for x in sorted_nums_ids if x[1] != _id]
                other_ids_folded = _fold_node_set(other_ids)
                display.warning(
                    f"{_id} RealMemory reduced by {reduction} bytes to match {other_ids_folded}"
                )
            output.append((new_num, _id))
        return output
    # divide and conquer. split the range at the biggest gap
    # for each element, the corresponding gap is the distance between it and the previous element
    gaps = []
    for i, (num, _) in enumerate(sorted_nums_ids):
        if i == 0:
            gaps.append(-1)
        else:
            gaps.append(num - sorted_nums_ids[i - 1][0])
    # https://stackoverflow.com/a/11825864/18696276
    biggest_gap_index = max(range(len(gaps)), key=gaps.__getitem__)
    # https://stackoverflow.com/a/1724975/18696276
    return itertools.chain(
        _cluster(sorted_nums_ids[:biggest_gap_index], max_reduction),
        _cluster(sorted_nums_ids[biggest_gap_index:], max_reduction),
    )


def cluster_mem(
    _: object,
    node_specs_mem: dict[str, dict] = None,
    node_specs_nomem: dict[str, dict] = None,
    max_reduction_MB=1000,
):
    output = node_specs_mem.copy()
    for grouping in pack(node_specs_nomem):
        mems_hostnames = sorted(
            [(node_specs_mem[x]["RealMemory"], x) for x in _unfold_node_set(grouping["NodeName"])]
        )
        for mem, hostname in _cluster(mems_hostnames, max_reduction_MB * 1000 * 1000):
            output[hostname]["RealMemory"] = mem
    return output


class FilterModule:
    def filters(self):
        return dict(
            slurm_node_specs_pack=pack,
            slurm_node_specs_unpack=unpack,
            slurm_node_specs_cluster_realmemory=cluster_mem,
        )

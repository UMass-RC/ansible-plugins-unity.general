#!/usr/bin/python

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
name: directory
short_description: remove unexpected items from directory listing
description: ""
options:
  path:
    description: absolute path to directory
    type: str
    required: true
  expected:
    description:
      - list of files and subdirectories that should exist in the directory
      - subdirectories must end with "/"
    type: list
    elements: str
    required: true
author: Simon Leary <simon.leary42@proton.me>
version_added: 2.18.1
"""

EXAMPLES = r"""
- name: remove unwanted files
  directory:
    path: /path/to/directory
    expected:
      - file1.txt
      - file2.txt
      - subdir1/
"""

RETURN = r"""
diff:
    description: directory listing diff, with total file/subdir counts in the headers
    type: dict
    returned: always
    sample:
      before_header: '/path/to/directory: 24 total files, 2 total subdirectories'
      after_header: '/path/to/directory: 5 total files, 1 total subdirectories'
      before: |
        file1.txt
        subdir1/
        subdir2/
      after:
        file1.txt
        subdir2/
"""

import os
import shutil
import traceback
from ansible.module_utils.basic import AnsibleModule


def listdir_classify_subdirs(path: str) -> list[str]:
    "os.listdir but directories get a trailing '/'. see the `--classify` argument for `ls`"
    return [x + ("/" if os.path.isdir(os.path.join(path, x)) else "") for x in os.listdir(path)]


def format_total_file_subdir_counts(path: str) -> str:
    '"{path}: {files} total files, {subdirs} total subdirectories"'
    files = 0
    subdirs = 0
    for _, walk_subdirs, walk_files in os.walk(path):
        subdirs += len(walk_subdirs)
        files += len(walk_files)
    return f"{path}: {files} total files, {subdirs} total subdirectories"


def main():
    module = AnsibleModule(
        argument_spec=dict(
            path=dict(type="str", required=True),
            expected=dict(type="list", elements="str", required=True),
        ),
        supports_check_mode=True,
    )

    path = module.params["path"]
    expected_listing = module.params["expected"]
    result = {"diff": {}}

    if not os.path.exists(path):
        module.fail_json(f"Path {path} does not exist", **result)

    if not os.path.isdir(path):
        module.fail_json(f"Path {path} is not a directory", **result)

    before_listing = listdir_classify_subdirs(path)
    result["diff"]["before"] = "\n".join(sorted(before_listing))
    result["diff"]["before_header"] = format_total_file_subdir_counts(path)

    if expected_not_found := set(expected_listing) - set(before_listing):
        msg = " ".join(
            [
                f"the following items were expected but not found: {expected_not_found}.",
                f"directory: '{path}'."
                f"all items found: '{before_listing}'."
                "this module only deletes, it doesn't create.",
            ]
        )
        if module.check_mode:
            msg += " outside check mode this error is fatal."
            module.warn(msg)
        else:
            module.fail_json(msg, **result)

    to_remove = set(before_listing) - set(expected_listing)
    result["changed"] = len(to_remove) > 0

    if module.check_mode:
        result["diff"]["after"] = "\n".join(sorted(expected_listing))
    else:
        # remove items one at a time from diff so that diff is accurate even if error occurs
        # during middle of loop
        result["diff"]["after"] = "\n".join(sorted(before_listing))
        for item in to_remove:
            full_path = os.path.join(path, item.rstrip("/"))
            try:
                if os.path.isfile(full_path):
                    os.remove(full_path)
                elif os.path.isdir(full_path):
                    shutil.rmtree(full_path)
                # this is ugly but ansible won't take accept a list of strings
                new_diff_after = result["diff"]["after"].splitlines()
                new_diff_after.remove(item)
                result["diff"]["after"] = "\n".join(new_diff_after)
            except OSError:
                module.fail_json(
                    f"failed to remove {full_path}: {traceback.format_exc()}", **result
                )
        after_listing = listdir_classify_subdirs(path)
        result["diff"]["after_header"] = format_total_file_subdir_counts(path)
        # double check
        if set(after_listing) != set(expected_listing):
            module.fail_json(
                " ".join(
                    [
                        "directory listing after deletions does not match the expected listing!",
                        "was some other process also modifying this directory?",
                        f"expected listing: '{expected_listing}'",
                        f"current listing: '{after_listing}'",
                    ]
                ),
                **result,
            )

    module.exit_json(**result)


if __name__ == "__main__":
    main()

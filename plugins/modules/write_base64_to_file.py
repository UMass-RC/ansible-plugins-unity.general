#!/usr/bin/python

"""
writes bytes to file, and also sets owner/group/permissions. owner/group/permissions are required.
"""

import os
import re
import pwd
import grp
import stat
import base64
import hashlib
import binascii
import tempfile

from typing import List

from ansible.module_utils.basic import AnsibleModule


def examine_file(path: str) -> dict:

    def human_readable_size(st_size) -> str:
        if st_size < 1024:
            return f"{st_size} bytes"
        current_size = st_size
        for suffix in ["KiB", "MiB", "GiB", "TiB", "PiB"]:
            current_size = current_size / 1024
            if current_size < 1024:
                return f"{current_size:.2f} {suffix}"
        return f"{current_size:.2f} {suffix}"

    def human_readable_file_type(st_mode) -> str:
        func2file_type = {
            stat.S_ISREG: "regular file",
            stat.S_ISDIR: "directory",
            stat.S_ISCHR: "character device",
            stat.S_ISBLK: "block device",
            stat.S_ISFIFO: "FIFO/pipe",
            stat.S_ISLNK: "symlink",
            stat.S_ISSOCK: "socket",
        }
        for func, file_type in func2file_type.items():
            if func(st_mode):
                return file_type
        return "unknown"

    def _human_readable_stat(path: str) -> dict:
        path_stat = os.stat(path, follow_symlinks=False)
        return {
            "path": path,
            "owner": pwd.getpwuid(path_stat.st_uid).pw_name,
            "group": grp.getgrgid(path_stat.st_gid).gr_name,
            "file_type": human_readable_file_type(path_stat.st_mode),
            "mode": stat.filemode(path_stat.st_mode),
            "size": human_readable_size(path_stat.st_size),
        }

    def get_symlink_destination_absolute(symlink_path: str) -> str:
        destination_path = os.readlink(symlink_path)
        if not os.path.isabs(destination_path):
            # "/a/b/c" -> "../d" = "a/b/c/../d"
            return os.path.abspath(os.path.join(os.path.dirname(symlink_path), destination_path))
        return destination_path

    def human_readable_stat(path) -> List[dict]:
        """
        Return a list of human-readable stat dictionaries. If the path is a symlink,
        append another dict to the list using the destination of that symlink, and so on.
        """
        path = os.path.abspath(path)
        output = [_human_readable_stat(path)]
        seen_paths = [path]  # To handle cyclic symlinks
        while output[-1]["file_type"] == "symlink":
            path = get_symlink_destination_absolute(path)
            if path in seen_paths:
                raise RecursionError(f"Cyclic symlinks detected: {seen_paths + [path]}")
            output.append(_human_readable_stat(path))
        return output

    output = {}
    try:
        output["stat"] = human_readable_stat(path)
        output["state"] = "present"
        if output["stat"][-1]["file_type"] == "regular file":  # follow symlinks
            try:
                with open(path, "r", encoding="utf8") as fp:
                    output["content"] = fp.read()
            except UnicodeDecodeError:
                with open(path, "rb") as fp:
                    output["content"] = (
                        f"content ommitted, binary file. sha1sum: {hashlib.sha1(fp.read()).hexdigest()}"
                    )
        elif output["stat"][-1]["file_type"] == "directory":  # follow symlinks
            output["content"] = os.listdir(path)
        else:
            output["content"] = "content ommitted, special file."
    except FileNotFoundError:
        output = {"state": "absent", "stat": None, "content": None}
    return output


def minimize_examination(examination: dict) -> dict:
    if examination["state"] == "absent":
        return examination
    return {
        "state": examination["state"],
        "content": examination["content"],
        "stat": [
            {
                "owner": examination["stat"][-1]["owner"],
                "group": examination["stat"][-1]["group"],
                "mode": examination["stat"][-1]["mode"],
            }
        ],
    }


def format_diffs(examination_before: dict, examination_after: dict) -> list:
    output = []
    # automatic content comparison diffs by ansible need it to be this way
    if "content" in examination_before and "content" in examination_after:
        output.append(
            {"before": examination_before["content"], "after": examination_after["content"]}
        )
        del examination_before["content"]
        del examination_after["content"]
    output.append({"before": examination_before, "after": examination_after})
    return output


def main():
    module_args = dict(
        content=dict(type="str", required=True),
        dest=dict(type="str", required=True),
        owner=dict(type="str", required=True),
        group=dict(type="str", required=True),
        mode=dict(type="str", required=True),
    )
    module = AnsibleModule(argument_spec=module_args, supports_check_mode=True)
    content = module.params["content"]
    dest = module.params["dest"]
    owner = module.params["owner"]
    group = module.params["group"]
    mode = module.params["mode"]
    result = {}
    if os.path.exists(dest) and not os.path.isfile(dest):
        module.exit_json(failed=True, msg="destination already exists but is not a file!")
    if not isinstance(mode, str):
        module.exit_json(failed=True, msg='mode must be a string! example: "0755"')
    if not re.fullmatch(r"0[0-7]{3}", mode):
        module.exit_json(failed=True, msg='mode is not valid! example: "0755"')
    try:
        owner_uid = pwd.getpwnam(owner).pw_uid
    except KeyError:
        module.exit_json(failed=True, msg=f'no such user: "{owner}"')
    try:
        group_gid = grp.getgrnam(group).gr_gid
    except KeyError:
        module.exit_json(failed=True, msg=f'no such group: "{group}"')
    try:
        content_bytes = base64.b64decode(content)
    except binascii.Error:
        module.exit_json(failed=True, msg="content is not valid base64!")

    examination_before = examine_file(dest)

    tmp_fd, tmp_path = tempfile.mkstemp(dir=module.tmpdir)
    os.chmod(tmp_path, 0o600)
    os.write(tmp_fd, content_bytes)
    os.close(tmp_fd)
    os.chown(tmp_path, uid=owner_uid, gid=group_gid)
    os.chmod(tmp_path, int(mode, 8))

    examination_before_min = minimize_examination(examination_before)
    examination_tmp_min = minimize_examination(examine_file(tmp_path))
    if examination_before_min != examination_tmp_min:
        result["changed"] = True
        if module.check_mode:
            result["diff"] = format_diffs(examination_before_min, examination_tmp_min)
            os.remove(tmp_path)
        else:
            module.atomic_move(tmp_path, dest, keep_dest_attrs=False)
            examination_after = examine_file(dest)
            result["diff"] = format_diffs(examination_before, examination_after)
    module.exit_json(**result)


if __name__ == "__main__":
    main()

#!/usr/bin/python
import re
from ansible.module_utils.basic import AnsibleModule

DOCUMENTATION = """
  name: bitwarden_copy_attachment
  author: Simon Leary <simon.leary42@proton.me>
  requirements:
    - bw (command line utility)
    - be logged into bitwarden
    - bitwarden vault unlocked
    - E(BW_SESSION) environment variable set
    - P(community.general.bitwarden#lookup)
  short_description: copies an attachment from bitwarden to remote host
  version_added: 2.18.1
  description:
    - Just a bit of plumbing between `bitwarden_attachment_download` and `copy`.
    - Due to the sensitive nature of this content, owner/group/mode are required, and
    - no_log is always enabled.
    - All options other than item_name and attachment_filename are forwarded to `copy`.
  options:
    item_name:
      description: bitwarden item name
      type: str
      required: true
    attachment_filename:
      description: see the unity.general.bitwarden lookup plugin for more information
      type: str
      required: true
    collection_id:
      description: see the unity.general.bitwarden lookup plugin for more information
      type: str
    enable_logging:
      description: _ansible_no_log will be added to the result unless this is True
      type: bool
      default: false
    owner:
      description: see the copy module for more information
      type: str
      required: true
    group:
      description: see the copy module for more information
      type: str
      required: true
    mode:
      description: |
        Must be a string representation of a posix permission bitmask in octal digits.
        No special permissions allowed, only ---rwxrwxrwx.
        Example: "0755".
        This is much stricter than the copy module.
      type: str
      required: true
  notes: []
  seealso:
    - plugin: community.general.bitwarden
      plugin_type: lookup
    - plugin: unity.general.bitwarden
      plugin_type: lookup
  extends_documentation_fragment:
    - unity.general.ramdisk_cache
"""


if __name__ == "__main__":
    """
    this is a stub module meant only to validate parameters and set default values
    """
    module_args = dict(
        item_name=dict(type="str", required=True),
        attachment_filename=dict(type="str", required=True),
        collection_id=dict(type="str", required=False),
        enable_logging=dict(type="bool", required=False, default=False),
        dest=dict(type="str", required=True),
        owner=dict(type="str", required=True),
        group=dict(type="str", required=True),
        mode=dict(type="str", required=True),
    )
    module = AnsibleModule(argument_spec=module_args, supports_check_mode=True)
    if not isinstance(module.params["mode"], str):
        module.exit_json(failed=True, msg='mode must be a string! example: "0755"')
    if not re.fullmatch(r"0[0-7]{3}", module.params["mode"]):
        module.exit_json(failed=True, msg='mode is not valid! example: "0755"')
    module.exit_json(failed=False, params=module.params)

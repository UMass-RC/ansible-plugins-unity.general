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
      desctiption: bitwarden item name
      type: str
      required: true
    attachment_filename:
      description: see the unity.general.bitwarden lookup plugin for more information
      type: str
      required: true
    attachment_filename:
      description: see the unity.general.bitwarden lookup plugin for more information
      type: str
      required: true
    collection_id:
      description: see the unity.general.bitwarden lookup plugin for more information
      type: str
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

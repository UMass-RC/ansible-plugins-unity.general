# nested extend not working - don't forget to also extend from the ramdisk_cache fragment!
class ModuleDocFragment(object):
    DOCUMENTATION = r"""
      requirements:
        - diff command
        - diffr command (optional)
      options:
        diff_redact_bitwarden:
          description: check bitwarden cache file for secrets and remove them from diff if they exist
          type: bool
          default: false
          ini:
            - section: diff
              key: redact_bitwarden
          env:
            - name: DIFF_REDACT_BITWARDEN
    """

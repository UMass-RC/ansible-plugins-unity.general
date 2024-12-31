class ModuleDocFragment(object):
    DOCUMENTATION = r"""
      options:
        diff_formatter:
          description: |
            Pipe the normal diff through this shell command for better formatting.
            Iff the value is exactly "NONE", then formatting will be skipped.
            When diff_redact_bitwarden is enabled, all color must be stripped from the normal diff,
            so if you opt in to diff_redact_bitwarden and you opt out of diff_formatter, then
            your diff will be monochrome.
          type: str
          default: NONE
          ini:
            - section: diff
              key: formatter
          env:
            - name: DIFF_FORMATTER
    """

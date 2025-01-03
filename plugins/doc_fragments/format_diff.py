class ModuleDocFragment(object):
    DOCUMENTATION = r"""
      options:
        diff_formatter:
          description: |
            Pipe the normal diff through this shell command for better formatting.
            Iff the value is exactly "NONE", then formatting will be skipped.
          type: str
          default: NONE
          ini:
            - section: diff
              key: formatter
          env:
            - name: DIFF_FORMATTER
    """

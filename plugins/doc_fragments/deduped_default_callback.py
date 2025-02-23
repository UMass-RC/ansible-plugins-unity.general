class ModuleDocFragment(object):
    DOCUMENTATION = r"""
      options:
        check_mode_markers:
          description: see default callback documentation
          default: true
        result_format:
          description: see default callback documentation
          default: yaml
        pretty_results:
          description: see default callback documentation
          default: true
        display_ok_hosts:
          description: see default callback documentation
          default: false
        display_skipped_hosts:
          description: see default callback documentation
          default: false
        wrap_text:
          description: |
            whether or not to hard-wrap text in callback output.
            this helps the readability of the left margin of the output, but makes the right margin
            awkward and makes the output less useful to copy/paste.
          type: bool
          default: true
          ini:
            - section: defaults
              key: wrap_text
          env:
            - name: ANSIBLE_WRAP_TEXT
    """

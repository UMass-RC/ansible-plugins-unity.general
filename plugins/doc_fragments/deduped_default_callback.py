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
        display_skipped_hosts:
          description: see default callback documentation
        wrap_text:
          description: |
            whether or not to hard-wrap text in callback output.
            this helps the readability of the left margin of the output, but makes the right margin
            awkward and makes the output less useful to copy/paste.
          type: bool
          default: true
          ini:
            - section: callback
              key: wrap_text
          env:
            - name: ANSIBLE_CALLBACK_WRAP_TEXT
        display_messages:
          description: whether to print result messages at end of task
          type: bool
          default: true
          ini:
            - section: callback
              key: display_messages
          env:
            - name: ANSIBLE_CALLBACK_DISPLAY_MESSAGES
    """

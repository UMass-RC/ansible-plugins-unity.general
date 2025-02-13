class ModuleDocFragment(object):
    DOCUMENTATION = r"""
      options:
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
            - name: CALLBACK_WRAP_TEXT
    """

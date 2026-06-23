import os
import sys
import re
from ansible.plugins.callback import CallbackBase

DOCUMENTATION = r"""
  name: warn_ignore_down
  type: notification
  short_description: Warn that nodes slurm thinks are down will get skipped
  version_added: 1.0
  description: |
    * Normally, when you run this playbook, unless you have provision=true set, down nodes will be skipped
    * This warns out about that and tells you how to fix it without using provision mode
"""

class CallbackModule(CallbackBase):
    CALLBACK_VERSION = 2.0
    CALLBACK_TYPE = 'notification'
    CALLBACK_NAME = 'warn_ignore_down'
    CALLBACK_NEEDS_WHITELIST = False

    TARGET_SUBSTRINGS = ('compute-nodes',)

    def _find_playbook_basename(self, playbook):
        try:
            fn = getattr(playbook, '_file_name', None) or getattr(playbook, 'filename', None)
            if fn:
                return os.path.basename(fn)
        except Exception:
            pass
        for arg in sys.argv[1:]:
            if arg.endswith('.yml') or arg.endswith('.yaml'):
                return os.path.basename(arg)
        return None

    def _extra_var_present_in_argv(self, argv):
        patterns = [
            r'\binclude_down\s*[:=]\s*true\b',
            r'\bprovision\s*[:=]\s*true\b',
            r'"include_down"\s*:\s*true',
            r'"provision"\s*:\s*true',
            r"'include_down'\s*:\s*true",
            r"'provision'\s*:\s*true",
        ]
        for p in patterns:
            if re.search(p, argv, re.I):
                return True
        return False

    def _maybe_emit_warning(self, playbook):
        try:
            pbname = self._find_playbook_basename(playbook)
            if not pbname:
                return
            if not any(sub in pbname for sub in self.TARGET_SUBSTRINGS):
                return
            argv = ' '.join(sys.argv)
            if self._extra_var_present_in_argv(argv):
                return
            self._display.warning(
                "Currently ignoring nodes the facts cache says slurm says are not responding. "
                "To change this behavior, set either provision or include_down to true"
            )
        except Exception as e:
            try:
                self._display.error("warn_ignore_down plugin internal error: %s" % (e,))
            except Exception:
                pass

    def v2_playbook_on_start(self, playbook):
        # primary hook: emit the warning when appropriate
        try:
            self._maybe_emit_warning(playbook)
        except Exception:
            # never raise from callback handlers
            pass

    def v2_playbook_on_play_start(self, play):
        # defensive no-op to avoid exceptions during play start
        try:
            return
        except Exception:
            try:
                self._display.error("warn_ignore_down plugin play_start error")
            except Exception:
                pass

    def v2_playbook_on_stats(self, stats):
        # defensive no-op
        try:
            return
        except Exception:
            pass

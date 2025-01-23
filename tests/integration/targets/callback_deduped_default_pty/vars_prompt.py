import os
import sys
import shlex
import pexpect
import tempfile

from ansible.module_utils.six import PY2

PROMPT = "input:"
RESPONSE = "foobar"

with tempfile.NamedTemporaryFile(delete=False) as stderr_file:
    stderr_filename = stderr_file.name
bash_cmd = shlex.join(["ansible-playbook", "vars_prompt.yml"] + sys.argv[1:])
cmd = shlex.join(["bash", "-c", bash_cmd]) + f" 2> '{stderr_filename}'"
print(cmd, file=sys.stderr)
child = pexpect.spawn(cmd, timeout=10)
child.logfile = sys.stdout if PY2 else sys.stdout.buffer  # duplicate output to my stdout
try:
    child.expect(PROMPT)
except pexpect.ExceptionPexpect as e:
    raise Exception(f'prompt not found! given: "{child.before}"') from e
child.send(RESPONSE)
child.send("\r")
child.expect(pexpect.EOF)
child.close()
assert child.exitstatus == 0
with open(stderr_filename, "r", encoding="utf8") as stderr_file:
    stderr_content = stderr_file.read()
os.remove(stderr_filename)
assert stderr_content == "", f"non empty stderr:\n{stderr_content}"

#!/bin/bash
playbook_path="$1"
tempfile="$(mktemp)" || exit 2
ansible-playbook "$playbook_path" &>"$tempfile" || exit 2
grep -qi "failure using method .* in callback plugin" "$tempfile" && exit 1

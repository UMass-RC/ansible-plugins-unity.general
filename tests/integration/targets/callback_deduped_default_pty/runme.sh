#!/bin/bash
set -eu
export ANSIBLE_DIFF_ALWAYS=1
export ANSIBLE_STDOUT_CALLBACK="unity.general.deduped_default"
python vars_prompt.py -i ./inventory.yml "$@"

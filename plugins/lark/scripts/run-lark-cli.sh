#!/bin/sh
set -eu
SCRIPT_DIRECTORY="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
exec sh "${SCRIPT_DIRECTORY}/run-python.sh" "${SCRIPT_DIRECTORY}/lark_cli.py"  "$@"

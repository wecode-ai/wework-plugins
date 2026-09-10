#!/bin/sh
set -eu
WECOM_SCRIPT_DIRECTORY="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
exec /bin/sh "${WECOM_SCRIPT_DIRECTORY}/run-python.sh" "${WECOM_SCRIPT_DIRECTORY}/wecom_cli.py" "$@"

#!/bin/sh

set -eu

if [ "$#" -lt 1 ]; then
    echo "Usage: sh run-python.sh <script.py> [arguments...]" >&2
    exit 2
fi

DWS_SCRIPT_DIRECTORY="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
DWS_EXECUTABLE="$(/bin/sh "${DWS_SCRIPT_DIRECTORY}/install-dws.sh")"
DWS_BIN_DIRECTORY="$(dirname -- "${DWS_EXECUTABLE}")"

if command -v python3 >/dev/null 2>&1; then
    DWS_PYTHON="$(command -v python3)"
elif command -v python >/dev/null 2>&1; then
    DWS_PYTHON="$(command -v python)"
else
    echo "Python 3 is required to run this DWS helper script." >&2
    exit 3
fi

PATH="${DWS_BIN_DIRECTORY}:${PATH}" exec "${DWS_PYTHON}" "$@"


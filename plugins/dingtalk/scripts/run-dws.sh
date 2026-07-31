#!/bin/sh

set -eu

DWS_SCRIPT_DIRECTORY="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
DWS_EXECUTABLE="$(/bin/sh "${DWS_SCRIPT_DIRECTORY}/install-dws.sh")"
exec "${DWS_EXECUTABLE}" "$@"


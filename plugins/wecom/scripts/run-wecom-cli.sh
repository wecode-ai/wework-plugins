#!/bin/sh

set -eu

WECOM_SCRIPT_DIRECTORY="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
WECOM_EXECUTABLE="$(/bin/sh "${WECOM_SCRIPT_DIRECTORY}/install-wecom-cli.sh")"

unset WECOM_ACCESS_TOKEN WECOM_BOT_ID WECOM_SECRET
exec "${WECOM_EXECUTABLE}" "$@"


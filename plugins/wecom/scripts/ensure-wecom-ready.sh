#!/bin/sh

set -eu

WECOM_SCRIPT_DIRECTORY="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
WECOM_EXECUTABLE="$(/bin/sh "${WECOM_SCRIPT_DIRECTORY}/install-wecom-cli.sh")"

unset WECOM_ACCESS_TOKEN WECOM_BOT_ID WECOM_SECRET

if ! "${WECOM_EXECUTABLE}" contact --help >/dev/null 2>&1; then
    echo "WeCom CLI has no usable local robot configuration. Opening QR authorization..." >&2
    "${WECOM_EXECUTABLE}" init --noninteractive
fi

if ! "${WECOM_EXECUTABLE}" contact --help >/dev/null 2>&1; then
    echo "WeCom QR authorization did not produce a usable local configuration." >&2
    exit 20
fi

echo "WeCom CLI is installed and locally configured."


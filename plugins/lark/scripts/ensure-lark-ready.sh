#!/bin/sh

set -eu

LARK_SCRIPT_DIRECTORY="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
LARK_EXECUTABLE="$(/bin/sh "${LARK_SCRIPT_DIRECTORY}/install-lark-cli.sh")"

unset LARKSUITE_CLI_USER_ACCESS_TOKEN LARKSUITE_CLI_BRAND LARKSUITE_CLI_APP_ID
export LARKSUITE_CLI_NO_UPDATE_NOTIFIER=1
export LARKSUITE_CLI_NO_SKILLS_NOTIFIER=1

has_local_user_auth() {
    LARK_AUTH_STATUS="$("${LARK_EXECUTABLE}" auth status --json 2>/dev/null)" ||
        return 1
    printf '%s\n' "${LARK_AUTH_STATUS}" |
        grep -E '"identity"[[:space:]]*:[[:space:]]*"user"' >/dev/null
}

if ! "${LARK_EXECUTABLE}" config show >/dev/null 2>&1; then
    echo "Lark CLI is not configured. Complete the Feishu QR setup shown below." >&2
    "${LARK_EXECUTABLE}" config init --new --brand feishu --lang zh
fi

if ! has_local_user_auth; then
    echo "Lark CLI has no local user authorization. Complete the browser authorization below." >&2
    "${LARK_EXECUTABLE}" auth login --recommend
fi

if ! has_local_user_auth; then
    echo "Lark authorization did not produce a usable local user identity." >&2
    exit 20
fi

echo "Lark CLI is installed, configured and locally authorized."

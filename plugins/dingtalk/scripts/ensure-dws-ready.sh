#!/bin/sh

set -eu

DWS_SCRIPT_DIRECTORY="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
DWS_EXECUTABLE="$(/bin/sh "${DWS_SCRIPT_DIRECTORY}/install-dws.sh")"

auth_status_is_authenticated() {
    DWS_AUTH_STATUS="$("${DWS_EXECUTABLE}" auth status --format json 2>/dev/null || true)"
    printf '%s\n' "${DWS_AUTH_STATUS}" |
        grep -E '"authenticated"[[:space:]]*:[[:space:]]*true' >/dev/null 2>&1
}

auth_probe_result() {
    DWS_AUTH_PROBE_OUTPUT="$("${DWS_EXECUTABLE}" contact user get-self --format json 2>&1)" &&
        return 0
    if printf '%s\n' "${DWS_AUTH_PROBE_OUTPUT}" |
        grep -E 'not_authenticated|AUTH_TOKEN_EXPIRED|USER_TOKEN_ILLEGAL|未登录|Token验证失败' >/dev/null 2>&1; then
        return 1
    fi
    return 2
}

DWS_LOGIN_REQUIRED=false
if ! auth_status_is_authenticated; then
    DWS_LOGIN_REQUIRED=true
else
    if auth_probe_result; then
        DWS_INITIAL_PROBE_STATUS=0
    else
        DWS_INITIAL_PROBE_STATUS=$?
    fi
    if [ "${DWS_INITIAL_PROBE_STATUS}" -eq 1 ]; then
        DWS_LOGIN_REQUIRED=true
    elif [ "${DWS_INITIAL_PROBE_STATUS}" -eq 2 ]; then
        echo "DWS reports a local login; the read-only auth probe was unavailable for a non-authentication reason." >&2
    fi
fi

if [ "${DWS_LOGIN_REQUIRED}" = true ]; then
    echo "DWS is not authenticated. Opening DingTalk browser authorization..." >&2
    # Complete local OAuth login without blocking on operation-specific PAT
    # permissions, which DWS handles when a command requires them.
    "${DWS_EXECUTABLE}" auth login --format json
fi

if ! auth_status_is_authenticated; then
    echo "DWS authorization did not produce a usable local login." >&2
    exit 20
fi

if auth_probe_result; then
    DWS_FINAL_PROBE_STATUS=0
else
    DWS_FINAL_PROBE_STATUS=$?
fi
if [ "${DWS_FINAL_PROBE_STATUS}" -eq 1 ]; then
    echo "DWS authorization completed, but the local token is still rejected." >&2
    exit 21
fi

echo "DWS is installed and authenticated."

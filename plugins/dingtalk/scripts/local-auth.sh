#!/bin/sh

set -eu

ACTION="${1:-health}"
DWS_EXECUTABLE="${WEGENT_LOCAL_AUTH_TOOL:-}"

json_status() {
    printf '{"status":"%s","hint":"%s"}\n' "$1" "$2"
}

if [ -z "${DWS_EXECUTABLE}" ] || [ ! -x "${DWS_EXECUTABLE}" ]; then
    json_status error "The bundled DWS CLI is unavailable."
    exit 0
fi

is_authenticated() {
    AUTH_STATUS="$("${DWS_EXECUTABLE}" auth status --format json 2>/dev/null || true)"
    printf '%s\n' "${AUTH_STATUS}" |
        grep -E '"authenticated"[[:space:]]*:[[:space:]]*true' >/dev/null 2>&1
}

login() {
    # Installation authentication ends with the OAuth callback. Recommended
    # PAT permissions are operation-specific and must not keep this dialog open.
    "${DWS_EXECUTABLE}" auth login --format json >/dev/null 2>&1
}

wait_for_authentication() {
    ATTEMPT=1
    while [ "${ATTEMPT}" -le 10 ]; do
        if is_authenticated; then
            return 0
        fi
        if [ "${ATTEMPT}" -lt 10 ]; then
            sleep 0.5
        fi
        ATTEMPT=$((ATTEMPT + 1))
    done
    return 1
}

case "${ACTION}" in
    health)
        if is_authenticated; then
            json_status ok "DingTalk authorization is ready."
        else
            json_status need_login "DingTalk authorization is required."
        fi
        ;;
    login)
        if is_authenticated; then
            json_status ok "DingTalk authorization is ready."
            exit 0
        fi
        if login; then
            LOGIN_EXIT_CODE=0
        else
            LOGIN_EXIT_CODE=$?
        fi
        # Persisted authentication is authoritative because native process
        # errors can race with an otherwise successful browser callback.
        if wait_for_authentication; then
            json_status ok "DingTalk authorization is ready."
        elif [ "${LOGIN_EXIT_CODE}" -ne 0 ]; then
            json_status error "DingTalk OAuth command failed (exit code ${LOGIN_EXIT_CODE})."
        else
            json_status error "DingTalk OAuth callback completed, but DWS did not persist an authenticated login."
        fi
        ;;
    logout)
        if ! is_authenticated; then
            json_status ok "DingTalk is already logged out."
            exit 0
        fi
        if "${DWS_EXECUTABLE}" auth logout --yes --format json >/dev/null 2>&1; then
            json_status ok "DingTalk login was removed."
        else
            json_status error "Unable to remove the DingTalk login."
        fi
        ;;
    *)
        json_status error "Unsupported local authorization action."
        ;;
esac

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

grant_recommended_permissions() {
    "${DWS_EXECUTABLE}" pat chmod --recommend --yes --format json >/dev/null 2>&1
}

login_with_recommended_permissions() {
    # JSON mode returns PAT_BATCH_AUTH_PENDING to the host immediately. Table
    # mode keeps the DWS-owned browser flow running until the user approves,
    # rejects, or the authorization expires.
    "${DWS_EXECUTABLE}" auth login --recommend --format table >/dev/null 2>&1
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
            if grant_recommended_permissions; then
                json_status ok "DingTalk authorization is ready."
            else
                json_status error "DingTalk recommended permission authorization did not complete."
            fi
            exit 0
        fi
        if login_with_recommended_permissions &&
            is_authenticated; then
            json_status ok "DingTalk authorization is ready."
        else
            json_status error "DingTalk browser authorization did not complete."
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

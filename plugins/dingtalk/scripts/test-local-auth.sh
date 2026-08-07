#!/bin/sh

set -eu

has_arg() {
    EXPECTED_ARG="$1"
    shift
    for ARG in "$@"; do
        if [ "${ARG}" = "${EXPECTED_ARG}" ]; then
            return 0
        fi
    done
    return 1
}

if [ "${1:-}" = "auth" ]; then
    case "${2:-}" in
        status)
            if [ -f "${DWS_MOCK_STATE_DIR}/authenticated" ]; then
                printf '{"authenticated":true}\n'
            else
                printf '{"authenticated":false}\n'
            fi
            ;;
        login)
            shift 2
            printf 'auth login %s\n' "$*" >>"${DWS_MOCK_STATE_DIR}/calls"
            : >"${DWS_MOCK_STATE_DIR}/authenticated"
            if has_arg --recommend "$@" && has_arg --yes "$@"; then
                : >"${DWS_MOCK_STATE_DIR}/recommended"
                printf '{"success":true}\n'
                exit 0
            fi
            printf '{"success":false,"code":"PAT_BATCH_AUTH_PENDING"}\n' >&2
            exit 4
            ;;
    esac
    exit 0
fi

if [ "${1:-}" = "pat" ] && [ "${2:-}" = "chmod" ]; then
    shift 2
    printf 'pat chmod %s\n' "$*" >>"${DWS_MOCK_STATE_DIR}/calls"
    if [ "${DWS_MOCK_FAIL_GRANT:-0}" = "1" ]; then
        exit 4
    fi
    if has_arg --recommend "$@" && has_arg --yes "$@"; then
        : >"${DWS_MOCK_STATE_DIR}/recommended"
        printf '{"success":true}\n'
        exit 0
    fi
    exit 4
fi

SCRIPT_DIRECTORY="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
AUTH_SCRIPT="${SCRIPT_DIRECTORY}/local-auth.sh"
TEST_ROOT="$(mktemp -d)"
trap 'rm -rf -- "${TEST_ROOT}"' EXIT HUP INT TERM

assert_contains() {
    VALUE="$1"
    EXPECTED="$2"
    if ! printf '%s\n' "${VALUE}" | grep -F "${EXPECTED}" >/dev/null; then
        printf 'Expected output to contain %s, got: %s\n' "${EXPECTED}" "${VALUE}" >&2
        exit 1
    fi
}

FRESH_STATE="${TEST_ROOT}/fresh"
mkdir -p "${FRESH_STATE}"
FRESH_OUTPUT="$(
    DWS_MOCK_STATE_DIR="${FRESH_STATE}" \
        WEGENT_LOCAL_AUTH_TOOL="$0" \
        sh "${AUTH_SCRIPT}" login
)"
assert_contains "${FRESH_OUTPUT}" '"status":"ok"'
assert_contains "$(cat "${FRESH_STATE}/calls")" 'auth login --recommend --yes --format json'
test -f "${FRESH_STATE}/recommended"

RETRY_STATE="${TEST_ROOT}/retry"
mkdir -p "${RETRY_STATE}"
: >"${RETRY_STATE}/authenticated"
RETRY_OUTPUT="$(
    DWS_MOCK_STATE_DIR="${RETRY_STATE}" \
        WEGENT_LOCAL_AUTH_TOOL="$0" \
        sh "${AUTH_SCRIPT}" login
)"
assert_contains "${RETRY_OUTPUT}" '"status":"ok"'
assert_contains "$(cat "${RETRY_STATE}/calls")" 'pat chmod --recommend --yes --format json'
test -f "${RETRY_STATE}/recommended"

FAILED_STATE="${TEST_ROOT}/failed"
mkdir -p "${FAILED_STATE}"
: >"${FAILED_STATE}/authenticated"
FAILED_OUTPUT="$(
    DWS_MOCK_FAIL_GRANT=1 \
        DWS_MOCK_STATE_DIR="${FAILED_STATE}" \
        WEGENT_LOCAL_AUTH_TOOL="$0" \
        sh "${AUTH_SCRIPT}" login
)"
assert_contains "${FAILED_OUTPUT}" '"status":"error"'
assert_contains "${FAILED_OUTPUT}" 'recommended permission authorization did not complete'

printf 'DingTalk local authorization tests passed.\n'

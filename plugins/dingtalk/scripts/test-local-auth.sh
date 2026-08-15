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

if [ "${1:-}" = "version" ]; then
    printf 'dws version 1.0.54\n'
    exit 0
fi

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
            printf 'Opening DingTalk browser authorization...\n' >&2
            if [ "${DWS_MOCK_LOGIN_FAIL:-0}" = "1" ]; then
                exit 4
            fi
            if has_arg --format "$@" &&
                has_arg json "$@" &&
                ! has_arg --recommend "$@"; then
                : >"${DWS_MOCK_STATE_DIR}/authenticated"
                printf '{"success":true}\n'
                if [ "${DWS_MOCK_LOGIN_EXIT_AFTER_AUTH:-0}" = "1" ]; then
                    exit 4
                fi
                exit 0
            fi
            exit 4
            ;;
    esac
    exit 0
fi

if [ "${1:-}" = "contact" ] &&
    [ "${2:-}" = "user" ] &&
    [ "${3:-}" = "get-self" ]; then
    if [ -f "${DWS_MOCK_STATE_DIR}/authenticated" ]; then
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
    if ! printf '%s\n' "${VALUE}" | grep -F -- "${EXPECTED}" >/dev/null; then
        printf 'Expected output to contain %s, got: %s\n' "${EXPECTED}" "${VALUE}" >&2
        exit 1
    fi
}

assert_not_contains() {
    VALUE="$1"
    UNEXPECTED="$2"
    if printf '%s\n' "${VALUE}" | grep -F -- "${UNEXPECTED}" >/dev/null; then
        printf 'Expected output not to contain %s, got: %s\n' "${UNEXPECTED}" "${VALUE}" >&2
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
assert_contains "$(cat "${FRESH_STATE}/calls")" 'auth login --format json'
assert_not_contains "$(cat "${FRESH_STATE}/calls")" '--recommend'
test -f "${FRESH_STATE}/authenticated"

NONZERO_AUTH_STATE="${TEST_ROOT}/nonzero-auth"
mkdir -p "${NONZERO_AUTH_STATE}"
NONZERO_AUTH_OUTPUT="$(
    DWS_MOCK_LOGIN_EXIT_AFTER_AUTH=1 \
        DWS_MOCK_STATE_DIR="${NONZERO_AUTH_STATE}" \
        WEGENT_LOCAL_AUTH_TOOL="$0" \
        sh "${AUTH_SCRIPT}" login
)"
assert_contains "${NONZERO_AUTH_OUTPUT}" '"status":"ok"'
test -f "${NONZERO_AUTH_STATE}/authenticated"

REJECTED_STATE="${TEST_ROOT}/rejected"
mkdir -p "${REJECTED_STATE}"
REJECTED_OUTPUT="$(
    DWS_MOCK_LOGIN_FAIL=1 \
        DWS_MOCK_STATE_DIR="${REJECTED_STATE}" \
        WEGENT_LOCAL_AUTH_TOOL="$0" \
        sh "${AUTH_SCRIPT}" login
)"
assert_contains "${REJECTED_OUTPUT}" '"status":"error"'
assert_contains "${REJECTED_OUTPUT}" 'OAuth command failed (exit code 4)'
test ! -f "${REJECTED_STATE}/authenticated"

RETRY_STATE="${TEST_ROOT}/retry"
mkdir -p "${RETRY_STATE}"
: >"${RETRY_STATE}/authenticated"
RETRY_OUTPUT="$(
    DWS_MOCK_STATE_DIR="${RETRY_STATE}" \
        WEGENT_LOCAL_AUTH_TOOL="$0" \
        sh "${AUTH_SCRIPT}" login
)"
assert_contains "${RETRY_OUTPUT}" '"status":"ok"'
test ! -f "${RETRY_STATE}/calls"

READY_STATE="${TEST_ROOT}/ready"
mkdir -p "${READY_STATE}"
READY_OUTPUT="$(
    DWS_BINARY_PATH="$0" \
        DWS_MOCK_STATE_DIR="${READY_STATE}" \
        sh "${SCRIPT_DIRECTORY}/ensure-dws-ready.sh" 2>&1
)"
assert_contains "${READY_OUTPUT}" 'DWS is installed and authenticated.'
assert_contains "$(cat "${READY_STATE}/calls")" 'auth login --format json'
assert_not_contains "$(cat "${READY_STATE}/calls")" '--recommend'
test -f "${READY_STATE}/authenticated"

NONZERO_READY_STATE="${TEST_ROOT}/nonzero-ready"
mkdir -p "${NONZERO_READY_STATE}"
NONZERO_READY_OUTPUT="$(
    DWS_BINARY_PATH="$0" \
        DWS_MOCK_LOGIN_EXIT_AFTER_AUTH=1 \
        DWS_MOCK_STATE_DIR="${NONZERO_READY_STATE}" \
        sh "${SCRIPT_DIRECTORY}/ensure-dws-ready.sh" 2>&1
)"
assert_contains "${NONZERO_READY_OUTPUT}" 'DWS is installed and authenticated.'
test -f "${NONZERO_READY_STATE}/authenticated"

printf 'DingTalk local authorization tests passed.\n'

#!/bin/sh

set -eu

DWS_RELEASE_VERSION="1.0.58"

find_working_dws() {
    if [ -n "${DWS_BINARY_PATH:-}" ] &&
        [ -x "${DWS_BINARY_PATH}" ] &&
        "${DWS_BINARY_PATH}" version >/dev/null 2>&1; then
        printf '%s\n' "${DWS_BINARY_PATH}"
        return 0
    fi
    if command -v dws >/dev/null 2>&1; then
        DWS_CANDIDATE="$(command -v dws)"
        if "${DWS_CANDIDATE}" version >/dev/null 2>&1; then
            printf '%s\n' "${DWS_CANDIDATE}"
            return 0
        fi
    fi
    for DWS_CANDIDATE in \
        /opt/homebrew/bin/dws \
        /usr/local/bin/dws
    do
        if [ -x "${DWS_CANDIDATE}" ] &&
            "${DWS_CANDIDATE}" version >/dev/null 2>&1; then
            printf '%s\n' "${DWS_CANDIDATE}"
            return 0
        fi
    done
    return 1
}

download_file() {
    DWS_SOURCE_URL="$1"
    DWS_DESTINATION="$2"
    if command -v curl >/dev/null 2>&1; then
        curl --fail --show-error --location \
            --retry 3 --retry-all-errors --connect-timeout 15 \
            --output "${DWS_DESTINATION}" "${DWS_SOURCE_URL}"
    elif command -v wget >/dev/null 2>&1; then
        wget --tries=4 --timeout=15 --output-document="${DWS_DESTINATION}" \
            "${DWS_SOURCE_URL}"
    else
        echo "Neither curl nor wget is available for the verified DWS download." >&2
        return 1
    fi
}

sha256_file() {
    DWS_TARGET_FILE="$1"
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "${DWS_TARGET_FILE}" | awk '{print $1}'
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "${DWS_TARGET_FILE}" | awk '{print $1}'
    elif command -v openssl >/dev/null 2>&1; then
        openssl dgst -sha256 "${DWS_TARGET_FILE}" | awk '{print $NF}'
    else
        echo "No SHA-256 verifier is available." >&2
        return 1
    fi
}

resolve_release() {
    case "$(uname -s)" in
        Darwin)
            DWS_RELEASE_OS="darwin"
            ;;
        Linux)
            DWS_RELEASE_OS="linux"
            ;;
        *)
            echo "This DWS installer supports macOS and Linux." >&2
            return 1
            ;;
    esac

    case "$(uname -m)" in
        x86_64|amd64)
            DWS_RELEASE_ARCH="x64"
            ;;
        arm64|aarch64)
            DWS_RELEASE_ARCH="arm64"
            ;;
        *)
            echo "Unsupported DWS architecture: $(uname -m)" >&2
            return 1
            ;;
    esac

    DWS_PLATFORM="${DWS_RELEASE_OS}-${DWS_RELEASE_ARCH}"
    case "${DWS_PLATFORM}" in
        darwin-x64)
            DWS_ARCHIVE_URL="https://github.com/DingTalk-Real-AI/dingtalk-workspace-cli/releases/download/v1.0.58/dws-darwin-amd64.tar.gz"
            DWS_EXPECTED_SHA="4c12e35e5bf7e0905812cd42dc94a5345068a2c16e306bb50b13c5c78b5cb95d"
            ;;
        darwin-arm64)
            DWS_ARCHIVE_URL="https://github.com/DingTalk-Real-AI/dingtalk-workspace-cli/releases/download/v1.0.58/dws-darwin-arm64.tar.gz"
            DWS_EXPECTED_SHA="7d98599f90cae9d42b51ff2863efc87dbfb4a3176ff3c84fc2216110c0157a70"
            ;;
        linux-x64)
            DWS_ARCHIVE_URL="https://github.com/DingTalk-Real-AI/dingtalk-workspace-cli/releases/download/v1.0.58/dws-linux-amd64.tar.gz"
            DWS_EXPECTED_SHA="3ccadcc6f070a39d2b2ba20429a4fcdc2f21639bf79f34361dc7d16f501bfda6"
            ;;
        linux-arm64)
            DWS_ARCHIVE_URL="https://github.com/DingTalk-Real-AI/dingtalk-workspace-cli/releases/download/v1.0.58/dws-linux-arm64.tar.gz"
            DWS_EXPECTED_SHA="5ef6bde24bc3db6a11a0f1d0b3343a048956b2cbcf6cd3409a037fb6ba425489"
            ;;
        *)
            echo "Unsupported DWS platform: ${DWS_PLATFORM}" >&2
            return 1
            ;;
    esac
}

if DWS_EXECUTABLE="$(find_working_dws)"; then
    printf '%s\n' "${DWS_EXECUTABLE}"
    exit 0
fi

if [ -z "${HOME:-}" ]; then
    echo "A user home directory is required for local DWS installation." >&2
    exit 10
fi

resolve_release
DWS_INSTALL_DIRECTORY="${HOME}/.wegent-executor/tools/dws/${DWS_RELEASE_VERSION}/${DWS_PLATFORM}"
DWS_INSTALL_TARGET="${DWS_INSTALL_DIRECTORY}/dws"
if [ -x "${DWS_INSTALL_TARGET}" ] &&
    "${DWS_INSTALL_TARGET}" version >/dev/null 2>&1; then
    printf '%s\n' "${DWS_INSTALL_TARGET}"
    exit 0
fi

if ! command -v tar >/dev/null 2>&1; then
    echo "The tar utility is required to install DWS." >&2
    exit 11
fi

DWS_TEMP_DIRECTORY="$(mktemp -d "${TMPDIR:-/tmp}/wegent-dws.XXXXXX")"
trap 'rm -rf -- "${DWS_TEMP_DIRECTORY}"' EXIT HUP INT TERM
DWS_ARCHIVE_PATH="${DWS_TEMP_DIRECTORY}/dws.tar.gz"
DWS_EXPANDED_DIRECTORY="${DWS_TEMP_DIRECTORY}/expanded"
mkdir -p "${DWS_EXPANDED_DIRECTORY}"

echo "Downloading verified DingTalk Workspace CLI ${DWS_RELEASE_VERSION} for ${DWS_PLATFORM}..." >&2
download_file "${DWS_ARCHIVE_URL}" "${DWS_ARCHIVE_PATH}"
DWS_ACTUAL_SHA="$(sha256_file "${DWS_ARCHIVE_PATH}")"
if [ "${DWS_ACTUAL_SHA}" != "${DWS_EXPECTED_SHA}" ]; then
    echo "The downloaded DWS archive failed SHA-256 verification." >&2
    exit 12
fi

tar -xzf "${DWS_ARCHIVE_PATH}" -C "${DWS_EXPANDED_DIRECTORY}"
DWS_EXTRACTED_BINARY="$(find "${DWS_EXPANDED_DIRECTORY}" -type f -name dws -print | head -n 1)"
if [ -z "${DWS_EXTRACTED_BINARY}" ]; then
    echo "The verified DWS archive did not contain a dws binary." >&2
    exit 13
fi

mkdir -p "${DWS_INSTALL_DIRECTORY}"
cp "${DWS_EXTRACTED_BINARY}" "${DWS_INSTALL_TARGET}.new"
chmod 0755 "${DWS_INSTALL_TARGET}.new"
mv "${DWS_INSTALL_TARGET}.new" "${DWS_INSTALL_TARGET}"
if ! "${DWS_INSTALL_TARGET}" version >/dev/null 2>&1; then
    echo "The verified DWS binary is not runnable at ${DWS_INSTALL_TARGET}." >&2
    exit 14
fi

printf '%s\n' "${DWS_INSTALL_TARGET}"

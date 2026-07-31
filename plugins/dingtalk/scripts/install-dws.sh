#!/bin/sh

set -eu

DWS_RELEASE_VERSION="1.0.46"

find_working_dws() {
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
            DWS_ARCHIVE_URL="https://p11-market.byteimg.com/tos-cn-i-17oceyzymr/binaries/dws-cli/1.0.46/darwin-x64.zip"
            DWS_EXPECTED_SHA="064d5b2cda4a49840a0f4851c1fa84acb92b92d9657c08af0c71d419138604bc"
            ;;
        darwin-arm64)
            DWS_ARCHIVE_URL="https://p11-market.byteimg.com/tos-cn-i-17oceyzymr/binaries/dws-cli/1.0.46/darwin-arm64-1783747172810957192.zip"
            DWS_EXPECTED_SHA="ef832cb98ead9790a47a3b149bc09c30c0695ad6641109fc3534dd879bf6b2c2"
            ;;
        linux-x64)
            DWS_ARCHIVE_URL="https://p11-market.byteimg.com/tos-cn-i-17oceyzymr/binaries/dws-cli/1.0.46/linux-x64-1783747396763108997.zip"
            DWS_EXPECTED_SHA="cd976c9b8cbb0bdac871601d958862566648ee325469655fe9dfbe35149af06b"
            ;;
        linux-arm64)
            DWS_ARCHIVE_URL="https://p11-market.byteimg.com/tos-cn-i-17oceyzymr/binaries/dws-cli/1.0.46/linux-arm64-1783747080290971851.zip"
            DWS_EXPECTED_SHA="24e2ddc6d584713ce0da8c49cc99593f86af84301a4ca1de527c775e8079aec4"
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

if ! command -v unzip >/dev/null 2>&1; then
    echo "The unzip utility is required to install DWS." >&2
    exit 11
fi

DWS_TEMP_DIRECTORY="$(mktemp -d "${TMPDIR:-/tmp}/wegent-dws.XXXXXX")"
trap 'rm -rf -- "${DWS_TEMP_DIRECTORY}"' EXIT HUP INT TERM
DWS_ARCHIVE_PATH="${DWS_TEMP_DIRECTORY}/dws.zip"
DWS_EXPANDED_DIRECTORY="${DWS_TEMP_DIRECTORY}/expanded"
mkdir -p "${DWS_EXPANDED_DIRECTORY}"

echo "Downloading verified DingTalk Workspace CLI ${DWS_RELEASE_VERSION} for ${DWS_PLATFORM}..." >&2
download_file "${DWS_ARCHIVE_URL}" "${DWS_ARCHIVE_PATH}"
DWS_ACTUAL_SHA="$(sha256_file "${DWS_ARCHIVE_PATH}")"
if [ "${DWS_ACTUAL_SHA}" != "${DWS_EXPECTED_SHA}" ]; then
    echo "The downloaded DWS archive failed SHA-256 verification." >&2
    exit 12
fi

unzip -q "${DWS_ARCHIVE_PATH}" -d "${DWS_EXPANDED_DIRECTORY}"
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


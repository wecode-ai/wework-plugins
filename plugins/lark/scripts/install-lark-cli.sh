#!/bin/sh

set -eu

LARK_RELEASE_VERSION="1.0.68"

download_file() {
    LARK_SOURCE_URL="$1"
    LARK_DESTINATION="$2"
    if command -v curl >/dev/null 2>&1; then
        curl --fail --show-error --location \
            --retry 3 --retry-all-errors --connect-timeout 15 \
            --output "${LARK_DESTINATION}" "${LARK_SOURCE_URL}"
    elif command -v wget >/dev/null 2>&1; then
        wget --tries=4 --timeout=15 --output-document="${LARK_DESTINATION}" \
            "${LARK_SOURCE_URL}"
    else
        echo "Neither curl nor wget is available for the verified Lark CLI download." >&2
        return 1
    fi
}

download_with_fallback() {
    if download_file "${LARK_PRIMARY_URL}" "$1"; then
        return 0
    fi
    echo "The primary Lark CLI download failed; retrying the official fallback mirror..." >&2
    download_file "${LARK_FALLBACK_URL}" "$1"
}

sha256_file() {
    LARK_TARGET_FILE="$1"
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "${LARK_TARGET_FILE}" | awk '{print $1}'
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "${LARK_TARGET_FILE}" | awk '{print $1}'
    elif command -v openssl >/dev/null 2>&1; then
        openssl dgst -sha256 "${LARK_TARGET_FILE}" | awk '{print $NF}'
    else
        echo "No SHA-256 verifier is available." >&2
        return 1
    fi
}

resolve_release() {
    case "$(uname -s)" in
        Darwin)
            LARK_RELEASE_OS="darwin"
            ;;
        Linux)
            LARK_RELEASE_OS="linux"
            ;;
        *)
            echo "This Lark CLI installer supports macOS and Linux." >&2
            return 1
            ;;
    esac

    case "$(uname -m)" in
        x86_64|amd64)
            LARK_RELEASE_ARCH="x64"
            ;;
        arm64|aarch64)
            LARK_RELEASE_ARCH="arm64"
            ;;
        *)
            echo "Unsupported Lark CLI architecture: $(uname -m)" >&2
            return 1
            ;;
    esac

    LARK_PLATFORM="${LARK_RELEASE_OS}-${LARK_RELEASE_ARCH}"
    case "${LARK_PLATFORM}" in
        darwin-arm64)
            LARK_EXPECTED_SHA="354a03f45a46d9111aad9d6ec4464a791e3d6a0fcdf0f1c1c6422bc2ea924ed3"
            LARK_PRIMARY_URL="https://p11-market.byteimg.com/tos-cn-i-17oceyzymr/binaries/lark-cli/1.0.68/darwin-arm64.zip"
            LARK_FALLBACK_URL="https://p16-market-sg.ibyteimg.com/tos-alisg-i-qmhakdvxf5-sg/binaries/lark-cli/1.0.69/darwin-arm64-1784086394769505699.zip"
            ;;
        darwin-x64)
            LARK_EXPECTED_SHA="d71750eaa3e7bc3bd92bca554222392f6e4189fa330eb26a046108c6ccef2576"
            LARK_PRIMARY_URL="https://p11-market.byteimg.com/tos-cn-i-17oceyzymr/binaries/lark-cli/1.0.68/darwin-amd64.zip"
            LARK_FALLBACK_URL="https://p16-market-sg.ibyteimg.com/tos-alisg-i-qmhakdvxf5-sg/binaries/lark-cli/1.0.69/darwin-x64-1784086377564221452.zip"
            ;;
        linux-arm64)
            LARK_EXPECTED_SHA="26730b2ca702bd8a705f6d51db6fd21a8fb2fbcf139435ddf4cc8a0c06b58907"
            LARK_PRIMARY_URL="https://p11-market.byteimg.com/tos-cn-i-17oceyzymr/binaries/lark-cli/1.0.68/linux-arm64.zip"
            LARK_FALLBACK_URL="https://p16-market-sg.ibyteimg.com/tos-alisg-i-qmhakdvxf5-sg/binaries/lark-cli/1.0.69/linux-arm64-1784086402284791762.zip"
            ;;
        linux-x64)
            LARK_EXPECTED_SHA="fbedf22a35ffa0bb25aee86daadd811e20ada078c4ebff6a97da1d683ae88eea"
            LARK_PRIMARY_URL="https://p11-market.byteimg.com/tos-cn-i-17oceyzymr/binaries/lark-cli/1.0.68/linux-amd64.zip"
            LARK_FALLBACK_URL="https://p16-market-sg.ibyteimg.com/tos-alisg-i-qmhakdvxf5-sg/binaries/lark-cli/1.0.69/linux-x64-1784086385368816923.zip"
            ;;
        *)
            echo "Unsupported Lark CLI platform: ${LARK_PLATFORM}" >&2
            return 1
            ;;
    esac
}

is_expected_version() {
    "$1" --version 2>/dev/null | grep -F "${LARK_RELEASE_VERSION}" >/dev/null
}

if [ -z "${HOME:-}" ]; then
    echo "A user home directory is required for local Lark CLI installation." >&2
    exit 10
fi

resolve_release
LARK_INSTALL_DIRECTORY="${HOME}/.wegent-executor/tools/lark-cli/${LARK_RELEASE_VERSION}/${LARK_PLATFORM}"
LARK_INSTALL_TARGET="${LARK_INSTALL_DIRECTORY}/lark-cli"
if [ -x "${LARK_INSTALL_TARGET}" ] && is_expected_version "${LARK_INSTALL_TARGET}"; then
    printf '%s\n' "${LARK_INSTALL_TARGET}"
    exit 0
fi

if command -v lark-cli >/dev/null 2>&1; then
    LARK_PATH_CANDIDATE="$(command -v lark-cli)"
    if is_expected_version "${LARK_PATH_CANDIDATE}"; then
        printf '%s\n' "${LARK_PATH_CANDIDATE}"
        exit 0
    fi
fi

if ! command -v unzip >/dev/null 2>&1; then
    echo "The unzip utility is required to install Lark CLI." >&2
    exit 11
fi

LARK_TEMP_DIRECTORY="$(mktemp -d "${TMPDIR:-/tmp}/wegent-lark.XXXXXX")"
trap 'rm -rf -- "${LARK_TEMP_DIRECTORY}"' EXIT HUP INT TERM
LARK_ARCHIVE_PATH="${LARK_TEMP_DIRECTORY}/lark-cli.zip"
LARK_EXPANDED_DIRECTORY="${LARK_TEMP_DIRECTORY}/expanded"
mkdir -p "${LARK_EXPANDED_DIRECTORY}"

echo "Downloading verified Lark CLI ${LARK_RELEASE_VERSION} for ${LARK_PLATFORM}..." >&2
download_with_fallback "${LARK_ARCHIVE_PATH}"
LARK_ACTUAL_SHA="$(sha256_file "${LARK_ARCHIVE_PATH}")"
if [ "${LARK_ACTUAL_SHA}" != "${LARK_EXPECTED_SHA}" ]; then
    echo "The downloaded Lark CLI archive failed SHA-256 verification." >&2
    exit 12
fi

unzip -q "${LARK_ARCHIVE_PATH}" -d "${LARK_EXPANDED_DIRECTORY}"
LARK_EXTRACTED_BINARY="$(find "${LARK_EXPANDED_DIRECTORY}" -type f -name lark-cli -print | head -n 1)"
if [ -z "${LARK_EXTRACTED_BINARY}" ]; then
    echo "The verified archive did not contain a lark-cli binary." >&2
    exit 13
fi

mkdir -p "${LARK_INSTALL_DIRECTORY}"
cp "${LARK_EXTRACTED_BINARY}" "${LARK_INSTALL_TARGET}.new"
chmod 0755 "${LARK_INSTALL_TARGET}.new"
mv "${LARK_INSTALL_TARGET}.new" "${LARK_INSTALL_TARGET}"
if ! is_expected_version "${LARK_INSTALL_TARGET}"; then
    echo "The verified Lark CLI binary is not version ${LARK_RELEASE_VERSION}." >&2
    exit 14
fi

printf '%s\n' "${LARK_INSTALL_TARGET}"

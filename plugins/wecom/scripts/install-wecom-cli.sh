#!/bin/sh

set -eu

WECOM_RELEASE_VERSION="0.1.9"

find_working_wecom_cli() {
    if command -v wecom-cli >/dev/null 2>&1; then
        WECOM_CANDIDATE="$(command -v wecom-cli)"
        if "${WECOM_CANDIDATE}" --version >/dev/null 2>&1; then
            printf '%s\n' "${WECOM_CANDIDATE}"
            return 0
        fi
    fi
    for WECOM_CANDIDATE in \
        /opt/homebrew/bin/wecom-cli \
        /usr/local/bin/wecom-cli
    do
        if [ -x "${WECOM_CANDIDATE}" ] &&
            "${WECOM_CANDIDATE}" --version >/dev/null 2>&1; then
            printf '%s\n' "${WECOM_CANDIDATE}"
            return 0
        fi
    done
    return 1
}

download_file() {
    WECOM_SOURCE_URL="$1"
    WECOM_DESTINATION="$2"
    if command -v curl >/dev/null 2>&1; then
        curl --fail --show-error --location \
            --retry 3 --retry-all-errors --connect-timeout 15 \
            --output "${WECOM_DESTINATION}" "${WECOM_SOURCE_URL}"
    elif command -v wget >/dev/null 2>&1; then
        wget --tries=4 --timeout=15 --output-document="${WECOM_DESTINATION}" \
            "${WECOM_SOURCE_URL}"
    else
        echo "Neither curl nor wget is available for the verified WeCom CLI download." >&2
        return 1
    fi
}

download_with_fallback() {
    if download_file "${WECOM_PRIMARY_URL}" "$1"; then
        return 0
    fi
    echo "The primary WeCom download failed; retrying the official fallback mirror..." >&2
    download_file "${WECOM_FALLBACK_URL}" "$1"
}

sha256_file() {
    WECOM_TARGET_FILE="$1"
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "${WECOM_TARGET_FILE}" | awk '{print $1}'
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "${WECOM_TARGET_FILE}" | awk '{print $1}'
    elif command -v openssl >/dev/null 2>&1; then
        openssl dgst -sha256 "${WECOM_TARGET_FILE}" | awk '{print $NF}'
    else
        echo "No SHA-256 verifier is available." >&2
        return 1
    fi
}

resolve_release() {
    case "$(uname -s)" in
        Darwin)
            WECOM_RELEASE_OS="darwin"
            ;;
        Linux)
            WECOM_RELEASE_OS="linux"
            ;;
        *)
            echo "This WeCom CLI installer supports macOS and Linux." >&2
            return 1
            ;;
    esac

    case "$(uname -m)" in
        x86_64|amd64)
            WECOM_RELEASE_ARCH="x64"
            ;;
        arm64|aarch64)
            WECOM_RELEASE_ARCH="arm64"
            ;;
        *)
            echo "Unsupported WeCom CLI architecture: $(uname -m)" >&2
            return 1
            ;;
    esac

    WECOM_PLATFORM="${WECOM_RELEASE_OS}-${WECOM_RELEASE_ARCH}"
    case "${WECOM_PLATFORM}" in
        darwin-arm64)
            WECOM_EXPECTED_SHA="85d498c8cd22b3d0aba340bb5fece7a0f521198cff5dc4013b42334feae73298"
            WECOM_PRIMARY_URL="https://p11-market.byteimg.com/tos-cn-i-17oceyzymr/binaries/wecom-cli/0.1.9/darwin-arm64-1783946808514786799.zip"
            WECOM_FALLBACK_URL="https://p16-market-sg.ibyteimg.com/tos-alisg-i-qmhakdvxf5-sg/binaries/wecom-cli/0.1.9/darwin-arm64-1784086431492074504.zip"
            ;;
        darwin-x64)
            WECOM_EXPECTED_SHA="1d4c0e29bd95ac1f7ea2d8125dd5552bb8f1340e38c10a85035ce6bdeff8726d"
            WECOM_PRIMARY_URL="https://p11-market.byteimg.com/tos-cn-i-17oceyzymr/binaries/wecom-cli/0.1.9/darwin-x64-1783946977346123173.zip"
            WECOM_FALLBACK_URL="https://p16-market-sg.ibyteimg.com/tos-alisg-i-qmhakdvxf5-sg/binaries/wecom-cli/0.1.9/darwin-x64-1784086414872124457.zip"
            ;;
        linux-x64)
            WECOM_EXPECTED_SHA="f720dfdd5cb9348f93409305fac39e0a3c0ea295a9bd52f1fcc9a14dcfa41811"
            WECOM_PRIMARY_URL="https://p11-market.byteimg.com/tos-cn-i-17oceyzymr/binaries/wecom-cli/0.1.9/linux-x64-1783946816984780361.zip"
            WECOM_FALLBACK_URL="https://p16-market-sg.ibyteimg.com/tos-alisg-i-qmhakdvxf5-sg/binaries/wecom-cli/0.1.9/linux-x64-1784086426552688969.zip"
            ;;
        linux-arm64)
            WECOM_EXPECTED_SHA="518cbb45c349665a725a7f301d9dd25920575186adfb63532cfc47315941ce10"
            WECOM_PRIMARY_URL="https://p11-market.byteimg.com/tos-cn-i-17oceyzymr/binaries/wecom-cli/0.1.9/linux-arm64-1783946835153593555.zip"
            WECOM_FALLBACK_URL="https://p16-market-sg.ibyteimg.com/tos-alisg-i-qmhakdvxf5-sg/binaries/wecom-cli/0.1.9/linux-arm64-1784086420275492848.zip"
            ;;
        *)
            echo "Unsupported WeCom CLI platform: ${WECOM_PLATFORM}" >&2
            return 1
            ;;
    esac
}

if WECOM_EXECUTABLE="$(find_working_wecom_cli)"; then
    printf '%s\n' "${WECOM_EXECUTABLE}"
    exit 0
fi

if [ -z "${HOME:-}" ]; then
    echo "A user home directory is required for local WeCom CLI installation." >&2
    exit 10
fi

resolve_release
WECOM_INSTALL_DIRECTORY="${HOME}/.wegent-executor/tools/wecom-cli/${WECOM_RELEASE_VERSION}/${WECOM_PLATFORM}"
WECOM_INSTALL_TARGET="${WECOM_INSTALL_DIRECTORY}/wecom-cli"
if [ -x "${WECOM_INSTALL_TARGET}" ] &&
    "${WECOM_INSTALL_TARGET}" --version >/dev/null 2>&1; then
    printf '%s\n' "${WECOM_INSTALL_TARGET}"
    exit 0
fi

if ! command -v unzip >/dev/null 2>&1; then
    echo "The unzip utility is required to install WeCom CLI." >&2
    exit 11
fi

WECOM_TEMP_DIRECTORY="$(mktemp -d "${TMPDIR:-/tmp}/wegent-wecom.XXXXXX")"
trap 'rm -rf -- "${WECOM_TEMP_DIRECTORY}"' EXIT HUP INT TERM
WECOM_ARCHIVE_PATH="${WECOM_TEMP_DIRECTORY}/wecom-cli.zip"
WECOM_EXPANDED_DIRECTORY="${WECOM_TEMP_DIRECTORY}/expanded"
mkdir -p "${WECOM_EXPANDED_DIRECTORY}"

echo "Downloading verified WeCom CLI ${WECOM_RELEASE_VERSION} for ${WECOM_PLATFORM}..." >&2
download_with_fallback "${WECOM_ARCHIVE_PATH}"
WECOM_ACTUAL_SHA="$(sha256_file "${WECOM_ARCHIVE_PATH}")"
if [ "${WECOM_ACTUAL_SHA}" != "${WECOM_EXPECTED_SHA}" ]; then
    echo "The downloaded WeCom CLI archive failed SHA-256 verification." >&2
    exit 12
fi

unzip -q "${WECOM_ARCHIVE_PATH}" -d "${WECOM_EXPANDED_DIRECTORY}"
WECOM_EXTRACTED_BINARY="$(find "${WECOM_EXPANDED_DIRECTORY}" -type f -name wecom-cli -print | head -n 1)"
if [ -z "${WECOM_EXTRACTED_BINARY}" ]; then
    echo "The verified archive did not contain a wecom-cli binary." >&2
    exit 13
fi

mkdir -p "${WECOM_INSTALL_DIRECTORY}"
cp "${WECOM_EXTRACTED_BINARY}" "${WECOM_INSTALL_TARGET}.new"
chmod 0755 "${WECOM_INSTALL_TARGET}.new"
mv "${WECOM_INSTALL_TARGET}.new" "${WECOM_INSTALL_TARGET}"
if ! "${WECOM_INSTALL_TARGET}" --version >/dev/null 2>&1; then
    echo "The verified WeCom CLI binary is not runnable at ${WECOM_INSTALL_TARGET}." >&2
    exit 14
fi

printf '%s\n' "${WECOM_INSTALL_TARGET}"


"""Verified native package loading and serialized access to the upstream store."""

import contextlib
import hashlib
import json
import lzma
import os
import platform
import subprocess
import tempfile
import time
from pathlib import Path

from wegent_plugin_auth import AuthError, local_configuration

ROOT = Path(__file__).resolve().parent


def state_root():
    path = Path.home() / ".wegent-executor/plugin-auth/wecom"
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


@contextlib.contextmanager
def locked():
    # All profiles share one OS keychain namespace, including custom config dirs.
    with (state_root() / "source.lock").open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        deadline = time.monotonic() + 30
        while True:
            try:
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise AuthError("plugin_auth_source_busy") from None
                time.sleep(0.05)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def environment(source=True):
    allowed = {
        "HOME",
        "USERPROFILE",
        "PATH",
        "SYSTEMROOT",
        "WINDIR",
        "APPDATA",
        "LOCALAPPDATA",
        "TEMP",
        "TMP",
        "LANG",
        "WEGENT_EXECUTOR_HOME",
        "WEGENT_PLUGIN_AUTH_PORT",
        "WECOM_CLI_CONFIG_DIR",
    }
    result = {key: value for key, value in os.environ.items() if key in allowed}
    settings = local_configuration()
    if set(settings) - {"WECOM_CLI_CONFIG_DIR"}:
        raise AuthError("plugin_auth_invalid_local_configuration")
    result.update(settings)
    if not source:
        result.pop("WECOM_CLI_CONFIG_DIR", None)
    return result


def companion():
    system = {"Darwin": "darwin", "Linux": "linux", "Windows": "windows"}.get(
        platform.system()
    )
    arch = {
        "arm64": "arm64",
        "aarch64": "arm64",
        "x86_64": "amd64",
        "AMD64": "amd64",
    }.get(platform.machine())
    folder = ROOT / "native" / f"{system}-{arch}"
    blob, metadata = (
        folder / "wecom-account-auth.xz",
        folder / "wecom-account-auth.json",
    )
    if blob.is_symlink() or metadata.is_symlink() or metadata.stat().st_size > 65536:
        raise AuthError("plugin_auth_package_sync_required")
    value = json.loads(metadata.read_text(encoding="utf-8"))
    if (
        value.get("nativeProtocolVersion") != 1
        or value.get("target") != f"{system}/{arch}"
        or not 0 < value.get("binaryBytes", 0) < 80 * 1024 * 1024
    ):
        raise AuthError("plugin_auth_package_sync_required")
    packed = blob.read_bytes()
    if hashlib.sha256(packed).hexdigest() != value["compressedSha256"]:
        raise AuthError("plugin_auth_package_sync_required")
    cache = (
        Path(
            os.environ.get(
                "WEGENT_EXECUTOR_HOME", str(Path.home() / ".wegent-executor")
            )
        )
        / "plugin-native/wecom"
    )
    cache.mkdir(parents=True, exist_ok=True, mode=0o700)
    digest = value["binarySha256"]
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise AuthError("plugin_auth_package_sync_required")
    executable = cache / (digest + (".exe" if system == "windows" else ""))
    if executable.is_symlink():
        raise AuthError("plugin_auth_package_sync_required")
    if (
        executable.exists()
        and hashlib.sha256(executable.read_bytes()).hexdigest() == digest
    ):
        return executable
    decoder = lzma.LZMADecompressor(memlimit=128 * 1024 * 1024)
    content = decoder.decompress(packed, max_length=value["binaryBytes"] + 1)
    if (
        not decoder.eof
        or len(content) != value["binaryBytes"]
        or hashlib.sha256(content).hexdigest() != digest
    ):
        raise AuthError("plugin_auth_package_sync_required")
    fd, temporary = tempfile.mkstemp(dir=cache)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o700)
        os.replace(temporary, executable)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return executable


def invoke(arguments, *, capture=False, timeout=240):
    source = not (
        len(arguments) > 1 and arguments[0] == "__wegent" and arguments[1] == "run"
    )
    return subprocess.run(
        [str(companion()), *arguments],
        env=environment(source=source),
        capture_output=capture,
        timeout=timeout,
        check=False,
    )

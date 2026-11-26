"""Coordinate plugin authentication processes sharing one GitHub CLI profile.

Locks coordinate callers of this protocol, including different plugin versions.
External gh commands and older hosts that do not take this lock remain outside
that coordination. Lock files contain a single marker byte, never credentials.
"""

from __future__ import annotations

import errno
import hashlib
import os
from contextlib import contextmanager
from pathlib import Path

from .errors import GitHubError


def profile_directory(environment=None):
    environment = os.environ if environment is None else environment
    if environment.get("GH_CONFIG_DIR"):
        path = Path(environment["GH_CONFIG_DIR"])
    elif environment.get("XDG_CONFIG_HOME"):
        path = Path(environment["XDG_CONFIG_HOME"]) / "gh"
    elif os.name == "nt" and environment.get("APPDATA"):
        path = Path(environment["APPDATA"]) / "GitHub CLI"
    else:
        path = Path.home() / ".config" / "gh"
    try:
        return path.expanduser().resolve()
    except (OSError, ValueError, RuntimeError):
        raise GitHubError(
            "gh_auth_profile_invalid", "The GitHub CLI profile directory is invalid."
        ) from None


def lock_path(environment=None):
    profile = os.path.normcase(str(profile_directory(environment)))
    digest = hashlib.sha256(profile.encode("utf-8")).hexdigest()
    return Path.home() / ".wework" / "plugin-auth" / "gh-locks" / (digest + ".lock")


@contextmanager
def account_lock(*, environment=None):
    """Take a nonblocking process lock for login or logout of this gh profile."""
    descriptor = None
    locked = False
    try:
        path = lock_path(environment)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags, 0o600)
        os.set_inheritable(descriptor, False)
        if os.fstat(descriptor).st_size == 0:
            os.write(descriptor, b"\x00")
        os.lseek(descriptor, 0, os.SEEK_SET)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            os.fchmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        locked = True
    except OSError as error:
        if descriptor is not None:
            os.close(descriptor)
        code = (
            "gh_login_in_progress"
            if error.errno in {errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK}
            else "gh_auth_lock_failed"
        )
        message = (
            "Another plugin authentication operation is using this GitHub CLI profile. Complete or cancel it before retrying."
            if code == "gh_login_in_progress"
            else "The GitHub CLI authentication lock could not be opened."
        )
        raise GitHubError(code, message) from None
    try:
        yield
    finally:
        if descriptor is not None:
            try:
                if locked:
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    if os.name == "nt":
                        import msvcrt

                        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)

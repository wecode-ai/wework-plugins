"""Disconnect only gh's active local account; never revoke a server grant."""

import os
import re
import subprocess
import threading
import time

from . import cli
from .auth_state import account_lock
from .errors import GitHubError

TIMEOUT = 20
MAX_OUTPUT = 8192
_HINTS = {
    "gh_missing": "Install GitHub CLI (gh) on the execution device.",
    "gh_logout_env_token": "GitHub CLI uses GH_TOKEN or GITHUB_TOKEN from the device environment. Remove that environment setting before signing out.",
    "gh_login_in_progress": "A GitHub account operation is already running on this device. Finish or cancel it before signing out.",
    "gh_auth_profile_invalid": "The GitHub CLI profile directory is invalid. Check the profile configuration on this device.",
    "gh_auth_lock_failed": "The GitHub CLI authentication lock could not be opened on this device.",
    "gh_logout_timeout": "GitHub CLI sign-out timed out. Check the connection status before retrying.",
    "gh_logout_failed": "GitHub CLI could not remove the active local account. No other account was selected for sign-out.",
    "gh_logout_output_limit": "GitHub CLI sign-out output exceeded its safety limit.",
    "gh_logout_health_failed": "GitHub CLI connection status could not be checked. Retry the health check.",
}


def _error(code, *, changed=False):
    hint = (
        "GitHub CLI reported that the active account's local credentials were removed. "
        "Refresh the connection status to check any remaining account."
        if changed
        else _HINTS.get(code, _HINTS["gh_logout_failed"])
    )
    return {
        "status": "error",
        "errorCode": code,
        "hint": hint,
        "changed": changed,
        "accountRevoked": changed,
    }


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise GitHubError("gh_logout_timeout", _HINTS["gh_logout_timeout"])
    return remaining


def _run(arguments, deadline):
    """Only fixed config/logout commands call this bounded, non-TTY runner."""
    _remaining(deadline)
    try:
        process = subprocess.Popen(
            ["gh", *arguments],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=cli._environment(),
            bufsize=0,
            **(
                {"creationflags": subprocess.CREATE_NO_WINDOW}
                if os.name == "nt"
                else {}
            ),
        )
    except FileNotFoundError:
        raise GitHubError("gh_missing", _HINTS["gh_missing"]) from None
    except OSError:
        raise GitHubError("gh_logout_failed", _HINTS["gh_logout_failed"]) from None

    output, diagnostic = bytearray(), bytearray()
    overflow, failed = threading.Event(), threading.Event()

    def read(stream, target):
        try:
            while True:
                chunk = stream.read(min(4096, MAX_OUTPUT + 1 - len(target)))
                if not chunk:
                    return
                target.extend(chunk)
                if len(target) > MAX_OUTPUT:
                    overflow.set()
                    cli._stop(process)
                    return
        except (OSError, ValueError):
            failed.set()
            cli._stop(process)

    threads = [
        threading.Thread(target=read, args=(process.stdout, output), daemon=True),
        threading.Thread(target=read, args=(process.stderr, diagnostic), daemon=True),
    ]
    try:
        for thread in threads:
            thread.start()
        try:
            code = process.wait(timeout=_remaining(deadline))
        except subprocess.TimeoutExpired:
            raise GitHubError(
                "gh_logout_timeout", _HINTS["gh_logout_timeout"]
            ) from None
        for thread in threads:
            thread.join(timeout=min(0.5, _remaining(deadline)))
        if overflow.is_set():
            raise GitHubError(
                "gh_logout_output_limit", _HINTS["gh_logout_output_limit"]
            )
        if failed.is_set() or any(thread.is_alive() for thread in threads):
            raise GitHubError("gh_logout_failed", _HINTS["gh_logout_failed"])
        return code, bytes(output), bytes(diagnostic)
    finally:
        cli._stop(process)
        try:
            process.wait(timeout=1)
        except (subprocess.TimeoutExpired, OSError):
            pass
        for stream in (process.stdout, process.stderr):
            try:
                stream.close()
            except OSError:
                pass
        for thread in threads:
            if thread.ident is not None:
                thread.join(timeout=0.2)


def _active_profile(deadline):
    code, output, diagnostic = _run(
        ["config", "get", "user", "--host", "github.com"], deadline
    )
    if code != 0:
        if b'could not find key "user"' in diagnostic.lower():
            return None
        raise GitHubError("gh_logout_failed", _HINTS["gh_logout_failed"])
    login = output.decode("utf-8", errors="replace").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}", login):
        raise GitHubError("gh_logout_failed", _HINTS["gh_logout_failed"])
    return login


def _connected(deadline):
    try:
        cli._run(
            ["api", "user", "--silent", "--hostname", "github.com", "--method", "GET"],
            timeout=_remaining(deadline),
        )
        return True
    except GitHubError as error:
        if error.code == "github_auth_required":
            return False
        if error.code == "gh_missing":
            raise GitHubError("gh_missing", _HINTS["gh_missing"]) from None
        if error.code in ("github_cli_timeout", "gh_logout_timeout"):
            raise GitHubError(
                "gh_logout_timeout", _HINTS["gh_logout_timeout"]
            ) from None
        raise GitHubError(
            "gh_logout_health_failed", _HINTS["gh_logout_health_failed"]
        ) from None


def logout():
    """Use the shared account lock and return only credential-free JSON fields."""
    if os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN"):
        return _error("gh_logout_env_token")
    changed = False
    try:
        with account_lock():
            deadline = time.monotonic() + TIMEOUT
            login = _active_profile(deadline)
            if login is not None:
                # Exact host/user is non-interactive in gh and removes only this
                # profile. Another saved account may become active afterwards.
                code, _, _ = _run(
                    ["auth", "logout", "--hostname", "github.com", "--user", login],
                    deadline,
                )
                if code != 0:
                    raise GitHubError("gh_logout_failed", _HINTS["gh_logout_failed"])
                # Record the completed local mutation before any follow-up can
                # fail. Retrying logout could otherwise remove the next account.
                # accountRevoked marks local credential removal, not a
                # claim that GitHub's server-side OAuth grant was revoked.
                changed = True
                if _active_profile(deadline) == login:
                    raise GitHubError("gh_logout_failed", _HINTS["gh_logout_failed"])
            return {
                "status": "ok",
                "connected": _connected(deadline),
                "changed": changed,
                "accountRevoked": changed,
            }
    except GitHubError as error:
        code = error.code if error.code in _HINTS else "gh_logout_failed"
        return _error(code, changed=changed)
    except (OSError, ValueError):
        return _error("gh_logout_failed", changed=changed)

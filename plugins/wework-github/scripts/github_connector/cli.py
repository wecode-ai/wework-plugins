"""Bounded GitHub CLI transport using the device's existing gh authentication.

This module never exports tokens or opens gh's credential/configuration files.
GH_TOKEN, GITHUB_TOKEN and GH_CONFIG_DIR retain GitHub CLI's normal precedence.
Only non-interactive, fixed-host API calls are made; the plugin page owns login/logout.
"""

import json
import os
import re
import subprocess
import threading
from urllib.parse import unquote, urlencode

from .errors import GitHubError

MAX_STDOUT = 4 * 1024 * 1024
MAX_STDERR = 8 * 1024
MAX_INPUT = 2 * 1024 * 1024
TIMEOUT = 30
_METHODS = {"GET", "POST", "PUT", "PATCH"}
_ACCEPTS = {"application/vnd.github+json", "application/vnd.github.diff", "text/plain"}


def _environment():
    environment = os.environ.copy()
    # Account management and business tools use the same device environment/profile.
    # Debugging can expose request payloads or signed download URLs on stderr.
    for key in ("GH_DEBUG", "DEBUG", "GH_FORCE_TTY"):
        environment.pop(key, None)
    environment.update(
        GH_PROMPT_DISABLED="1",
        GH_PAGER="",
        PAGER="",
        NO_COLOR="1",
        CLICOLOR="0",
        GH_NO_UPDATE_NOTIFIER="1",
        GH_NO_EXTENSION_UPDATE_NOTIFIER="1",
    )
    return environment


def _invalid(message):
    raise GitHubError("invalid_request", message)


def _endpoint(path, query):
    if (
        type(path) is not str
        or not path.startswith("/")
        or path.startswith("//")
        or len(path) > 8192
        or any(c in path for c in "?#\\{}")
        or re.search(r"%(?![0-9A-Fa-f]{2})", path)
        or any(ord(c) <= 32 or ord(c) == 127 for c in path)
    ):
        _invalid("Invalid GitHub API path.")
    for part in path[1:].split("/"):
        decoded = unquote(part)
        if (
            not part
            or any(p in (".", "..") for p in decoded.split("/"))
            or "\\" in decoded
            or any(ord(c) < 32 or ord(c) == 127 for c in decoded)
        ):
            _invalid("Invalid GitHub API path segment.")
    if not (
        path in {"/user", "/user/repos", "/search/repositories", "/search/issues"}
        or path.startswith("/repos/")
    ):
        _invalid("Unsupported GitHub API path.")
    if query is not None and type(query) is not dict:
        _invalid("GitHub query parameters must be an object.")
    encoded = {}
    for key, value in (query or {}).items():
        if (
            type(key) is not str
            or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key)
            or type(value) not in (str, int, bool)
        ):
            _invalid("Invalid GitHub query parameter.")
        if type(value) is str and ("\x00" in value or len(value) > 8192):
            _invalid("Invalid GitHub query value.")
        encoded[key] = str(value).lower() if type(value) is bool else value
    endpoint = path.lstrip("/")
    if encoded:
        endpoint += "?" + urlencode(encoded)
    return endpoint


def _failure(returncode, stderr):
    # Provider stderr is classification input only, never a user-visible message.
    message = stderr.decode("utf-8", errors="replace").lower()
    if returncode == 4 or re.search(r"\bhttp\s+401\b", message):
        return GitHubError(
            "github_auth_required",
            "Connect GitHub using this plugin's browser account page.",
        )
    if re.search(r"\bhttp\s+429\b", message) or "rate limit" in message:
        return GitHubError(
            "github_rate_limited", "GitHub rate limit reached. Wait before retrying."
        )
    if re.search(r"\bhttp\s+403\b", message):
        return GitHubError(
            "github_forbidden",
            "GitHub denied the request. Check repository access, organization policies, or rate limits.",
        )
    if re.search(r"\bhttp\s+404\b", message):
        return GitHubError(
            "github_not_found",
            "The GitHub resource does not exist or is not accessible.",
        )
    return GitHubError(
        "github_cli_failed",
        "GitHub CLI could not complete the request. Check connectivity and permissions; no operation was automatically retried.",
    )


def _stop(process):
    try:
        if process.poll() is None:
            process.kill()
    except OSError:
        pass


def _run(arguments, payload=None, *, timeout=TIMEOUT):
    """Drain both output pipes concurrently with hard bounds and a wall timeout."""
    try:
        process = subprocess.Popen(
            ["gh", *arguments],
            stdin=subprocess.PIPE if payload is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_environment(),
            bufsize=0,
            **(
                {"creationflags": subprocess.CREATE_NO_WINDOW}
                if os.name == "nt"
                else {}
            ),
        )
    except FileNotFoundError:
        raise GitHubError(
            "gh_missing", "Install GitHub CLI (gh) on the execution device."
        ) from None
    except OSError:
        raise GitHubError(
            "github_cli_start_failed", "GitHub CLI could not be started."
        ) from None

    output, diagnostic = bytearray(), bytearray()
    overflow = threading.Event()
    io_failed = threading.Event()

    def read(stream, target, limit):
        try:
            while True:
                chunk = stream.read(min(65536, limit + 1 - len(target)))
                if not chunk:
                    return
                target.extend(chunk)
                if len(target) > limit:
                    overflow.set()
                    _stop(process)
                    return
        except (OSError, ValueError):
            io_failed.set()
            _stop(process)

    def write():
        try:
            view = memoryview(payload)
            while view:
                count = process.stdin.write(view)
                if not count:
                    raise OSError()
                view = view[count:]
        except (OSError, ValueError):
            io_failed.set()
        finally:
            try:
                process.stdin.close()
            except OSError:
                pass

    threads = [
        threading.Thread(
            target=read, args=(process.stdout, output, MAX_STDOUT), daemon=True
        ),
        threading.Thread(
            target=read, args=(process.stderr, diagnostic, MAX_STDERR), daemon=True
        ),
    ]
    if payload is not None:
        threads.append(threading.Thread(target=write, daemon=True))
    try:
        for thread in threads:
            thread.start()
        try:
            returncode = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _stop(process)
            raise GitHubError(
                "github_cli_timeout",
                "GitHub CLI request timed out; it was not automatically retried.",
            ) from None
        for thread in threads:
            thread.join(timeout=2)
        if overflow.is_set():
            raise GitHubError(
                "response_too_large",
                "GitHub CLI output exceeded its safety limit; narrow the request or use pagination.",
            )
        if any(thread.is_alive() for thread in threads):
            raise GitHubError(
                "github_cli_io_failed", "GitHub CLI did not close its request streams."
            )
        if returncode != 0:
            raise _failure(returncode, bytes(diagnostic))
        if io_failed.is_set():
            raise GitHubError(
                "github_cli_io_failed",
                "GitHub CLI could not complete its request streams.",
            )
        return bytes(output)
    finally:
        _stop(process)
        try:
            process.wait(timeout=2)
        except (subprocess.TimeoutExpired, OSError):
            pass
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass
        for thread in threads:
            if thread.ident is not None:
                thread.join(timeout=0.2)


class GitHubClient:
    def __init__(self, *, timeout=TIMEOUT):
        self.timeout = timeout

    def _request(self, method, path, query=None, body=None, accept=None):
        if type(method) is not str or method not in _METHODS:
            _invalid("Unsupported GitHub API method.")
        if accept is not None and (type(accept) is not str or accept not in _ACCEPTS):
            _invalid("Unsupported GitHub response format.")
        endpoint = _endpoint(path, query)
        if body is not None and (method == "GET" or type(body) is not dict):
            _invalid("GitHub write bodies must be JSON objects.")
        arguments = [
            "api",
            endpoint,
            "--hostname",
            "github.com",
            "--method",
            method,
            "--header",
            "Accept: " + (accept or "application/vnd.github+json"),
            "--header",
            "X-GitHub-Api-Version: 2022-11-28",
        ]
        payload = None
        if body is not None:
            try:
                payload = json.dumps(body, ensure_ascii=False, allow_nan=False).encode(
                    "utf-8"
                )
            except (ValueError, TypeError, UnicodeError, RecursionError):
                _invalid("GitHub request body must be valid JSON.")
            if len(payload) > MAX_INPUT:
                _invalid("GitHub request body exceeds 2 MiB.")
            arguments.extend(
                ["--input", "-", "--header", "Content-Type: application/json"]
            )
        return _run(arguments, payload, timeout=self.timeout)

    def request(self, method, path, query=None, body=None, accept=None):
        raw = self._request(method, path, query, body, accept)
        if not raw:
            return None
        text = raw.decode("utf-8", errors="replace")
        try:
            return json.loads(text)
        except ValueError:
            return text

    def job_logs(self, path):
        # gh follows GitHub's signed log redirect itself and strips authentication
        # on cross-host redirects. Never export a token or signed Location header.
        if type(path) is not str or not re.fullmatch(
            r"/repos/[A-Za-z0-9-]+/[A-Za-z0-9_.-]+/actions/jobs/[1-9][0-9]*/logs", path
        ):
            _invalid("Invalid GitHub job-log endpoint.")
        return self._request("GET", path, accept="text/plain").decode(
            "utf-8", errors="replace"
        )


def health():
    """Check the same active gh profile as Wework, without printing token data."""
    try:
        _run(["api", "user", "--silent", "--hostname", "github.com", "--method", "GET"])
        return {"status": "ok"}
    except GitHubError as error:
        return {
            "status": "need_login" if error.code == "github_auth_required" else "error",
            "code": error.code,
            "hint": error.message,
        }

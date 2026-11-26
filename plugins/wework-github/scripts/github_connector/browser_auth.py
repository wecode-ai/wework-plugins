"""Own a short-lived loopback authorization page; emit no process output."""

from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import math
import os
import re
import secrets
import signal
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from . import auth, auth_logout
from .errors import GitHubError

MAX_HEADERS = 32
MAX_HEADER_BYTES = 8192
MAX_BODY_BYTES = 1024
MAX_CONNECTIONS = 8
TERMINAL = frozenset({"ok", "error", "expired", "cancelled"})
TITLE = "GitHub（Wework）"
DESCRIPTION = "使用 GitHub CLI 官方授权，与当前执行设备上的 gh 共用活动账号。"
LOGOUT_DESCRIPTION = (
    "登录、切换或退出会同时影响本插件、原 GitHub 入口和同一配置下的终端 gh。"
    "退出只移除本机所选账号凭据，不撤销 GitHub 网站授权，也不删除其他保存的账号。"
    "卸载插件不会退出共享账号。"
)
_ASSET = Path(__file__).resolve().parents[2] / "assets" / "auth.html"


def _system_open(address):
    # Configured browser commands can print the capability URL. A separate
    # bounded helper isolates their OS file descriptors from the MCP stream.
    try:
        environment = {
            key: value
            for key, value in os.environ.items()
            if key.upper()
            not in {
                "GH_TOKEN",
                "GITHUB_TOKEN",
                "GH_ENTERPRISE_TOKEN",
                "GITHUB_ENTERPRISE_TOKEN",
            }
        }
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys, webbrowser; sys.exit(0 if webbrowser.open(sys.argv[1]) else 1)",
                address,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=4,
            check=False,
            env=environment,
            **(
                {"creationflags": subprocess.CREATE_NO_WINDOW}
                if sys.platform == "win32"
                else {}
            ),
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _text(value, limit=1000):
    if not isinstance(value, str):
        return None
    return "".join(c for c in value[:limit] if ord(c) >= 32 and ord(c) != 127)


def _github_url(value):
    if not isinstance(value, str) or len(value) > 2048:
        return None
    if any(ord(c) <= 32 or ord(c) == 127 for c in value) or any(
        c in value for c in "\\?#"
    ):
        return None
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme == "https"
            and parsed.netloc.lower() == "github.com"
            and parsed.path in {"/login/device", "/login/device/"}
        ):
            return "https://github.com/login/device"
    except ValueError:
        pass
    return None


def _safe_event(value, *, final=False):
    if not isinstance(value, dict):
        return {}
    status = value.get("status")
    allowed = (
        TERMINAL if final else TERMINAL | {"preparing", "waiting_browser", "verifying"}
    )
    result = {"status": status} if status in allowed else {}
    for key in ("title", "description", "hint", "logoutDescription"):
        text = _text(value.get(key), 120 if key == "title" else 1000)
        if text is not None:
            result[key] = text
    for key in ("errorCode", "code"):
        code = value.get(key)
        if isinstance(code, str) and re.fullmatch(r"[a-z0-9_]{1,80}", code):
            result[key] = code
    for key in ("changed", "accountRevoked", "connected"):
        if isinstance(value.get(key), bool):
            result[key] = value[key]
    if not final and status == "waiting_browser":
        code = value.get("userCode")
        url = _github_url(value.get("verificationUrl"))
        if (
            isinstance(code, str)
            and re.fullmatch(r"[A-Z0-9]{4}-[A-Z0-9]{4}", code)
            and url
        ):
            result.update(userCode=code, verificationUrl=url)
    return result


def _failure(code, hint, *, status="error"):
    return {
        "status": status,
        "code": code,
        "errorCode": code,
        "hint": hint,
        "title": TITLE,
        "description": DESCRIPTION,
        "logoutDescription": LOGOUT_DESCRIPTION,
    }


def _page():
    raw = _ASSET.read_bytes()
    if len(raw) > 128 * 1024:
        raise ValueError("Authorization asset is too large")
    text = raw.decode("utf-8")
    directives = [
        "default-src 'none'",
        "connect-src 'self'",
        "base-uri 'none'",
        "form-action 'none'",
        "frame-ancestors 'none'",
    ]
    for tag, directive in (("script", "script-src"), ("style", "style-src")):
        parts = re.findall(rf"<{tag}>([\s\S]*?)</{tag}>", text)
        hashes = [
            "'sha256-"
            + base64.b64encode(hashlib.sha256(part.encode()).digest()).decode()
            + "'"
            for part in parts
        ]
        directives.append(directive + " " + " ".join(hashes or ["'none'"]))
    return raw, "; ".join(directives)


class _Session:
    def __init__(self, action, login, logout, deadline, confirmation_deadline, popen):
        self.action = action
        self.login = login
        self.logout = logout
        self.deadline = deadline
        self.confirmation_deadline = confirmation_deadline
        self.popen = popen
        self.secret = secrets.token_urlsafe(32)
        self.csrf = secrets.token_urlsafe(32)
        self.lock = threading.Lock()
        self.cancel = threading.Event()
        self.finished = threading.Event()
        self.terminal_seen = threading.Event()
        self.started = False
        self.worker = None
        self.result = None
        self.state = {
            "status": "awaiting_confirmation",
            "title": TITLE,
            "description": DESCRIPTION,
            "logoutDescription": LOGOUT_DESCRIPTION,
            "hint": "请确认共享账号影响后继续。",
        }

    def snapshot(self):
        with self.lock:
            return {
                **self.state,
                "action": self.action,
                "csrfToken": self.csrf,
                "started": self.started,
                "done": self.finished.is_set(),
                "secondsRemaining": max(
                    0,
                    math.ceil(
                        (self.deadline if self.started else self.confirmation_deadline)
                        - time.monotonic()
                    ),
                ),
            }

    def on_event(self, value):
        safe = _safe_event(value)
        with self.lock:
            if not self.finished.is_set():
                self.state.update(safe)
                if safe.get("status") != "waiting_browser" or "userCode" not in safe:
                    self.state.pop("userCode", None)
                    self.state.pop("verificationUrl", None)

    def finish(self, value):
        safe = _safe_event(value, final=True)
        if safe.get("status") not in TERMINAL:
            safe = _failure(
                "gh_browser_auth_failed", "此次账号操作未完成，请重新检查连接状态。"
            )
        if "hint" not in safe:
            if safe["status"] == "ok":
                safe["hint"] = (
                    "账号操作已完成；仍有已保存的账号可连接，请返回 Wework 检查当前身份。"
                    if safe.get("connected") is True
                    else "账号操作已完成，请返回 Wework 检查连接状态。"
                )
            elif safe.get("accountRevoked"):
                safe["hint"] = (
                    "本机退出已生效，但后续检查未完成。请刷新连接状态，不要重复退出其他账号。"
                )
            else:
                safe["hint"] = "此次账号操作已结束，请返回 Wework 检查实际连接状态。"
        with self.lock:
            if self.finished.is_set():
                return
            self.result = {
                "title": TITLE,
                "description": DESCRIPTION,
                "logoutDescription": LOGOUT_DESCRIPTION,
                **safe,
            }
            self.state = dict(self.result)
            self.finished.set()

    def cancel_before_start(self):
        self.cancel.set()
        with self.lock:
            started = self.started
        if not started:
            self.finish(
                _failure(
                    "gh_browser_auth_cancelled",
                    "已取消此次操作；未启动登录或退出。",
                    status="cancelled",
                )
            )

    def confirm(self):
        with self.lock:
            if self.started or self.finished.is_set() or self.cancel.is_set():
                return False
            if time.monotonic() >= self.confirmation_deadline:
                return False
            self.started = True
            self.state.update(status="preparing", hint="正在处理账号操作。")
            self.worker = threading.Thread(target=self._execute, daemon=True)
            try:
                self.worker.start()
                return True
            except (RuntimeError, OSError):
                self.worker = None
                self.started = False
        self.finish(
            _failure(
                "gh_browser_auth_failed", "无法启动此次账号操作；未启动登录或退出。"
            )
        )
        return False

    def _execute(self):
        try:
            if self.action == "login":
                value = self.login(
                    on_event=self.on_event,
                    cancel_event=self.cancel,
                    timeout=max(0, self.deadline - time.monotonic()),
                    popen=self.popen,
                )
            else:
                # Once confirmed, logout must report its real bounded result.
                # Cancelling a partially committed mutation cannot undo it.
                value = self.logout()
            self.finish(value)
        except Exception:  # noqa: BLE001 - Never expose provider or adapter details.
            self.finish(
                _failure(
                    "gh_browser_auth_failed", "此次账号操作未完成，请检查连接状态。"
                )
            )


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False
    allow_reuse_address = False

    def __init__(self, *args, **kwargs):
        self.slots = threading.BoundedSemaphore(MAX_CONNECTIONS)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        # Do not block the accept loop behind a queue of idle local sockets.
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            self.shutdown_request(request)
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def handle_error(self, _request, _client_address):
        # Paths and response content can contain a device code or local secret.
        pass


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    server_version = "LocalAuth"
    sys_version = ""

    def setup(self):
        super().setup()
        self.connection.settimeout(2)

    def log_message(self, _format, *_args):
        pass

    def send_error(self, code, message=None, explain=None):
        self._respond(code)

    def parse_request(self):
        if len(self.raw_requestline) > 2048 or len(self.raw_requestline.split()) != 3:
            self.close_connection = True
            return False
        headers = []
        total = 0
        for _ in range(MAX_HEADERS + 1):
            line = self.rfile.readline(MAX_HEADER_BYTES + 1)
            total += len(line)
            if len(line) > MAX_HEADER_BYTES or total > MAX_HEADER_BYTES:
                self.close_connection = True
                return False
            headers.append(line)
            if line in (b"\r\n", b"\n"):
                break
            if not line or len(headers) > MAX_HEADERS:
                self.close_connection = True
                return False
        original = self.rfile
        self.rfile = io.BytesIO(b"".join(headers))
        try:
            return super().parse_request()
        finally:
            self.rfile = original

    def handle_expect_100(self):
        self.send_error(417)
        return False

    def _respond(
        self, status, body=b"", content_type="application/json; charset=utf-8"
    ):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Content-Security-Policy", self.server.page_csp)
        self.end_headers()
        self.wfile.write(body)

    def _route(self):
        if self.headers.get_all("Host", []) != [self.server.authority]:
            self._respond(403)
            return None
        origins = self.headers.get_all("Origin", [])
        if origins and origins != [self.server.origin]:
            self._respond(403)
            return None
        if self.headers.get("Transfer-Encoding") is not None:
            self._respond(400)
            return None
        prefix = "/" + self.server.session.secret + "/"
        if not self.path.startswith(prefix):
            self._respond(404)
            return None
        route = self.path[len(prefix) :]
        if route not in {"", "state", "confirm", "cancel", "ack"}:
            self._respond(404)
            return None
        return route

    def do_GET(self):
        route = self._route()
        if route is None:
            return
        if self.headers.get("Content-Length") not in (None, "0"):
            self._respond(400)
        elif route == "":
            self._respond(200, self.server.page, "text/html; charset=utf-8")
        elif route == "state":
            with self.server.session.lock:
                self.server.session.last_seen = time.monotonic()
            state = self.server.session.snapshot()
            self._respond(200, json.dumps(state, ensure_ascii=False).encode())
        else:
            self._respond(405)

    def do_POST(self):
        route = self._route()
        if route is None:
            return
        if route not in {"confirm", "cancel", "ack"}:
            self._respond(405)
            return
        if self.headers.get_all("Origin", []) != [self.server.origin]:
            self._respond(403)
            return
        lengths = self.headers.get_all("Content-Length", [])
        if len(lengths) != 1 or not re.fullmatch(r"[0-9]{1,4}", lengths[0]):
            self._respond(400)
            return
        length = int(lengths[0])
        if length > MAX_BODY_BYTES:
            self._respond(413)
            return
        if self.headers.get_all("Content-Type", []) != ["application/json"]:
            self._respond(415)
            return
        try:
            raw = self.rfile.read(length)
            value = json.loads(raw)
            token = value.get("csrfToken") if isinstance(value, dict) else None
            if (
                set(value) != {"csrfToken"}
                or not isinstance(token, str)
                or not hmac.compare_digest(token, self.server.session.csrf)
            ):
                self._respond(403)
                return
        except (ValueError, TypeError, OSError):
            self._respond(400)
            return
        if route == "confirm":
            accepted = self.server.session.confirm()
            self._respond(202 if accepted else 409, b"{}")
        elif route == "ack":
            self._respond(200, b"{}")
            if self.server.session.finished.is_set():
                self.server.session.terminal_seen.set()
        else:
            self.server.session.cancel_before_start()
            self._respond(200, b"{}")


def _budget(timeout):
    total = 600 if timeout is None else timeout
    if not isinstance(total, (float, int)) or not math.isfinite(total):
        raise ValueError("Invalid authorization timeout")
    return max(0, min(float(total), 600))


class BrowserSession:
    """Own one local page until completion, cancellation or expiry.

    Its public ID is unrelated to either local HTTP secret. Calls to close wait
    for the reusable authentication core to reap its owned process tree.
    """

    def __init__(
        self,
        action="login",
        *,
        opener=None,
        login=None,
        logout=None,
        timeout=None,
        parent=None,
        popen=None,
        lease=120,
    ):
        if action not in {"login", "logout"}:
            raise GitHubError("gh_browser_auth_invalid", "不支持的账号操作。")
        self.session_id = str(uuid.uuid4())
        self.server = None
        self.server_started = False
        self.observer = parent
        self.session = None
        self.monitor = None
        self.monitor_started = False
        self.stop = threading.Event()
        self.closed = threading.Event()
        self.lease = lease
        self._initialize(action, opener, login, logout, timeout, popen)

    def _initialize(self, action, opener, login, logout, timeout, popen):
        try:
            total = _budget(timeout)
            if total <= 0 or (action == "logout" and total <= 23):
                raise GitHubError(
                    "gh_browser_auth_expired", "本次操作的确认时间不足；未修改账号。"
                )
            page, csp = _page()
            deadline = time.monotonic() + total
            # Reserve logout's bounded 20 seconds plus cleanup before expiry.
            confirm_deadline = deadline - 23 if action == "logout" else deadline
            self.session = _Session(
                action,
                login or auth.run_login,
                logout or auth_logout.logout,
                deadline,
                confirm_deadline,
                popen,
            )
            self.session.last_seen = None
            self.observer = self.observer or auth.parent_watch()
            if not self.observer.alive():
                raise GitHubError(
                    "gh_browser_auth_cancelled", "授权窗口已关闭；未启动账号操作。"
                )
            self.server = _Server(("127.0.0.1", 0), _Handler)
            self.server.session = self.session
            self.server.page = page
            self.server.page_csp = csp
            self.server.authority = f"127.0.0.1:{self.server.server_port}"
            self.server.origin = "http://" + self.server.authority
            address = f"{self.server.origin}/{self.session.secret}/"
            threading.Thread(
                target=self.server.serve_forever,
                kwargs={"poll_interval": 0.05},
                daemon=True,
            ).start()
            self.server_started = True
            self._open(opener or _system_open, address)
            self.monitor = threading.Thread(target=self._monitor, daemon=True)
            self.monitor.start()
            self.monitor_started = True
        except (
            BaseException
        ) as error:  # Cleanup also precedes signal-driven exit during construction.
            self.close()
            if not isinstance(error, Exception):
                raise
            if isinstance(error, GitHubError):
                raise
            raise GitHubError(
                "gh_browser_auth_failed", "无法准备本机账号管理页，请重试。"
            ) from None

    def _open(self, opener, address):
        completed = threading.Event()
        accepted = []

        def invoke():
            try:
                accepted.append(opener(address) is not False)
            except Exception:  # noqa: BLE001
                accepted.append(False)
            finally:
                completed.set()

        threading.Thread(target=invoke, daemon=True).start()
        if not completed.wait(5) or not accepted or not accepted[0]:
            raise GitHubError(
                "gh_browser_open_failed",
                "无法打开本机账号管理页。请在具有浏览器的执行设备上重试。",
            )

    def _monitor(self):
        try:
            while not self.session.finished.wait(0.05):
                if self.stop.is_set() or not self.observer.alive():
                    self.session.cancel_before_start()
                now = time.monotonic()
                with self.session.lock:
                    started = self.session.started
                    last_seen = self.session.last_seen
                if last_seen is not None and now - last_seen >= self.lease:
                    self.session.cancel_before_start()
                if not started and now >= self.session.confirmation_deadline:
                    self.session.finish(
                        _failure(
                            "gh_browser_auth_expired",
                            "确认时间已结束；未启动登录或退出。",
                            status="expired",
                        )
                    )
                elif started and now >= self.session.deadline:
                    self.session.cancel.set()
            # The terminal GET is followed by an explicit page acknowledgment.
            # A disconnected browser cannot retain the server indefinitely.
            terminal_deadline = time.monotonic() + 120
            while not self.stop.is_set() and not self.session.terminal_seen.wait(0.05):
                if not self.observer.alive() or time.monotonic() >= min(
                    self.session.deadline, terminal_deadline
                ):
                    break
        except Exception:  # noqa: BLE001
            self.session.cancel_before_start()
        finally:
            self.session.cancel.set()
            if self.session.worker is not None:
                self.session.worker.join()
            self._shutdown()

    def _shutdown(self):
        if self.server is not None:
            if self.server_started:
                self.server.shutdown()
            self.server.server_close()
        if self.observer is not None:
            self.observer.close()
        self.closed.set()

    def public_result(self):
        return {
            "status": "browser_opened",
            "sessionId": self.session_id,
            "title": TITLE,
            "hint": "请在已打开的本机页面确认账号操作。页面打开不表示登录或退出已完成。",
        }

    def outcome(self):
        """Return a completed safe result without waiting for the page's ack."""
        with self.session.lock:
            return dict(self.session.result) if self.session.finished.is_set() else None

    def wait(self):
        self.closed.wait()
        return dict(
            self.session.result
            or _failure(
                "gh_browser_auth_failed", "此次账号操作未完成，请检查连接状态。"
            )
        )

    def close(self):
        self.stop.set()
        if self.session is not None:
            self.session.cancel_before_start()
        if self.monitor_started:
            self.closed.wait()
        else:
            if self.session is not None and self.session.worker is not None:
                self.session.worker.join()
            self._shutdown()


def start(action="login", **kwargs):
    """Open an owned page; callers must close it when their MCP process stops."""
    return BrowserSession(action, **kwargs)


def run(action="login", **kwargs):
    """Synchronous CLI adapter; its entry point prints only the final result."""
    session = None
    handlers = {}
    try:
        if threading.current_thread() is threading.main_thread():

            def stop_session(_number, _frame):
                if session is None:
                    # __init__ cleans up before this leaves the construction.
                    raise KeyboardInterrupt
                session.stop.set()
                session.session.cancel_before_start()

            for signum in (signal.SIGINT, signal.SIGTERM):
                handlers[signum] = signal.getsignal(signum)
                signal.signal(signum, stop_session)
        session = start(action, **kwargs)
        return session.wait()
    except KeyboardInterrupt:
        return _failure(
            "gh_browser_auth_cancelled",
            "已停止此次账号操作，请检查实际连接状态。",
            status="cancelled",
        )
    except GitHubError as error:
        return _failure(error.code, error.message)
    finally:
        if session is not None:
            session.close()
        for signum, handler in handlers.items():
            signal.signal(signum, handler)

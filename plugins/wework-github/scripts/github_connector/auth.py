"""Plugin-owned, cancellable GitHub CLI login with safe callback events."""

from __future__ import annotations

import math
import os
import queue
import signal
import subprocess
import threading
import time
import uuid

from . import cli, owned_process
from .auth_prompt import PromptParser
from .auth_state import account_lock
from .errors import GitHubError

TITLE = "GitHub（Wework）"
DESCRIPTION = "使用 GitHub CLI 官方授权；与执行设备上的 gh 及原 GitHub 入口共享账号。"
MAX_OUTPUT = 65536
MAX_DRAIN_SECONDS = 2
_HINTS = {
    "preparing": "正在准备 GitHub 官方授权。",
    "waiting_browser": "请打开 GitHub 官方页面，输入一次性验证码并完成授权。",
    "verifying": "正在确认此设备上的 GitHub 连接。",
    "ok": "GitHub 已连接；账号与此设备上的 gh 共享。",
    "gh_login_cancelled": "已停止此次授权；若刚完成 GitHub 确认，请刷新连接状态。取消不会撤销已保存的登录。",
    "gh_login_timeout": "此次授权已超时，请重新连接并使用新的验证码。",
    "gh_login_env_token": "此设备的 GH_TOKEN 或 GITHUB_TOKEN 正在提供凭据。请先移除该环境设置，再通过浏览器登录。",
    "gh_login_in_progress": "另一个插件正在操作此 GitHub CLI 账号配置。请先完成或取消该操作。",
    "gh_missing": "请先在执行设备上安装 GitHub CLI（gh）。",
    "gh_login_failed": "GitHub CLI 未完成授权。请检查网络后重新连接。",
    "gh_login_verification_failed": "授权结束，但尚无法确认连接。请检查网络并重新检查连接状态。",
    "gh_login_output_limit": "GitHub CLI 输出超过安全限制，此次授权已停止。",
    "gh_login_parent_unavailable": "授权窗口已关闭或无法监控，此次授权已停止。",
    "gh_auth_profile_invalid": "GitHub CLI 账号配置目录无效，请检查执行设备上的配置。",
    "gh_auth_lock_failed": "无法锁定 GitHub CLI 账号配置，请检查执行设备上的目录权限。",
}


class _Stopped(Exception):
    def __init__(self, status, code):
        super().__init__(code)
        self.status = status
        self.code = code


class _Control:
    def __init__(self, cancel_event=None):
        self.stopped = threading.Event()
        self.cancel_event = cancel_event
        self.reason = ("cancelled", "gh_login_cancelled")

    def request(self, status="cancelled", code="gh_login_cancelled"):
        if not self.stopped.is_set():
            self.reason = (status, code)
            self.stopped.set()

    def check(self, deadline, parent):
        if self.cancel_event is not None and self.cancel_event.is_set():
            self.request()
        if self.stopped.is_set():
            raise _Stopped(*self.reason)
        if not parent.alive():
            raise _Stopped("cancelled", "gh_login_parent_unavailable")
        if time.monotonic() >= deadline:
            raise _Stopped("expired", "gh_login_timeout")


class _ParentWatch:
    """Observe the launcher without os.kill(pid, 0) on Windows (which kills)."""

    def __init__(self, environment):
        try:
            self.pid = int(environment.get("WEWORK_GITHUB_PARENT_PID", os.getppid()))
            if self.pid <= 0 or self.pid == os.getpid() or self.pid > 0xFFFFFFFF:
                raise ValueError
        except (TypeError, ValueError):
            raise GitHubError(
                "gh_login_parent_unavailable", "Invalid parent process."
            ) from None
        self.direct_parent = self.pid == os.getppid()
        self.handle = None
        self.kernel = None
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            self.kernel.OpenProcess.argtypes = [
                wintypes.DWORD,
                wintypes.BOOL,
                wintypes.DWORD,
            ]
            self.kernel.OpenProcess.restype = wintypes.HANDLE
            self.kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            self.kernel.WaitForSingleObject.restype = wintypes.DWORD
            self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            self.kernel.CloseHandle.restype = wintypes.BOOL
            self.handle = self.kernel.OpenProcess(0x00100000, False, self.pid)
            if not self.handle:
                raise GitHubError(
                    "gh_login_parent_unavailable", "Cannot observe parent."
                )

    def alive(self):
        if self.kernel is not None:
            return self.kernel.WaitForSingleObject(self.handle, 0) == 258
        if self.direct_parent and os.getppid() != self.pid:
            return False
        try:
            os.kill(self.pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False
        return True

    def close(self):
        if self.handle is not None:
            self.kernel.CloseHandle(self.handle)
            self.handle = None

    def __enter__(self):
        return self

    def __exit__(self, *_exception):
        self.close()


def parent_watch(environment=None):
    """Return the shared cross-platform parent observer; call close() on exit.

    The plugin's local browser server can use this same observer before a login
    begins, without duplicating Windows process-handling or reading stdin.
    """
    return _ParentWatch(os.environ if environment is None else environment)


def _reap(process, threads):
    try:
        owned_process.cleanup(process)
    finally:
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass
        for thread in threads:
            if thread.ident is not None:
                thread.join(timeout=0.2)


def _run(
    arguments, *, environment, deadline, parent, control, emit, popen, prompt=False
):
    control.check(deadline, parent)
    try:
        process = owned_process.spawn(
            ["gh", *arguments],
            popen=popen,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            bufsize=0,
        )
    except FileNotFoundError:
        raise _Stopped("error", "gh_missing") from None
    except OSError:
        raise _Stopped("error", "gh_login_failed") from None
    chunks = queue.Queue(maxsize=32)
    reader_stop = threading.Event()
    parser = PromptParser() if prompt else None

    def put(item):
        while not reader_stop.is_set():
            try:
                chunks.put(item, timeout=0.05)
                return
            except queue.Full:
                continue

    def read(stream, name):
        total = 0
        try:
            while not reader_stop.is_set():
                chunk = stream.read(4096)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_OUTPUT:
                    control.request("error", "gh_login_output_limit")
                    return
                put((name, chunk))
            put((name, None))
        except (OSError, ValueError):
            if not reader_stop.is_set():
                control.request("error", "gh_login_failed")

    threads = [
        threading.Thread(target=read, args=(process.stdout, "stdout"), daemon=True),
        threading.Thread(target=read, args=(process.stderr, "stderr"), daemon=True),
    ]
    try:
        # The non-TTY web flow only prints its URL. No interactive responses or
        # browser commands are sent to gh, and no PAT is read from stdin.
        process.stdin.close()
        for thread in threads:
            thread.start()
        ended = set()
        drain_deadline = None
        while True:
            control.check(deadline, parent)
            if process.poll() is not None:
                if drain_deadline is None:
                    # A successful leader can leave descendants holding both
                    # pipes open. Clean its registered tree before waiting for
                    # EOF, then drain queued bytes so prompt fragments survive.
                    owned_process.cleanup(process)
                    drain_deadline = time.monotonic() + MAX_DRAIN_SECONDS
                if len(ended) == 2:
                    return process.wait(timeout=1)
                if time.monotonic() >= drain_deadline:
                    raise _Stopped("error", "gh_login_failed")
            try:
                name, chunk = chunks.get(
                    timeout=min(0.05, max(0.001, deadline - time.monotonic()))
                )
            except queue.Empty:
                continue
            if chunk is None:
                ended.add(name)
            if parser is not None:
                result = (
                    parser.finish(stream=name)
                    if chunk is None
                    else parser.feed(chunk, stream=name)
                )
                if result is not None:
                    emit(
                        "waiting_browser",
                        userCode=result["userCode"],
                        verificationUrl=result["verificationUrl"],
                    )
    finally:
        reader_stop.set()
        _reap(process, threads)


def _execute_login(*, on_event, control, timeout, popen):
    """Run one safe callback lifecycle for the plugin-owned browser UI."""
    session_id = str(uuid.uuid4())
    parent = None
    handlers = {}
    status, code = "error", "gh_login_failed"

    def event_value(event_status, *, error_code=None, **fields):
        value = {
            "status": event_status,
            "sessionId": session_id,
            "title": TITLE,
            "description": DESCRIPTION,
            "hint": _HINTS.get(error_code or event_status, _HINTS["gh_login_failed"]),
            **fields,
        }
        if error_code is not None:
            value["errorCode"] = error_code
            value["code"] = error_code
        return value

    def emit(event_status, **fields):
        # A UI owns its copy; mutation by a callback cannot alter the result.
        on_event(event_value(event_status, **fields))

    try:
        emit("preparing")
        environment = cli._environment()
        if environment.get("GH_TOKEN") or environment.get("GITHUB_TOKEN"):
            raise _Stopped("error", "gh_login_env_token")
        if not isinstance(timeout, (int, float)) or not math.isfinite(timeout):
            raise _Stopped("error", "gh_login_failed")
        deadline = time.monotonic() + max(0, min(timeout, 600))
        parent = _ParentWatch(environment)
        if threading.current_thread() is threading.main_thread():
            for signum in (signal.SIGINT, signal.SIGTERM):
                handlers[signum] = signal.getsignal(signum)
                signal.signal(signum, lambda _number, _frame: control.request())
        with account_lock(environment=environment):
            runner = {
                "environment": environment,
                "deadline": deadline,
                "parent": parent,
                "control": control,
                "emit": emit,
                "popen": popen or subprocess.Popen,
            }
            result = _run(
                [
                    "auth",
                    "login",
                    "--hostname",
                    "github.com",
                    "--web",
                    "--git-protocol",
                    "https",
                ],
                prompt=True,
                **runner,
            )
            if result != 0:
                raise _Stopped("error", "gh_login_failed")
            emit("verifying")
            result = _run(
                [
                    "api",
                    "user",
                    "--silent",
                    "--hostname",
                    "github.com",
                    "--method",
                    "GET",
                ],
                **runner,
            )
            if result != 0:
                raise _Stopped("error", "gh_login_verification_failed")
            control.check(deadline, parent)
            status, code = "ok", None
    except _Stopped as stopped:
        status, code = stopped.status, stopped.code
    except GitHubError as error:
        status, code = (
            "error",
            error.code if error.code in _HINTS else "gh_login_failed",
        )
    # Authentication is an output boundary: unexpected OS/adapter failures must
    # never turn provider diagnostics or exception values into an event/traceback.
    except Exception:  # noqa: BLE001
        status, code = "error", "gh_login_failed"
    finally:
        for signum, handler in handlers.items():
            signal.signal(signum, handler)
        if parent is not None:
            parent.close()
    # Cleanup and account-lock release precede both terminal delivery and return.
    # Closing the local page after this result cannot abandon an owned gh child.
    terminal = event_value(status, error_code=code)
    try:
        on_event(dict(terminal))
    except Exception:  # noqa: BLE001 - UI delivery is a safe output boundary.
        pass
    return terminal


def run_login(*, on_event, cancel_event, timeout=600, popen=None):
    """Run the reusable login core and return its safe final event.

    ``on_event(dict)`` receives preparing/browser/verifying and terminal states.
    ``cancel_event`` is a threading.Event controlled by the plugin's local UI;
    it remains observable during login and verification. The call blocks until
    owned gh processes have been cleaned up and the profile lock is released.
    No stdin pipe or host-specific authentication protocol is required.
    """
    return _execute_login(
        on_event=on_event,
        control=_Control(cancel_event),
        timeout=timeout,
        popen=popen,
    )

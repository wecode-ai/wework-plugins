"""Own browser authentication sessions for the lifetime of the MCP process."""

import collections
import threading
import time
import uuid

from . import browser_auth
from .errors import GitHubError

MAX_ACTIVE = 3
MAX_HISTORY = 20
TERMINAL = {"ok", "error", "expired", "cancelled"}

DEFINITIONS = [
    {
        "name": "open_account_manager",
        "description": "Open this plugin's local browser page to sign in or confirm sign-out of the shared GitHub CLI account. This only opens a page; inspect get_account_manager_status for the actual result.",
        "inputSchema": {
            "type": "object",
            "properties": {"action": {"type": "string", "enum": ["login", "logout"]}},
            "required": ["action"],
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "openWorldHint": False,
        },
    },
    {
        "name": "get_account_manager_status",
        "description": "Check an opened account page without exposing its URL or device code. Optionally wait up to 20 seconds for completion; browser_opened does not mean authentication succeeded.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "sessionId": {"type": "string"},
                "wait_seconds": {"type": "integer", "minimum": 0, "maximum": 20},
            },
            "required": ["sessionId"],
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "openWorldHint": False,
        },
    },
    {
        "name": "close_account_manager",
        "description": "Close this plugin's account page when the user requests cancellation. Cancellation cannot undo a login or sign-out already performed; inspect the returned result.",
        "inputSchema": {
            "type": "object",
            "properties": {"sessionId": {"type": "string"}},
            "required": ["sessionId"],
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "openWorldHint": False,
        },
    },
]


def _terminal(value):
    if type(value) is not dict or value.get("status") not in TERMINAL:
        return {
            "status": "error",
            "code": "github_account_manager_failed",
            "hint": "The account page did not return a verified result.",
        }
    # The capability URL and one-time code are confined to the browser page.
    return {
        key: value[key]
        for key in (
            "status",
            "title",
            "description",
            "hint",
            "errorCode",
            "code",
            "changed",
            "accountRevoked",
            "connected",
        )
        if key in value
    }


class AccountManagers:
    def __init__(self, *, start=None):
        self._start = start or browser_auth.start
        self._lock = threading.RLock()
        self._sessions = collections.OrderedDict()
        self._closing = False

    def open(self, action):
        if type(action) is not str or action not in {"login", "logout"}:
            raise GitHubError("invalid_arguments", "Choose login or logout.")
        with self._lock:
            if self._closing:
                raise GitHubError(
                    "github_account_manager_closed", "The GitHub plugin is stopping."
                )
            if (
                sum(not record["done"].is_set() for record in self._sessions.values())
                >= MAX_ACTIVE
            ):
                raise GitHubError(
                    "github_account_manager_busy",
                    "Close an existing GitHub account page before opening another.",
                )
            # start() must clean up if opening the browser fails or is interrupted.
            session = self._start(action)
            if self._closing:
                session.close()
                raise GitHubError(
                    "github_account_manager_closed", "The GitHub plugin is stopping."
                )
            session_id = str(uuid.uuid4())
            record = {
                "session": session,
                "done": threading.Event(),
                "result": None,
                "action": action,
            }
            self._sessions[session_id] = record
            self._trim()
            monitor = threading.Thread(target=self._finish, args=(record,), daemon=True)
            try:
                monitor.start()
            except BaseException:
                self._sessions.pop(session_id, None)
                session.close()
                raise
        return {
            "status": "browser_opened",
            "sessionId": session_id,
            "action": action,
            "hint": "请在执行设备的浏览器账户页完成操作；页面已打开不代表登录或退出成功。",
        }

    def _finish(self, record):
        try:
            result = _terminal(record["session"].wait())
        except Exception:  # noqa: BLE001 - No local URL or provider diagnostics may reach the model.
            result = _terminal(None)
        finally:
            try:
                record["session"].close()
            except Exception:  # noqa: BLE001
                result = {
                    **result,
                    "status": "error",
                    "code": "github_account_cleanup_failed",
                    "hint": "Account-page cleanup failed. Refresh the connection status before another operation.",
                }
        with self._lock:
            record["result"] = result
            record["done"].set()
            self._trim()

    def _trim(self):
        while len(self._sessions) > MAX_HISTORY:
            key = next(
                (
                    key
                    for key, value in self._sessions.items()
                    if value["done"].is_set()
                ),
                None,
            )
            if key is None:
                break
            self._sessions.pop(key)

    def _record(self, session_id):
        try:
            if type(session_id) is not str or str(uuid.UUID(session_id)) != session_id:
                raise ValueError
        except (ValueError, TypeError, AttributeError):
            raise GitHubError(
                "invalid_arguments", "A valid account-page sessionId is required."
            ) from None
        with self._lock:
            record = self._sessions.get(session_id)
        if record is None:
            raise GitHubError(
                "github_account_session_missing",
                "This account-page session is no longer available.",
            )
        return record

    def status(self, session_id, wait_seconds=0):
        if type(wait_seconds) is not int or not 0 <= wait_seconds <= 20:
            raise GitHubError(
                "invalid_arguments", "wait_seconds must be an integer from 0 to 20."
            )
        record = self._record(session_id)
        deadline = time.monotonic() + wait_seconds
        while True:
            if record["done"].is_set():
                value = record["result"]
            else:
                value = record["session"].outcome()
            if value is not None:
                return {
                    "sessionId": session_id,
                    "action": record["action"],
                    **_terminal(value),
                }
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            record["done"].wait(min(0.05, remaining))
        return {
            "status": "waiting_for_user",
            "sessionId": session_id,
            "action": record["action"],
            "hint": "账户页尚未完成；请继续在执行设备浏览器操作。",
        }

    def close(self, session_id):
        record = self._record(session_id)
        record["session"].close()
        return self.status(session_id, 20)

    def shutdown(self):
        with self._lock:
            self._closing = True
            records = [
                record
                for record in self._sessions.values()
                if not record["done"].is_set()
            ]
        for record in records:
            try:
                record["session"].close()
            except Exception:  # noqa: BLE001 - Continue stopping the remaining owned pages.
                record["result"] = _terminal(None)
                record["done"].set()

    def call(self, name, arguments):
        keys = set(arguments)
        if name == "open_account_manager" and keys == {"action"}:
            if type(arguments["action"]) is not str:
                raise GitHubError("invalid_arguments", "Choose login or logout.")
            return self.open(arguments["action"])
        if name in {"get_account_manager_status", "close_account_manager"}:
            allowed = (
                {"sessionId", "wait_seconds"}
                if name == "get_account_manager_status"
                else {"sessionId"}
            )
            if "sessionId" not in arguments or keys - allowed:
                raise GitHubError(
                    "invalid_arguments",
                    "Use the account-page sessionId returned by open_account_manager.",
                )
            if name == "close_account_manager":
                return self.close(arguments["sessionId"])
            return self.status(arguments["sessionId"], arguments.get("wait_seconds", 0))
        raise GitHubError("invalid_arguments", "Invalid account-manager arguments.")

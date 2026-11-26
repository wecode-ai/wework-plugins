"""Verify MCP account-page ownership without a browser or real credentials."""

import importlib
import io
import json
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "plugins/wework-github/scripts")
)
module = importlib.import_module("github_connector.account_manager")
mcp = importlib.import_module("github_connector.mcp")
GitHubError = importlib.import_module("github_connector.errors").GitHubError


class FakePage:
    def __init__(self):
        self.finished = threading.Event()
        self.result = None
        self.closed = False

    def wait(self):
        self.finished.wait(3)
        return self.result

    def outcome(self):
        return self.result

    def close(self):
        self.closed = True
        if self.result is None:
            self.result = {"status": "cancelled"}
        self.finished.set()


class AccountManagerTests(unittest.TestCase):
    def setUp(self):
        self.pages = []

        def start(_action):
            page = FakePage()
            self.pages.append(page)
            return page

        self.factory = mock.Mock(side_effect=start)
        self.manager = module.AccountManagers(start=self.factory)
        self.addCleanup(self.manager.shutdown)

    def test_open_does_not_claim_authentication_and_wait_is_bounded(self):
        opened = self.manager.open("login")
        self.assertEqual(opened["status"], "browser_opened")
        state = self.manager.status(opened["sessionId"])
        self.assertEqual(state["status"], "waiting_for_user")
        self.assertNotIn("connected", state)
        for invalid in (-1, 21, True, 1.5):
            with self.subTest(invalid=invalid), self.assertRaises(GitHubError):
                self.manager.status(opened["sessionId"], invalid)

    def test_verified_outcome_is_available_while_page_still_serves_terminal_ui(self):
        opened = self.manager.open("login")
        self.pages[0].result = {
            "status": "ok",
            "connected": True,
            "userCode": "ABCD-1234",
            "verificationUrl": "https://github.com/login/device",
            "url": "http://127.0.0.1:1/SECRET/",
            "csrfToken": "SECRET",
        }
        self.assertFalse(self.pages[0].finished.is_set())
        state = self.manager.status(opened["sessionId"])
        self.assertEqual(state["status"], "ok")
        self.assertTrue(state["connected"])
        serialized = json.dumps(state)
        self.assertNotIn("ABCD-1234", serialized)
        self.assertNotIn("http", serialized)
        self.assertNotIn("SECRET", serialized)
        self.assertFalse(self.pages[0].closed)

    def test_completed_sign_out_retains_actual_change_and_shared_account_state(self):
        opened = self.manager.open("logout")
        self.pages[0].result = {
            "status": "error",
            "errorCode": "gh_logout_health_failed",
            "accountRevoked": True,
            "changed": True,
        }
        self.pages[0].finished.set()
        state = self.manager.status(opened["sessionId"], 1)
        self.assertTrue(state["changed"])
        self.assertTrue(state["accountRevoked"])
        self.assertNotIn("connected", state)

    def test_wait_returns_operation_outcome_before_page_acknowledgment(self):
        opened = self.manager.open("login")

        def finish_operation():
            time.sleep(0.05)
            self.pages[0].result = {"status": "ok", "connected": True}

        worker = threading.Thread(target=finish_operation)
        worker.start()
        self.addCleanup(worker.join)
        before = time.monotonic()
        state = self.manager.status(opened["sessionId"], 2)
        self.assertEqual(state["status"], "ok")
        self.assertLess(time.monotonic() - before, 1)
        self.assertFalse(self.pages[0].finished.is_set())

    def test_cancel_is_owned_and_shutdown_closes_remaining_pages(self):
        first = self.manager.open("login")
        self.manager.open("logout")
        result = self.manager.close(first["sessionId"])
        self.assertEqual(result["status"], "cancelled")
        self.assertTrue(self.pages[0].closed)
        self.manager.shutdown()
        self.assertTrue(self.pages[1].closed)
        with self.assertRaises(GitHubError):
            self.manager.open("login")

    def test_session_count_is_bounded_without_opening_another_browser(self):
        for _ in range(module.MAX_ACTIVE):
            self.manager.open("login")
        with self.assertRaises(GitHubError) as caught:
            self.manager.open("login")
        self.assertEqual(caught.exception.code, "github_account_manager_busy")
        self.assertEqual(len(self.pages), module.MAX_ACTIVE)

    def test_invalid_ids_and_unrecognized_arguments_cannot_control_another_page(self):
        self.manager.open("login")
        for value in (None, "", "../secret", 4):
            with self.subTest(value=value), self.assertRaises(GitHubError):
                self.manager.close(value)
        with self.assertRaises(GitHubError):
            self.manager.call(
                "open_account_manager",
                {"action": "login", "url": "https://evil.example"},
            )
        self.assertFalse(self.pages[0].closed)

    def test_start_failure_leaves_no_session_or_false_success(self):
        with (
            mock.patch.object(
                self.manager,
                "_start",
                side_effect=GitHubError("gh_browser_open_failed", "fixture"),
            ),
            self.assertRaises(GitHubError),
        ):
            self.manager.open("login")
        self.assertEqual(len(self.manager._sessions), 0)

    def test_authentication_tools_use_standard_mcp_and_keep_the_session_url_private(
        self,
    ):
        with mock.patch.object(mcp, "_managers", self.manager):
            response = mcp.dispatch(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "open_account_manager",
                        "arguments": {"action": "login"},
                    },
                }
            )
        result = json.loads(response["result"]["content"][0]["text"])
        self.assertEqual(result["status"], "browser_opened")
        self.assertNotIn("url", result)
        self.assertNotIn("userCode", result)
        self.assertNotIn("pluginKey", result)
        self.factory.assert_called_once_with("login")

    def test_mcp_eof_closes_pages_and_never_emits_auth_progress_to_stdout(self):
        self.manager.open("login")
        output = io.StringIO()
        with mock.patch.object(mcp, "_managers", self.manager):
            mcp.serve(io.BytesIO(b'{"jsonrpc":"2.0","id":1,"method":"ping"}\n'), output)
        self.assertTrue(self.pages[0].closed)
        self.assertEqual(json.loads(output.getvalue())["result"], {})

    def test_deep_json_and_invalid_request_ids_do_not_stop_mcp(self):
        payload = (
            "[" * 30000
            + "]" * 30000
            + '\n{"jsonrpc":"2.0","id":{"nested":1},"method":"ping"}'
            + '\n{"jsonrpc":"2.0","id":2,"method":"ping"}\n'
        )
        output = io.StringIO()
        with mock.patch.object(mcp, "_managers", self.manager):
            mcp.serve(io.BytesIO(payload.encode()), output)
        responses = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(responses[0]["error"]["code"], -32700)
        self.assertEqual(responses[1]["error"]["code"], -32600)
        self.assertIsNone(responses[1]["id"])
        self.assertEqual(responses[2]["result"], {})


if __name__ == "__main__":
    unittest.main()

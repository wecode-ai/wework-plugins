"""Use real loopback HTTP and fake account adapters, never a browser or gh."""

import contextlib
import http.client
import importlib
import io
import json
import os
import shlex
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.parse import urlsplit
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins/wework-github/scripts"))
browser = importlib.import_module("github_connector.browser_auth")
GitHubError = importlib.import_module("github_connector.errors").GitHubError


class Parent:
    def __init__(self):
        self.live = True
        self.closed = False

    def alive(self):
        return self.live

    def close(self):
        self.closed = True


class Login:
    def __init__(self):
        self.called = threading.Event()
        self.complete = threading.Event()
        self.cleaned = threading.Event()
        self.calls = 0

    def __call__(self, *, on_event, cancel_event, timeout, popen):
        self.calls += 1
        self.called.set()
        on_event(
            {
                "status": "waiting_browser",
                "title": "GitHub（Wework）",
                "hint": "请在官方页面完成授权。",
                "userCode": "ABCD-1234",
                "verificationUrl": "https://github.com/login/device",
            }
        )
        deadline = time.monotonic() + timeout
        while not self.complete.is_set() and not cancel_event.wait(0.01):
            if time.monotonic() >= deadline:
                break
        if cancel_event.is_set():
            result = {"status": "cancelled", "errorCode": "gh_login_cancelled"}
        elif not self.complete.is_set():
            result = {"status": "expired", "errorCode": "gh_login_timeout"}
        else:
            result = {"status": "ok", "connected": True}
        # The core's contract places both final callback and return after cleanup.
        self.cleaned.set()
        on_event(result)
        return result


class BrowserAuthTests(unittest.TestCase):
    def begin(self, action="login", **kwargs):
        self.login = kwargs.pop("login", Login())
        self.parent = kwargs.pop("parent", Parent())
        addresses = []

        def opener(address):
            addresses.append(address)
            return True

        session = browser.start(
            action, opener=opener, login=self.login, parent=self.parent, **kwargs
        )
        self.addCleanup(session.close)
        self.session = session
        self.address = urlsplit(addresses[0])
        self.origin = f"http://{self.address.netloc}"
        self.path = self.address.path
        return session

    def request(
        self, route="state", *, method="GET", body=None, headers=None, path=None
    ):
        values = {"Host": self.address.netloc}
        if method == "POST":
            values.update(Origin=self.origin, **{"Content-Type": "application/json"})
            if body is None:
                body = json.dumps({"csrfToken": self.token()})
        values.update(headers or {})
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.address.port, timeout=2
        )
        try:
            connection.request(
                method, path or self.path + route, body=body, headers=values
            )
            response = connection.getresponse()
            return response.status, response.read(), dict(response.getheaders())
        finally:
            connection.close()

    def state(self):
        status, body, _ = self.request()
        self.assertEqual(status, 200)
        return json.loads(body)

    def token(self):
        return self.state()["csrfToken"]

    def wait_for(self, predicate):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            value = predicate()
            if value:
                return value
            time.sleep(0.01)
        self.fail("Timed out waiting for a fake-flow condition")

    def terminal(self):
        self.wait_for(lambda: self.session.outcome())
        state = self.state()
        self.assertTrue(state["done"])
        self.assertEqual(self.request("ack", method="POST")[0], 200)
        self.assertTrue(self.session.closed.wait(2))
        return self.session.wait()

    def test_opened_is_not_authenticated_and_login_waits_for_explicit_confirmation(
        self,
    ):
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
            session = self.begin()
            public = session.public_result()
            self.assertEqual(public["status"], "browser_opened")
            self.assertNotIn("connected", public)
            self.assertNotIn(self.path.strip("/"), json.dumps(public))
            self.assertIsNone(session.outcome())
            self.assertFalse(self.login.called.is_set())
            state = self.state()
            self.assertEqual(state["status"], "awaiting_confirmation")
            self.assertIn("共用", state["description"])
            self.assertIn("不撤销", state["logoutDescription"])
            self.assertEqual(self.request("confirm", method="POST")[0], 202)
            self.assertTrue(self.login.called.wait(1))
            state = self.wait_for(
                lambda: (
                    self.state()
                    if self.state()["status"] == "waiting_browser"
                    else None
                )
            )
            self.assertEqual(state["userCode"], "ABCD-1234")
            self.assertEqual(
                state["verificationUrl"], "https://github.com/login/device"
            )
            self.login.complete.set()
            result = self.terminal()
        self.assertEqual(captured.getvalue(), "")
        self.assertEqual(result["status"], "ok")
        self.assertTrue(self.login.cleaned.is_set())
        for forbidden in (
            "ABCD-1234",
            "https://github.com/login/device",
            self.path.strip("/"),
        ):
            self.assertNotIn(forbidden, json.dumps(result))

    def test_outcome_is_available_before_page_ack_and_server_keeps_terminal_state(self):
        session = self.begin()
        self.request("confirm", method="POST")
        self.assertTrue(self.login.called.wait(1))
        self.login.complete.set()
        outcome = self.wait_for(session.outcome)
        self.assertTrue(self.login.cleaned.is_set())
        self.assertEqual(outcome["status"], "ok")
        self.assertFalse(session.closed.is_set())
        self.assertTrue(self.state()["done"])
        self.assertEqual(self.terminal()["status"], "ok")

    def test_cancel_before_confirmation_does_not_call_account_adapter(self):
        self.begin()
        self.assertEqual(self.request("cancel", method="POST")[0], 200)
        self.assertEqual(self.terminal()["status"], "cancelled")
        self.assertEqual(self.login.calls, 0)

    def test_page_cancel_and_owner_close_wait_for_worker_cleanup(self):
        session = self.begin()
        self.request("confirm", method="POST")
        self.assertTrue(self.login.called.wait(1))
        self.assertEqual(self.request("cancel", method="POST")[0], 200)
        self.assertEqual(self.terminal()["status"], "cancelled")
        self.assertTrue(self.login.cleaned.is_set())
        self.assertTrue(self.parent.closed)
        session.close()
        with self.assertRaises(OSError):
            self.request()

    def test_parent_death_cancels_the_owned_attempt(self):
        session = self.begin()
        self.request("confirm", method="POST")
        self.assertTrue(self.login.called.wait(1))
        self.parent.live = False
        self.wait_for(session.outcome)
        session.close()
        self.assertEqual(session.wait()["status"], "cancelled")
        self.assertTrue(self.login.cleaned.is_set())

    def test_confirmation_expiry_and_page_lease_are_bounded(self):
        session = self.begin(timeout=0.15)
        self.assertTrue(session.closed.wait(2))
        self.assertEqual(session.wait()["status"], "expired")
        self.assertFalse(self.login.called.is_set())
        session = self.begin(lease=0.15)
        self.state()
        self.request("confirm", method="POST")
        self.assertTrue(self.login.called.wait(1))
        self.wait_for(session.outcome)
        session.close()
        self.assertEqual(session.wait()["status"], "cancelled")
        self.assertTrue(self.login.cleaned.is_set())

    def test_parent_death_during_terminal_ack_grace_closes_immediately(self):
        session = self.begin()
        self.request("confirm", method="POST")
        self.assertTrue(self.login.called.wait(1))
        self.login.complete.set()
        self.wait_for(session.outcome)
        self.assertFalse(session.closed.is_set())
        self.parent.live = False
        self.assertTrue(session.closed.wait(2))
        self.assertEqual(session.wait()["status"], "ok")

    def test_logout_requires_confirmation_and_retains_real_mutation_result(self):
        calls = []

        def logout():
            calls.append(True)
            return {
                "status": "error",
                "errorCode": "gh_logout_health_failed",
                "changed": True,
                "accountRevoked": True,
            }

        self.begin("logout", logout=logout)
        self.assertFalse(calls)
        self.assertEqual(self.state()["action"], "logout")
        self.assertEqual(self.request("confirm", method="POST")[0], 202)
        result = self.terminal()
        self.assertEqual(len(calls), 1)
        self.assertTrue(result["changed"])
        self.assertTrue(result["accountRevoked"])
        self.assertNotIn("connected", result)

    def test_close_during_confirmed_logout_waits_for_real_result(self):
        called, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def logout():
            called.set()
            release.wait(3)
            return {
                "status": "ok",
                "changed": True,
                "accountRevoked": True,
                "connected": True,
            }

        session = self.begin("logout", logout=logout)
        self.request("confirm", method="POST")
        self.assertTrue(called.wait(1))
        closing = threading.Thread(target=session.close)
        closing.start()
        self.assertIsNone(session.outcome())
        self.assertTrue(closing.is_alive())
        release.set()
        closing.join(2)
        self.assertFalse(closing.is_alive())
        self.assertTrue(session.wait()["changed"])
        self.assertTrue(session.wait()["connected"])

    def test_origin_host_nonce_and_csrf_protect_mutations(self):
        self.begin()
        token = self.token()
        cases = [
            ({"Origin": "https://attacker.example"}, {"csrfToken": token}, 403),
            ({"Host": "attacker.example"}, {"csrfToken": token}, 403),
            ({}, {"csrfToken": "wrong"}, 403),
            ({}, {"csrfToken": token, "argv": ["auth", "token"]}, 403),
            ({"Content-Type": "text/plain"}, {"csrfToken": token}, 415),
        ]
        for headers, body, expected in cases:
            with self.subTest(headers=headers, body=body):
                self.assertEqual(
                    self.request(
                        "confirm", method="POST", body=json.dumps(body), headers=headers
                    )[0],
                    expected,
                )
        self.assertEqual(self.request("confirm")[0], 405)
        self.assertEqual(self.request(path="/wrong/state")[0], 404)
        self.assertEqual(
            self.request(path=self.path + "state?url=https://attacker.example")[0], 404
        )
        self.assertEqual(self.request("arbitrary-command", method="POST")[0], 404)
        self.assertFalse(self.login.called.is_set())

    def test_body_and_headers_are_bounded_and_responses_are_not_cacheable(self):
        self.begin()
        self.assertEqual(
            self.request("confirm", method="POST", body="x" * 1025)[0], 413
        )
        _, _, headers = self.request()
        self.assertIn("no-store", headers["Cache-Control"])
        self.assertEqual(headers["Referrer-Policy"], "no-referrer")
        self.assertEqual(headers["Cross-Origin-Resource-Policy"], "same-origin")
        self.assertFalse(
            any(key.lower().startswith("access-control-") for key in headers)
        )
        with socket.create_connection(
            ("127.0.0.1", self.address.port), timeout=2
        ) as connection:
            message = (
                f"GET {self.path}state HTTP/1.1\r\nHost: {self.address.netloc}\r\nX-Large: "
                + "x" * 9000
                + "\r\n\r\n"
            )
            connection.sendall(message.encode())
            try:
                self.assertEqual(connection.recv(1024), b"")
            except ConnectionResetError:
                pass
        self.assertEqual(self.state()["status"], "awaiting_confirmation")

    def test_missing_or_duplicate_security_headers_never_start_login(self):
        self.begin()
        data = json.dumps({"csrfToken": self.token()}).encode()
        valid = [
            ("Host", self.address.netloc),
            ("Origin", self.origin),
            ("Content-Type", "application/json"),
            ("Content-Length", str(len(data))),
        ]
        cases = [
            ([value for value in valid if value[0] != "Origin"], 403),
            (valid + [("Host", self.address.netloc)], 403),
            (valid + [("Origin", self.origin)], 403),
            (valid + [("Content-Length", str(len(data)))], 400),
            (valid + [("Transfer-Encoding", "chunked")], 400),
        ]
        for headers, expected in cases:
            with self.subTest(headers=headers):
                connection = http.client.HTTPConnection(
                    "127.0.0.1", self.address.port, timeout=2
                )
                try:
                    connection.putrequest(
                        "POST",
                        self.path + "confirm",
                        skip_host=True,
                        skip_accept_encoding=True,
                    )
                    for key, value in headers:
                        connection.putheader(key, value)
                    connection.endheaders(data)
                    response = connection.getresponse()
                    self.assertEqual(response.status, expected)
                    response.read()
                finally:
                    connection.close()
        self.assertFalse(self.login.called.is_set())

    def test_page_has_no_external_assets_or_html_injection_sinks(self):
        self.begin()
        status, body, headers = self.request("")
        self.assertEqual(status, 200)
        html = body.decode()
        self.assertIn("default-src 'none'", headers["Content-Security-Policy"])
        self.assertIn("script-src 'sha256-", headers["Content-Security-Policy"])
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertNotIn("unsafe-inline", headers["Content-Security-Policy"])
        self.assertNotIn("innerHTML", html)
        self.assertNotIn('src="http', html)
        self.assertIn("navigator.sendBeacon", html)
        self.assertIn("'ack'", html)
        self.assertIn("textContent", html)

    def test_invalid_provider_url_or_codes_never_become_clickable_page_state(self):
        self.begin()
        for url in (
            "https://github.com.evil/login/device",
            "javascript:alert(1)",
            "https://user@github.com/login/device",
            "https://github.com/login/device?token=PRIVATE",
        ):
            self.session.session.on_event(
                {
                    "status": "waiting_browser",
                    "userCode": "ABCD-1234",
                    "verificationUrl": url,
                }
            )
            state = self.state()
            self.assertNotIn("userCode", state)
            self.assertNotIn("verificationUrl", state)

    def test_failed_browser_open_has_no_account_side_effects_or_secret_output(self):
        flow = Login()
        parent = Parent()
        captured = io.StringIO()

        def fail(address):
            raise OSError("PRIVATE " + address)

        with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
            with self.assertRaises(GitHubError) as error:
                browser.start(opener=fail, login=flow, parent=parent)
        self.assertEqual(error.exception.code, "gh_browser_open_failed")
        self.assertNotIn("PRIVATE", error.exception.message)
        self.assertFalse(flow.called.is_set())
        self.assertTrue(parent.closed)
        self.assertEqual(captured.getvalue(), "")

    def test_signal_during_construction_waits_for_confirmed_worker_cleanup(self):
        flow, parent = Login(), Parent()

        def interrupt(instance, _opener, _address):
            self.assertTrue(instance.session.confirm())
            self.assertTrue(flow.called.wait(1))
            raise KeyboardInterrupt

        with mock.patch.object(browser.BrowserSession, "_open", new=interrupt):
            with self.assertRaises(KeyboardInterrupt):
                browser.start(login=flow, parent=parent)
        self.assertTrue(flow.cleaned.is_set())
        self.assertTrue(parent.closed)

    def test_cli_signal_handler_is_installed_before_browser_construction(self):
        flow, parent = Login(), Parent()
        previous = signal.getsignal(signal.SIGTERM)

        def interrupt(instance, _opener, _address):
            self.assertTrue(instance.session.confirm())
            self.assertTrue(flow.called.wait(1))
            signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)

        with mock.patch.object(browser.BrowserSession, "_open", new=interrupt):
            result = browser.run(login=flow, parent=parent)
        self.assertEqual(result["status"], "cancelled")
        self.assertTrue(flow.cleaned.is_set())
        self.assertTrue(parent.closed)
        self.assertEqual(signal.getsignal(signal.SIGTERM), previous)

    def test_thread_start_failures_close_sockets_without_waiting_for_absent_threads(
        self,
    ):
        original_start = threading.Thread.start
        for target in ("serve_forever", "_monitor"):
            parent = Parent()
            servers = []
            original_server = browser._Server

            def server(*args, **kwargs):
                value = original_server(*args, **kwargs)
                servers.append(value)
                return value

            def fail(thread):
                if getattr(thread._target, "__name__", "") == target:
                    raise RuntimeError("PRIVATE thread startup error")
                return original_start(thread)

            with (
                self.subTest(target=target),
                mock.patch.object(browser, "_Server", side_effect=server),
                mock.patch.object(threading.Thread, "start", new=fail),
            ):
                with self.assertRaises(GitHubError):
                    browser.start(
                        opener=lambda _address: True, login=Login(), parent=parent
                    )
            self.assertEqual(servers[0].socket.fileno(), -1)
            self.assertTrue(parent.closed)

    def test_default_opener_isolates_dummy_browser_output_and_authentication_environment(
        self,
    ):
        with tempfile.TemporaryDirectory(prefix="wework-test-browser-") as folder:
            folder = Path(folder)
            dummy = folder / "dummy_browser.py"
            record = folder / "record.json"
            dummy.write_text(
                "import json, os, pathlib, sys\n"
                "print(sys.argv[-1], flush=True)\n"
                "print('PRIVATE_BROWSER_DETAIL', file=sys.stderr, flush=True)\n"
                "pathlib.Path(os.environ['WEWORK_TEST_BROWSER_RECORD']).write_text(json.dumps({"
                "'url': sys.argv[-1], 'tokens': [key for key in os.environ if key.upper() in "
                "['GH_TOKEN','GITHUB_TOKEN','GH_ENTERPRISE_TOKEN','GITHUB_ENTERPRISE_TOKEN']]}))\n",
                encoding="utf-8",
            )
            helper = folder / "invoke.py"
            helper.write_text(
                "import sys\n"
                f"sys.path.insert(0, {str(ROOT / 'plugins/wework-github/scripts')!r})\n"
                "from github_connector.browser_auth import _system_open\n"
                "sys.exit(0 if _system_open('http://127.0.0.1:12345/SECRET_CAPABILITY/') else 1)\n",
                encoding="utf-8",
            )
            environment = {
                **os.environ,
                "BROWSER": f"{shlex.quote(sys.executable)} {shlex.quote(str(dummy))} %s",
                "WEWORK_TEST_BROWSER_RECORD": str(record),
                "GH_TOKEN": "PRIVATE_A",
                "GITHUB_TOKEN": "PRIVATE_B",
                "GH_ENTERPRISE_TOKEN": "PRIVATE_C",
                "GITHUB_ENTERPRISE_TOKEN": "PRIVATE_D",
            }
            result = subprocess.run(
                [sys.executable, str(helper)],
                capture_output=True,
                env=environment,
                timeout=8,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, b"")
            self.assertEqual(result.stderr, b"")
            value = json.loads(record.read_text())
            self.assertEqual(value["url"], "http://127.0.0.1:12345/SECRET_CAPABILITY/")
            self.assertEqual(value["tokens"], [])

    def test_default_opener_failure_is_bounded_and_discards_raw_process_details(self):
        with mock.patch.object(
            browser.subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired("PRIVATE", 4),
        ) as invoke:
            self.assertFalse(browser._system_open("http://127.0.0.1:12345/SECRET/"))
        arguments = invoke.call_args.kwargs
        self.assertEqual(arguments["stdout"], subprocess.DEVNULL)
        self.assertEqual(arguments["stderr"], subprocess.DEVNULL)
        self.assertEqual(arguments["stdin"], subprocess.DEVNULL)
        self.assertEqual(arguments["timeout"], 4)
        self.assertNotIn("shell", arguments)

    def test_account_worker_start_failure_is_terminal_without_account_side_effects(
        self,
    ):
        self.begin()
        original_start = threading.Thread.start

        def fail(thread):
            if getattr(thread._target, "__name__", "") == "_execute":
                raise RuntimeError("PRIVATE worker startup error")
            return original_start(thread)

        with mock.patch.object(threading.Thread, "start", new=fail):
            self.assertEqual(self.request("confirm", method="POST")[0], 409)
        self.assertEqual(self.terminal()["status"], "error")
        self.assertFalse(self.login.called.is_set())

    def test_idle_connection_limit_rejects_excess_without_blocking_recovery(self):
        self.begin()
        connections = []
        try:
            for _ in range(browser.MAX_CONNECTIONS):
                connection = socket.create_connection(
                    ("127.0.0.1", self.address.port), timeout=2
                )
                connection.sendall(b"GET ")
                connections.append(connection)
            self.wait_for(lambda: self.session.server.slots._value == 0)
            with socket.create_connection(
                ("127.0.0.1", self.address.port), timeout=1
            ) as excess:
                excess.sendall(b"GET ")
                try:
                    self.assertEqual(excess.recv(1024), b"")
                except ConnectionResetError:
                    pass
        finally:
            for connection in connections:
                connection.close()
        self.wait_for(
            lambda: self.session.server.slots._value == browser.MAX_CONNECTIONS
        )
        self.assertEqual(self.state()["status"], "awaiting_confirmation")
        self.assertFalse(self.login.called.is_set())


if __name__ == "__main__":
    unittest.main()

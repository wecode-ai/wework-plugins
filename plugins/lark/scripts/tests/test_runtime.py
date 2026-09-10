import contextlib
import http.server
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
import lark_cli
import local_auth
import native_runtime as native
from wegent_plugin_auth import AuthError


class RuntimeTests(unittest.TestCase):
    def test_scope_requests_are_bounded_and_validated(self):
        lark_cli.validate_scopes(["--scope", "im:message:readonly offline_access"])
        for value in (
            None,
            "--scope",
            [],
            ["--scope"],
            ["--scope", 1],
            ["--app-secret", "bad"],
            ["--scope", "x\n"],
            ["--scope", "x"] * 5,
        ):
            with self.assertRaises(AuthError):
                lark_cli.validate_scopes(value)

    def test_identity_routing_is_explicit(self):
        self.assertEqual(lark_cli.identity(["im", "--as", "bot"]), "bot")
        self.assertEqual(lark_cli.identity(["im", "--as=user"]), "user")
        with self.assertRaises(AuthError):
            lark_cli.identity(["im", "--as"])

    def test_managed_commands_cannot_start_a_second_login(self):
        for args in (
            ["auth", "login"],
            ["config", "init"],
            ["profile", "switch"],
            ["event", "consume", "im.message.receive_v1"],
        ):
            with self.assertRaises(AuthError):
                lark_cli.business_args(args)
        self.assertEqual(lark_cli.business_args(["auth", "status"]), ["account-status"])

    def test_browser_only_opens_official_verification_origins(self):
        self.assertEqual(
            list(
                local_auth.verification_urls(
                    "https://accounts.feishu.cn/oauth/verify?code=synthetic"
                )
            ),
            ["https://accounts.feishu.cn/oauth/verify?code=synthetic"],
        )
        for value in (
            "https://evil.invalid",
            "https://accounts.feishu.cn.evil.invalid",
            "https://user@accounts.feishu.cn",
            "https://accounts.feishu.cn:444/",
        ):
            self.assertEqual(list(local_auth.verification_urls(value)), [])

    def test_failed_relogin_preserves_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            receipt = Path(directory) / "receipt.json"
            receipt.touch()
            with patch.object(
                native, "context", return_value={"user": "account"}
            ), patch.object(native, "receipt", return_value=receipt), patch.object(
                native, "invoke", return_value=subprocess.CompletedProcess([], 1)
            ), patch.object(
                local_auth, "browser_flow", side_effect=AuthError("failed")
            ):
                with self.assertRaises(AuthError):
                    local_auth.login_locked()
                self.assertTrue(receipt.exists())

    def test_managed_health_does_not_read_tokens(self):
        with tempfile.TemporaryDirectory() as directory:
            receipt = Path(directory) / "receipt.json"
            receipt.touch()
            with patch.object(native, "locked", contextlib.nullcontext), patch.object(
                native, "context", return_value={"user": "account"}
            ), patch.object(native, "receipt", return_value=receipt), patch.object(
                native, "invoke", side_effect=AssertionError("local token probe")
            ), patch.object(
                local_auth, "run_account_command", return_value='{"status":"ok"}'
            ) as run:
                self.assertEqual(local_auth.action("health"), "ok")
                run.side_effect = AuthError("plugin_auth_device_not_granted")
                self.assertEqual(local_auth.action("health"), "need_login")

    def test_cloud_process_only_sends_public_broker_request(self):
        requests = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                requests.append(
                    json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                )
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"stdout":"{\\"status\\":\\"ok\\"}"}')

        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            capabilities = home / "executor/capabilities"
            capabilities.mkdir(parents=True)
            (capabilities / "manifest.json").write_text(
                json.dumps(
                    {
                        "plugins": {
                            "lark": {
                                "managed": True,
                                "enabled": True,
                                "installed_plugin_id": 123,
                                "store_path": str(SCRIPTS.parent),
                            }
                        }
                    }
                )
            )
            server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            env = {
                **os.environ,
                "HOME": str(home),
                "USERPROFILE": str(home),
                "WEGENT_EXECUTOR_HOME": str(home / "executor"),
                "WEGENT_PLUGIN_AUTH_MODE": "cloud",
                "WEGENT_PLUGIN_AUTH_BROKER": f"http://127.0.0.1:{server.server_port}/v1/run",
                "WEGENT_PLUGIN_AUTH_BROKER_TOKEN": "a" * 64,
            }
            try:
                for args, slug in [
                    (["im", "messages", "list"], "lark"),
                    (["im", "messages", "list", "--as", "bot"], "lark-app"),
                    (["--ready"], "lark"),
                ]:
                    result = subprocess.run(
                        [sys.executable, str(SCRIPTS / "lark_cli.py"), *args],
                        env=env,
                        capture_output=True,
                        timeout=15,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr.decode())
                    self.assertEqual(requests[-1]["connector_slug"], slug)
                    self.assertNotIn("credential", requests[-1])
                self.assertFalse((home / ".wegent-executor").exists())
            finally:
                server.shutdown()
                server.server_close()

    def test_source_lock_blocks_another_real_process(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home)}
            code = f"import sys;sys.path.insert(0,{str(SCRIPTS)!r});import native_runtime as n;\nwith n.locked(): print('locked',flush=True);sys.stdin.read()"
            child = subprocess.Popen(
                [sys.executable, "-c", code],
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
            )
            try:
                self.assertEqual(child.stdout.readline().rstrip(b"\r\n"), b"locked")
                probe = subprocess.Popen(
                    [
                        sys.executable,
                        "-c",
                        f"import sys;sys.path.insert(0,{str(SCRIPTS)!r});import native_runtime as n;\nwith n.locked(): print('entered',flush=True)",
                    ],
                    env=env,
                    stdout=subprocess.PIPE,
                )
                time.sleep(0.15)
                self.assertIsNone(probe.poll())
                child.communicate(timeout=5)
                out, _ = probe.communicate(timeout=5)
                self.assertEqual(out.rstrip(b"\r\n"), b"entered")
            finally:
                if child.poll() is None:
                    child.kill()
                    child.communicate()

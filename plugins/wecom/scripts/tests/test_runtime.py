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
import wecom_cli
import local_auth
import native_runtime as native
from wegent_plugin_auth import AuthError


class RuntimeTests(unittest.TestCase):
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
                            "wecom": {
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
                    (["msg", "get_msg_chat_list", "{}"], "wecom"),
                    (["--ready"], "wecom"),
                ]:
                    result = subprocess.run(
                        [sys.executable, str(SCRIPTS / "wecom_cli.py"), *args],
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

    def test_cloud_disallows_login_and_configuration(self):
        for arguments in (["init"], ["auth", "login"], ["cache", "clear"]):
            with self.assertRaises(AuthError):
                wecom_cli.business_args(arguments)
        self.assertEqual(wecom_cli.business_args(["auth", "show"]), ["account-status"])

    def test_logout_propagates_cleanup_failure(self):
        with patch.object(native, "locked", contextlib.nullcontext), patch.object(
            native, "invoke", return_value=subprocess.CompletedProcess([], 1)
        ):
            with self.assertRaises(AuthError):
                local_auth.action("logout")

    def test_source_environment_drops_overrides_and_business_config(self):
        with patch.dict(
            os.environ,
            {
                "WECOM_CLI_CONFIG_DIR": "synthetic",
                "WECOM_SECRET": "synthetic-secret",
                "WECOM_CLI_MCP_CONFIG_ENDPOINT": "https://evil.invalid",
                "HTTPS_PROXY": "https://evil.invalid",
            },
            clear=True,
        ):
            self.assertEqual(native.environment()["WECOM_CLI_CONFIG_DIR"], "synthetic")
            self.assertNotIn("WECOM_CLI_CONFIG_DIR", native.environment(source=False))
            for name in (
                "WECOM_SECRET",
                "WECOM_CLI_MCP_CONFIG_ENDPOINT",
                "HTTPS_PROXY",
            ):
                self.assertNotIn(name, native.environment())

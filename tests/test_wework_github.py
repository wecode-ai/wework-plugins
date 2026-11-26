"""Package and stdio checks which never use the developer's real gh account."""

import importlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins/wework-github"
ENTRY = PLUGIN / "scripts/github.py"
sys.path.insert(0, str(PLUGIN / "scripts"))
mcp = importlib.import_module("github_connector.mcp")


class PackageTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js is not installed")
    def test_missing_python_emits_a_complete_terminal_auth_event(self):
        launcher = (PLUGIN / "scripts/mcp-launcher.mjs").as_uri()
        script = (
            "import cp from 'node:child_process';"
            "import {syncBuiltinESMExports} from 'node:module';"
            "cp.spawnSync = () => ({error: new Error('fixture_missing')});"
            "syncBuiltinESMExports();"
            "process.argv = [process.execPath, 'launcher', 'login'];"
            f"await import({json.dumps(launcher)});"
        )
        result = subprocess.run(
            [shutil.which("node"), "--input-type=module", "-e", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=10,
        )
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(result.stderr, "")
        event = json.loads(result.stdout)
        self.assertEqual(event["status"], "error")
        self.assertNotIn("protocol", event)
        self.assertEqual(event["errorCode"], "local_auth_runtime_missing")
        self.assertEqual(event["title"], "GitHub（Wework）")

    def test_plugin_identity_and_shared_authorization_contract(self):
        manifest = json.loads(
            (PLUGIN / ".codex-plugin/plugin.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["name"], "wework-github")
        self.assertNotIn("apps", manifest)
        self.assertFalse((PLUGIN / ".app.json").exists())
        self.assertNotIn("connectors", manifest)
        mcp_config = json.loads((PLUGIN / ".mcp.json").read_text(encoding="utf-8"))
        server = mcp_config["mcpServers"]["wework_github"]
        self.assertEqual(
            set(server["env_vars"]),
            {
                "GH_TOKEN",
                "GITHUB_TOKEN",
                "GH_CONFIG_DIR",
                "XDG_CONFIG_HOME",
                "APPDATA",
                "BROWSER",
                "DISPLAY",
                "WAYLAND_DISPLAY",
                "XDG_RUNTIME_DIR",
                "DBUS_SESSION_BUS_ADDRESS",
                "XDG_CURRENT_DESKTOP",
                "XDG_SESSION_TYPE",
                "HTTP_PROXY",
                "HTTPS_PROXY",
                "ALL_PROXY",
                "NO_PROXY",
                "http_proxy",
                "https_proxy",
                "all_proxy",
                "no_proxy",
            },
        )
        self.assertNotIn("env", server)

    def test_custom_oauth_configuration_and_credential_owner_are_removed(self):
        source = PLUGIN / "scripts/github_connector"
        for name in ("config.py", "http.py", "secure_store.py"):
            self.assertFalse((source / name).exists(), name)
        content = subprocess.run(
            [sys.executable, str(ENTRY), "--help"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        self.assertEqual(content.returncode, 0, content.stderr)
        self.assertNotIn("configure", content.stdout)
        self.assertNotIn("client-id", content.stdout)

    def test_missing_cli_health_requires_install_without_any_oauth_setup(self):
        result = subprocess.run(
            [sys.executable, str(ENTRY), "health"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            env={**os.environ, "PATH": "", "PYTHONIOENCODING": "utf-8"},
            timeout=10,
        )
        self.assertEqual(result.stderr, "")
        self.assertNotEqual(result.returncode, 0)
        value = json.loads(result.stdout)
        self.assertEqual(value["status"], "error")
        self.assertEqual(value.get("errorCode", value.get("code")), "gh_missing")

    def test_cli_uses_plugin_page_without_any_host_handshake(self):
        entry = importlib.import_module("github")
        for action in ("login", "logout"):
            output = io.StringIO()
            with (
                self.subTest(action=action),
                mock.patch.object(sys, "argv", [str(ENTRY), action]),
                mock.patch(
                    "github_connector.browser_auth.run",
                    return_value={"status": "cancelled", "hint": "fixture cancelled"},
                ) as run,
                mock.patch.dict(os.environ, {"WEWORK_LOCAL_AUTH_PROTOCOL": ""}),
                mock.patch.object(sys, "stdout", output),
            ):
                self.assertEqual(entry.main(), 1)
            run.assert_called_once_with(action)
            value = json.loads(output.getvalue())
            self.assertEqual(value["status"], "cancelled")
            self.assertEqual(value["title"], "GitHub（Wework）")
            self.assertNotIn("userCode", value)
            self.assertNotIn("verificationUrl", value)


class StdioTests(unittest.TestCase):
    def exercise(self, command, cwd, missing_cli=False):
        requests = [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-06-18"},
            },
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "get_me" if missing_cli else "get_issue",
                    "arguments": {},
                },
            },
        ]
        environment = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        if missing_cli:
            environment["PATH"] = ""
        result = subprocess.run(
            command,
            input="".join(json.dumps(value) + "\n" for value in requests),
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            env=environment,
            cwd=cwd,
            timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        messages = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([value["id"] for value in messages], [1, 2, 3])
        self.assertEqual(messages[0]["result"]["serverInfo"]["name"], "wework_github")
        self.assertEqual(len(messages[1]["result"]["tools"]), 30)
        self.assertTrue(messages[2]["result"]["isError"])
        error = json.loads(messages[2]["result"]["content"][0]["text"])
        self.assertEqual(
            error["code"], "gh_missing" if missing_cli else "invalid_arguments"
        )

    def test_real_stdio_without_gh_or_client_id(self):
        self.exercise([sys.executable, str(ENTRY), "mcp"], ROOT, missing_cli=True)

    @unittest.skipUnless(shutil.which("node"), "Node.js is not installed")
    def test_manifest_launcher_from_unrelated_working_directory(self):
        server = json.loads((PLUGIN / ".mcp.json").read_text(encoding="utf-8"))[
            "mcpServers"
        ]["wework_github"]
        self.assertEqual(server["cwd"], ".")
        with tempfile.TemporaryDirectory(prefix="independent workspace ") as workspace:
            installed = Path(workspace) / "installed-plugin"
            shutil.copytree(
                PLUGIN, installed, ignore=shutil.ignore_patterns("__pycache__")
            )
            self.exercise(
                [server["command"], *server["args"]], installed / server["cwd"]
            )

    def test_tool_results_and_auth_required_identity_reach_the_client(self):
        client = mock.Mock()
        client.request.return_value = {"login": "fixture-user"}
        with mock.patch.object(mcp, "GitHubClient", return_value=client):
            result = mcp.dispatch(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "get_me"},
                }
            )
        self.assertFalse(result["result"]["isError"])
        self.assertEqual(
            json.loads(result["result"]["content"][0]["text"]),
            {"login": "fixture-user"},
        )
        error_type = importlib.import_module("github_connector.errors").GitHubError
        client.request.side_effect = error_type(
            "github_auth_required", "Use the shared GitHub login"
        )
        with mock.patch.object(mcp, "GitHubClient", return_value=client):
            result = mcp.dispatch(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "get_me"},
                }
            )
        error = json.loads(result["result"]["content"][0]["text"])
        self.assertEqual(error["code"], "github_auth_required")
        self.assertNotIn("pluginKey", error)
        self.assertNotIn("connectorSlug", error)
        self.assertIn("open_account_manager", error["message"])

    def test_invalid_messages_do_not_crash_or_echo_input(self):
        output = io.StringIO()
        mcp.serve(
            io.BytesIO(b'not-json\n{"jsonrpc":"2.0","id":7,"method":"unknown"}\n'),
            output,
        )
        messages = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(
            [value["error"]["code"] for value in messages], [-32700, -32601]
        )


if __name__ == "__main__":
    unittest.main()

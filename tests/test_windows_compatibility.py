from __future__ import annotations

import json
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Optional


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PLUGINS_ROOT = REPOSITORY_ROOT / "plugins"


def powershell_executable() -> Optional[str]:
    if sys.platform == "win32":
        system_root = os.environ.get("SystemRoot")
        if system_root:
            candidate = (
                Path(system_root)
                / "System32"
                / "WindowsPowerShell"
                / "v1.0"
                / "powershell.exe"
            )
            if candidate.is_file():
                return str(candidate)
    return shutil.which("powershell") or shutil.which("pwsh")


class TestPluginInventory(unittest.TestCase):
    def test_registered_public_plugins_have_valid_manifests(self):
        manifests = sorted(PLUGINS_ROOT.glob("*/.codex-plugin/plugin.json"))
        marketplace = json.loads(
            (REPOSITORY_ROOT / ".agents/plugins/marketplace.json").read_text(encoding="utf-8")
        )
        registered = [entry["name"] for entry in marketplace["plugins"]]
        self.assertEqual(len(registered), len(set(registered)))
        self.assertEqual(
            set(registered), {path.parents[1].name for path in manifests}
        )
        for manifest_path in manifests:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest_path.parents[1].name, manifest["name"])
            self.assertRegex(
                manifest["version"],
                r"^\d+\.\d+\.\d+$",
            )
            package_path = manifest_path.parents[1] / "package.json"
            if package_path.is_file():
                package = json.loads(package_path.read_text(encoding="utf-8"))
                self.assertEqual(manifest["version"], package["version"])

    def test_every_local_auth_shell_entry_has_a_windows_sibling(self):
        for manifest_path in sorted(PLUGINS_ROOT.glob("*/.codex-plugin/plugin.json")):
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            plugin_root = manifest_path.parents[1]
            for connector in manifest.get("connectors", []):
                local_auth = connector.get("localAuth") or {}
                for action in ("health", "start", "poll", "logout"):
                    command = local_auth.get(action)
                    if not command or not str(command[0]).endswith(".sh"):
                        continue
                    sibling = plugin_root / Path(command[0]).with_suffix(".ps1")
                    self.assertTrue(
                        sibling.is_file(),
                        f"{manifest['name']} {action} has no PowerShell sibling",
                    )

    def test_native_powershell_calls_use_a_compatibility_boundary(self):
        requirements = {
            "dingtalk": {
                "helper": "invoke-dws.ps1",
                "scripts": (
                    "ensure-dws-ready.ps1",
                    "install-dws.ps1",
                    "local-auth.ps1",
                    "run-dws.ps1",
                    "run-python.ps1",
                ),
            },
            "lark": {
                "helper": "invoke-native-command.ps1",
                "scripts": (
                    "ensure-lark-ready.ps1",
                    "install-lark-cli.ps1",
                    "run-lark-cli.ps1",
                ),
            },
            "wecom": {
                "helper": "invoke-native-command.ps1",
                "scripts": (
                    "ensure-wecom-ready.ps1",
                    "install-wecom-cli.ps1",
                    "run-wecom-cli.ps1",
                ),
            },
        }
        for plugin, requirement in requirements.items():
            scripts = PLUGINS_ROOT / plugin / "scripts"
            helper = scripts / str(requirement["helper"])
            helper_text = helper.read_text(encoding="utf-8")
            self.assertIn("$ErrorActionPreference = 'Continue'", helper_text)
            self.assertIn("$LASTEXITCODE", helper_text)
            for name in requirement["scripts"]:
                content = (scripts / name).read_text(encoding="utf-8")
                self.assertIn(
                    str(requirement["helper"]),
                    content,
                    f"{plugin}/{name} bypasses the native-command boundary",
                )
            installer = next(scripts.glob("install-*.ps1"))
            self.assertIn(
                "SecurityProtocolType]::Tls12",
                installer.read_text(encoding="utf-8"),
                f"{plugin} installer does not force TLS 1.2 on PowerShell 5.1",
            )
        dingtalk_python = (
            PLUGINS_ROOT / "dingtalk/scripts/run-python.ps1"
        ).read_text(encoding="utf-8")
        self.assertIn("Get-Command py", dingtalk_python)
        self.assertIn("@('-3')", dingtalk_python)

    def test_python3_skill_commands_include_windows_launcher_guidance(self):
        for skill_path in sorted(PLUGINS_ROOT.glob("**/SKILL.md")):
            content = skill_path.read_text(encoding="utf-8")
            if "python3" not in content:
                continue
            self.assertIn(
                "Windows",
                content,
                f"{skill_path.relative_to(REPOSITORY_ROOT)} lacks Windows guidance",
            )
            self.assertIn(
                "py -3",
                content,
                f"{skill_path.relative_to(REPOSITORY_ROOT)} lacks py launcher guidance",
            )

    def test_reference_python_commands_inherit_windows_launcher_guidance(self):
        shared_guidance = {
            "lark": (PLUGINS_ROOT / "lark/skills/lark-shared/SKILL.md"),
            "product-design": (
                PLUGINS_ROOT / "product-design/skills/user-context/SKILL.md"
            ),
        }
        for path in sorted(PLUGINS_ROOT.glob("**/*.md")):
            if "python3" not in path.read_text(encoding="utf-8"):
                continue
            plugin = path.relative_to(PLUGINS_ROOT).parts[0]
            guidance_path = shared_guidance.get(plugin, path)
            guidance = guidance_path.read_text(encoding="utf-8")
            self.assertIn("Windows", guidance)
            self.assertIn("py -3", guidance)

    def test_python_bootstraps_include_windows_venv_and_runtime_paths(self):
        for script in sorted(PLUGINS_ROOT.glob("*/**/scripts/bootstrap.py")):
            content = script.read_text(encoding="utf-8")
            self.assertIn('root / "Scripts" / "python.exe"', content)
            self.assertIn('root / "python" / "python.exe"', content)


class TestPortableDocumentation(unittest.TestCase):
    def test_markdown_has_no_posix_only_temp_paths_or_heredocs(self):
        forbidden = (
            "/tmp/",
            "/path/to/",
            "/absolute/path/to/",
            "~/downloads/",
            "<<'EOF'",
            '<<"EOF"',
            "<< 'EOF'",
            '<< "EOF"',
        )
        for path in sorted(PLUGINS_ROOT.glob("**/*.md")):
            content = path.read_text(encoding="utf-8")
            for token in forbidden:
                self.assertNotIn(
                    token,
                    content,
                    f"{path.relative_to(REPOSITORY_ROOT)} contains {token}",
                )

    def test_shell_code_blocks_do_not_require_posix_utilities(self):
        shell_languages = {"bash", "sh", "shell", "zsh", "powershell", "ps1"}
        command = re.compile(
            r"^\s*(cat|grep|head|tail|sed|awk|jq|xmllint|printf|curl)\b"
        )
        pipeline = re.compile(r"\|\s*(jq|grep|head|tail|sed|awk)\b")
        process_pipeline = re.compile(
            r"^\s*[^#].*\s\|\s*(lark-cli|dws|wecom-cli|python|node)\b"
        )
        ignored_failure = re.compile(r"\|\|\s*true\b")
        assignment = re.compile(r"^\s*[A-Za-z_][A-Za-z0-9_]*=|\$\(")
        control = re.compile(r"^\s*(for|while|do|done|case|esac)\b")
        violations: list[str] = []
        for path in sorted(PLUGINS_ROOT.glob("**/*.md")):
            language: Optional[str] = None
            for line_number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                if line.startswith("```"):
                    language = None if language is not None else line[3:].strip().lower()
                    continue
                if language not in shell_languages or line.lstrip().startswith("#"):
                    continue
                if (
                    command.search(line)
                    or pipeline.search(line)
                    or process_pipeline.search(line)
                    or ignored_failure.search(line)
                    or assignment.search(line)
                    or control.search(line)
                ):
                    violations.append(
                        f"{path.relative_to(REPOSITORY_ROOT)}:{line_number}: {line}"
                    )
        self.assertEqual([], violations, "POSIX-only shell syntax:\n" + "\n".join(violations))

    def test_large_reference_sets_define_windows_execution_rules(self):
        dingtalk = (PLUGINS_ROOT / "dingtalk/skills/dws/SKILL.md").read_text(
            encoding="utf-8"
        )
        lark = (PLUGINS_ROOT / "lark/skills/lark-shared/SKILL.md").read_text(
            encoding="utf-8"
        )
        for content in (dingtalk, lark):
            self.assertIn("Windows PowerShell", content)
            self.assertIn("合并成单行", content)
            self.assertIn("heredoc", content)


class TestPortableHelpers(unittest.TestCase):
    def test_presigned_put_omits_content_type_by_default(self):
        script_path = (
            PLUGINS_ROOT
            / "dingtalk/skills/dws/scripts/put_presigned_file.py"
        )
        spec = importlib.util.spec_from_file_location("put_presigned_file", script_path)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        received: dict[str, object] = {}

        class PutHandler(BaseHTTPRequestHandler):
            def do_PUT(self):
                received["content_type"] = self.headers.get("Content-Type")
                received["body"] = self.rfile.read(int(self.headers["Content-Length"]))
                self.send_response(200)
                self.end_headers()

            def log_message(self, _format, *args):
                return

        server = HTTPServer(("127.0.0.1", 0), PutHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                payload = Path(directory) / "meeting.mp4"
                payload.write_bytes(b"portable-upload")
                module.upload(
                    f"http://127.0.0.1:{server.server_port}/upload?signature=test",
                    payload,
                    None,
                )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        self.assertIsNone(received["content_type"])
        self.assertEqual(b"portable-upload", received["body"])


class TestScriptSyntax(unittest.TestCase):
    @unittest.skipUnless(shutil.which("sh"), "POSIX shell is not available")
    def test_all_posix_shell_scripts_parse(self):
        for script in sorted(PLUGINS_ROOT.glob("**/*.sh")):
            completed = subprocess.run(
                ["sh", "-n", str(script)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)

    @unittest.skipUnless(powershell_executable(), "PowerShell is not available")
    def test_all_powershell_scripts_parse(self):
        executable = powershell_executable()
        assert executable
        parser = (
            "$tokens = $null; $errors = $null; "
            "[System.Management.Automation.Language.Parser]::ParseFile("
            "$env:PLUGIN_PS_FILE, [ref]$tokens, [ref]$errors) | Out-Null; "
            "if ($errors.Count -gt 0) { "
            "$errors | ForEach-Object { Write-Error $_ }; exit 1 }"
        )
        for script in sorted(PLUGINS_ROOT.glob("**/*.ps1")):
            environment = {**os.environ, "PLUGIN_PS_FILE": str(script)}
            completed = subprocess.run(
                [executable, "-NoProfile", "-NonInteractive", "-Command", parser],
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()

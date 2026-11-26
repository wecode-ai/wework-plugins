"""Exercise real Node/Python/browser-page flows with fake browsers and gh."""

import json
import os
import http.client
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.parse import urlsplit

PLUGIN = Path(__file__).resolve().parents[1] / "plugins/wework-github"
LAUNCHER = PLUGIN / "scripts/mcp-launcher.mjs"
FAKE_GH = r"""
import os, pathlib, subprocess, sys, time
root = pathlib.Path(os.environ['GH_CONFIG_DIR'])
if sys.argv[1:3] == ['api', 'user']:
    (root / 'api-seen').touch()
    if (root / 'authenticated').exists():
        sys.exit(0)
    sys.stderr.write('HTTP 401 PRIVATE_DIAGNOSTIC\n')
    sys.exit(4)
if sys.argv[1:3] == ['auth', 'login']:
    (root / 'child.pid').write_text(str(os.getpid()))
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
    (root / 'grandchild.pid').write_text(str(child.pid))
    sys.stderr.write('PRIVATE_DIAGNOSTIC ghp_FAKE_SECRET\n')
    sys.stderr.write('! First copy your one-time code: ABCD-1234\n')
    sys.stderr.write('Open this URL to continue in your web browser: https://github.com/login/device\n')
    sys.stderr.flush()
    deadline = time.monotonic() + 30
    while not (root / 'allow-complete').exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    if (root / 'allow-complete').exists():
        (root / 'authenticated').touch()
        sys.exit(0)
sys.exit(2)
"""
NODE_PRELOAD = r"""
import cp from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { syncBuiltinESMExports } from 'node:module'
const spawn = cp.spawn
const spawnSync = cp.spawnSync
cp.spawnSync = (command, args, options) => {
  const result = spawnSync(command, args, options)
  fs.writeFileSync(path.join(process.env.WEWORK_TEST_BROWSER_ROOT, 'probe.json'), JSON.stringify({
    command, status: result.status, error: result.error?.code,
    executable: result.status === 0 ? result.stdout.toString('utf8').trim() : undefined,
  }))
  return result
}
cp.spawn = (command, args, options) => {
  const connector = args.some(arg => arg.endsWith('github.py'))
  if (connector) {
    args = ['-c', `import faulthandler, os, runpy, sys
trace = open(os.path.join(os.environ['WEWORK_TEST_BROWSER_ROOT'], 'startup-stack'), 'w')
faulthandler.dump_traceback_later(10, file=trace)
script = sys.argv[1]
sys.argv = sys.argv[1:]
sys.path.insert(0, os.path.dirname(script))
runpy.run_path(script, run_name='__main__')`, ...args]
  }
  const child = spawn(command, args, options)
  if (connector && child.pid) {
    fs.writeFileSync(path.join(process.env.WEWORK_TEST_BROWSER_ROOT, 'connector.pid'), String(child.pid))
  }
  return child
}
syncBuiltinESMExports()
"""
FAKE_OPENER = r"""
import os, sys
from pathlib import Path
root = Path(os.environ['WEWORK_TEST_BROWSER_ROOT'])
temporary = root / 'browser-url.tmp'
temporary.write_text(sys.argv[1], encoding='utf-8')
temporary.replace(root / 'browser-url')
print('PRIVATE_BROWSER_CAPABILITY ' + sys.argv[1])
print('PRIVATE_BROWSER_DIAGNOSTIC', file=sys.stderr)
"""


@unittest.skipUnless(shutil.which("node"), "Node required")
class AuthLauncherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.name != "nt":
            return
        cls.powershell = shutil.which("powershell.exe")
        if not cls.powershell:
            raise unittest.SkipTest(
                "Windows PowerShell required for native launcher test"
            )
        cls.native = tempfile.TemporaryDirectory(prefix="wework-gh-test-native-")
        cls.addClassCleanup(cls.native.cleanup)
        cls.executable = Path(cls.native.name) / "gh.exe"
        compiler = Path(cls.native.name) / "compile.ps1"
        compiler.write_text(
            r"""$source = @'
using System;
using System.Diagnostics;
using System.Text;
public class FakeGh {
  static string Quote(string value) { return "\"" + value.Replace("\"", "\\\"") + "\""; }
  public static int Main(string[] args) {
    var command = new StringBuilder(Quote(Environment.GetEnvironmentVariable("WEWORK_TEST_GH_SCRIPT")));
    foreach (var arg in args) command.Append(" ").Append(Quote(arg));
    var process = new Process();
    process.StartInfo.FileName = Environment.GetEnvironmentVariable("WEWORK_TEST_PYTHON");
    process.StartInfo.Arguments = command.ToString();
    process.StartInfo.UseShellExecute = false;
    process.Start();
    process.WaitForExit();
    return process.ExitCode;
  }
}
'@
Add-Type -TypeDefinition $source -OutputAssembly $env:WEWORK_TEST_GH_EXE -OutputType ConsoleApplication
""",
            encoding="utf-8",
        )
        compiled = subprocess.run(
            [
                cls.powershell,
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(compiler),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
            env={**os.environ, "WEWORK_TEST_GH_EXE": str(cls.executable)},
        )
        if compiled.returncode or not cls.executable.is_file():
            raise AssertionError(
                "Could not compile native fake-gh fixture: " + compiled.stderr
            )

    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="wework gh browser launcher ")
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.profile = self.root / "profile"
        self.profile.mkdir()
        (self.root / "home").mkdir()
        self.fake_script = self.root / "fake-gh.py"
        self.fake_script.write_text(FAKE_GH, encoding="utf-8")
        (self.root / "preload.mjs").write_text(NODE_PRELOAD, encoding="utf-8")
        (self.root / "fake-opener.py").write_text(FAKE_OPENER, encoding="utf-8")
        if os.name == "nt":
            shutil.copyfile(self.executable, self.root / "gh.exe")
        else:
            fake = self.root / "gh"
            fake.write_text(f"#!{sys.executable}\n" + FAKE_GH, encoding="utf-8")
            fake.chmod(0o700)
        self.processes = []
        self.addCleanup(self.cleanup_processes)

    def cleanup_processes(self):
        for record in self.processes:
            process = record["process"]
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
            for name in ("connector.pid", "child.pid", "grandchild.pid"):
                path = (self.root if name == "connector.pid" else self.profile) / name
                if path.exists():
                    pid = int(path.read_text())
                    if self.alive(pid):
                        try:
                            os.kill(
                                pid,
                                signal.SIGTERM if os.name == "nt" else signal.SIGKILL,
                            )
                        except OSError:
                            pass
            for thread in record["readers"]:
                thread.join(timeout=2)
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    stream.close()

    def start(self, action="login", *, direct_node=False):
        for name in ("browser-url", "connector.pid"):
            (self.root / name).unlink(missing_ok=True)
        for name in (
            "child.pid",
            "grandchild.pid",
            "allow-complete",
            "authenticated",
            "api-seen",
        ):
            (self.profile / name).unlink(missing_ok=True)
        environment = {
            key: value
            for key, value in os.environ.items()
            if key.upper()
            not in {
                "GH_TOKEN",
                "GITHUB_TOKEN",
                "GH_CONFIG_DIR",
                "GH_HOST",
                "GH_DEBUG",
                "DEBUG",
                "WEWORK_LOCAL_AUTH_PROTOCOL",
                "WEWORK_LOCAL_AUTH_PARENT_PID",
                "WEWORK_GITHUB_PARENT_PID",
            }
        }
        environment.update(
            PATH=str(self.root) + os.pathsep + os.environ.get("PATH", ""),
            GH_CONFIG_DIR=str(self.profile),
            NODE_OPTIONS="--import=" + (self.root / "preload.mjs").as_uri(),
            BROWSER=shlex.join(
                [sys.executable, str(self.root / "fake-opener.py"), "%s"]
            ),
            WEWORK_TEST_BROWSER_ROOT=str(self.root),
            WEWORK_TEST_GH_SCRIPT=str(self.fake_script),
            WEWORK_TEST_PYTHON=sys.executable,
        )
        command = [shutil.which("node"), str(LAUNCHER)]
        if os.name == "nt" and not direct_node:
            command = [
                self.powershell,
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(PLUGIN / "scripts/local-auth.ps1"),
            ]
        process = subprocess.Popen(
            [*command, action],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            env=environment,
        )
        record = {
            "process": process,
            "stdout": bytearray(),
            "stderr": bytearray(),
            "readers": [],
        }
        for name in ("stdout", "stderr"):

            def collect(stream=getattr(process, name), target=record[name]):
                while True:
                    value = stream.read(4096)
                    if not value:
                        return
                    target.extend(value)

            reader = threading.Thread(target=collect, daemon=True)
            reader.start()
            record["readers"].append(reader)
        self.processes.append(record)
        self.assertIsNone(process.stdin)

        def opened():
            if (self.root / "browser-url").exists():
                return True
            trace = self.root / "startup-stack"
            if trace.exists() and trace.stat().st_size:
                self.fail("Connector startup stack: " + trace.read_text(encoding="utf-8"))
            if process.poll() is not None:
                for reader in record["readers"]:
                    reader.join(timeout=1)
                try:
                    terminal = json.loads(bytes(record["stdout"]).decode("utf-8"))
                except (ValueError, UnicodeError):
                    terminal = {}
                self.fail(
                    f"Launcher exited before opening: returncode={process.returncode}, "
                    f"status={terminal.get('status')}, code={terminal.get('code')}, "
                    f"stdoutBytes={len(record['stdout'])}, stderrBytes={len(record['stderr'])}"
                )
            return False

        # The launcher can spend 2 x 10 seconds probing Python before the
        # browser's bounded 5-second open. This readiness budget covers both;
        # an exited launcher still fails immediately with safe diagnostics.
        try:
            self.wait_for(opened, timeout=30)
        except AssertionError as error:
            probe = self.root / "probe.json"
            self.fail(
                f"{error}; connectorStarted={(self.root / 'connector.pid').exists()}, "
                f"probe={probe.read_text(encoding='utf-8') if probe.exists() else 'missing'}"
            )
        record["address"] = urlsplit(
            (self.root / "browser-url").read_text(encoding="utf-8")
        )
        initial = self.state(record)
        self.assertEqual(initial["status"], "awaiting_confirmation")
        self.assertEqual(initial["action"], action)
        self.assertFalse(initial["started"])
        self.assert_silent(record)
        return record

    def wait_for(self, predicate, timeout=8):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = predicate()
            if value:
                return value
            time.sleep(0.02)
        self.fail("Timed out waiting for the isolated browser flow")

    def request(self, record, route="state", *, method="GET"):
        address = record["address"]
        headers = {"Host": address.netloc}
        body = None
        if method == "POST":
            headers.update(
                Origin=f"http://{address.netloc}",
                **{"Content-Type": "application/json"},
            )
            body = json.dumps({"csrfToken": self.state(record)["csrfToken"]})
        connection = http.client.HTTPConnection("127.0.0.1", address.port, timeout=2)
        try:
            connection.request(method, address.path + route, body=body, headers=headers)
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def state(self, record):
        status, body = self.request(record)
        self.assertEqual(status, 200)
        return json.loads(body)

    def state_with(self, record, status):
        deadline = time.monotonic() + 8
        diagnostic = {}
        while time.monotonic() < deadline:
            value = self.state(record)
            diagnostic = {
                key: value.get(key) for key in ("status", "code", "done", "started")
            }
            if value["status"] == status:
                return value
            if value["done"]:
                self.fail(
                    f"Unexpected safe terminal state while waiting for {status}: {diagnostic}"
                )
            time.sleep(0.02)
        diagnostic.update(
            fakeCompletionAllowed=(self.profile / "allow-complete").exists(),
            fakeAccountSaved=(self.profile / "authenticated").exists(),
            fakeVerificationStarted=(self.profile / "api-seen").exists(),
            launcherReturncode=record["process"].poll(),
        )
        self.fail(f"Browser flow did not reach {status}: {diagnostic}")

    def confirm(self, record):
        self.assertEqual(self.request(record, "confirm", method="POST")[0], 202)
        value = self.state_with(record, "waiting_browser")
        self.assertEqual(value["verificationUrl"], "https://github.com/login/device")
        self.assertEqual(value["userCode"], "ABCD-1234")
        self.assert_silent(record)

    def assert_silent(self, record):
        self.assertEqual(bytes(record["stdout"]), b"")
        self.assertEqual(bytes(record["stderr"]), b"")

    @staticmethod
    def alive(pid):
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.OpenProcess.argtypes = [
                wintypes.DWORD,
                wintypes.BOOL,
                wintypes.DWORD,
            ]
            kernel.OpenProcess.restype = wintypes.HANDLE
            kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            kernel.WaitForSingleObject.restype = wintypes.DWORD
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            handle = kernel.OpenProcess(0x00100000, False, pid)
            if not handle:
                return False
            try:
                return kernel.WaitForSingleObject(handle, 0) == 258
            finally:
                kernel.CloseHandle(handle)
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        probe = subprocess.run(
            ["ps", "-p", str(pid), "-o", "stat="],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        return probe.returncode == 0 and not probe.stdout.strip().startswith("Z")

    def assert_auth_children_stopped(self):
        pids = [
            int((self.profile / name).read_text())
            for name in ("child.pid", "grandchild.pid")
        ]
        self.wait_for(lambda: all(not self.alive(pid) for pid in pids), timeout=5)

    def finish_transport(self, record, *, code):
        self.assertEqual(record["process"].wait(timeout=6), code)
        for reader in record["readers"]:
            reader.join(timeout=6)
            self.assertFalse(
                reader.is_alive(), "The connector must close inherited streams"
            )
        output = bytes(record["stdout"]).decode("utf-8")
        self.assertEqual(bytes(record["stderr"]), b"")
        for secret in (
            "ABCD-1234",
            "github.com/login/device",
            "http://127.0.0.1",
            "PRIVATE",
            "ghp_FAKE_SECRET",
            record["address"].path.strip("/"),
        ):
            self.assertNotIn(secret, output)
        return output

    def finish(self, record, *, code):
        output = self.finish_transport(record, code=code)
        self.assertEqual(len(output.splitlines()), 1)
        result = json.loads(output)
        self.assertEqual(result["title"], "GitHub（Wework）")
        return result

    def acknowledge(self, record):
        self.assertEqual(self.request(record, "ack", method="POST")[0], 200)

    def test_devnull_stdin_page_cancel_cleans_tree_and_releases_profile(self):
        record = self.start()
        self.confirm(record)
        time.sleep(0.1)
        self.assertEqual(self.state(record)["status"], "waiting_browser")
        self.assertEqual(self.request(record, "cancel", method="POST")[0], 200)
        terminal = self.state_with(record, "cancelled")
        self.assertTrue(terminal["done"])
        self.assert_auth_children_stopped()
        self.assert_silent(record)
        self.acknowledge(record)
        self.assertEqual(self.finish(record, code=1)["status"], "cancelled")
        second = self.start()
        self.confirm(second)
        (self.profile / "allow-complete").touch()
        self.state_with(second, "ok")
        self.acknowledge(second)
        self.assertEqual(self.finish(second, code=0)["status"], "ok")

    def test_devnull_stdin_success_keeps_capabilities_on_page_and_only_prints_final_json(
        self,
    ):
        record = self.start()
        self.confirm(record)
        self.assertFalse((self.profile / "authenticated").exists())
        self.assert_silent(record)
        (self.profile / "allow-complete").touch()
        terminal = self.state_with(record, "ok")
        self.assertTrue(terminal["done"])
        self.assertNotIn("userCode", terminal)
        self.assertNotIn("verificationUrl", terminal)
        self.assert_auth_children_stopped()
        self.assert_silent(record)
        self.acknowledge(record)
        self.assertEqual(self.finish(record, code=0)["status"], "ok")

    def test_logout_page_cancel_before_confirmation_never_runs_gh(self):
        record = self.start("logout")
        self.assertEqual(self.request(record, "cancel", method="POST")[0], 200)
        self.state_with(record, "cancelled")
        self.assertFalse((self.profile / "child.pid").exists())
        self.assert_silent(record)
        self.acknowledge(record)
        self.assertEqual(self.finish(record, code=1)["status"], "cancelled")

    @unittest.skipUnless(os.name == "posix", "POSIX signal forwarding")
    def test_sigterm_cleans_python_and_gh_without_page_acknowledgment(self):
        record = self.start(direct_node=True)
        self.confirm(record)
        record["process"].send_signal(signal.SIGTERM)
        self.assertEqual(self.finish(record, code=1)["status"], "cancelled")
        self.assert_auth_children_stopped()

    def test_killing_node_triggers_python_parent_watch_and_cleans_tree_without_ack(
        self,
    ):
        record = self.start(direct_node=True)
        self.confirm(record)
        record["process"].kill()
        expected = 1 if os.name == "nt" else -signal.SIGKILL
        output = self.finish_transport(record, code=expected)
        # TerminateProcess closes the Windows launcher's inherited transport;
        # it cannot guarantee delivery of a final child response. Graceful
        # cancellation above must still deliver exactly one terminal result.
        if os.name != "nt" or output:
            self.assertEqual(len(output.splitlines()), 1)
            result = json.loads(output)
            self.assertEqual(
                result["status"],
                "cancelled",
                f"Safe terminal code: {result.get('code')}",
            )
        connector_pid = int((self.root / "connector.pid").read_text(encoding="utf-8"))
        self.wait_for(lambda: not self.alive(connector_pid), timeout=5)
        self.assert_auth_children_stopped()
        # A new operation on the same profile proves the killed owner left
        # neither a live account worker nor a held cross-process auth lock.
        next_record = self.start(direct_node=True)
        self.confirm(next_record)
        self.request(next_record, "cancel", method="POST")
        self.state_with(next_record, "cancelled")
        self.acknowledge(next_record)
        self.assertEqual(self.finish(next_record, code=1)["status"], "cancelled")
        self.assert_auth_children_stopped()


if __name__ == "__main__":
    unittest.main()

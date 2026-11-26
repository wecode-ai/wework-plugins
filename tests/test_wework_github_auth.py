"""Exercise isolated fake-gh sessions; never authenticate or read real accounts."""

import importlib
import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins/wework-github/scripts"))
auth = importlib.import_module("github_connector.auth")
state = importlib.import_module("github_connector.auth_state")
GitHubError = importlib.import_module("github_connector.errors").GitHubError
_POPEN = subprocess.Popen
_FAKE = r"""
import json, os, subprocess, sys, time
from pathlib import Path
root = Path(os.environ['WEWORK_TEST_AUTH_ROOT'])
args = sys.argv[1:]
mode = os.environ.get('WEWORK_TEST_AUTH_MODE', '')
with (root / 'calls').open('a') as stream:
    stream.write(json.dumps({'args': args, 'stdin': sys.stdin.read(), 'tty': [sys.stdin.isatty(), sys.stdout.isatty(), sys.stderr.isatty()], 'env': {key: os.environ.get(key) for key in ('GH_DEBUG', 'DEBUG', 'GH_FORCE_TTY', 'GH_PROMPT_DISABLED', 'GH_CONFIG_DIR')}}) + '\n')
if args[:2] == ['api', 'user']:
    if mode == 'verify_hang':
        time.sleep(20)
    if mode == 'verify_fail':
        sys.stderr.write('PRIVATE_VERIFICATION ghp_SECRET')
        sys.exit(1)
    sys.exit(0)
if args[:2] != ['auth', 'login']:
    raise AssertionError('Unexpected command')
if mode == 'child':
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
    (root / 'child-pid').write_text(str(child.pid))
sys.stderr.write('PRIVATE_DIAGNOSTIC ghp_SECRET\n')
sys.stderr.flush()
if mode == 'stdout_large':
    sys.stdout.write('PRIVATE' * 11000)
    sys.stdout.flush()
elif mode == 'stderr_large':
    sys.stderr.write('PRIVATE' * 11000)
    sys.stderr.flush()
else:
    if mode != 'url_only':
        sys.stderr.write('! First copy your one-time code: ABCD-EF12\n')
        sys.stderr.flush()
    if mode != 'code_only':
        url = 'https://attacker.example/login/device' if mode == 'bad_url' else 'https://github.com/login/device'
        sys.stdout.write('Open this URL to continue in your web browser: ' + url + '\n')
        sys.stdout.flush()
if mode in ('hang', 'child', 'stdout_large', 'stderr_large'):
    time.sleep(20)
if mode == 'fail':
    sys.exit(1)
"""


MAX_TEST_CONTROL = 4096


def _read_control(source, control):
    """Use raw fd reads: an abandoned daemon never holds sys.stdin's IO lock."""
    try:
        try:
            descriptor = source.fileno()
        except (AttributeError, io.UnsupportedOperation):
            descriptor = None
        pending = bytearray()
        while not control.finished.is_set() and not control.stopped.is_set():
            chunk = (
                os.read(descriptor, MAX_TEST_CONTROL)
                if descriptor is not None
                else source.read(MAX_TEST_CONTROL)
            )
            if not chunk:
                control.request()
                return
            if not isinstance(chunk, bytes):
                control.request("error", "gh_login_control_invalid")
                return
            pending.extend(chunk)
            while b"\n" in pending:
                line, _, tail = pending.partition(b"\n")
                pending = bytearray(tail)
                if len(line) > MAX_TEST_CONTROL:
                    control.request("error", "gh_login_control_invalid")
                    return
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except (ValueError, UnicodeError, RecursionError):
                    control.request("error", "gh_login_control_invalid")
                    return
                if value == {"action": "cancel"}:
                    control.request()
                else:
                    control.request("error", "gh_login_control_invalid")
                return
            if len(pending) > MAX_TEST_CONTROL:
                control.request("error", "gh_login_control_invalid")
                return
    except (OSError, ValueError):
        control.request()


def _pipe_login(*, stdin=None, stdout=None, timeout=600, popen=None):
    """Test-only pipe controller for the callback core; never shipped with the plugin."""
    source = sys.stdin.buffer if stdin is None else stdin
    target = sys.stdout if stdout is None else stdout
    control = auth._Control()
    control.finished = threading.Event()

    def write_event(value):
        target.write(
            json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n"
        )
        target.flush()

    control_thread = threading.Thread(
        target=_read_control, args=(source, control), daemon=True
    )
    control_thread.start()
    try:
        value = auth._execute_login(
            on_event=write_event, control=control, timeout=timeout, popen=popen
        )
        return 0 if value["status"] == "ok" else 1
    finally:
        control.finished.set()
        control_thread.join(timeout=0.1)


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="wework-gh-auth-test-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.fake = self.root / "fake_gh.py"
        self.fake.write_text(_FAKE)
        environment = {
            key: value
            for key, value in os.environ.items()
            if key.upper()
            not in {
                "GH_TOKEN",
                "GITHUB_TOKEN",
                "WEWORK_LOCAL_AUTH_PARENT_PID",
                "WEWORK_GITHUB_PARENT_PID",
                "WEWORK_LOCAL_AUTH_PROTOCOL",
            }
        }
        environment.update(
            WEWORK_TEST_AUTH_ROOT=str(self.root),
            GH_CONFIG_DIR=str(self.root / "profile"),
        )
        patch = mock.patch.dict(os.environ, environment, clear=True)
        patch.start()
        self.addCleanup(patch.stop)
        patch = mock.patch.object(state.Path, "home", return_value=self.root)
        patch.start()
        self.addCleanup(patch.stop)
        read_fd, write_fd = os.pipe()
        self.control = os.fdopen(read_fd, "rb", buffering=0)
        self.writer = os.fdopen(write_fd, "wb", buffering=0)
        self.addCleanup(self.control.close)
        self.addCleanup(self.writer.close)
        self.output = io.StringIO()
        self.spawned = []
        self.on_spawn = None
        self.timers = []
        self.addCleanup(self.finish_timers)

    def finish_timers(self):
        for timer in self.timers:
            timer.join(timeout=1)

    def later(self, callback, delay=0.12):
        timer = threading.Timer(delay, callback)
        self.timers.append(timer)
        timer.start()

    def spawn(self, arguments, **kwargs):
        self.assertEqual(arguments[0], "gh")
        self.assertEqual(kwargs["stdin"], subprocess.PIPE)
        self.assertEqual(kwargs["stdout"], subprocess.PIPE)
        self.assertEqual(kwargs["stderr"], subprocess.PIPE)
        self.assertEqual(kwargs["bufsize"], 0)
        with self.assertRaises(GitHubError) as locked, state.account_lock():
            self.fail("gh must start only while holding the shared profile lock")
        self.assertEqual(locked.exception.code, "gh_login_in_progress")
        process = _POPEN([sys.executable, str(self.fake), *arguments[1:]], **kwargs)
        self.spawned.append(process)
        if self.on_spawn:
            self.on_spawn(arguments)
        return process

    def run_login(self, *, mode="", timeout=3, stdin=None, stdout=None, popen=None):
        os.environ["WEWORK_TEST_AUTH_MODE"] = mode
        return _pipe_login(
            stdin=self.control if stdin is None else stdin,
            stdout=self.output if stdout is None else stdout,
            timeout=timeout,
            popen=popen or self.spawn,
        )

    def events(self):
        return [json.loads(line) for line in self.output.getvalue().splitlines()]

    def records(self):
        path = self.root / "calls"
        return (
            [json.loads(line) for line in path.read_text().splitlines()]
            if path.exists()
            else []
        )

    def assert_clean(self, *, events=None):
        self.assertTrue(all(process.poll() is not None for process in self.spawned))
        self.assertTrue(all(process.returncode is not None for process in self.spawned))
        with state.account_lock():
            pass
        self.assertNotIn("PRIVATE", self.output.getvalue())
        self.assertNotIn("ghp_SECRET", self.output.getvalue())
        events = self.events() if events is None else events
        self.assertEqual(len({event["sessionId"] for event in events}), 1)
        for event in events:
            self.assertNotIn("protocol", event)
            self.assertEqual(event["title"], auth.TITLE)
            self.assertEqual(event["description"], auth.DESCRIPTION)
            self.assertTrue(event["hint"])
            if "errorCode" in event:
                self.assertEqual(event["code"], event["errorCode"])

    def test_callback_core_has_no_stdin_or_host_protocol_dependency(self):
        events = []

        def receive(event):
            if event["status"] == "ok":
                self.assertTrue(
                    all(process.poll() is not None for process in self.spawned)
                )
                with state.account_lock():
                    pass
            events.append(event)

        with (
            mock.patch.object(sys, "stdin", None),
            mock.patch.dict(os.environ, {"WEWORK_LOCAL_AUTH_PROTOCOL": "unsupported"}),
        ):
            result = auth.run_login(
                on_event=receive,
                cancel_event=threading.Event(),
                timeout=3,
                popen=self.spawn,
            )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(
            [event["status"] for event in events],
            ["preparing", "waiting_browser", "verifying", "ok"],
        )
        self.assertEqual(events[-1], result)
        events[-1]["status"] = "mutated_ui_copy"
        self.assertEqual(result["status"], "ok")
        events[-1]["status"] = "ok"
        self.assert_clean(events=events)

    def test_callback_can_bridge_local_page_cancellation_to_running_gh(self):
        os.environ["WEWORK_TEST_AUTH_MODE"] = "hang"
        cancelled = threading.Event()
        events = []

        def receive(event):
            events.append(event)
            if event["status"] == "waiting_browser":
                cancelled.set()

        result = auth.run_login(
            on_event=receive, cancel_event=cancelled, timeout=3, popen=self.spawn
        )
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(result["code"], "gh_login_cancelled")
        self.assertEqual(events[-1], result)
        self.assertEqual(len(self.spawned), 1)
        self.assert_clean(events=events)

    def test_callback_cancel_event_remains_live_during_verification_process(self):
        os.environ["WEWORK_TEST_AUTH_MODE"] = "verify_hang"
        cancelled = threading.Event()
        events = []
        self.on_spawn = lambda arguments: (
            self.later(cancelled.set) if arguments[1:3] == ["api", "user"] else None
        )
        result = auth.run_login(
            on_event=events.append, cancel_event=cancelled, timeout=3, popen=self.spawn
        )
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(len(self.spawned), 2)
        self.assert_clean(events=events)

    def test_callback_cancel_before_start_never_spawns_gh(self):
        cancelled = threading.Event()
        cancelled.set()
        events = []
        result = auth.run_login(
            on_event=events.append, cancel_event=cancelled, timeout=3, popen=self.spawn
        )
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(
            [event["status"] for event in events], ["preparing", "cancelled"]
        )
        self.assertFalse(self.spawned)
        self.assert_clean(events=events)

    def test_callback_failure_still_cleans_gh_and_returns_only_safe_fields(self):
        os.environ["WEWORK_TEST_AUTH_MODE"] = "hang"
        events = []

        def receive(event):
            if event["status"] == "waiting_browser":
                raise RuntimeError("PRIVATE_CALLBACK ghp_SECRET")
            events.append(event)

        result = auth.run_login(
            on_event=receive,
            cancel_event=threading.Event(),
            timeout=3,
            popen=self.spawn,
        )
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["code"], "gh_login_failed")
        self.assertNotIn("PRIVATE", json.dumps(events))
        self.assertEqual(events[-1], result)
        self.assert_clean(events=events)

    def test_callback_core_runs_in_server_worker_without_signal_registration(self):
        events, results = [], []

        def execute():
            results.append(
                auth.run_login(
                    on_event=events.append,
                    cancel_event=threading.Event(),
                    timeout=3,
                    popen=self.spawn,
                )
            )

        with mock.patch.object(
            auth.signal,
            "signal",
            side_effect=AssertionError("worker may not install process signals"),
        ):
            worker = threading.Thread(target=execute)
            worker.start()
            worker.join(timeout=5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(results[0]["status"], "ok")
        self.assert_clean(events=events)

    def test_only_plugin_parent_pid_setting_is_supported(self):
        with auth.parent_watch({"WEWORK_LOCAL_AUTH_PARENT_PID": "invalid"}) as observer:
            self.assertEqual(observer.pid, os.getppid())
            self.assertTrue(observer.alive())
        with auth.parent_watch(
            {
                "WEWORK_GITHUB_PARENT_PID": str(os.getppid()),
                "WEWORK_LOCAL_AUTH_PARENT_PID": "invalid",
            }
        ) as observer:
            self.assertTrue(observer.alive())
        with self.assertRaises(GitHubError) as stopped:
            auth.parent_watch(
                {
                    "WEWORK_GITHUB_PARENT_PID": "invalid",
                    "WEWORK_LOCAL_AUTH_PARENT_PID": str(os.getppid()),
                }
            )
        self.assertEqual(stopped.exception.code, "gh_login_parent_unavailable")

    def test_success_emits_only_safe_paired_prompt_and_verifies_under_shared_lock(self):
        os.environ.update(GH_DEBUG="api", DEBUG="1", GH_FORCE_TTY="1")
        self.assertEqual(self.run_login(), 0)
        events = self.events()
        self.assertEqual(
            [event["status"] for event in events],
            ["preparing", "waiting_browser", "verifying", "ok"],
        )
        self.assertEqual(
            events[1]["verificationUrl"], "https://github.com/login/device"
        )
        self.assertEqual(events[1]["userCode"], "ABCD-EF12")
        calls = self.records()
        self.assertEqual(
            calls[0]["args"],
            [
                "auth",
                "login",
                "--hostname",
                "github.com",
                "--web",
                "--git-protocol",
                "https",
            ],
        )
        self.assertEqual(
            calls[1]["args"],
            ["api", "user", "--silent", "--hostname", "github.com", "--method", "GET"],
        )
        for call in calls:
            self.assertEqual(call["stdin"], "")
            self.assertEqual(call["tty"], [False, False, False])
            self.assertEqual(call["env"]["GH_PROMPT_DISABLED"], "1")
            self.assertEqual(call["env"]["GH_CONFIG_DIR"], str(self.root / "profile"))
            self.assertTrue(
                all(
                    call["env"][key] is None
                    for key in ("GH_DEBUG", "DEBUG", "GH_FORCE_TTY")
                )
            )
        self.assert_clean()

    def test_unpaired_or_untrusted_prompts_never_create_browser_action(self):
        for mode in ("url_only", "code_only", "bad_url"):
            with self.subTest(mode=mode):
                self.output = io.StringIO()
                self.assertEqual(self.run_login(mode=mode), 0)
                self.assertNotIn(
                    "waiting_browser", [event["status"] for event in self.events()]
                )
                self.assert_clean()

    def test_environment_tokens_block_login_without_process_or_lock(self):
        for key in ("GH_TOKEN", "GITHUB_TOKEN"):
            with (
                self.subTest(key=key),
                mock.patch.dict(os.environ, {key: "PRIVATE_ENV_TOKEN"}),
                mock.patch.object(auth, "account_lock") as lock,
            ):
                self.output = io.StringIO()
                self.assertEqual(self.run_login(), 1)
                self.assertEqual(self.events()[-1]["errorCode"], "gh_login_env_token")
                self.assertFalse(self.spawned)
                lock.assert_not_called()
                self.assert_clean()

    def test_missing_cli_and_failed_login_are_redacted(self):
        self.assertEqual(
            self.run_login(popen=mock.Mock(side_effect=FileNotFoundError("PRIVATE"))), 1
        )
        self.assertEqual(self.events()[-1]["errorCode"], "gh_missing")
        self.output = io.StringIO()
        self.assertEqual(self.run_login(mode="fail"), 1)
        self.assertEqual(self.events()[-1]["errorCode"], "gh_login_failed")
        self.assertNotIn("verifying", [event["status"] for event in self.events()])
        self.assert_clean()

    def test_verification_failure_never_claims_connected(self):
        self.assertEqual(self.run_login(mode="verify_fail"), 1)
        self.assertEqual(self.events()[-1]["errorCode"], "gh_login_verification_failed")
        self.assertNotIn("ok", [event["status"] for event in self.events()])
        self.assert_clean()

    def test_control_cancel_and_eof_kill_and_reap_login(self):
        self.later(lambda: self.writer.write(b'{"action":"cancel"}\n'))
        self.assertEqual(self.run_login(mode="hang"), 1)
        self.assertEqual(self.events()[-1]["status"], "cancelled")
        self.assert_clean()
        self.output = io.StringIO()
        self.assertEqual(self.run_login(stdin=io.BytesIO()), 1)
        self.assertEqual(self.events()[-1]["status"], "cancelled")
        self.assert_clean()

    def test_invalid_or_oversized_control_is_fail_closed(self):
        for value in (
            b'{"action":"launch","secret":"PRIVATE"}\n',
            b"not-json\n",
            b'"' + b"a" * 5000 + b'"\n',
            b"[" * 1500 + b"]" * 1500 + b"\n",
            b"null\n",
        ):
            with self.subTest(length=len(value)):
                self.output = io.StringIO()
                self.assertEqual(self.run_login(stdin=io.BytesIO(value)), 1)
                self.assertEqual(
                    self.events()[-1]["errorCode"], "gh_login_control_invalid"
                )
                self.assert_clean()

    def test_timeout_covers_login_and_verification_and_releases_lock(self):
        for mode in ("hang", "verify_hang"):
            with self.subTest(mode=mode):
                self.output = io.StringIO()
                started = time.monotonic()
                self.assertEqual(self.run_login(mode=mode, timeout=0.35), 1)
                self.assertLess(time.monotonic() - started, 3)
                self.assertEqual(self.events()[-1]["status"], "expired")
                self.assertEqual(self.events()[-1]["errorCode"], "gh_login_timeout")
                self.assert_clean()

    def test_control_cancel_during_verification_is_monitored(self):
        def started(arguments):
            if arguments[1:3] == ["api", "user"]:
                self.later(lambda: self.writer.write(b'{"action":"cancel"}\n'))

        self.on_spawn = started
        self.assertEqual(self.run_login(mode="verify_hang"), 1)
        self.assertEqual(self.events()[-1]["status"], "cancelled")
        self.assert_clean()

    def test_signals_request_cancellation_and_handlers_are_restored(self):
        previous = {
            number: signal.getsignal(number)
            for number in (signal.SIGINT, signal.SIGTERM)
        }
        handlers = {}
        original = signal.signal

        def install(number, callback):
            handlers[number] = callback
            return original(number, callback)

        self.on_spawn = lambda _arguments: self.later(
            lambda: handlers[signal.SIGTERM](signal.SIGTERM, None)
        )
        with mock.patch.object(auth.signal, "signal", side_effect=install):
            self.assertEqual(self.run_login(mode="hang"), 1)
        self.assertEqual(self.events()[-1]["status"], "cancelled")
        for number, handler in previous.items():
            self.assertIs(signal.getsignal(number), handler)
        self.assert_clean()

    def test_parent_loss_cancels_even_when_stdin_stays_open(self):
        alive = threading.Event()
        alive.set()
        parent = mock.Mock()
        parent.alive.side_effect = alive.is_set
        self.on_spawn = lambda _arguments: self.later(alive.clear)
        with mock.patch.object(auth, "_ParentWatch", return_value=parent):
            self.assertEqual(self.run_login(mode="hang"), 1)
        self.assertEqual(self.events()[-1]["errorCode"], "gh_login_parent_unavailable")
        parent.close.assert_called_once()
        self.assert_clean()

    def test_invalid_parent_never_starts_authentication(self):
        for pid in ("0", "-1", "invalid", str(os.getpid())):
            with (
                self.subTest(pid=pid),
                mock.patch.dict(os.environ, {"WEWORK_GITHUB_PARENT_PID": pid}),
            ):
                self.output = io.StringIO()
                self.assertEqual(self.run_login(), 1)
                self.assertEqual(
                    self.events()[-1]["errorCode"], "gh_login_parent_unavailable"
                )
                self.assertFalse(self.spawned)
                self.assert_clean()

    def test_output_limits_stop_both_streams_without_forwarding_them(self):
        for mode in ("stdout_large", "stderr_large"):
            with self.subTest(mode=mode):
                self.output = io.StringIO()
                self.assertEqual(self.run_login(mode=mode), 1)
                self.assertEqual(
                    self.events()[-1]["errorCode"], "gh_login_output_limit"
                )
                self.assert_clean()

    def test_broken_event_pipe_still_reaps_process_and_releases_lock(self):
        class BrokenOutput:
            def write(self, value):
                if json.loads(value)["status"] == "waiting_browser":
                    raise BrokenPipeError("PRIVATE")

            def flush(self):
                pass

        self.assertEqual(self.run_login(mode="hang", stdout=BrokenOutput()), 1)
        self.assertTrue(all(process.poll() is not None for process in self.spawned))
        with state.account_lock():
            pass

    def test_terminal_event_follows_cleanup_and_lock_release(self):
        target = self.output
        test = self

        class CheckingOutput:
            def write(self, value):
                if json.loads(value)["status"] in (
                    "ok",
                    "expired",
                    "cancelled",
                    "error",
                ):
                    test.assertTrue(
                        all(process.poll() is not None for process in test.spawned)
                    )
                    with state.account_lock():
                        pass
                target.write(value)

            def flush(self):
                pass

        self.assertEqual(self.run_login(stdout=CheckingOutput()), 0)
        self.assert_clean()

    def test_same_profile_busy_error_does_not_start_gh(self):
        with state.account_lock():
            self.assertEqual(self.run_login(), 1)
        self.assertFalse(self.spawned)
        self.assertEqual(self.events()[-1]["errorCode"], "gh_login_in_progress")
        self.assert_clean()

    def test_cancel_cleans_a_descendant_inheriting_gh_output_pipes(self):
        child_pid = self.root / "child-pid"

        def cancel_when_child_exists():
            until = time.monotonic() + 2
            while not child_pid.exists() and time.monotonic() < until:
                time.sleep(0.01)
            self.writer.write(b'{"action":"cancel"}\n')

        self.later(cancel_when_child_exists, delay=0.01)
        self.assertEqual(self.run_login(mode="child"), 1)
        self.assertTrue(child_pid.exists())
        pid = int(child_pid.read_text())
        if os.name == "nt":
            try:
                parent = auth._ParentWatch({"WEWORK_GITHUB_PARENT_PID": str(pid)})
            except GitHubError as error:
                self.assertEqual(error.code, "gh_login_parent_unavailable")
            else:
                try:
                    self.assertFalse(parent.alive())
                finally:
                    parent.close()
        else:
            # A reparented zombie is terminated too; kill(0) alone sees zombies.
            probe = subprocess.run(
                ["ps", "-p", str(pid), "-o", "stat="],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertTrue(
                probe.returncode != 0 or probe.stdout.strip().startswith("Z")
            )
        self.assert_clean()


class ProfileLockTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="wework-gh-lock-test-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        patch = mock.patch.object(state.Path, "home", return_value=self.root)
        patch.start()
        self.addCleanup(patch.stop)

    def test_profile_precedence_and_appdata_platform_boundary(self):
        environment = {
            "GH_CONFIG_DIR": str(self.root / "explicit"),
            "XDG_CONFIG_HOME": str(self.root / "xdg"),
            "APPDATA": str(self.root / "appdata"),
        }
        self.assertEqual(
            state.profile_directory(environment), (self.root / "explicit").resolve()
        )
        del environment["GH_CONFIG_DIR"]
        self.assertEqual(
            state.profile_directory(environment), (self.root / "xdg/gh").resolve()
        )
        del environment["XDG_CONFIG_HOME"]
        expected = (
            self.root / "appdata/GitHub CLI"
            if os.name == "nt"
            else self.root / ".config/gh"
        )
        self.assertEqual(state.profile_directory(environment), expected.resolve())
        if os.name != "nt":
            self.assertEqual(
                state.lock_path(environment), state.lock_path({"APPDATA": "different"})
            )

    def test_stable_lock_outside_package_isolates_profiles_and_never_holds_credentials(
        self,
    ):
        first = {"GH_CONFIG_DIR": str(self.root / "a")}
        same = {"GH_CONFIG_DIR": str(self.root / "a/../a")}
        other = {"GH_CONFIG_DIR": str(self.root / "b")}
        self.assertEqual(state.lock_path(first), state.lock_path(same))
        self.assertTrue(state.lock_path(first).is_relative_to(self.root / ".wework"))
        with state.account_lock(environment=first):
            with (
                self.assertRaises(GitHubError) as locked,
                state.account_lock(environment=same),
            ):
                self.fail("same real gh profile must contend")
            self.assertEqual(locked.exception.code, "gh_login_in_progress")
            with state.account_lock(environment=other):
                pass
        self.assertEqual(state.lock_path(first).read_bytes(), b"\x00")
        if os.name != "nt":
            self.assertEqual(state.lock_path(first).stat().st_mode & 0o777, 0o600)
        with state.account_lock(environment=same):
            pass

    def test_independent_processes_and_working_directories_share_the_same_lock(self):
        script = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
Path.home = classmethod(lambda cls: Path(sys.argv[2]))
from github_connector.auth_state import account_lock
from github_connector.errors import GitHubError
try:
    with account_lock(environment={'GH_CONFIG_DIR': sys.argv[3]}):
        print('acquired')
except GitHubError as error:
    print(error.code)
    sys.exit(73)
"""
        profile = {"GH_CONFIG_DIR": str(self.root / "shared-profile")}
        command = [
            sys.executable,
            "-c",
            script,
            str(ROOT / "plugins/wework-github/scripts"),
            str(self.root),
            profile["GH_CONFIG_DIR"],
        ]
        with state.account_lock(environment=profile):
            result = subprocess.run(
                command,
                cwd=self.root,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        self.assertEqual(result.returncode, 73)
        self.assertEqual(result.stdout.strip(), "gh_login_in_progress")
        result = subprocess.run(
            command,
            cwd=self.root,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "acquired")


if __name__ == "__main__":
    unittest.main()

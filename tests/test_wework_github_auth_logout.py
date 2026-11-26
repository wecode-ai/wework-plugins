"""Exercise plugin-owned logout against a temporary fake gh, never real accounts."""

import contextlib
import importlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins/wework-github/scripts"))
logout_module = importlib.import_module("github_connector.auth_logout")
GitHubError = importlib.import_module("github_connector.errors").GitHubError
_POPEN = subprocess.Popen
_FAKE = r"""
import json, os, sys, time
from pathlib import Path
root = Path(os.environ['WEWORK_TEST_LOGOUT_ROOT'])
mode = os.environ.get('WEWORK_TEST_LOGOUT_MODE', '')
args = sys.argv[1:]
record = {'args': args, 'stdin': sys.stdin.read(), 'env': {key: os.environ.get(key) for key in ('GH_CONFIG_DIR', 'GH_DEBUG', 'DEBUG', 'GH_PROMPT_DISABLED')}}
with (root / 'calls').open('a') as stream:
    stream.write(json.dumps(record) + '\n')
if mode == 'sleep':
    time.sleep(10)
if mode in ('stdout_large', 'stderr_large'):
    (sys.stdout if mode == 'stdout_large' else sys.stderr).write('PRIVATE' * 3000)
    sys.exit(1)
state = json.loads((root / 'state').read_text())
if args[:3] == ['config', 'get', 'user']:
    if mode == 'config_error' or (mode == 'verify_error' and len(state) == 1):
        sys.stderr.write('PRIVATE_CONFIG_DETAIL')
        sys.exit(1)
    if mode == 'invalid_profile':
        sys.stdout.write(os.environ.get('WEWORK_TEST_PROFILE', '--evil'))
    elif state:
        sys.stdout.write(state[0] + '\n')
    else:
        sys.stderr.write('could not find key "user" PRIVATE')
        sys.exit(1)
elif args[:2] == ['auth', 'logout']:
    if mode == 'logout_fail':
        sys.stderr.write('PRIVATE_TOKEN ghp_SECRET')
        sys.exit(1)
    if mode != 'no_removal':
        state.remove(args[args.index('--user') + 1])
        (root / 'state').write_text(json.dumps(state))
    sys.stderr.write('PRIVATE_LOGOUT_DETAIL')
elif args[:2] == ['api', 'user']:
    if mode == 'health_network':
        sys.stderr.write('PRIVATE_NETWORK_DETAIL')
        sys.exit(1)
    if not state:
        sys.stderr.write('HTTP 401 PRIVATE_TOKEN ghp_SECRET')
        sys.exit(1)
else:
    raise AssertionError('Unexpected gh command')
"""


class LogoutTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="wework-gh-logout-test-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.fake = self.root / "fake_gh.py"
        self.fake.write_text(_FAKE)
        (self.root / "state").write_text(json.dumps(["active-user", "other-user"]))
        environment = {
            key: value
            for key, value in os.environ.items()
            if key.upper()
            not in {
                "GH_TOKEN",
                "GITHUB_TOKEN",
                "GH_CONFIG_DIR",
                "GH_HOST",
                "GH_ENTERPRISE_TOKEN",
                "GITHUB_ENTERPRISE_TOKEN",
            }
        }
        environment.update(
            WEWORK_TEST_LOGOUT_ROOT=str(self.root),
            GH_CONFIG_DIR=str(self.root / "profile"),
        )
        self.environment = mock.patch.dict(os.environ, environment, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.spawned = []
        self.locked = False

        @contextlib.contextmanager
        def lock():
            self.assertFalse(self.locked)
            self.locked = True
            try:
                yield
            finally:
                self.locked = False

        self.lock = mock.patch.object(logout_module, "account_lock", side_effect=lock)
        self.lock.start()
        self.addCleanup(self.lock.stop)

        def spawn(arguments, **kwargs):
            self.assertTrue(self.locked, "all gh operations must hold the shared lock")
            self.assertEqual(arguments[0], "gh")
            process = _POPEN([sys.executable, str(self.fake), *arguments[1:]], **kwargs)
            self.spawned.append(process)
            return process

        self.popen = mock.patch.object(
            logout_module.subprocess, "Popen", side_effect=spawn
        )
        self.popen.start()
        self.addCleanup(self.popen.stop)

    def records(self):
        path = self.root / "calls"
        return (
            [json.loads(line) for line in path.read_text().splitlines()]
            if path.exists()
            else []
        )

    def assert_safe(self, result):
        value = json.dumps(result)
        self.assertNotIn("PRIVATE", value)
        self.assertNotIn("ghp_SECRET", value)
        self.assertTrue(all(process.poll() is not None for process in self.spawned))
        self.assertFalse(self.locked)
        self.assertIsInstance(result["changed"], bool)
        self.assertIsInstance(result["accountRevoked"], bool)

    def test_only_active_account_is_removed_and_other_saved_account_stays_connected(
        self,
    ):
        os.environ.update(GH_DEBUG="api", DEBUG="1")
        result = logout_module.logout()
        self.assertEqual(
            result,
            {
                "status": "ok",
                "connected": True,
                "changed": True,
                "accountRevoked": True,
            },
        )
        self.assertEqual(json.loads((self.root / "state").read_text()), ["other-user"])
        calls = self.records()
        self.assertEqual(
            calls[1]["args"],
            ["auth", "logout", "--hostname", "github.com", "--user", "active-user"],
        )
        self.assertEqual(
            calls[-1]["args"],
            ["api", "user", "--silent", "--hostname", "github.com", "--method", "GET"],
        )
        for call in calls:
            self.assertEqual(call["stdin"], "")
            self.assertEqual(call["env"]["GH_CONFIG_DIR"], str(self.root / "profile"))
            self.assertIsNone(call["env"]["GH_DEBUG"])
            self.assertIsNone(call["env"]["DEBUG"])
            self.assertEqual(call["env"]["GH_PROMPT_DISABLED"], "1")
        self.assert_safe(result)

    def test_final_account_logout_and_empty_logout_are_idempotent(self):
        (self.root / "state").write_text(json.dumps(["active-user"]))
        self.assertEqual(
            logout_module.logout(),
            {
                "status": "ok",
                "connected": False,
                "changed": True,
                "accountRevoked": True,
            },
        )
        self.assertEqual(
            logout_module.logout(),
            {
                "status": "ok",
                "connected": False,
                "changed": False,
                "accountRevoked": False,
            },
        )
        self.assertEqual(
            sum(call["args"][:2] == ["auth", "logout"] for call in self.records()), 1
        )

    def test_environment_token_blocks_logout_before_lock_or_process(self):
        for key in ("GH_TOKEN", "GITHUB_TOKEN"):
            with (
                self.subTest(key=key),
                mock.patch.dict(os.environ, {key: "PRIVATE_TOKEN"}),
            ):
                result = logout_module.logout()
                self.assertEqual(result["errorCode"], "gh_logout_env_token")
                self.assertFalse(result["accountRevoked"])
                self.assertFalse(result["changed"])
                self.assertFalse(self.spawned)
                self.assert_safe(result)

    def test_invalid_profile_and_config_errors_never_attempt_logout(self):
        for mode in ("invalid_profile", "config_error"):
            for profile in ("--evil", "user;command", "user\nother", "a" * 40, ""):
                with (
                    self.subTest(mode=mode, profile=profile),
                    mock.patch.dict(
                        os.environ,
                        {
                            "WEWORK_TEST_LOGOUT_MODE": mode,
                            "WEWORK_TEST_PROFILE": profile,
                        },
                    ),
                ):
                    before = len(self.records())
                    result = logout_module.logout()
                    self.assertEqual(result["errorCode"], "gh_logout_failed")
                    self.assertFalse(result["accountRevoked"])
                    self.assertFalse(result["changed"])
                    self.assertEqual(len(self.records()), before + 1)
                    self.assert_safe(result)

    def test_failed_or_unconfirmed_logout_never_selects_another_account(self):
        for mode in ("logout_fail", "no_removal"):
            with (
                self.subTest(mode=mode),
                mock.patch.dict(os.environ, {"WEWORK_TEST_LOGOUT_MODE": mode}),
            ):
                result = logout_module.logout()
                self.assertEqual(result["errorCode"], "gh_logout_failed")
                self.assertEqual(result["accountRevoked"], mode == "no_removal")
                self.assertEqual(result["changed"], mode == "no_removal")
                self.assertEqual(
                    json.loads((self.root / "state").read_text()),
                    ["active-user", "other-user"],
                )
                self.assert_safe(result)

    def test_health_network_failure_does_not_claim_disconnected(self):
        os.environ["WEWORK_TEST_LOGOUT_MODE"] = "health_network"
        result = logout_module.logout()
        self.assertEqual(result["errorCode"], "gh_logout_health_failed")
        self.assertTrue(result["changed"])
        self.assertTrue(result["accountRevoked"])
        self.assertIn("Refresh the connection status", result["hint"])
        self.assertNotIn("retry", result["hint"].lower())
        self.assertNotIn("connected", result)
        self.assertEqual(json.loads((self.root / "state").read_text()), ["other-user"])
        self.assertEqual(
            sum(call["args"][:2] == ["auth", "logout"] for call in self.records()), 1
        )
        self.assert_safe(result)

    def test_post_logout_profile_verification_failure_preserves_local_mutation(self):
        os.environ["WEWORK_TEST_LOGOUT_MODE"] = "verify_error"
        result = logout_module.logout()
        self.assertEqual(result["errorCode"], "gh_logout_failed")
        self.assertTrue(result["changed"])
        self.assertTrue(result["accountRevoked"])
        self.assertNotIn("connected", result)
        self.assertIn("Refresh the connection status", result["hint"])
        self.assertEqual(json.loads((self.root / "state").read_text()), ["other-user"])
        self.assert_safe(result)

    def test_timeout_output_limits_and_missing_cli_are_redacted_and_reaped(self):
        for mode, code in (
            ("sleep", "gh_logout_timeout"),
            ("stdout_large", "gh_logout_output_limit"),
            ("stderr_large", "gh_logout_output_limit"),
        ):
            with (
                self.subTest(mode=mode),
                mock.patch.dict(os.environ, {"WEWORK_TEST_LOGOUT_MODE": mode}),
                mock.patch.object(
                    logout_module, "TIMEOUT", 0.25 if mode == "sleep" else 5
                ),
            ):
                result = logout_module.logout()
                self.assertEqual(result["errorCode"], code)
                self.assertFalse(result["accountRevoked"])
                self.assertFalse(result["changed"])
                self.assert_safe(result)
        with mock.patch.object(
            logout_module.subprocess, "Popen", side_effect=FileNotFoundError()
        ):
            result = logout_module.logout()
            self.assertEqual(result["errorCode"], "gh_missing")
            self.assertFalse(result["accountRevoked"])
            self.assertFalse(result["changed"])

    def test_account_lock_conflict_has_no_side_effects(self):
        for code in (
            "gh_login_in_progress",
            "gh_auth_profile_invalid",
            "gh_auth_lock_failed",
        ):
            with (
                self.subTest(code=code),
                mock.patch.object(
                    logout_module,
                    "account_lock",
                    side_effect=GitHubError(code, "PRIVATE"),
                ),
            ):
                result = logout_module.logout()
            self.assertEqual(result["errorCode"], code)
            self.assertFalse(result["accountRevoked"])
            self.assertFalse(result["changed"])
            self.assertFalse(self.spawned)
            self.assert_safe(result)


if __name__ == "__main__":
    unittest.main()

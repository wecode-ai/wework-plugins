"""Run the CLI boundary against a temporary fake gh, never real authentication."""

import importlib
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins/wework-github/scripts"))
cli = importlib.import_module("github_connector.cli")
GitHubError = importlib.import_module("github_connector.errors").GitHubError
_POPEN = subprocess.Popen
_FAKE = r"""
import json, os, sys, time
mode = os.environ.get('WEWORK_TEST_GH_MODE', 'record')
if mode == 'sleep':
    time.sleep(10)
elif mode in ('stdout_large', 'stderr_large'):
    stream = sys.stdout.buffer if mode == 'stdout_large' else sys.stderr.buffer
    stream.write(b'x' * (4 * 1024 * 1024 + 10 if mode == 'stdout_large' else 9000))
    stream.flush()
elif mode == 'fail':
    sys.stdout.write('PRIVATE_PROVIDER_BODY')
    sys.stderr.write(os.environ.get('WEWORK_TEST_GH_ERROR', 'private unknown failure'))
    sys.exit(int(os.environ.get('WEWORK_TEST_GH_EXIT', '1')))
elif mode == 'empty':
    pass
elif mode == 'logs':
    sys.stdout.write('line one\nline two\n')
else:
    args = sys.argv[1:]
    body = sys.stdin.read() if '--input' in args else None
    result = {'args': args, 'body': body, 'environment': {
        key: os.environ.get(key) for key in ('GH_TOKEN', 'GITHUB_TOKEN', 'GH_CONFIG_DIR',
        'GH_HOST', 'GH_PROMPT_DISABLED', 'GH_PAGER', 'PAGER', 'NO_COLOR', 'GH_DEBUG', 'DEBUG', 'GH_FORCE_TTY')}}
    sys.stdout.write(json.dumps(result))
"""


class GitHubCliTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="wework-gh-cli-test-")
        self.addCleanup(self.directory.cleanup)
        self.fake = Path(self.directory.name) / "fake_gh.py"
        self.fake.write_text(_FAKE, encoding="utf-8")
        # Do not forward any real developer authentication to the fake process.
        environment = {
            key: value
            for key, value in os.environ.items()
            if key.upper()
            not in {
                "GH_TOKEN",
                "GITHUB_TOKEN",
                "GH_ENTERPRISE_TOKEN",
                "GITHUB_ENTERPRISE_TOKEN",
                "GH_CONFIG_DIR",
                "GH_HOST",
            }
        }
        environment.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        self.environment = mock.patch.dict(os.environ, environment, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.spawned = []

        def fake_popen(arguments, **kwargs):
            self.assertEqual(arguments[0], "gh")
            process = _POPEN([sys.executable, str(self.fake), *arguments[1:]], **kwargs)
            self.spawned.append(process)
            return process

        self.popen = mock.patch.object(cli.subprocess, "Popen", side_effect=fake_popen)
        self.popen.start()
        self.addCleanup(self.popen.stop)
        self.client = cli.GitHubClient()

    def mode(self, name, **extra):
        os.environ["WEWORK_TEST_GH_MODE"] = name
        os.environ.update(extra)

    def error(self, code, operation):
        with self.assertRaises(GitHubError) as caught:
            operation()
        self.assertEqual(caught.exception.code, code)
        self.assertNotIn("PRIVATE_PROVIDER_BODY", caught.exception.message)
        self.assertNotIn("private", caught.exception.message.lower())
        self.assertTrue(all(process.poll() is not None for process in self.spawned))
        return caught.exception

    def test_read_uses_fixed_host_and_encoded_query_without_field_magic(self):
        result = self.client.request(
            "GET",
            "/search/issues",
            query={
                "q": '@secret/file & repo:octo/demo "中文"',
                "page": 2,
                "per_page": 100,
            },
        )
        args = result["args"]
        self.assertEqual(args[0], "api")
        self.assertEqual(
            parse_qs(urlsplit(args[1]).query),
            {
                "q": ['@secret/file & repo:octo/demo "中文"'],
                "page": ["2"],
                "per_page": ["100"],
            },
        )
        self.assertEqual(args[args.index("--hostname") + 1], "github.com")
        self.assertEqual(args[args.index("--method") + 1], "GET")
        self.assertNotIn("--field", args)
        self.assertNotIn("--raw-field", args)
        self.assertNotIn("--input", args)
        self.assertNotIn("--paginate", args)
        self.assertIsNone(result["body"])

    def test_all_write_methods_send_only_stdin_json_and_do_not_retry(self):
        body = {
            "title": "Sensitive business title",
            "body": "line1\nline2 @file",
            "labels": [],
            "milestone": None,
            "draft": False,
            "nested": {"n": 2},
        }
        for method in ("POST", "PATCH", "PUT"):
            with self.subTest(method=method):
                before = len(self.spawned)
                result = self.client.request(
                    method, "/repos/octo/demo/issues", body=body
                )
                self.assertEqual(json.loads(result["body"]), body)
                self.assertNotIn(body["title"], " ".join(result["args"]))
                self.assertEqual(
                    result["args"][result["args"].index("--input") + 1], "-"
                )
                self.assertEqual(len(self.spawned), before + 1)
        self.mode("fail", WEWORK_TEST_GH_ERROR="HTTP 500 private diagnostic")
        before = len(self.spawned)
        self.error(
            "github_cli_failed",
            lambda: self.client.request("POST", "/repos/octo/demo/issues", body=body),
        )
        self.assertEqual(len(self.spawned), before + 1)

    def test_empty_write_body_and_empty_response_are_preserved(self):
        result = self.client.request(
            "POST", "/repos/octo/demo/actions/runs/1/rerun", body={}
        )
        self.assertEqual(json.loads(result["body"]), {})
        self.assertIn("--input", result["args"])
        self.mode("empty")
        self.assertIsNone(
            self.client.request("POST", "/repos/octo/demo/actions/runs/1/rerun")
        )

    def test_diff_and_job_logs_use_gh_without_exporting_tokens_or_locations(self):
        result = self.client.request(
            "GET", "/repos/octo/demo/pulls/2", accept="application/vnd.github.diff"
        )
        self.assertIn("Accept: application/vnd.github.diff", result["args"])
        self.mode("logs")
        self.assertEqual(
            self.client.job_logs("/repos/octo/demo/actions/jobs/3/logs"),
            "line one\nline two\n",
        )
        with mock.patch.object(cli, "_run", return_value=b"log") as run:
            self.client.job_logs("/repos/octo/demo/actions/jobs/3/logs")
        args = run.call_args.args[0]
        self.assertNotIn("--include", args)
        self.assertNotIn("--verbose", args)
        self.assertNotIn("auth", args)
        self.assertNotIn("token", args)
        self.assertNotIn("Authorization", " ".join(args))

    def test_inherits_shared_gh_authentication_environment_but_disables_debug(self):
        os.environ.update(
            GH_TOKEN="fake-gh-token",
            GITHUB_TOKEN="fake-github-token",
            GH_CONFIG_DIR="profile owned by host",
            GH_HOST="other.example",
            GH_DEBUG="api",
            DEBUG="1",
            GH_FORCE_TTY="1",
        )
        result = self.client.request("GET", "/user")
        environment = result["environment"]
        self.assertEqual(environment["GH_TOKEN"], "fake-gh-token")
        self.assertEqual(environment["GITHUB_TOKEN"], "fake-github-token")
        self.assertEqual(environment["GH_CONFIG_DIR"], "profile owned by host")
        self.assertEqual(environment["GH_HOST"], "other.example")
        self.assertEqual(
            result["args"][result["args"].index("--hostname") + 1], "github.com"
        )
        self.assertEqual(environment["GH_PROMPT_DISABLED"], "1")
        self.assertEqual(environment["GH_PAGER"], "")
        self.assertEqual(environment["PAGER"], "")
        self.assertEqual(environment["NO_COLOR"], "1")
        for key in ("GH_DEBUG", "DEBUG", "GH_FORCE_TTY"):
            self.assertIsNone(environment[key])
        # The transport shares gh's active account; it neither creates a plugin
        # profile nor promises token/logout isolation from other gh consumers.
        self.assertEqual(os.environ["GH_DEBUG"], "api")

    def test_auth_rate_permission_and_network_classification_is_redacted(self):
        for exit_code, diagnostic, expected in (
            ("4", "private login message", "github_auth_required"),
            (
                "1",
                "gh: Bad credentials (HTTP 401) private token",
                "github_auth_required",
            ),
            ("1", "gh: Forbidden (HTTP 403) private data", "github_forbidden"),
            ("1", "gh: Not Found (HTTP 404) private URL", "github_not_found"),
            (
                "1",
                "gh: rate limit exceeded (HTTP 403) private URL",
                "github_rate_limited",
            ),
            ("1", "gh: status (HTTP 429) private URL", "github_rate_limited"),
            (
                "1",
                "private connection reset; gh auth login help text",
                "github_cli_failed",
            ),
        ):
            with self.subTest(expected=expected, diagnostic=diagnostic):
                self.mode(
                    "fail",
                    WEWORK_TEST_GH_ERROR=diagnostic,
                    WEWORK_TEST_GH_EXIT=exit_code,
                )
                before = len(self.spawned)
                self.error(expected, lambda: self.client.request("GET", "/user"))
                self.assertEqual(len(self.spawned), before + 1)

    def test_timeout_and_output_limits_kill_and_reap_processes(self):
        self.mode("sleep")
        started = time.monotonic()
        self.error(
            "github_cli_timeout",
            lambda: cli.GitHubClient(timeout=0.15).request("GET", "/user"),
        )
        # A child that never reads stdin must not block the caller before the
        # timeout can fire, even when the JSON body exceeds the pipe capacity.
        self.error(
            "github_cli_timeout",
            lambda: cli.GitHubClient(timeout=0.15).request(
                "POST", "/repos/octo/demo/issues", body={"body": "x" * 300000}
            ),
        )
        self.assertLess(time.monotonic() - started, 3)
        for mode in ("stdout_large", "stderr_large"):
            with self.subTest(mode=mode):
                self.mode(mode)
                self.error(
                    "response_too_large", lambda: self.client.request("GET", "/user")
                )

    def test_health_returns_only_status_and_uses_silent_user_probe(self):
        with mock.patch.object(
            cli, "_run", return_value=b"private unexpected output"
        ) as run:
            self.assertEqual(cli.health(), {"status": "ok"})
        self.assertEqual(
            run.call_args.args[0],
            ["api", "user", "--silent", "--hostname", "github.com", "--method", "GET"],
        )
        self.mode("fail", WEWORK_TEST_GH_EXIT="4", WEWORK_TEST_GH_ERROR="private")
        self.assertEqual(cli.health()["status"], "need_login")
        self.mode(
            "fail", WEWORK_TEST_GH_EXIT="1", WEWORK_TEST_GH_ERROR="HTTP 403 private"
        )
        self.assertEqual(cli.health()["status"], "error")
        self.assertEqual(cli.health()["code"], "github_forbidden")

    def test_missing_gh_and_spawn_errors_are_safe(self):
        for failure, expected in (
            (FileNotFoundError("private"), "gh_missing"),
            (PermissionError("private"), "github_cli_start_failed"),
        ):
            with mock.patch.object(cli.subprocess, "Popen", side_effect=failure):
                self.error(expected, lambda: self.client.request("GET", "/user"))

    def test_invalid_endpoints_methods_headers_and_bodies_never_spawn(self):
        calls = [
            lambda: self.client.request("DELETE", "/user"),
            lambda: self.client.request("get", "/user"),
            lambda: self.client.request("GET", "https://evil.example/user"),
            lambda: self.client.request("GET", "//evil.example/user"),
            lambda: self.client.request("GET", "/user?token=x"),
            lambda: self.client.request("GET", "/user#secret"),
            lambda: self.client.request("GET", "/user\n"),
            lambda: self.client.request("GET", "/repos/a/b/../user"),
            lambda: self.client.request("GET", "/repos/a/b/%2e%2e/user"),
            lambda: self.client.request("GET", "/repos/a/b/contents/bad%escape"),
            lambda: self.client.request("GET", "/repos/{owner}/{repo}/issues"),
            lambda: self.client.request("GET", "/applications/123/token"),
            lambda: self.client.request(
                "GET", "/user", accept="text/plain\r\nAuthorization: secret"
            ),
            lambda: self.client.request("GET", "/user", query={"q": ["x"]}),
            lambda: self.client.request("GET", "/user", body={}),
            lambda: self.client.request("POST", "/repos/a/b/issues", body=[1]),
            lambda: self.client.request(
                "POST", "/repos/a/b/issues", body={"value": float("nan")}
            ),
            lambda: self.client.request(
                "POST", "/repos/a/b/issues", body={"value": "x" * cli.MAX_INPUT}
            ),
            lambda: self.client.job_logs("https://evil.example/log"),
            lambda: self.client.job_logs("/repos/a/b/actions/jobs/0/logs"),
        ]
        for operation in calls:
            with self.subTest(operation=operation):
                self.error("invalid_request", operation)
        self.assertEqual(self.spawned, [])


if __name__ == "__main__":
    unittest.main()

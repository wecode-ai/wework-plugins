"""Exercise the GitHub tool boundary without credentials or network traffic."""

import importlib
import re
import sys
import unittest
from pathlib import Path
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins/wework-github/scripts"))
tools = importlib.import_module("github_connector.tools")
GitHubError = importlib.import_module("github_connector.errors").GitHubError
SHA = "a" * 40
REPO = {"owner": "octo-org", "repo": "demo.repo"}


class RecordingClient:
    def __init__(self):
        self.calls = []

    def request(self, method, path, query=None, body=None, accept=None):
        call = {
            "method": method,
            "path": path,
            "query": query,
            "body": body,
            "accept": accept,
        }
        self.calls.append(call)
        return {"recorded": call}

    def job_logs(self, path):
        self.calls.append({"logs": path})
        return "safe log output"


class GitHubToolsTests(unittest.TestCase):
    def setUp(self):
        self.client = RecordingClient()

    def call(self, name, arguments=None, repository=True):
        args = {**REPO, **(arguments or {})} if repository else (arguments or {})
        return tools.call(name, args, self.client)

    def invalid(self, name, arguments, repository=True):
        with self.assertRaises(GitHubError) as error:
            self.call(name, arguments, repository)
        self.assertEqual(error.exception.code, "invalid_arguments")
        self.assertEqual(self.client.calls, [])

    def test_core_read_routes_and_response_passthrough(self):
        routes = [
            ("get_me", {}, "/user", False),
            ("list_repositories", {}, "/user/repos", False),
            (
                "search_repositories",
                {"q": "language:python"},
                "/search/repositories",
                False,
            ),
            ("search_issues", {"q": "is:issue"}, "/search/issues", False),
            ("get_file_contents", {}, "/repos/octo-org/demo.repo/contents", True),
            ("list_commits", {}, "/repos/octo-org/demo.repo/commits", True),
            (
                "get_commit",
                {"ref": SHA},
                "/repos/octo-org/demo.repo/commits/" + SHA,
                True,
            ),
            ("list_branches", {}, "/repos/octo-org/demo.repo/branches", True),
            ("list_issues", {}, "/repos/octo-org/demo.repo/issues", True),
            (
                "get_issue",
                {"issue_number": 3},
                "/repos/octo-org/demo.repo/issues/3",
                True,
            ),
            ("list_pull_requests", {}, "/repos/octo-org/demo.repo/pulls", True),
            (
                "get_pull_request",
                {"pull_number": 4},
                "/repos/octo-org/demo.repo/pulls/4",
                True,
            ),
            (
                "list_pull_request_files",
                {"pull_number": 4},
                "/repos/octo-org/demo.repo/pulls/4/files",
                True,
            ),
            ("list_workflow_runs", {}, "/repos/octo-org/demo.repo/actions/runs", True),
            (
                "get_workflow_run",
                {"run_id": 8},
                "/repos/octo-org/demo.repo/actions/runs/8",
                True,
            ),
            (
                "list_workflow_jobs",
                {"run_id": 8},
                "/repos/octo-org/demo.repo/actions/runs/8/jobs",
                True,
            ),
        ]
        for name, args, path, repository in routes:
            with self.subTest(name=name):
                result = self.call(name, args, repository)
                self.assertEqual(result["recorded"]["path"], path)
                self.assertEqual(result["recorded"]["method"], "GET")
                self.assertIsNone(result["recorded"]["body"])

    def test_paths_quote_individual_segments_and_refs(self):
        self.call(
            "get_file_contents", {"path": "设计/hello #1?.md", "ref": "feature/x"}
        )
        call = self.client.calls[-1]
        self.assertEqual(
            call["path"],
            "/repos/octo-org/demo.repo/contents/%E8%AE%BE%E8%AE%A1/hello%20%231%3F.md",
        )
        self.assertEqual(call["query"], {"ref": "feature/x"})
        self.call("get_commit", {"ref": "feature/x?y#z"})
        self.assertTrue(self.client.calls[-1]["path"].endswith("/feature%2Fx%3Fy%23z"))

    def test_query_values_remain_data_for_transport_encoding(self):
        query = 'repo:octo-org/demo.repo is:pr "a&b" #中文'
        self.call(
            "search_issues", {"q": query, "page": 2, "per_page": 100}, repository=False
        )
        sent = self.client.calls[-1]["query"]
        self.assertEqual(sent, {"q": query, "page": 2, "per_page": 100})
        self.assertIn("a%26b", urlencode(sent))
        self.call("list_issues", {"labels": ["bug", "help wanted"]})
        self.assertEqual(self.client.calls[-1]["query"], {"labels": "bug,help wanted"})
        self.call("list_branches", {"protected": False})
        self.assertEqual(self.client.calls[-1]["query"], {"protected": "false"})

    def test_workflow_selector_has_only_fixed_routes(self):
        for workflow in (123, "ci.yml", "release.yaml"):
            self.call(
                "list_workflow_runs",
                {"workflow_id": workflow, "exclude_pull_requests": True},
            )
            sent = self.client.calls[-1]
            self.assertEqual(
                sent["path"],
                f"/repos/octo-org/demo.repo/actions/workflows/{workflow}/runs",
            )
            self.assertEqual(sent["query"], {"exclude_pull_requests": "true"})

    def test_diff_and_job_logs_use_dedicated_transport(self):
        self.call("get_pull_request_diff", {"pull_number": 12})
        self.assertEqual(self.client.calls[-1]["accept"], "application/vnd.github.diff")
        self.assertEqual(self.call("get_job_logs", {"job_id": 9}), "safe log output")
        self.assertEqual(
            self.client.calls[-1],
            {"logs": "/repos/octo-org/demo.repo/actions/jobs/9/logs"},
        )

    def test_create_branch_and_file_business_payloads(self):
        self.call("create_branch", {"branch": "feature/fix", "sha": SHA})
        self.assertEqual(
            self.client.calls[-1]["body"], {"ref": "refs/heads/feature/fix", "sha": SHA}
        )
        self.assertEqual(self.client.calls[-1]["method"], "POST")
        person = {"name": "Developer", "email": "dev@example.test"}
        payload = {
            "path": "src/new file.txt",
            "message": "Fix file\n\nDetails",
            "content": "aGVsbG8=",
            "sha": SHA,
            "branch": "feature/fix",
            "author": person,
        }
        self.call("create_or_update_file", payload)
        sent = self.client.calls[-1]
        self.assertEqual(sent["method"], "PUT")
        self.assertEqual(
            sent["path"], "/repos/octo-org/demo.repo/contents/src/new%20file.txt"
        )
        self.assertEqual(
            sent["body"],
            {key: value for key, value in payload.items() if key != "path"},
        )

    def test_issue_and_comment_payloads_preserve_clear_operations(self):
        payload = {
            "title": "Bug",
            "body": "Steps\nExpected",
            "labels": ["bug"],
            "assignees": ["octocat"],
            "milestone": 2,
        }
        self.call("create_issue", payload)
        self.assertEqual(self.client.calls[-1]["body"], payload)
        self.call(
            "update_issue",
            {
                "issue_number": 5,
                "body": "",
                "labels": [],
                "assignees": [],
                "milestone": None,
                "state": "closed",
            },
        )
        self.assertEqual(
            self.client.calls[-1]["body"],
            {
                "body": "",
                "labels": [],
                "assignees": [],
                "milestone": None,
                "state": "closed",
            },
        )
        self.call("add_issue_comment", {"issue_number": 5, "body": "Follow-up"})
        self.assertEqual(
            self.client.calls[-1]["path"], "/repos/octo-org/demo.repo/issues/5/comments"
        )
        self.assertEqual(self.client.calls[-1]["body"], {"body": "Follow-up"})

    def test_pr_and_review_payloads(self):
        payload = {
            "title": "Fix",
            "head": "octocat:feature/fix",
            "base": "main",
            "body": "What changed",
            "draft": True,
            "maintainer_can_modify": False,
        }
        self.call("create_pull_request", payload)
        self.assertEqual(self.client.calls[-1]["body"], payload)
        self.call(
            "update_pull_request", {"pull_number": 7, "state": "closed", "body": ""}
        )
        self.assertEqual(self.client.calls[-1]["body"], {"state": "closed", "body": ""})
        comment = {
            "path": "src/main.py",
            "body": "Please simplify",
            "line": 9,
            "side": "RIGHT",
            "start_line": 7,
            "start_side": "RIGHT",
        }
        review = {
            "pull_number": 7,
            "body": "Review",
            "event": "REQUEST_CHANGES",
            "commit_id": SHA,
            "comments": [comment],
        }
        self.call("create_pull_request_review", review)
        self.assertEqual(
            self.client.calls[-1]["body"],
            {key: value for key, value in review.items() if key != "pull_number"},
        )
        self.call("rerun_workflow", {"run_id": 8, "enable_debug_logging": False})
        self.assertEqual(self.client.calls[-1]["body"], {"enable_debug_logging": False})

    def test_invalid_owner_repo_and_traversal_never_reach_client(self):
        for owner in (
            "../evil",
            "a/b",
            "https://evil.test",
            "octo%2Forg",
            "octo?x=1",
            "x\n",
        ):
            with self.subTest(owner=owner):
                self.invalid("list_branches", {"owner": owner})
        for repo in (
            ".",
            "..",
            "a/b",
            "evil?x=1",
            "evil#frag",
            "evil%2frepo",
            "x" * 101,
        ):
            with self.subTest(repo=repo):
                self.invalid("list_branches", {"repo": repo})
        for path in (
            "../secret",
            "/root",
            "a/../b",
            "./x",
            "a//b",
            "a/",
            "a\\b",
            "a\nfile",
        ):
            with self.subTest(path=path):
                self.invalid("get_file_contents", {"path": path})
        self.invalid("list_workflow_runs", {"workflow_id": "../../workflow.yml"})
        for ref in (".", "..", "a/../b", "a//b", "a\n"):
            self.invalid("get_commit", {"ref": ref})

    def test_malformed_and_extra_arguments_are_rejected(self):
        for arguments in (None, [], "text", 1, True):
            with self.subTest(arguments=arguments):
                for name in ("get_me", "update_issue"):
                    with self.assertRaises(GitHubError) as error:
                        tools.call(name, arguments, self.client)
                    self.assertEqual(error.exception.code, "invalid_arguments")
        for name, arguments in (
            ("get_issue", {}),
            ("get_issue", {"issue_number": True}),
            ("get_issue", {"issue_number": "2"}),
            ("get_issue", {"issue_number": 0}),
            ("list_issues", {"page": 0}),
            ("list_issues", {"page": 10001}),
            ("list_issues", {"per_page": 101}),
            ("list_issues", {"per_page": 1.5}),
            ("list_issues", {"labels": "bug"}),
            ("list_issues", {"labels": ["bug", "bug"]}),
            ("list_branches", {"protected": "true"}),
            ("list_issues", {"state": "deleted"}),
            ("list_issues", {"url": "https://evil.test"}),
            (
                "create_pull_request",
                {"title": "x", "head": "x", "base": "main", "token": "secret"},
            ),
            ("create_issue", {"title": "x", "labels": [3]}),
            ("update_issue", {"issue_number": 1}),
            ("update_pull_request", {"pull_number": 1}),
            (
                "create_or_update_file",
                {"path": "x", "message": "m", "content": "not base64"},
            ),
            (
                "create_or_update_file",
                {"path": "x", "message": "m", "content": "", "author": {"name": "a"}},
            ),
            (
                "create_or_update_file",
                {
                    "path": "x",
                    "message": "m",
                    "content": "",
                    "author": {"name": "a", "email": "e", "token": "x"},
                },
            ),
        ):
            with self.subTest(name=name, arguments=arguments):
                self.invalid(name, arguments)
        for name in ("request", "copilot", "get_me/../../", [], None):
            with self.assertRaises(GitHubError) as error:
                tools.call(name, {}, self.client)
            self.assertEqual(error.exception.code, "unknown_tool")
        self.assertEqual(self.client.calls, [])

    def test_transport_errors_are_not_replaced_or_retried(self):
        error = GitHubError("rate_limited", "GitHub rate limit reached.")

        class FailingClient:
            def __init__(self):
                self.count = 0

            def request(self, *args, **kwargs):
                self.count += 1
                raise error

        client = FailingClient()
        with self.assertRaises(GitHubError) as caught:
            tools.call("create_issue", {**REPO, "title": "Bug"}, client)
        self.assertIs(caught.exception, error)
        self.assertEqual(client.count, 1)

    def test_invalid_branch_names_and_review_comments(self):
        for branch in (
            "refs/heads/x",
            ".hidden",
            "a..b",
            "a//b",
            "a.lock",
            "a/",
            "a@{b",
            "x y",
            "-x",
            "x~1",
            "x\\y",
        ):
            with self.subTest(branch=branch):
                self.invalid("create_branch", {"branch": branch, "sha": SHA})
        self.invalid(
            "create_pull_request_review", {"pull_number": 1, "event": "COMMENT"}
        )
        self.invalid("create_pull_request_review", {"pull_number": 1, "comments": []})
        self.invalid("create_pull_request_review", {"pull_number": 1, "body": ""})
        base = {"path": "file.py", "body": "x", "line": 5, "side": "RIGHT"}
        for comment in (
            {**base, "start_line": 6, "start_side": "RIGHT"},
            {**base, "start_line": 3},
            {**base, "start_side": "RIGHT"},
            {**base, "start_line": 3, "start_side": "LEFT"},
            {**base, "path": "../evil"},
            {**base, "unexpected": 1},
        ):
            with self.subTest(comment=comment):
                self.invalid(
                    "create_pull_request_review",
                    {"pull_number": 1, "comments": [comment]},
                )

    def test_definitions_are_strict_and_match_routing_and_write_annotations(self):
        definitions = tools.definitions()
        self.assertEqual(len({item["name"] for item in definitions}), len(definitions))
        expected_writes = {
            "create_branch",
            "create_or_update_file",
            "create_issue",
            "update_issue",
            "add_issue_comment",
            "create_pull_request",
            "update_pull_request",
            "create_pull_request_review",
            "rerun_workflow",
        }
        expected_destructive = {
            "create_or_update_file",
            "update_issue",
            "update_pull_request",
            "rerun_workflow",
        }
        for definition in definitions:
            with self.subTest(name=definition["name"]):
                name = definition["name"]
                schema = definition["inputSchema"]
                self.assertFalse(schema["additionalProperties"])
                self.assertTrue(set(schema["required"]) <= set(schema["properties"]))
                self.assertEqual(
                    definition["annotations"]["readOnlyHint"],
                    name not in expected_writes,
                )
                self.assertEqual(
                    definition["annotations"]["destructiveHint"],
                    name in expected_destructive,
                )
                # All documented properties belong to the fixed route, query, body,
                # or one of the explicit content/branch/workflow transformations.
                spec = tools._BY_NAME[name]
                routed = set(spec["query"]) | set(spec["body"])
                routed.update(re.findall(r"\{(\w+)\}", spec["path"]))
                if name in ("get_file_contents", "create_or_update_file"):
                    routed.add("path")
                if name == "create_branch":
                    routed.update(("branch", "sha"))
                if name == "list_workflow_runs":
                    routed.add("workflow_id")
                self.assertEqual(routed, set(schema["properties"]))
        definitions[0]["inputSchema"]["additionalProperties"] = True
        self.assertFalse(tools.definitions()[0]["inputSchema"]["additionalProperties"])
        self.assertFalse(any("copilot" in item["name"] for item in definitions))


if __name__ == "__main__":
    unittest.main()

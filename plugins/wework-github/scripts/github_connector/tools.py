"""Explicit GitHub REST tools; no arbitrary requests or model-service tools.

The same schemas validate calls and describe them to the MCP client.  Transport,
credentials, pagination responses, and safe log redirects belong to the client.
"""

import base64
import binascii
import re
from copy import deepcopy
from urllib.parse import quote

from .errors import GitHubError


def _string(description, *, limit=4096, empty=False, enum=None, pattern=None):
    result = {
        "type": "string",
        "description": description,
        "minLength": 0 if empty else 1,
        "maxLength": limit,
    }
    if enum is not None:
        result["enum"] = enum
    if pattern is not None:
        result["pattern"] = pattern
    return result


def _integer(description, maximum=2**53 - 1):
    return {
        "type": "integer",
        "description": description,
        "minimum": 1,
        "maximum": maximum,
    }


def _boolean(description):
    return {"type": "boolean", "description": description}


def _array(item, description, maximum=100):
    return {
        "type": "array",
        "description": description,
        "items": item,
        "maxItems": maximum,
        "uniqueItems": True,
    }


def _object(properties, required=()):
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


_OWNER = _string(
    "Repository owner (user or organization).",
    limit=39,
    pattern=r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$",
)
_REPO = _string(
    "Repository name, without a .git suffix.", limit=100, pattern=r"^[A-Za-z0-9_.-]+$"
)
_PAGE = {
    "page": _integer("Page number, starting at 1.", 10000),
    "per_page": _integer("Results per page, at most 100.", 100),
}
_DIRECTION = _string("Sort direction.", enum=["asc", "desc"])
_REF = _string("Git branch, tag, or commit reference.", limit=1024)
_SHA = _string(
    "Full 40-character commit/blob SHA.", limit=40, pattern=r"^[0-9a-fA-F]{40}$"
)
_TEXT = _string(
    "Markdown body; an empty string clears the body.", limit=65536, empty=True
)
_TITLE = _string("Title.", limit=256)
_LABELS = _array(_string("Label name.", limit=100), "Label names.")
_ASSIGNEES = _array(_OWNER, "GitHub usernames to assign.", 10)
_PERSON = _object(
    {
        "name": _string("Author/committer name.", limit=256),
        "email": _string("Author/committer email.", limit=320),
    },
    ("name", "email"),
)
_FILE_PATH = _string("Repository-relative path; no empty or dot path segments.")
_COMMENT = _object(
    {
        "path": _FILE_PATH,
        "body": _string("Review comment Markdown.", limit=65536),
        "line": _integer("Last line of the comment in the diff."),
        "side": _string("Diff side.", enum=["LEFT", "RIGHT"]),
        "start_line": _integer("First line for a multi-line comment."),
        "start_side": _string("Side for the first line.", enum=["LEFT", "RIGHT"]),
    },
    ("path", "body", "line", "side"),
)


def _tool(
    name,
    description,
    method,
    path,
    *,
    fields=None,
    required=(),
    query=(),
    body=(),
    repository=True,
    accept=None,
    destructive=False,
    at_least_one=(),
):
    properties = {"owner": _OWNER, "repo": _REPO} if repository else {}
    properties.update(fields or {})
    needed = (["owner", "repo"] if repository else []) + list(required)
    schema = _object(properties, needed)
    if at_least_one:
        schema["anyOf"] = [{"required": [field]} for field in at_least_one]
    read = method == "GET"
    return {
        "definition": {
            "name": name,
            "description": description,
            "inputSchema": schema,
            "annotations": {
                "readOnlyHint": read,
                "destructiveHint": destructive,
                "idempotentHint": read,
                "openWorldHint": True,
            },
        },
        "method": method,
        "path": path,
        "query": query,
        "body": body,
        "accept": accept,
    }


_TOOLS = [
    _tool(
        "get_me", "Get the authenticated GitHub user.", "GET", "/user", repository=False
    ),
    _tool(
        "list_repositories",
        "List repositories accessible to the authenticated user.",
        "GET",
        "/user/repos",
        repository=False,
        fields={
            **_PAGE,
            "visibility": _string(
                "Repository visibility.", enum=["all", "public", "private"]
            ),
            "sort": _string(
                "Repository sort.", enum=["created", "updated", "pushed", "full_name"]
            ),
            "direction": _DIRECTION,
        },
        query=("visibility", "sort", "direction", "page", "per_page"),
    ),
    _tool(
        "search_repositories",
        "Search repositories using GitHub search syntax.",
        "GET",
        "/search/repositories",
        repository=False,
        fields={
            **_PAGE,
            "q": _string("GitHub repository search query.", limit=256),
            "sort": _string(
                "Search sort.", enum=["stars", "forks", "help-wanted-issues", "updated"]
            ),
            "order": _DIRECTION,
        },
        required=("q",),
        query=("q", "sort", "order", "page", "per_page"),
    ),
    _tool(
        "search_issues",
        "Search issues and pull requests; use is:issue or is:pr to filter.",
        "GET",
        "/search/issues",
        repository=False,
        fields={
            **_PAGE,
            "q": _string("GitHub issue/PR search query.", limit=256),
            "sort": _string(
                "Search sort.",
                enum=[
                    "comments",
                    "reactions",
                    "reactions-+1",
                    "reactions--1",
                    "reactions-smile",
                    "reactions-thinking_face",
                    "reactions-heart",
                    "reactions-tada",
                    "interactions",
                    "created",
                    "updated",
                ],
            ),
            "order": _DIRECTION,
        },
        required=("q",),
        query=("q", "sort", "order", "page", "per_page"),
    ),
    _tool(
        "get_file_contents",
        "Read a file or directory. Files retain GitHub's base64 encoding.",
        "GET",
        "/repos/{owner}/{repo}/contents",
        fields={
            "path": _string(
                "Relative file/directory path; omit or use empty string for root.",
                empty=True,
            ),
            "ref": _REF,
        },
        query=("ref",),
    ),
    _tool(
        "list_commits",
        "List repository commits.",
        "GET",
        "/repos/{owner}/{repo}/commits",
        fields={
            **_PAGE,
            "sha": _REF,
            "path": _FILE_PATH,
            "since": _string("ISO 8601 lower timestamp bound.", limit=100),
            "until": _string("ISO 8601 upper timestamp bound.", limit=100),
        },
        query=("sha", "path", "since", "until", "page", "per_page"),
    ),
    _tool(
        "get_commit",
        "Get commit details and changed files.",
        "GET",
        "/repos/{owner}/{repo}/commits/{ref}",
        fields={"ref": _REF, **_PAGE},
        required=("ref",),
        query=("page", "per_page"),
    ),
    _tool(
        "list_branches",
        "List repository branches.",
        "GET",
        "/repos/{owner}/{repo}/branches",
        fields={
            **_PAGE,
            "protected": _boolean("Filter protected/unprotected branches."),
        },
        query=("protected", "page", "per_page"),
    ),
    _tool(
        "create_branch",
        "Create a branch from an existing commit SHA.",
        "POST",
        "/repos/{owner}/{repo}/git/refs",
        fields={
            "branch": _string(
                "New branch name without refs/heads/ prefix.", limit=1024
            ),
            "sha": _SHA,
        },
        required=("branch", "sha"),
    ),
    _tool(
        "create_or_update_file",
        "Create/update one file using base64 content. Supply current blob sha when updating.",
        "PUT",
        "/repos/{owner}/{repo}/contents",
        fields={
            "path": _FILE_PATH,
            "message": _string("Commit message.", limit=4096),
            "content": _string("Base64-encoded file bytes.", limit=1400000, empty=True),
            "sha": _SHA,
            "branch": _REF,
            "committer": _PERSON,
            "author": _PERSON,
        },
        required=("path", "message", "content"),
        body=("message", "content", "sha", "branch", "committer", "author"),
        destructive=True,
    ),
    _tool(
        "list_issues",
        "List repository issues; GitHub also includes pull requests in this endpoint.",
        "GET",
        "/repos/{owner}/{repo}/issues",
        fields={
            **_PAGE,
            "state": _string("Issue state.", enum=["open", "closed", "all"]),
            "labels": _LABELS,
            "assignee": _string("Username, * for any, or none.", limit=39),
            "creator": _OWNER,
            "mentioned": _OWNER,
            "sort": _string("Issue sort.", enum=["created", "updated", "comments"]),
            "direction": _DIRECTION,
            "since": _string("ISO 8601 lower timestamp bound.", limit=100),
        },
        query=(
            "state",
            "labels",
            "assignee",
            "creator",
            "mentioned",
            "sort",
            "direction",
            "since",
            "page",
            "per_page",
        ),
    ),
    _tool(
        "get_issue",
        "Get an issue and its current metadata.",
        "GET",
        "/repos/{owner}/{repo}/issues/{issue_number}",
        fields={"issue_number": _integer("Issue number.")},
        required=("issue_number",),
    ),
    _tool(
        "create_issue",
        "Create a repository issue.",
        "POST",
        "/repos/{owner}/{repo}/issues",
        fields={
            "title": _TITLE,
            "body": _TEXT,
            "assignees": _ASSIGNEES,
            "labels": _LABELS,
            "milestone": _integer("Milestone number."),
        },
        required=("title",),
        body=("title", "body", "assignees", "labels", "milestone"),
    ),
    _tool(
        "update_issue",
        "Update an issue. Empty assignees/labels clear those fields.",
        "PATCH",
        "/repos/{owner}/{repo}/issues/{issue_number}",
        fields={
            "issue_number": _integer("Issue number."),
            "title": _TITLE,
            "body": _TEXT,
            "state": _string("New state.", enum=["open", "closed"]),
            "state_reason": _string(
                "Reason for state.", enum=["completed", "not_planned", "reopened"]
            ),
            "assignees": _ASSIGNEES,
            "labels": _LABELS,
            "milestone": {
                "anyOf": [_integer("Milestone number."), {"type": "null"}],
                "description": "Milestone number, or null to clear.",
            },
        },
        required=("issue_number",),
        body=(
            "title",
            "body",
            "state",
            "state_reason",
            "assignees",
            "labels",
            "milestone",
        ),
        at_least_one=(
            "title",
            "body",
            "state",
            "state_reason",
            "assignees",
            "labels",
            "milestone",
        ),
        destructive=True,
    ),
    _tool(
        "add_issue_comment",
        "Add a comment to an issue or pull request.",
        "POST",
        "/repos/{owner}/{repo}/issues/{issue_number}/comments",
        fields={
            "issue_number": _integer("Issue/PR number."),
            "body": _string("Comment Markdown.", limit=65536),
        },
        required=("issue_number", "body"),
        body=("body",),
    ),
    _tool(
        "list_pull_requests",
        "List repository pull requests.",
        "GET",
        "/repos/{owner}/{repo}/pulls",
        fields={
            **_PAGE,
            "state": _string("PR state.", enum=["open", "closed", "all"]),
            "head": _string("Filter by user:branch.", limit=1100),
            "base": _REF,
            "sort": _string(
                "PR sort.", enum=["created", "updated", "popularity", "long-running"]
            ),
            "direction": _DIRECTION,
        },
        query=("state", "head", "base", "sort", "direction", "page", "per_page"),
    ),
    _tool(
        "get_pull_request",
        "Get a pull request.",
        "GET",
        "/repos/{owner}/{repo}/pulls/{pull_number}",
        fields={"pull_number": _integer("Pull request number.")},
        required=("pull_number",),
    ),
    _tool(
        "get_pull_request_diff",
        "Get a pull request as a unified diff.",
        "GET",
        "/repos/{owner}/{repo}/pulls/{pull_number}",
        fields={"pull_number": _integer("Pull request number.")},
        required=("pull_number",),
        accept="application/vnd.github.diff",
    ),
    _tool(
        "create_pull_request",
        "Create a pull request between existing branches.",
        "POST",
        "/repos/{owner}/{repo}/pulls",
        fields={
            "title": _TITLE,
            "head": _string("Source branch, optionally user:branch.", limit=1100),
            "base": _REF,
            "body": _TEXT,
            "draft": _boolean("Create a draft PR."),
            "maintainer_can_modify": _boolean("Allow maintainer edits."),
        },
        required=("title", "head", "base"),
        body=("title", "head", "base", "body", "draft", "maintainer_can_modify"),
    ),
    _tool(
        "update_pull_request",
        "Update PR metadata or state; this tool does not merge a PR.",
        "PATCH",
        "/repos/{owner}/{repo}/pulls/{pull_number}",
        fields={
            "pull_number": _integer("Pull request number."),
            "title": _TITLE,
            "body": _TEXT,
            "base": _REF,
            "state": _string("New state.", enum=["open", "closed"]),
            "maintainer_can_modify": _boolean("Allow maintainer edits."),
        },
        required=("pull_number",),
        body=("title", "body", "base", "state", "maintainer_can_modify"),
        at_least_one=("title", "body", "base", "state", "maintainer_can_modify"),
        destructive=True,
    ),
    _tool(
        "list_pull_request_files",
        "List changed files for a pull request.",
        "GET",
        "/repos/{owner}/{repo}/pulls/{pull_number}/files",
        fields={
            "pull_number": _integer("Pull request number."),
            **_PAGE,
        },
        required=("pull_number",),
        query=("page", "per_page"),
    ),
    _tool(
        "create_pull_request_review",
        "Create/submit a PR review; omit event for a pending review.",
        "POST",
        "/repos/{owner}/{repo}/pulls/{pull_number}/reviews",
        fields={
            "pull_number": _integer("Pull request number."),
            "commit_id": _SHA,
            "body": _TEXT,
            "event": _string(
                "Review action.", enum=["APPROVE", "REQUEST_CHANGES", "COMMENT"]
            ),
            "comments": _array(_COMMENT, "Inline diff comments.", 100),
        },
        required=("pull_number",),
        body=("commit_id", "body", "event", "comments"),
        at_least_one=("body", "comments", "event"),
    ),
    _tool(
        "list_workflow_runs",
        "List Actions runs for a repository or a specific workflow.",
        "GET",
        "/repos/{owner}/{repo}/actions/runs",
        fields={
            **_PAGE,
            "workflow_id": {
                "anyOf": [
                    _integer("Workflow ID."),
                    _string(
                        "Workflow filename.",
                        limit=255,
                        pattern=r"^[A-Za-z0-9_.-]+\.ya?ml$",
                    ),
                ]
            },
            "actor": _OWNER,
            "branch": _REF,
            "event": _string(
                "Workflow trigger event.", limit=100, pattern=r"^[A-Za-z0-9_]+$"
            ),
            "status": _string(
                "Run status or conclusion.",
                enum=[
                    "queued",
                    "in_progress",
                    "completed",
                    "waiting",
                    "pending",
                    "requested",
                    "action_required",
                    "cancelled",
                    "failure",
                    "neutral",
                    "skipped",
                    "stale",
                    "success",
                    "timed_out",
                ],
            ),
            "head_sha": _SHA,
            "exclude_pull_requests": _boolean("Exclude pull-request runs."),
        },
        query=(
            "actor",
            "branch",
            "event",
            "status",
            "head_sha",
            "exclude_pull_requests",
            "page",
            "per_page",
        ),
    ),
    _tool(
        "get_workflow_run",
        "Get an Actions workflow run.",
        "GET",
        "/repos/{owner}/{repo}/actions/runs/{run_id}",
        fields={"run_id": _integer("Workflow run ID.")},
        required=("run_id",),
    ),
    _tool(
        "list_workflow_jobs",
        "List jobs for an Actions run.",
        "GET",
        "/repos/{owner}/{repo}/actions/runs/{run_id}/jobs",
        fields={
            "run_id": _integer("Workflow run ID."),
            **_PAGE,
            "filter": _string("Run attempts to include.", enum=["latest", "all"]),
        },
        required=("run_id",),
        query=("filter", "page", "per_page"),
    ),
    _tool(
        "get_job_logs",
        "Download an Actions job log via the client's safe redirect handler.",
        "GET",
        "/repos/{owner}/{repo}/actions/jobs/{job_id}/logs",
        fields={"job_id": _integer("Workflow job ID.")},
        required=("job_id",),
    ),
    _tool(
        "rerun_workflow",
        "Re-run an Actions workflow; workflows may deploy or have external effects.",
        "POST",
        "/repos/{owner}/{repo}/actions/runs/{run_id}/rerun",
        fields={
            "run_id": _integer("Workflow run ID."),
            "enable_debug_logging": _boolean("Enable debug logging for the re-run."),
        },
        required=("run_id",),
        body=("enable_debug_logging",),
        destructive=True,
    ),
]
_BY_NAME = {tool["definition"]["name"]: tool for tool in _TOOLS}


def definitions():
    """Return isolated MCP definitions so callers cannot mutate validation rules."""
    return deepcopy([tool["definition"] for tool in _TOOLS])


def _invalid(message):
    raise GitHubError("invalid_arguments", message)


def _validate(value, schema, field="arguments"):
    if schema.get("type") == "object" and type(value) is not dict:
        _invalid(f"{field} must be an object.")
    if "anyOf" in schema:
        for candidate in schema["anyOf"]:
            # Tool-level anyOf expresses that at least one update field exists.
            if "type" not in candidate and "required" in candidate:
                if all(name in value for name in candidate["required"]):
                    break
            else:
                try:
                    _validate(value, candidate, field)
                    break
                except GitHubError:
                    pass
        else:
            _invalid(f"{field} does not match any allowed shape.")
    kind = schema.get("type")
    if kind is None:
        return
    if kind == "object":
        if type(value) is not dict:
            _invalid(f"{field} must be an object.")
        props = schema["properties"]
        if any(key not in props for key in value):
            _invalid(f"{field} contains unsupported fields.")
        if any(key not in value for key in schema.get("required", [])):
            _invalid(f"{field} is missing required fields.")
        for key, entry in value.items():
            _validate(entry, props[key], f"{field}.{key}")
    elif kind == "string":
        if type(value) is not str:
            _invalid(f"{field} must be a string.")
        if (
            not schema.get("minLength", 0)
            <= len(value)
            <= schema.get("maxLength", 65536)
        ):
            _invalid(f"{field} has an invalid length.")
        # Newlines are legitimate in Markdown/file content, but never NUL.
        if "\x00" in value:
            _invalid(f"{field} cannot contain NUL.")
        if "pattern" in schema and re.fullmatch(schema["pattern"], value) is None:
            _invalid(f"{field} has an invalid format.")
    elif kind == "integer":
        if (
            type(value) is not int
            or not schema["minimum"] <= value <= schema["maximum"]
        ):
            _invalid(f"{field} must be an integer in the allowed range.")
    elif kind == "boolean":
        if type(value) is not bool:
            _invalid(f"{field} must be a boolean.")
    elif kind == "null":
        if value is not None:
            _invalid(f"{field} must be null.")
    elif kind == "array":
        if type(value) is not list or len(value) > schema["maxItems"]:
            _invalid(f"{field} must be an array within the allowed size.")
        for index, entry in enumerate(value):
            _validate(entry, schema["items"], f"{field}[{index}]")
        if schema.get("uniqueItems") and any(
            entry in value[:i] for i, entry in enumerate(value)
        ):
            _invalid(f"{field} must not contain duplicates.")
    else:
        raise RuntimeError("Unsupported internal tool schema")
    if "enum" in schema and value not in schema["enum"]:
        _invalid(f"{field} has an unsupported value.")


def _path(value, *, root=False):
    if root and value == "":
        return ""
    if (
        not value
        or "\\" in value
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
        or any(part in ("", ".", "..") for part in value.split("/"))
    ):
        _invalid(
            "Repository paths must be relative and cannot contain dot or empty segments."
        )
    return "/".join(quote(part, safe="") for part in value.split("/"))


def _branch(value):
    # Match git check-ref-format rules needed by a branch creation request.
    if (
        value.startswith(("-", "refs/"))
        or value.endswith((".", "/"))
        or ".." in value
        or "@{" in value
        or value == "@"
        or re.search(r"[\x00-\x20\x7f~^:?*\[\\]", value)
        or any(
            not part or part.startswith(".") or part.endswith(".lock")
            for part in value.split("/")
        )
    ):
        _invalid("branch must be a valid new branch name without refs/heads/.")


def _business_validation(name, args):
    if args.get("repo") in (".", ".."):
        _invalid("repo must be a repository name.")
    if "path" in args:
        _path(args["path"], root=name == "get_file_contents")
    if name == "get_commit":
        ref = args["ref"]
        if (
            ref in (".", "..")
            or any(ord(char) < 32 or ord(char) == 127 for char in ref)
            or any(part in ("", ".", "..") for part in ref.split("/"))
        ):
            _invalid(
                "ref must be a branch, tag, or commit reference without dot segments."
            )
    if name == "create_branch":
        _branch(args["branch"])
    if name == "create_or_update_file":
        try:
            base64.b64decode(args["content"], validate=True)
        except (ValueError, binascii.Error):
            _invalid("content must be valid base64, without whitespace.")
    if name == "create_pull_request_review":
        if (
            args.get("event") in ("COMMENT", "REQUEST_CHANGES")
            and not args.get("body", "").strip()
        ):
            _invalid("body is required for COMMENT and REQUEST_CHANGES reviews.")
        if (
            not args.get("comments")
            and not args.get("body", "").strip()
            and "event" not in args
        ):
            _invalid("A pending review must contain a body or at least one comment.")
        for comment in args.get("comments", []):
            _path(comment["path"])
            if ("start_line" in comment) != ("start_side" in comment):
                _invalid(
                    "Multi-line review comments need both start_line and start_side."
                )
            if "start_line" in comment and (
                comment["start_line"] > comment["line"]
                or comment["start_side"] != comment["side"]
            ):
                _invalid(
                    "Multi-line review comments must use ordered lines on the same side."
                )


def call(name, arguments, client):
    """Validate a named tool and dispatch exactly one fixed GitHub API request."""
    if type(name) is not str or name not in _BY_NAME:
        raise GitHubError("unknown_tool", "Unknown GitHub tool.")
    tool = _BY_NAME[name]
    _validate(arguments, tool["definition"]["inputSchema"])
    _business_validation(name, arguments)
    values = {
        key: quote(str(arguments[key]), safe="")
        for key in re.findall(r"\{(\w+)\}", tool["path"])
    }
    path = tool["path"].format(**values)
    if name in ("get_file_contents", "create_or_update_file"):
        relative = _path(arguments.get("path", ""), root=name == "get_file_contents")
        if relative:
            path += "/" + relative
    elif name == "list_workflow_runs" and "workflow_id" in arguments:
        workflow = quote(str(arguments["workflow_id"]), safe="")
        path = f"/repos/{values['owner']}/{values['repo']}/actions/workflows/{workflow}/runs"
    if name == "get_job_logs":
        return client.job_logs(path)
    query = {key: arguments[key] for key in tool["query"] if key in arguments}
    if "labels" in query:
        query["labels"] = ",".join(query["labels"])
    query = {
        key: str(value).lower() if type(value) is bool else value
        for key, value in query.items()
    }
    body = {key: arguments[key] for key in tool["body"] if key in arguments}
    if name == "create_branch":
        body = {"ref": "refs/heads/" + arguments["branch"], "sha": arguments["sha"]}
    return client.request(
        tool["method"],
        path,
        query=query or None,
        body=(body if tool["method"] != "GET" else None),
        accept=tool["accept"],
    )

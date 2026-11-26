#!/usr/bin/env python3
"""Plugin-owned GitHub CLI authentication and fixed GitHub MCP tools."""

import argparse
import json
import sys

from github_connector import cli, mcp
from github_connector.errors import GitHubError

TITLE = "GitHub（Wework）"
DESCRIPTION = "使用 GitHub CLI 官方授权；与执行设备上的 gh 及原 GitHub 入口共享账号。"
LOGOUT_DESCRIPTION = (
    "退出会移除当前设备上该活动账号的 GitHub CLI 认证，同时影响共享该账号的入口。"
    "此操作不撤销 GitHub 网站上的授权，也不删除其他已保存账号；卸载插件不自动退出。"
)


def present(value):
    error_code = value.get("errorCode", value.get("code"))
    return {
        "title": TITLE,
        "description": DESCRIPTION,
        "logoutDescription": LOGOUT_DESCRIPTION,
        **value,
        **({"errorCode": error_code, "code": error_code} if error_code else {}),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("health", "connect", "disconnect", "login", "logout", "mcp")
    )
    args = parser.parse_args()
    if args.command == "mcp":
        mcp.serve()
        return 0
    try:
        if args.command == "health":
            value = cli.health()
        elif args.command in {"connect", "disconnect"}:
            from github_connector import native_auth

            value = getattr(native_auth, args.command)()
        else:
            from github_connector import browser_auth

            value = browser_auth.run(args.command)
    except GitHubError as error:
        value = {"status": "error", "code": error.code, "hint": error.message}
    except (Exception, KeyboardInterrupt):  # noqa: BLE001 - Never expose credential-bearing CLI diagnostics.
        value = {
            "status": "error",
            "code": "connector_internal_error",
            "hint": "The GitHub connection operation did not complete.",
        }
    print(json.dumps(present(value), ensure_ascii=False), flush=True)
    return 0 if value["status"] in {"ok", "need_login"} else 1


if __name__ == "__main__":
    sys.exit(main())

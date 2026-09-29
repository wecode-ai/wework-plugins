#!/usr/bin/env python3
"""Local DWS stays local; cloud commands use an access-only snapshot."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from wegent_plugin_auth import AuthError, run_account_command

ROOT = Path(__file__).resolve().parents[1]


def account_arguments(arguments: list[str]) -> tuple[str | None, list[str]]:
    account = None
    forwarded = []
    index = 0
    while index < len(arguments):
        value = arguments[index]
        if value == "--":
            forwarded.extend(arguments[index:])
            break
        if value == "--account-id" or value.startswith("--account-id="):
            if account is not None:
                raise AuthError("plugin_auth_account_selection_required")
            if value == "--account-id":
                index += 1
                if index >= len(arguments):
                    raise AuthError("plugin_auth_account_selection_required")
                account = arguments[index]
            else:
                account = value.split("=", 1)[1]
            if not account.strip() or len(account) > 256:
                raise AuthError("plugin_auth_account_selection_required")
        else:
            forwarded.append(value)
        index += 1
    return account, forwarded


def local_environment() -> dict[str, str]:
    environment = dict(os.environ)
    # A user may put this public wrapper on PATH. Installer probes must reject
    # it rather than recursively discovering another copy of the same wrapper.
    environment["WEGENT_DWS_RESOLVING"] = "1"
    return environment


def run_local(arguments: list[str], executable: str | None = None) -> int:
    environment = local_environment()
    if arguments == ["plugin-health"]:
        command = (
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(ROOT / "scripts/local-dws-ready.ps1"),
            ]
            if os.name == "nt"
            else ["/bin/sh", str(ROOT / "scripts/local-dws-ready.sh")]
        )
        return subprocess.call(command, env=environment)
    if executable is not None:
        return subprocess.call([executable, *arguments], env=environment)
    if os.name == "nt":
        command = [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(ROOT / "scripts/install-dws.ps1"),
            "-PrintPath",
        ]
    else:
        command = ["/bin/sh", str(ROOT / "scripts/install-dws.sh")]
    result = subprocess.run(
        command, env=environment, stdout=subprocess.PIPE, text=True, check=False
    )
    if result.returncode:
        return result.returncode
    lines = result.stdout.strip().splitlines()
    if not lines or not Path(lines[-1]).is_file():
        raise AuthError("plugin_auth_runtime_unsupported")
    return subprocess.call([lines[-1], *arguments], env=environment)


def main(arguments: list[str]) -> int:
    try:
        if os.environ.get("WEGENT_DWS_RESOLVING") == "1":
            return 126
        executable = None
        if arguments[:1] == ["--local-binary"]:
            if len(arguments) < 3:
                raise AuthError("plugin_auth_invalid_command")
            executable, arguments = arguments[1], arguments[2:]
        account, arguments = account_arguments(arguments)
        mode = os.environ.get("WEGENT_PLUGIN_AUTH_MODE", "")
        if mode not in {"", "local", "cloud"}:
            raise AuthError("plugin_auth_runtime_unsupported")
        if mode == "cloud" or account is not None:
            if executable is not None:
                raise AuthError("plugin_auth_invalid_command")
            if arguments == ["plugin-health"]:
                arguments = ["account-status"]
            output = run_account_command(
                ROOT, "dingtalk-access", arguments, account_id=account
            )
            sys.stdout.write(output)
            # Expected snapshot expiry is a business result, not a host protocol
            # failure. Preserve it for helpers without replaying the operation.
            try:
                result = json.loads(output)
            except ValueError:
                result = None
            if (
                isinstance(result, dict)
                and result.get("error") == "dingtalk_cloud_token_expired"
            ):
                return 1
            return 0
        return run_local(arguments, executable)
    except AuthError as error:
        print(json.dumps({"error": str(error)}))
        return 1
    except (OSError, ValueError):
        print(json.dumps({"error": "plugin_auth_execution_failed"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

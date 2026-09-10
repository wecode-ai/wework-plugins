"""Route public commands before any local authentication or installation."""

import json
import os
import sys
import re
from pathlib import Path

import native_runtime as native
from wegent_plugin_auth import AuthError, run_account_command

ROOT = Path(__file__).resolve().parents[1]


def validate_scopes(arguments):
    if (
        not isinstance(arguments, list)
        or not arguments
        or len(arguments) > 8
        or len(arguments) % 2
    ):
        raise AuthError("plugin_auth_invalid_command")
    for flag, value in zip(arguments[::2], arguments[1::2]):
        if (
            flag not in ("--domain", "--scope")
            or not isinstance(value, str)
            or not re.fullmatch(r"[A-Za-z0-9_:,. /-]{1,4096}", value)
        ):
            raise AuthError("plugin_auth_invalid_command")


def scope_request(arguments, bot_account):
    validate_scopes(arguments)
    if not bot_account:
        raise AuthError("plugin_auth_invalid_command")
    path = native.receipt(bot_account).with_suffix(".scopes.json")
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(arguments))
    temporary.replace(path)


def identity(arguments):
    result = "user"
    for index, argument in enumerate(arguments):
        if argument == "--as":
            if index + 1 >= len(arguments):
                raise AuthError("plugin_auth_invalid_command")
            result = arguments[index + 1]
        elif argument.startswith("--as="):
            result = argument.split("=", 1)[1]
    if result not in ("user", "bot", "auto"):
        raise AuthError("plugin_auth_invalid_command")
    return "user" if result == "auto" else result


def business_args(arguments):
    if (
        arguments
        and arguments[0] == "event"
        and (len(arguments) < 2 or arguments[1] not in ("list", "schema"))
    ):
        raise AuthError("plugin_auth_local_event_required")
    if arguments == ["--ready"] or arguments[:2] == ["auth", "status"]:
        return ["account-status"]
    if not arguments or arguments[0] in {
        "auth",
        "config",
        "profile",
        "init",
        "update",
        "skills",
        "plugin",
    }:
        raise AuthError("plugin_auth_use_native_login")
    return arguments


def main(arguments):
    who = identity(arguments)
    slug = "lark-app" if who == "bot" else "lark"
    if os.environ.get("WEGENT_PLUGIN_AUTH_MODE") == "cloud":
        sys.stdout.write(run_account_command(ROOT, slug, business_args(arguments)))
        return 0
    with native.locked():
        value = native.context()
        if (
            who == "user"
            and not any(arg.startswith("--as") for arg in arguments)
            and value.get("default_as") == "bot"
        ):
            who, slug = "bot", "lark-app"
        account = value.get(who, "")
        managed = account and native.receipt(account).exists()
        if not managed:
            if arguments == ["--ready"]:
                from local_auth import login_locked

                login_locked()
                print(json.dumps({"status": "ok"}))
                return 0
            return native.invoke(["local", *arguments]).returncode
        if arguments[:2] == ["auth", "login"]:
            scope_request(arguments[2:], value.get("bot", ""))
            raise AuthError("plugin_auth_reconnect_required")
    sys.stdout.write(
        run_account_command(ROOT, slug, business_args(arguments), account_id=account)
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except AuthError as error:
        print(json.dumps({"error": str(error)}))
        raise SystemExit(1)
    except Exception:
        print(json.dumps({"error": "plugin_auth_lark_failed"}))
        raise SystemExit(1)

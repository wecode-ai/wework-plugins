"""Route cloud commands before touching the source device or native package."""

import json
import os
import sys
from pathlib import Path
import native_runtime as native
from wegent_plugin_auth import AuthError, run_account_command

ROOT = Path(__file__).resolve().parents[1]


def business_args(arguments):
    if arguments == ["--ready"] or arguments[:2] == ["auth", "show"]:
        return ["account-status"]
    if not arguments or arguments[0] not in {
        "contact",
        "doc",
        "meeting",
        "msg",
        "schedule",
        "todo",
    }:
        raise AuthError("plugin_auth_use_native_login")
    return arguments


def main(arguments):
    if os.environ.get("WEGENT_PLUGIN_AUTH_MODE") == "cloud":
        sys.stdout.write(run_account_command(ROOT, "wecom", business_args(arguments)))
        return 0
    with native.locked():
        if arguments == ["--ready"]:
            from local_auth import login_locked

            login_locked()
            print(json.dumps({"status": "ok"}))
            return 0
        return native.invoke(arguments).returncode


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except AuthError as error:
        print(json.dumps({"error": str(error)}))
        raise SystemExit(1)
    except Exception:
        print(json.dumps({"error": "plugin_auth_wecom_failed"}))
        raise SystemExit(1)

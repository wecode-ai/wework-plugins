"""Original WeCom QR login remains on the source device."""

import json
import sys
import native_runtime as native
from wegent_plugin_auth import AuthError


def login_locked():
    if native.invoke(["__wegent", "local-health"], capture=True).returncode:
        result = native.invoke(["init", "--noninteractive"], capture=True, timeout=300)
        if result.returncode:
            raise AuthError("plugin_auth_login_failed")
    if native.invoke(["__wegent", "local-health"], capture=True).returncode:
        raise AuthError("plugin_auth_login_failed")


def action(command):
    with native.locked():
        if command == "login":
            login_locked()
            return "ok"
        if command == "logout":
            if native.invoke(["__wegent", "local-clear"], capture=True).returncode:
                raise AuthError("plugin_auth_logout_failed")
            return "ok"
        if command == "health":
            return (
                "ok"
                if native.invoke(["__wegent", "local-health"], capture=True).returncode
                == 0
                else "need_login"
            )
        raise AuthError("plugin_auth_invalid_command")


if __name__ == "__main__":
    try:
        if len(sys.argv) != 2:
            raise AuthError("plugin_auth_invalid_command")
        print(json.dumps({"status": action(sys.argv[1])}))
    except Exception:
        print(
            json.dumps(
                {"status": "error", "hint": "WeCom authentication did not complete."}
            )
        )

"""Original device flows, with verification links opened on the source device."""

import json
import queue
import re
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
from pathlib import Path

import native_runtime as native
from wegent_plugin_auth import AuthError, run_account_command

ROOT = Path(__file__).resolve().parents[1]


def verification_urls(line):
    for value in re.findall(r'https://[^\s<>"\x1b]+', line):
        parsed = urllib.parse.urlsplit(value)
        if (
            parsed.hostname in {"accounts.feishu.cn", "accounts.larksuite.com"}
            and parsed.port in (None, 443)
            and not parsed.username
            and not parsed.password
        ):
            yield value


def browser_flow(arguments):
    process = subprocess.Popen(
        [str(native.companion()), "local", *arguments],
        env=native.environment(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    events = queue.Queue()

    def consume():
        for line in process.stdout:
            events.put(line)
        events.put(None)

    reader = threading.Thread(target=consume, daemon=True)
    reader.start()
    opened = set()
    deadline = time.monotonic() + 240
    try:
        while time.monotonic() < deadline:
            try:
                line = events.get(timeout=0.2)
            except queue.Empty:
                continue
            if line is None:
                if process.wait(timeout=5) != 0:
                    raise AuthError("plugin_auth_login_failed")
                return
            for url in verification_urls(line):
                if url not in opened:
                    opened.add(url)
                    if not webbrowser.open(url):
                        raise AuthError("plugin_auth_browser_unavailable")
        raise AuthError("plugin_auth_login_timeout")
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        process.stdout.close()


def login_locked():
    if not native.context():
        browser_flow(["config", "init", "--new", "--brand", "feishu", "--lang", "zh"])
    value = native.context()
    request = native.receipt(value.get("bot", "")).with_suffix(".scopes.json")
    arguments = ["--recommend"]
    if request.exists():
        if request.stat().st_size > 40000:
            raise AuthError("plugin_auth_invalid_command")
        arguments = json.loads(request.read_text())
        from lark_cli import validate_scopes

        validate_scopes(arguments)
    if request.exists() or native.invoke(["local-health"], capture=True).returncode:
        browser_flow(["auth", "login", *arguments, "--json"])
    if native.invoke(["local-health"], capture=True).returncode:
        raise AuthError("plugin_auth_login_failed")
    # Clear routing only after the new grant exists; the host owns reconnect intent.
    value = native.context()
    if value.get("user"):
        native.receipt(value["user"]).unlink(missing_ok=True)
    request.unlink(missing_ok=True)


def action(command):
    with native.locked():
        value = native.context()
        account = value.get("user", "")
        managed = account and native.receipt(account).exists()
        if command == "login":
            login_locked()
            return "ok"
        if command == "logout":
            if value and native.invoke(["local-clear"], capture=True).returncode:
                raise AuthError("plugin_auth_logout_failed")
            if account:
                native.receipt(account).unlink(missing_ok=True)
            return "ok"
        if command != "health":
            raise AuthError("plugin_auth_invalid_command")
        if not managed:
            return (
                "ok"
                if native.invoke(["local-health"], capture=True).returncode == 0
                else "need_login"
            )
    try:
        value = json.loads(
            run_account_command(ROOT, "lark", ["account-status"], account_id=account)
        )
        return "ok" if value.get("status") == "ok" else "need_login"
    except (AuthError, ValueError):
        return "need_login"


if __name__ == "__main__":
    try:
        if len(sys.argv) != 2:
            raise AuthError("plugin_auth_invalid_command")
        print(json.dumps({"status": action(sys.argv[1])}))
    except Exception:
        print(
            json.dumps(
                {"status": "error", "hint": "Lark authentication did not complete."}
            )
        )

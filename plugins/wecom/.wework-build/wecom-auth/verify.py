"""Check native inventory, child status, and public broker routing from the artifact."""

import hashlib
import json
import os
import socket
import struct
import subprocess
import sys
import tempfile
from pathlib import Path


def verify_package(plugin, require_all=True):
    inventory = list((plugin / "scripts/native").glob("*/wecom-account-auth.json"))
    expected = {
        "darwin-amd64",
        "darwin-arm64",
        "linux-amd64",
        "linux-arm64",
        "windows-amd64",
    }
    if require_all and {p.parent.name for p in inventory} != expected:
        raise ValueError("Incomplete native platform inventory")
    for metadata in inventory:
        value = json.loads(metadata.read_text())
        blob = metadata.with_suffix(".xz")
        if hashlib.sha256(blob.read_bytes()).hexdigest() != value["compressedSha256"]:
            raise ValueError("Native artifact checksum mismatch")
    with tempfile.TemporaryDirectory() as temporary:
        home = Path(temporary)
        env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith(("WEGENT_", "LARK", "OPENCLAW", "HERMES"))
        }
        env.update(
            HOME=str(home),
            USERPROFILE=str(home),
            WEGENT_EXECUTOR_HOME=str(home / "executor"),
            PYTHONDONTWRITEBYTECODE="1",
        )
        # Exercise the packaged launcher and private channel with an invalid
        # business credential; never touch a real account or system keychain.
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            listener.settimeout(30)
            env["WEGENT_PLUGIN_AUTH_PORT"] = str(listener.getsockname()[1])
            process = subprocess.Popen(
                [
                    sys.executable,
                    str(plugin / "scripts/account-auth.py"),
                    "run",
                    "account-status",
                ],
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            try:
                process.stdin.write(b"x" * 32)
                process.stdin.close()
                process.stdin = None
                connection, _ = listener.accept()
                with connection:
                    connection.settimeout(15)
                    with connection.makefile("rwb", buffering=0) as stream:
                        nonce = b""
                        while len(nonce) < 32:
                            chunk = stream.read(32 - len(nonce))
                            if not chunk:
                                raise ValueError("Truncated native nonce")
                            nonce += chunk
                        if nonce != b"x" * 32:
                            raise ValueError("Native nonce mismatch")
                        payload = json.dumps(
                            {
                                "protocolVersion": 1,
                                "connectorSlug": "wecom",
                                "credentialType": "password",
                                "credential": {
                                    "username": "synthetic",
                                    "password": "synthetic-private-token",
                                    "unexpected": "denied",
                                },
                            }
                        ).encode()
                        stream.write(struct.pack(">I", len(payload)) + payload)
                out, err = process.communicate(timeout=20)
                if process.returncode == 0 or b"synthetic-private-token" in out + err:
                    raise ValueError(
                        "Packaged adapter accepted or exposed an invalid credential"
                    )
            finally:
                if process.poll() is None:
                    process.kill()
                process.communicate(timeout=5)
        subprocess.run(
            [
                sys.executable,
                "-m",
                "unittest",
                "discover",
                "-s",
                str(plugin / "scripts/tests"),
                "-v",
            ],
            env=env,
            check=True,
        )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--plugin", type=Path, required=True)
    parser.add_argument("--host-only", action="store_true")
    args = parser.parse_args()
    verify_package(args.plugin.resolve(), require_all=not args.host_only)

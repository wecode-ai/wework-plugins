"""Build a pinned upstream CLI plus Wegent's private credential boundary."""

import argparse
import hashlib
import json
import lzma
import os
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VERSION = "1.0.68"
SOURCE_SHA256 = "e23a0f85116dc4ef869ccb4c124dc02e2847aa8c6f414ee7d736c39a070e3a95"


def source_archive(directory, provided=None):
    path = provided or directory / "source.tar.gz"
    if provided is None:
        with urllib.request.urlopen(
            f"https://codeload.github.com/larksuite/cli/tar.gz/refs/tags/v{VERSION}",
            timeout=60,
        ) as response:
            path.write_bytes(response.read(100 * 1024 * 1024 + 1))
    if hashlib.sha256(path.read_bytes()).hexdigest() != SOURCE_SHA256:
        raise ValueError("Lark source checksum mismatch")
    return path


def prepare(directory, archive):
    with tarfile.open(archive) as source:
        source.extractall(directory, filter="data")
    root = directory / ("cli-" + VERSION)
    shutil.copytree(ROOT / "overlay", root / "cmd/wegent-account-auth")
    sdk = root / "internal/wegentpluginauth"
    sdk.mkdir()
    for path in (ROOT.parent / "plugin-auth-go").glob("*.go"):
        shutil.copyfile(path, sdk / path.name)
    # Intercept every upstream keychain entry point in managed business mode,
    # including direct UAT helpers that bypass Factory.WithKeychain.
    path = root / "internal/keychain/keychain.go"
    value = path.read_text(encoding="utf-8")
    changes = {
        "func Get(service, account string) (string, error) {": "func Get(service, account string) (string, error) {\n if WegentAccess != nil { return WegentAccess.Get(service, account) }",
        "func Set(service, account, data string) error {": "func Set(service, account, data string) error {\n if WegentAccess != nil { return WegentAccess.Set(service, account, data) }",
        "func Remove(service, account string) error {": "func Remove(service, account string) error {\n if WegentAccess != nil { return WegentAccess.Remove(service, account) }",
    }
    for old, new in changes.items():
        if value.count(old) != 1:
            raise ValueError("Upstream keychain boundary changed")
        value = value.replace(old, new)
    path.write_text(
        value
        + "\n// WegentAccess is set only in the native managed process.\nvar WegentAccess KeychainAccess\n",
        encoding="utf-8",
    )
    # The bundled executable has a private dispatcher before the upstream CLI.
    # Preserve the local event daemon's self-spawn entry point.
    startup = root / "internal/event/consume/startup.go"
    value = startup.read_text(encoding="utf-8")
    old = "cmd := exec.Command(exe, args...)"
    if value.count(old) != 1:
        raise ValueError("Upstream event daemon boundary changed")
    startup.write_text(
        value.replace(
            old, 'cmd := exec.Command(exe, append([]string{"local"}, args...)...)'
        ),
        encoding="utf-8",
    )
    return root


def build(root, output, target):
    system, arch = target.split("/")
    environment = {**os.environ, "GOOS": system, "GOARCH": arch, "CGO_ENABLED": "0"}
    raw = output.with_suffix(".exe" if system == "windows" else ".bin")
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "go",
            "build",
            "-trimpath",
            "-ldflags=-s -w",
            "-o",
            str(raw),
            "./cmd/wegent-account-auth",
        ],
        cwd=root,
        env=environment,
        check=True,
    )
    content = raw.read_bytes()
    output.write_bytes(lzma.compress(content, preset=6))
    metadata = {
        "nativeProtocolVersion": 1,
        "target": target,
        "upstreamVersion": VERSION,
        "upstreamSourceSha256": SOURCE_SHA256,
        "binarySha256": hashlib.sha256(content).hexdigest(),
        "binaryBytes": len(content),
        "compressedSha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "sources": {
            p.relative_to(ROOT.parent)
            .as_posix(): hashlib.sha256(p.read_bytes())
            .hexdigest()
            for p in sorted(
                [
                    *ROOT.glob("*.py"),
                    *(ROOT / "overlay").glob("*.go"),
                    *(ROOT.parent / "plugin-auth-go").glob("*.go"),
                ]
            )
        },
    }
    output.with_suffix(".json").write_text(json.dumps(metadata, indent=2) + "\n")
    raw.unlink()
    return metadata


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-archive", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--test", action="store_true")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="wegent-lark-build-") as temporary:
        directory = Path(temporary)
        root = prepare(directory, source_archive(directory, args.source_archive))
        if args.test:
            subprocess.run(
                [
                    "go",
                    "test",
                    "./internal/wegentpluginauth",
                    "./cmd/wegent-account-auth",
                ],
                cwd=root,
                check=True,
            )
        print(json.dumps(build(root, args.output.resolve(), args.target)))


if __name__ == "__main__":
    main()

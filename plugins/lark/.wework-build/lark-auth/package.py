"""Package the same verified companion for each supported desktop/cloud target."""

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from build import build, prepare, source_archive
from toolchain import ensure_go
from verify import verify_package

TARGETS = (
    "darwin/arm64",
    "darwin/amd64",
    "linux/amd64",
    "linux/arm64",
    "windows/amd64",
)


def assemble(plugin, output, provided=None):
    with tempfile.TemporaryDirectory(prefix="wegent-lark-package-") as temporary:
        directory = Path(temporary)
        ensure_go(directory)
        source = prepare(directory, source_archive(directory, provided))
        subprocess.run(
            ["go", "test", "./internal/wegentpluginauth", "./cmd/wegent-account-auth"],
            cwd=source,
            check=True,
        )
        staged = directory / "plugin"
        shutil.copytree(
            plugin,
            staged,
            symlinks=True,
            ignore=shutil.ignore_patterns(
                "__pycache__", "*.pyc", ".DS_Store", "native"
            ),
        )
        for path in staged.rglob("*"):
            if path.is_symlink() or path.name in {".env", ".git"}:
                raise ValueError("Plugin contains private state or a symlink")
        manifest = json.loads(
            (staged / ".codex-plugin/plugin.json").read_text(encoding="utf-8")
        )
        if (
            manifest["name"] != "lark"
            or manifest["connectors"][0]["accountAuth"]["exportMode"] != "exclusive"
        ):
            raise ValueError("Incorrect Lark auth declaration")
        for target in TARGETS:
            folder = staged / "scripts/native" / target.replace("/", "-")
            build(source, folder / "lark-account-auth.xz", target)
            shutil.copyfile(source / "LICENSE", folder / "UPSTREAM-LICENSE")
        verify_package(staged)
        output.parent.mkdir(parents=True, exist_ok=True)
        files = sorted(path for path in staged.rglob("*") if path.is_file())
        if sum(path.stat().st_size for path in files) > 200 * 1024 * 1024:
            raise ValueError("Expanded plugin exceeds package limit")
        candidate = directory / "plugin.zip"
        with zipfile.ZipFile(
            candidate, "w", zipfile.ZIP_DEFLATED, compresslevel=9
        ) as archive:
            for path in files:
                entry = zipfile.ZipInfo(
                    path.relative_to(staged).as_posix(), (1980, 1, 1, 0, 0, 0)
                )
                entry.create_system = 3
                entry.external_attr = (stat.S_IFREG | 0o644) << 16
                entry.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(entry, path.read_bytes(), compresslevel=9)
        if candidate.stat().st_size > 50 * 1024 * 1024:
            raise ValueError("Plugin archive exceeds upload limit")
        shutil.copyfile(candidate, output)
        checksum = hashlib.sha256(output.read_bytes()).hexdigest()
        output.with_name(output.name + ".sha256").write_text(
            checksum + "  " + output.name + "\n"
        )
        print(
            json.dumps(
                {
                    "artifact": str(output),
                    "sha256": checksum,
                    "bytes": output.stat().st_size,
                }
            )
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--plugin", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-archive", type=Path)
    args = parser.parse_args()
    assemble(args.plugin.resolve(), args.output.resolve(), args.source_archive)

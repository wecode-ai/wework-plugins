"""Verify pinned native assets and assemble a standalone marketplace package."""

import argparse
import hashlib
import json
import lzma
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path
from build import TARGETS, SOURCE_SHA256, RUST_VERSION, source_hash
from verify import verify_package


def validate_inventory(plugin):
    inventory = plugin / "scripts/native"
    if {path.name for path in inventory.iterdir() if path.is_dir()} != set(TARGETS):
        raise ValueError("All five native platforms are required")
    for target in TARGETS:
        folder = inventory / target
        metadata = json.loads(
            (folder / "wecom-account-auth.json").read_text(encoding="utf-8")
        )
        blob = folder / "wecom-account-auth.xz"
        if (
            metadata.get("sources") != source_hash()
            or metadata.get("upstreamSourceSha256") != SOURCE_SHA256
            or metadata.get("rustVersion") != RUST_VERSION
            or metadata.get("target") != target.replace("-", "/", 1)
        ):
            raise ValueError("Rebuild native assets after changing provider sources")
        packed = blob.read_bytes()
        if hashlib.sha256(packed).hexdigest() != metadata["compressedSha256"]:
            raise ValueError("Native asset checksum mismatch")
        decoder = lzma.LZMADecompressor(memlimit=128 * 1024 * 1024)
        data = decoder.decompress(packed, max_length=80 * 1024 * 1024)
        if (
            not decoder.eof
            or len(data) != metadata["binaryBytes"]
            or hashlib.sha256(data).hexdigest() != metadata["binarySha256"]
        ):
            raise ValueError("Native binary checksum mismatch")


def assemble(plugin, output):
    validate_inventory(plugin)
    verify_package(plugin)
    with tempfile.TemporaryDirectory(prefix="wecom-package-") as temporary:
        root = Path(temporary) / "plugin"
        shutil.copytree(
            plugin,
            root,
            symlinks=True,
            ignore=shutil.ignore_patterns(
                "__pycache__", "*.pyc", ".DS_Store", "build-info"
            ),
        )
        marker = root / "build-info/package.json"
        marker.parent.mkdir()
        marker.write_text(
            json.dumps(
                {
                    "protocolVersion": 1,
                    "platforms": sorted(TARGETS),
                    "sources": source_hash(),
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        files = sorted(p for p in root.rglob("*") if p.is_file())
        if any(p.is_symlink() or p.name in {".env", ".git"} for p in root.rglob("*")):
            raise ValueError("Private state or symlinks are forbidden")
        if sum(p.stat().st_size for p in files) > 200 * 1024 * 1024:
            raise ValueError("Package exceeds expanded limit")
        candidate = Path(temporary) / "plugin.zip"
        with zipfile.ZipFile(
            candidate, "w", zipfile.ZIP_DEFLATED, compresslevel=9
        ) as archive:
            for path in files:
                info = zipfile.ZipInfo(
                    path.relative_to(root).as_posix(), (1980, 1, 1, 0, 0, 0)
                )
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | 0o644) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, path.read_bytes(), compresslevel=9)
        if candidate.stat().st_size > 50 * 1024 * 1024:
            raise ValueError("Package exceeds upload limit")
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(candidate, output)
        checksum = hashlib.sha256(output.read_bytes()).hexdigest()
        output.with_name(output.name + ".sha256").write_text(
            checksum + "  " + output.name + "\n", encoding="utf-8"
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
    args = parser.parse_args()
    assemble(args.plugin.resolve(), args.output.resolve())

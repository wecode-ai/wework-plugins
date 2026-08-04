#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Run the plugin tool with shared or isolated Python dependencies."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_ROOT = SKILL_ROOT.parent.parent
PLUGIN_NAME = PLUGIN_ROOT.name
TOOL_PATH = SKILL_ROOT / "scripts" / "artifact_cli.py"
LOCK_PATH = SKILL_ROOT / "requirements.lock"
REQUIREMENTS = {
    "documents": (("docx", "python-docx", "1.2.0"),),
    "presentations": (
        ("PIL", "Pillow", "12.2.0"),
        ("pptx", "python-pptx", "1.0.2"),
    ),
    "pdf": (
        ("pdfplumber", "pdfplumber", "0.11.9"),
        ("pypdf", "pypdf", "6.10.0"),
        ("reportlab", "reportlab", "4.4.9"),
    ),
    "spreadsheets": (("openpyxl", "openpyxl", "3.1.5"),),
}


def python_in_venv(root: Path) -> Path:
    if os.name == "nt":
        return root / "Scripts" / "python.exe"
    return root / "bin" / "python3"


def dependency_probe() -> str:
    requirements = REQUIREMENTS.get(PLUGIN_NAME)
    if not requirements:
        raise SystemExit(f"Unsupported plugin environment: {PLUGIN_NAME}")
    checks = " and ".join(
        f"(util.find_spec({module!r}) and metadata.version({distribution!r}) == {version!r})"
        for module, distribution, version in requirements
    )
    return (
        "import importlib.metadata as metadata,importlib.util as util,sys;"
        f"sys.exit(0 if ({checks}) else 1)"
    )


def python_candidates() -> list[Path]:
    candidates: list[Path] = []
    configured = os.environ.get("CODEX_WORKSPACE_DEPENDENCIES", "").strip()
    if configured:
        root = Path(configured).expanduser()
        candidates.extend(
            [
                root / "python" / "bin" / "python3",
                root / "python" / "python.exe",
                root / "dependencies" / "python" / "bin" / "python3",
                root / "dependencies" / "python" / "python.exe",
            ]
        )
    candidates.append(Path(sys.executable))
    for command in ("python3", "python"):
        resolved = shutil.which(command)
        if resolved:
            candidates.append(Path(resolved))
    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key not in seen and candidate.is_file():
            seen.add(key)
            unique.append(candidate)
    return unique


def supports_plugin(python: Path) -> bool:
    environment = os.environ.copy()
    environment["PYTHONNOUSERSITE"] = "1"
    completed = subprocess.run(
        [str(python), "-c", dependency_probe()],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        env=environment,
    )
    return completed.returncode == 0


def supports_environment_install(python: Path) -> bool:
    completed = subprocess.run(
        [
            str(python),
            "-c",
            "import sys;sys.exit(0 if sys.version_info >= (3, 10) else 1)",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return completed.returncode == 0


def environment_home() -> Path:
    configured = os.environ.get("WEGENT_EXECUTOR_HOME", "").strip()
    if configured:
        return Path(configured).expanduser()
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA", "").strip()
        if local:
            return Path(local) / "Wegent"
    return Path.home() / ".wegent-executor"


def install_environment(base_python: Path, target: Path) -> Path:
    temporary = target.with_name(f".{target.name}-{os.getpid()}.staging")
    if temporary.exists():
        shutil.rmtree(temporary)
    temporary.parent.mkdir(parents=True, exist_ok=True)
    print(
        f"Preparing {PLUGIN_NAME} Python dependencies in {target}",
        file=sys.stderr,
    )
    try:
        subprocess.run(
            [str(base_python), "-m", "venv", str(temporary)],
            check=True,
        )
        temporary_python = python_in_venv(temporary)
        subprocess.run(
            [
                str(temporary_python),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--require-hashes",
                "--requirement",
                str(LOCK_PATH),
            ],
            check=True,
        )
        if not supports_plugin(temporary_python):
            raise RuntimeError("installed environment failed its import check")
        try:
            temporary.rename(target)
        except OSError:
            installed = python_in_venv(target)
            if not installed.is_file() or not supports_plugin(installed):
                raise
            shutil.rmtree(temporary)
    except BaseException:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise
    return python_in_venv(target)


def resolve_python() -> Path:
    if not LOCK_PATH.is_file():
        raise SystemExit(f"Dependency lock is missing: {LOCK_PATH}")
    candidates = python_candidates()
    for candidate in candidates:
        if supports_plugin(candidate):
            return candidate
    installers = [
        candidate for candidate in candidates if supports_environment_install(candidate)
    ]
    if not installers:
        raise SystemExit("Python 3.10 or newer is required to prepare this plugin")
    digest = hashlib.sha256(LOCK_PATH.read_bytes()).hexdigest()[:16]
    target = environment_home() / "plugin-envs" / "wework-public" / PLUGIN_NAME / digest
    installed = python_in_venv(target)
    if installed.is_file() and supports_plugin(installed):
        return installed
    if target.exists():
        shutil.rmtree(target)
    return install_environment(installers[0], target)


def main() -> None:
    python = resolve_python()
    environment = os.environ.copy()
    environment["PYTHONNOUSERSITE"] = "1"
    os.execve(
        str(python),
        [str(python), str(TOOL_PATH), *sys.argv[1:]],
        environment,
    )


if __name__ == "__main__":
    main()

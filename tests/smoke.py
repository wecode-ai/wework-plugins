#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Exercise the non-rendering command surface of the artifact plugins."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPOSITORY_ROOT / "tests" / "fixtures"


def run_plugin(
    plugin: str,
    environment_home: Path,
    *arguments: object,
) -> None:
    bootstrap = (
        REPOSITORY_ROOT
        / "plugins"
        / plugin
        / "skills"
        / plugin
        / "scripts"
        / "bootstrap.py"
    )
    environment = os.environ.copy()
    environment["WEGENT_EXECUTOR_HOME"] = str(environment_home)
    command = [sys.executable, str(bootstrap), *(str(value) for value in arguments)]
    subprocess.run(
        command,
        cwd=REPOSITORY_ROOT,
        env=environment,
        check=True,
    )


def smoke_documents(output: Path, environment_home: Path) -> None:
    created = output / "document.docx"
    edited = output / "document-edited.docx"
    run_plugin(
        "documents",
        environment_home,
        "create",
        "--spec",
        FIXTURES / "document.json",
        "--output",
        created,
    )
    run_plugin(
        "documents",
        environment_home,
        "inspect",
        "--input",
        created,
        "--output",
        output / "document.json",
    )
    run_plugin(
        "documents",
        environment_home,
        "replace",
        "--input",
        created,
        "--old",
        "开源 Documents",
        "--new",
        "Wecode Documents",
        "--output",
        edited,
    )
    run_plugin("documents", environment_home, "validate", "--input", edited)


def smoke_presentations(output: Path, environment_home: Path) -> None:
    created = output / "presentation.pptx"
    edited = output / "presentation-edited.pptx"
    run_plugin(
        "presentations",
        environment_home,
        "create",
        "--spec",
        FIXTURES / "presentation.json",
        "--output",
        created,
    )
    run_plugin(
        "presentations",
        environment_home,
        "inspect",
        "--input",
        created,
        "--output",
        output / "presentation.json",
    )
    run_plugin(
        "presentations",
        environment_home,
        "replace",
        "--input",
        created,
        "--old",
        "本地 MVP",
        "--new",
        "本地运行时",
        "--output",
        edited,
    )
    run_plugin("presentations", environment_home, "validate", "--input", edited)


def smoke_pdf(output: Path, environment_home: Path) -> None:
    created = output / "report.pdf"
    merged = output / "merged.pdf"
    selected = output / "selected.pdf"
    rotated = output / "rotated.pdf"
    run_plugin(
        "pdf",
        environment_home,
        "create",
        "--spec",
        FIXTURES / "pdf.json",
        "--output",
        created,
    )
    run_plugin(
        "pdf",
        environment_home,
        "inspect",
        "--input",
        created,
        "--text",
        "--output",
        output / "pdf.json",
    )
    run_plugin(
        "pdf",
        environment_home,
        "merge",
        "--inputs",
        created,
        created,
        "--output",
        merged,
    )
    run_plugin(
        "pdf",
        environment_home,
        "split",
        "--input",
        merged,
        "--pages",
        "2-3",
        "--output",
        selected,
    )
    run_plugin(
        "pdf",
        environment_home,
        "rotate",
        "--input",
        selected,
        "--degrees",
        90,
        "--pages",
        2,
        "--output",
        rotated,
    )
    run_plugin("pdf", environment_home, "validate", "--input", rotated)


def smoke_spreadsheets(output: Path, environment_home: Path) -> None:
    created = output / "workbook.xlsx"
    edited = output / "workbook-edited.xlsx"
    run_plugin(
        "spreadsheets",
        environment_home,
        "create",
        "--spec",
        FIXTURES / "spreadsheet.json",
        "--output",
        created,
    )
    run_plugin(
        "spreadsheets",
        environment_home,
        "inspect",
        "--input",
        created,
        "--output",
        output / "spreadsheet.json",
    )
    run_plugin(
        "spreadsheets",
        environment_home,
        "set-cell",
        "--input",
        created,
        "--sheet",
        "Summary",
        "--cell",
        "B2",
        "--value-json",
        125000,
        "--output",
        edited,
    )
    run_plugin(
        "spreadsheets",
        environment_home,
        "export-csv",
        "--input",
        edited,
        "--sheet",
        "Summary",
        "--output",
        output / "summary.csv",
    )
    run_plugin("spreadsheets", environment_home, "validate", "--input", edited)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="wework-plugin-smoke-") as temporary:
        root = Path(temporary)
        environment_home = root / "runtime"
        output = root / "output"
        output.mkdir()
        smoke_documents(output, environment_home)
        smoke_presentations(output, environment_home)
        smoke_pdf(output, environment_home)
        smoke_spreadsheets(output, environment_home)
    print("Artifact plugin smoke tests passed")


if __name__ == "__main__":
    main()

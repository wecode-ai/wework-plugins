#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Deterministic PDF creation, inspection, extraction, transforms, and validation."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from html import escape
from pathlib import Path

import pdfplumber
from pypdf import PdfReader, PdfWriter
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4, LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Image,
    ListFlowable,
    ListItem,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


def emit(payload: object, destination: str | None = None) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if destination:
        path = Path(destination)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{text}\n", encoding="utf-8")
    else:
        print(text)


def read_spec(path: str) -> dict[str, object]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError("PDF specification must be a JSON object")
    return value


def paragraph_text(value: object) -> str:
    return escape(str(value)).replace("\n", "<br/>")


def font_candidates() -> list[Path]:
    configured = os.environ.get("WEGENT_ARTIFACT_FONT", "").strip()
    candidates: list[Path] = []
    if configured:
        configured_path = Path(configured).expanduser()
        if not configured_path.is_file():
            raise FileNotFoundError(
                f"WEGENT_ARTIFACT_FONT does not exist: {configured_path}"
            )
        candidates.append(configured_path)
    candidates.extend(
        [
            Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
            Path("/Library/Fonts/Arial Unicode.ttf"),
            Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
            Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
            Path("C:/Windows/Fonts/msyh.ttc"),
            Path("C:/Windows/Fonts/arialuni.ttf"),
        ]
    )
    return candidates


def register_unicode_font() -> str:
    failures: list[str] = []
    for path in font_candidates():
        if not path.is_file():
            continue
        try:
            pdfmetrics.registerFont(TTFont("WegentArtifact", str(path)))
        except Exception as error:  # noqa: BLE001 - try the next local font
            failures.append(f"{path}: {error}")
        else:
            return "WegentArtifact"
    if os.environ.get("WEGENT_ARTIFACT_FONT", "").strip() and failures:
        raise RuntimeError(f"configured Unicode font is unusable: {failures[0]}")
    try:
        pdfmetrics.getFont("STSong-Light")
    except KeyError:
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    return "STSong-Light"


def build_styles() -> dict[str, ParagraphStyle]:
    font_name = register_unicode_font()
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "CleanTitle",
            parent=base["Title"],
            fontName=font_name,
            fontSize=24,
            leading=31,
            alignment=TA_CENTER,
            textColor=colors.HexColor("#0F172A"),
            spaceAfter=22,
        ),
        "heading": ParagraphStyle(
            "CleanHeading",
            parent=base["Heading2"],
            fontName=font_name,
            fontSize=16,
            leading=21,
            textColor=colors.HexColor("#1D4ED8"),
            spaceBefore=12,
            spaceAfter=8,
        ),
        "body": ParagraphStyle(
            "CleanBody",
            parent=base["BodyText"],
            fontName=font_name,
            fontSize=10.5,
            leading=16,
            textColor=colors.HexColor("#1E293B"),
            spaceAfter=8,
        ),
        "caption": ParagraphStyle(
            "CleanCaption",
            parent=base["BodyText"],
            fontName=font_name,
            fontSize=8.5,
            leading=12,
            alignment=TA_CENTER,
            textColor=colors.HexColor("#64748B"),
            spaceAfter=8,
        ),
    }


def table_flowable(
    value: dict[str, object], styles: dict[str, ParagraphStyle]
) -> Table:
    columns = value.get("columns", [])
    rows = value.get("rows", [])
    if not isinstance(columns, list) or not isinstance(rows, list):
        raise TypeError("table columns and rows must be arrays")
    data: list[list[object]] = []
    if columns:
        data.append(
            [Paragraph(paragraph_text(cell), styles["body"]) for cell in columns]
        )
    for row in rows:
        if not isinstance(row, list):
            raise TypeError("every table row must be an array")
        data.append([Paragraph(paragraph_text(cell), styles["body"]) for cell in row])
    if not data:
        raise ValueError("table must contain columns or rows")
    table = Table(data, repeatRows=1 if columns else 0, hAlign="LEFT")
    commands: list[tuple[object, ...]] = [
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]
    if columns:
        commands.extend(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E2E8F0")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#0F172A")),
            ]
        )
    table.setStyle(TableStyle(commands))
    return table


def build_story(spec: dict[str, object]) -> list[object]:
    styles = build_styles()
    story: list[object] = []
    title = str(spec.get("title", "")).strip()
    if title:
        story.append(Paragraph(paragraph_text(title), styles["title"]))
    content = spec.get("content", [])
    if not isinstance(content, list):
        raise TypeError("content must be an array")
    for item in content:
        if not isinstance(item, dict):
            raise TypeError("every content item must be an object")
        if "heading" in item:
            story.append(Paragraph(paragraph_text(item["heading"]), styles["heading"]))
        elif "paragraph" in item:
            story.append(Paragraph(paragraph_text(item["paragraph"]), styles["body"]))
        elif "bullets" in item:
            bullets = item["bullets"]
            if not isinstance(bullets, list):
                raise TypeError("bullets must be an array")
            story.append(
                ListFlowable(
                    [
                        ListItem(Paragraph(paragraph_text(value), styles["body"]))
                        for value in bullets
                    ],
                    bulletType="bullet",
                    leftIndent=18,
                )
            )
            story.append(Spacer(1, 8))
        elif "table" in item:
            table = item["table"]
            if not isinstance(table, dict):
                raise TypeError("table must be an object")
            story.append(table_flowable(table, styles))
            story.append(Spacer(1, 10))
        elif "image" in item:
            image = Image(str(Path(str(item["image"])).expanduser()))
            image.drawWidth = float(item.get("widthInches", 6.0)) * inch
            image.drawHeight = image.imageHeight * (image.drawWidth / image.imageWidth)
            story.append(image)
            caption = str(item.get("caption", "")).strip()
            if caption:
                story.append(Paragraph(paragraph_text(caption), styles["caption"]))
        elif item.get("pageBreak") is True:
            story.append(PageBreak())
        else:
            raise ValueError(f"unsupported content item: {sorted(item)}")
    return story


def command_create(args: argparse.Namespace) -> None:
    spec = read_spec(args.spec)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    page_size = A4 if str(spec.get("pageSize", "A4")).upper() == "A4" else LETTER
    document = SimpleDocTemplate(
        str(output),
        pagesize=page_size,
        title=str(spec.get("title", "")),
        author=str(spec.get("author", "")),
        subject=str(spec.get("subject", "")),
        leftMargin=float(spec.get("marginInches", 0.75)) * inch,
        rightMargin=float(spec.get("marginInches", 0.75)) * inch,
        topMargin=float(spec.get("marginInches", 0.75)) * inch,
        bottomMargin=float(spec.get("marginInches", 0.75)) * inch,
    )
    document.build(build_story(spec))
    emit({"status": "created", "path": str(output.resolve())})


def page_record(index: int, page: object, include_text: bool) -> dict[str, object]:
    box = page.mediabox
    record: dict[str, object] = {
        "index": index,
        "widthPoints": round(float(box.width), 3),
        "heightPoints": round(float(box.height), 3),
        "rotation": int(page.rotation or 0),
    }
    if include_text:
        record["text"] = page.extract_text() or ""
    return record


def command_inspect(args: argparse.Namespace) -> None:
    source = Path(args.input)
    reader = PdfReader(source)
    metadata = reader.metadata or {}
    emit(
        {
            "path": str(source.resolve()),
            "encrypted": reader.is_encrypted,
            "metadata": {str(key): str(value) for key, value in metadata.items()},
            "pages": [
                page_record(index, page, args.text)
                for index, page in enumerate(reader.pages, start=1)
            ],
        },
        args.output,
    )


def command_extract(args: argparse.Namespace) -> None:
    source = Path(args.input)
    with pdfplumber.open(source) as document:
        pages = []
        for index, page in enumerate(document.pages, start=1):
            record: dict[str, object] = {
                "index": index,
                "text": page.extract_text() or "",
            }
            if args.tables:
                record["tables"] = page.extract_tables()
            pages.append(record)
    emit({"path": str(source.resolve()), "pages": pages}, args.output)


def command_merge(args: argparse.Namespace) -> None:
    writer = PdfWriter()
    for input_path in args.inputs:
        writer.append(input_path)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as stream:
        writer.write(stream)
    emit({"status": "merged", "path": str(output.resolve()), "inputs": args.inputs})


def parse_pages(expression: str, total: int) -> list[int]:
    indexes: list[int] = []
    for token in expression.split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            start_text, end_text = token.split("-", 1)
            start, end = int(start_text), int(end_text)
            if start > end:
                raise ValueError(f"invalid descending page range: {token}")
            indexes.extend(range(start - 1, end))
        else:
            indexes.append(int(token) - 1)
    if not indexes or any(index < 0 or index >= total for index in indexes):
        raise ValueError(f"page selection is outside 1-{total}")
    return indexes


def command_split(args: argparse.Namespace) -> None:
    reader = PdfReader(args.input)
    indexes = parse_pages(args.pages, len(reader.pages))
    writer = PdfWriter()
    for index in indexes:
        writer.add_page(reader.pages[index])
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as stream:
        writer.write(stream)
    emit(
        {
            "status": "split",
            "path": str(output.resolve()),
            "pages": [i + 1 for i in indexes],
        }
    )


def command_rotate(args: argparse.Namespace) -> None:
    if args.degrees % 90 != 0:
        raise ValueError("rotation must be a multiple of 90 degrees")
    reader = PdfReader(args.input)
    indexes = (
        set(parse_pages(args.pages, len(reader.pages)))
        if args.pages
        else set(range(len(reader.pages)))
    )
    writer = PdfWriter()
    for index, page in enumerate(reader.pages):
        if index in indexes:
            page.rotate(args.degrees)
        writer.add_page(page)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as stream:
        writer.write(stream)
    emit({"status": "rotated", "path": str(output.resolve()), "degrees": args.degrees})


def dependency_roots() -> list[Path]:
    configured = os.environ.get("CODEX_WORKSPACE_DEPENDENCIES", "").strip()
    if not configured:
        return []
    root = Path(configured).expanduser()
    return [root, root / "dependencies"]


def find_binary(name: str) -> str | None:
    resolved = shutil.which(name)
    if resolved:
        return resolved
    for root in dependency_roots():
        for candidate in (
            root / "bin" / "override" / name,
            root / "bin" / "fallback" / name,
            root / "native" / "poppler" / "bin" / name,
            root / "native" / "poppler" / "poppler" / "bin" / name,
        ):
            if candidate.is_file():
                return str(candidate)
    return None


def command_render(args: argparse.Namespace) -> None:
    source = Path(args.input).resolve()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    pdftoppm = find_binary("pdftoppm")
    if not pdftoppm:
        raise RuntimeError("pdftoppm is required to render PDF pages")
    prefix = output_dir / source.stem
    subprocess.run(
        [pdftoppm, "-png", "-r", str(args.dpi), str(source), str(prefix)],
        check=True,
    )
    images = [str(path) for path in sorted(output_dir.glob(f"{source.stem}-*.png"))]
    emit({"images": images})


def command_validate(args: argparse.Namespace) -> None:
    source = Path(args.input)
    issues: list[str] = []
    warnings: list[str] = []
    try:
        reader = PdfReader(source)
        if reader.is_encrypted:
            warnings.append("PDF is encrypted; content validation is limited")
        if not reader.pages:
            issues.append("PDF has no pages")
        for index, page in enumerate(reader.pages, start=1):
            if float(page.mediabox.width) <= 0 or float(page.mediabox.height) <= 0:
                issues.append(f"page {index} has invalid media box dimensions")
            try:
                page.extract_text()
            except Exception as error:  # noqa: BLE001 - collect per-page failures
                issues.append(f"page {index} text extraction failed: {error}")
    except Exception as error:  # noqa: BLE001 - validation reports format failures
        issues.append(str(error))
    emit(
        {
            "valid": not issues,
            "issues": issues,
            "warnings": warnings,
            "path": str(source.resolve()),
        }
    )
    if issues:
        raise SystemExit(1)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create")
    create.add_argument("--spec", required=True)
    create.add_argument("--output", required=True)
    create.set_defaults(handler=command_create)
    inspect = subparsers.add_parser("inspect")
    inspect.add_argument("--input", required=True)
    inspect.add_argument("--output")
    inspect.add_argument("--text", action="store_true")
    inspect.set_defaults(handler=command_inspect)
    extract = subparsers.add_parser("extract")
    extract.add_argument("--input", required=True)
    extract.add_argument("--output")
    extract.add_argument("--tables", action="store_true")
    extract.set_defaults(handler=command_extract)
    merge = subparsers.add_parser("merge")
    merge.add_argument("--inputs", nargs="+", required=True)
    merge.add_argument("--output", required=True)
    merge.set_defaults(handler=command_merge)
    split = subparsers.add_parser("split")
    split.add_argument("--input", required=True)
    split.add_argument(
        "--pages", required=True, help="1-based pages, for example 1-3,5"
    )
    split.add_argument("--output", required=True)
    split.set_defaults(handler=command_split)
    rotate = subparsers.add_parser("rotate")
    rotate.add_argument("--input", required=True)
    rotate.add_argument("--degrees", type=int, required=True)
    rotate.add_argument("--pages", help="optional 1-based page selection")
    rotate.add_argument("--output", required=True)
    rotate.set_defaults(handler=command_rotate)
    render = subparsers.add_parser("render")
    render.add_argument("--input", required=True)
    render.add_argument("--output-dir", required=True)
    render.add_argument("--dpi", type=int, default=144)
    render.set_defaults(handler=command_render)
    validate = subparsers.add_parser("validate")
    validate.add_argument("--input", required=True)
    validate.set_defaults(handler=command_validate)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        args.handler(args)
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"pdf: {error}", file=sys.stderr)
        raise SystemExit(2) from error


if __name__ == "__main__":
    main()

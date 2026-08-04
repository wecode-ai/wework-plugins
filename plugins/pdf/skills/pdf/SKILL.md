---
name: pdf
description: Create, inspect, extract, merge, split, rotate, render, and validate PDF files with local open-source tools. Use when a request targets PDF output or input, requires page-level transforms, needs text or table extraction, or requires visual verification of a generated PDF.
---

# PDF

Use the deterministic PDF CLI for common creation and transformation tasks. Preserve source files and write transformed PDFs to new paths.

## Runtime

Prefer workspace dependency Python. Otherwise use Python 3:

```bash
python3 "$SKILL_DIR/scripts/bootstrap.py" <command> [arguments]
```

Missing packages are installed from the hashed lock into `~/.wegent-executor/plugin-envs/wework-public/pdf/`, never globally.

## Workflow

1. Run `inspect --text` or `extract` to understand an input PDF.
2. For new PDFs, author a JSON specification following [spec.md](references/spec.md), then run `create`.
3. Use `merge`, `split`, or `rotate` for page-level transformations.
4. Run `validate` on the final PDF.
5. Run `render` and inspect every output page image before delivery when layout matters.

## Commands

```bash
python3 "$SKILL_DIR/scripts/bootstrap.py" inspect --input source.pdf --text --output inspection.json
python3 "$SKILL_DIR/scripts/bootstrap.py" extract --input source.pdf --tables --output extraction.json
python3 "$SKILL_DIR/scripts/bootstrap.py" create --spec report.json --output report.pdf
python3 "$SKILL_DIR/scripts/bootstrap.py" merge --inputs first.pdf second.pdf --output merged.pdf
python3 "$SKILL_DIR/scripts/bootstrap.py" split --input merged.pdf --pages '1-3,5' --output selection.pdf
python3 "$SKILL_DIR/scripts/bootstrap.py" rotate --input source.pdf --degrees 90 --pages '2' --output rotated.pdf
python3 "$SKILL_DIR/scripts/bootstrap.py" validate --input result.pdf
python3 "$SKILL_DIR/scripts/bootstrap.py" render --input result.pdf --output-dir rendered
```

## Quality rules

- Do not overwrite input PDFs.
- Preserve page order intentionally and use 1-based page selections.
- Treat extracted text as an interpretation of PDF drawing instructions; verify tables and reading order against rendered pages.
- Warn when a PDF is encrypted or content extraction is incomplete.
- A structurally valid PDF still requires visual inspection when it is user-facing.

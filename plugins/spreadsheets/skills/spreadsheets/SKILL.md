---
name: spreadsheets
description: Create, inspect, edit, export, render, and validate XLSX workbooks with local open-source tools. Use for building editable spreadsheets from structured data, examining formulas and sheet geometry, changing cells, exporting worksheets to CSV, adding common charts, and visually reviewing workbook output.
---

# Spreadsheets

Use the deterministic workbook CLI and keep calculations editable inside the resulting XLSX. Never overwrite an input workbook.

## Runtime

Prefer workspace dependency Python. Otherwise use Python 3:

```bash
python3 "$SKILL_DIR/scripts/bootstrap.py" <command> [arguments]
```

Missing packages are installed from the hashed lock into `~/.wegent-executor/plugin-envs/wework-public/spreadsheets/`, never globally.

## Workflow

1. Run `inspect` before modifying an existing workbook.
2. For new workbooks, prepare a JSON specification from [spec.md](references/spec.md), then run `create`.
3. Use formulas for auditable calculations. Use `set-cell` for focused changes and `export-csv` for interchange.
4. Run `validate` after every change.
5. Run `render --images`, inspect all pages, and correct clipped columns, excessive pagination, or unreadable formatting.

## Commands

```bash
python3 "$SKILL_DIR/scripts/bootstrap.py" inspect --input source.xlsx --output inspection.json
python3 "$SKILL_DIR/scripts/bootstrap.py" create --spec workbook.json --output result.xlsx
python3 "$SKILL_DIR/scripts/bootstrap.py" set-cell --input source.xlsx --sheet Summary --cell B2 --value-json '125' --output result.xlsx
python3 "$SKILL_DIR/scripts/bootstrap.py" set-cell --input source.xlsx --sheet Summary --cell C2 --formula 'SUM(A2:B2)' --output result.xlsx
python3 "$SKILL_DIR/scripts/bootstrap.py" export-csv --input result.xlsx --sheet Summary --output summary.csv
python3 "$SKILL_DIR/scripts/bootstrap.py" validate --input result.xlsx
python3 "$SKILL_DIR/scripts/bootstrap.py" render --input result.xlsx --output-dir rendered --images
```

## Quality rules

- Store numbers, dates, booleans, and formulas as typed cell values rather than formatted strings.
- Use a styled header, readable column widths, freeze panes, and filters for non-trivial tables.
- Prefer formulas over hard-coded calculated outputs when users need an auditable model.
- Remember that the Python library does not calculate formulas; recalculate with Excel or LibreOffice when current values matter.
- Validate structure and visually review rendered output before delivery.

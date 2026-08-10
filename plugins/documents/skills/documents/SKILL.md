---
name: documents
description: 使用本地开源工具创建、检查、编辑、渲染与校验 Microsoft Word DOCX 文件。适用于起草可编辑报告与备忘录、提取文档结构、在尽量保留版式的前提下替换文本、检查 DOCX 包完整性，以及对渲染页面做视觉审阅。
---

# Documents

Use the bundled deterministic CLI for repeatable DOCX operations. Keep source files unchanged and write results to a new path.

## Runtime

Prefer the Python executable returned by the workspace dependency loader. Otherwise use an available Python 3 executable. Set `SKILL_DIR` to this skill directory and invoke:

```bash
python3 "$SKILL_DIR/scripts/bootstrap.py" <command> [arguments]
```

The bootstrap reuses compatible workspace packages. When packages are missing, it installs the hashed lock file into `~/.wegent-executor/plugin-envs/wework-public/documents/`; it never installs globally.

## Workflow

1. Run `inspect` before modifying an existing DOCX.
2. For new files, write a JSON specification following [spec.md](references/spec.md), then run `create`.
3. Use `replace` only for deliberate text substitutions. It preserves the first run's formatting when a replacement spans multiple runs, so visually verify affected paragraphs.
4. Run `validate` after every create or edit operation.
5. Run `render --images`, inspect every generated page image, and correct clipping, unexpected pagination, or weak hierarchy before delivery.

## Commands

```bash
python3 "$SKILL_DIR/scripts/bootstrap.py" inspect --input source.docx --output inspection.json
python3 "$SKILL_DIR/scripts/bootstrap.py" create --spec document.json --output result.docx
python3 "$SKILL_DIR/scripts/bootstrap.py" replace --input source.docx --old 'Draft' --new 'Final' --output result.docx
python3 "$SKILL_DIR/scripts/bootstrap.py" validate --input result.docx
python3 "$SKILL_DIR/scripts/bootstrap.py" render --input result.docx --output-dir rendered --images
```

## Quality rules

- Use headings, short paragraphs, lists, and tables intentionally; do not simulate structure with spaces.
- Preserve the original file when editing and use descriptive output names.
- Treat a successful package validation as structural evidence, not visual evidence; rendering and page review remain required.
- Do not claim tracked-change, comment, field-code, or macro preservation unless independently verified for the specific file.
- Use user-provided templates and images only from paths the user placed in scope.

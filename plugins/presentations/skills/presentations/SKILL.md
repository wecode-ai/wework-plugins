---
name: presentations
description: 使用本地开源工具创建、检查、编辑、渲染与校验可编辑的 PPTX 幻灯片。适用于根据大纲制作演示文稿、提取幻灯片文本与几何信息、替换内容、检查形状是否越界，以及对渲染后的幻灯片做视觉审阅。
---

# Presentations

Build concise, editable 16:9 decks with the deterministic presentation CLI. Work on copies when editing existing files.

## Runtime

Prefer workspace dependency Python. Otherwise use Python 3:

```bash
python3 "$SKILL_DIR/scripts/bootstrap.py" <command> [arguments]
```

Missing Python packages are installed from the hashed lock into `~/.wegent-executor/plugin-envs/wework-public/presentations/`, never globally.

## Workflow

1. Clarify audience, objective, and desired length from the request.
2. Run `inspect` before changing an existing PPTX.
3. For a new deck, write a JSON specification from [spec.md](references/spec.md), then run `create`.
4. Keep each slide focused on one claim. Prefer visual hierarchy and short bullets over prose blocks.
5. Run `validate`, then `render --images` and inspect every slide image.
6. Revise overflow, dense text, inconsistent hierarchy, or weak contrast before delivery.

## Commands

```bash
python3 "$SKILL_DIR/scripts/bootstrap.py" inspect --input source.pptx --output inspection.json
python3 "$SKILL_DIR/scripts/bootstrap.py" create --spec deck.json --output result.pptx
python3 "$SKILL_DIR/scripts/bootstrap.py" replace --input source.pptx --old 'Q1' --new 'Q2' --output result.pptx
python3 "$SKILL_DIR/scripts/bootstrap.py" validate --input result.pptx
python3 "$SKILL_DIR/scripts/bootstrap.py" render --input result.pptx --output-dir rendered --images
```

## Quality rules

- Use no more slides than needed to tell the story.
- Keep text within the slide canvas and avoid paragraphs where a chart, number, or short list is clearer.
- Preserve the source file and verify all edited slides visually.
- Treat render success and structural validation as separate requirements.
- Do not claim preservation of animations, transitions, embedded media, or unsupported extension data without targeted verification.

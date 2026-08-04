---
name: presentations
description: Create, inspect, edit, render, and validate editable PPTX slide decks with local open-source tools. Use for building presentations from an outline, extracting slide text and geometry, replacing content, checking shapes against slide boundaries, and visually reviewing rendered slides.
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

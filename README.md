# WeWork Public Plugins

General-purpose Codex plugins maintained for Wework and distributed through
the public plugin marketplace.

## Repository layout

```text
.agents/plugins/marketplace.json   # local marketplace registry
plugins/<name>/                    # one plugin per directory
  .codex-plugin/plugin.json        # required manifest
  skills/ commands/ scripts/ ...   # optional plugin surfaces
```

## Available plugins

- `dingtalk`: DingTalk collaboration through the local DWS CLI.
- `documents`: Create, inspect, edit, render, and validate DOCX files.
- `lark`: Feishu/Lark collaboration through the official local CLI.
- `pdf`: Create, inspect, transform, render, and validate PDF files.
- `presentations`: Create, inspect, edit, render, and validate PPTX decks.
- `product-design`: Product design exploration, audits, and interactive prototyping.
- `spreadsheets`: Create, inspect, edit, render, and validate XLSX workbooks.
- `wecom`: WeCom collaboration through the official local CLI.

## Local artifact runtime

The document, PDF, presentation, and spreadsheet plugins prefer compatible Python packages supplied by the host workspace runtime. If the required packages are unavailable, each plugin creates a versioned environment under `~/.wegent-executor/plugin-envs/wework-public/` from a hash-locked requirements file. The bootstrap never modifies system Python or the shared runtime. A first-time isolated installation requires Python 3.10 or newer with `venv` support and access to a Python package index.

LibreOffice and Poppler are optional host-provided native tools used for PDF conversion and page rendering. Creation, inspection, editing, and structural validation do not mutate those tools.

Run the cross-plugin structural smoke test with:

```bash
uv run python tests/smoke.py
```

## Adding a plugin

1. Create `plugins/<slug>/` with a valid `.codex-plugin/plugin.json`.
2. Keep credentials and user-specific state outside the repository.
3. Register the plugin in `.agents/plugins/marketplace.json`.
4. Validate the manifest and every bundled skill before submitting changes.

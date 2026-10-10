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

## Windows compatibility

All eight plugins are covered by repository-level Windows compatibility tests.
DingTalk, Lark, and WeCom ship PowerShell launchers that preserve native exit
codes even when Windows PowerShell 5.1 converts normal CLI stderr into error
records. Python-based skills document the Windows `py -3` launcher, and
reference commands avoid hard-coded POSIX temporary paths and heredocs.

Run the cross-plugin compatibility suite with:

```bash
python -m unittest discover -s tests -v
```

The `Windows compatibility` GitHub Actions workflow additionally parses every
PowerShell script and runs the native Windows authorization/exit-code tests on
`windows-latest`.

## DingTalk builds

DingTalk 0.3.4 declares its build in `plugins/dingtalk/.wework-build.json` and keeps
all reviewed build inputs inside `.wework-build/` in that plugin directory.
GitHub-to-internal-repository mirroring therefore retains everything needed by
the existing MR, package, test and automatic-release pipeline. No separate
account-authentication edition or manual artifact selection is needed.

```bash
uv run --no-project --python 3.12 python plugins/dingtalk/.wework-build/dws-auth/vendor.py --check .
uv run --no-project --python 3.12 python plugins/dingtalk/.wework-build/dws-auth/package.py --plugin plugins/dingtalk --output .ci-artifacts/dingtalk-account-auth.zip
```

The access-only adapter is maintained in this plugin repository; do not overwrite
it with the older exclusive-migration SDK from Wegent. Refresh the local inventory
with `plugins/dingtalk/.wework-build/dws-auth/vendor.py .` after changes.
The builder prepares pinned Go tools, preserves reviewed source, builds five native
targets and exercises the private adapter. The ordinary official publishing command
automatically runs this build; GitLab tests and releases the same resulting ZIP.
The GitHub workflow remains an additional cross-platform build check.

0.3.4 keeps local DWS as the sole refresh owner and syncs access-only snapshots
through WeWork 0.5.4. It does not require a new desktop release. This is not an
unconditional auto-upgrade for 0.3.3 users: retire any existing exclusive cloud
connection while the old definition is still installed, then restore local login
and install the new plugin. Do not delete handoff receipts or silently leave the
old cloud refresh owner running. See the [design and release gates](docs/dingtalk-auth-wework-0.5.4-design.md).

## Adding a plugin

1. Create `plugins/<slug>/` with a valid `.codex-plugin/plugin.json`.
2. Keep credentials and user-specific state outside the repository.
3. Register the plugin in `.agents/plugins/marketplace.json`.
4. Validate the manifest and every bundled skill before submitting changes.

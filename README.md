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
- `lark`: Feishu/Lark collaboration through the official local CLI.
- `wecom`: WeCom collaboration through the official local CLI.

## Adding a plugin

1. Create `plugins/<slug>/` with a valid `.codex-plugin/plugin.json`.
2. Keep credentials and user-specific state outside the repository.
3. Register the plugin in `.agents/plugins/marketplace.json`.
4. Validate the manifest and every bundled skill before submitting changes.

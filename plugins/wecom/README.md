# 企业微信插件

该插件将企业微信 `wecom@0.1.9` 的完整 Skills 适配为 Wegent/WeWork
可发布的 Codex 插件。

## 运行与授权

- 插件没有 Wegent Backend Connector，也不会把 Access Token、Bot ID 或
  Bot Secret 上传到服务端。
- 首次使用时，Skill 通过 `scripts/ensure-wecom-ready.*` 检查 CLI 和本地配置。
- PATH 中没有 `wecom-cli` 时，脚本会下载官方清单中的 0.1.9
  平台二进制并校验 SHA-256，然后安装到当前用户目录：
  - macOS/Linux：`~/.wegent-executor/tools/wecom-cli/0.1.9/<platform>/wecom-cli`
  - Windows：`%LOCALAPPDATA%\Wegent\tools\wecom-cli\0.1.9\win32-x64\wecom-cli.exe`
- 如果本机配置不存在或不可用，脚本执行
  `wecom-cli init --noninteractive`。CLI 会打开企业微信二维码页面，用户扫码后
  自动取得机器人配置。
- 官方 CLI 将 Bot 信息和 MCP 配置加密存放在 `~/.config/wecom`；加密密钥
  优先使用系统钥匙串，并保留权限为 `0600` 的本地文件兜底。
- 后续调用统一通过 `scripts/run-wecom-cli.*`，包装器会忽略进程环境中的
  Connector 凭据覆盖，只使用官方本机加密配置。

插件包不内置用户凭据或平台专用二进制。下载地址和 SHA-256 来自
`wecom@0.1.9` 的官方 binary manifest。

## 能力

包含 9 个 Skills：通讯录、消息、会议、日程、待办、普通文档、在线表格、
智能表格和智能文档。

Skills 与 CLI 基于企业微信官方
[WecomTeam/wecom-cli](https://github.com/WecomTeam/wecom-cli)，使用 MIT
许可证；原始许可证保留在插件根目录。

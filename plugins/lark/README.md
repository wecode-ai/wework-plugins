# 飞书插件

该插件将 `lark@1.0.3` 的 26 个 Skills 完整适配为 Wegent/WeWork 可发布的
Codex 插件。Skills 与官方 CLI `1.0.68` 保持版本配套。

## 运行与授权

- 插件没有 Wegent Backend Connector，也不会把 App Secret、用户 Access Token
  或 Refresh Token 上传到服务端。
- PATH 中没有配套的 `lark-cli 1.0.68` 时，安装器下载官方平台二进制，
  校验 SHA-256 后安装到当前用户目录：
  - macOS/Linux：`~/.wegent-executor/tools/lark-cli/1.0.68/<platform>/lark-cli`
  - Windows：`%LOCALAPPDATA%\Wegent\tools\lark-cli\1.0.68\win32-x64\lark-cli.exe`
- 首次使用运行 `scripts/ensure-lark-ready.*`：
  1. `lark-cli config init --new --brand feishu --lang zh` 显示飞书二维码和链接，
     用户在浏览器完成应用创建或绑定。
  2. `lark-cli auth login --recommend` 启动 Device Flow，用户在浏览器完成用户授权。
- 官方 CLI 将非敏感配置保存为权限 `0600` 的 `~/.lark-cli/config.json`。
  macOS/Windows 使用系统钥匙串保存 App Secret 和用户 Token；Linux 使用
  `~/.local/share/lark-cli` 下权限 `0600` 的 AES-GCM 加密文件。
- 后续调用统一通过 `scripts/run-lark-cli.*`，包装器忽略外部 Connector
  注入的 Token、Brand 和 App ID 环境变量，只使用本机官方 CLI 配置。
- 需要额外业务权限时，按照 `lark-shared` Skill 的最小权限规则增量 OAuth。
  高风险写操作仍由 CLI 的确认门禁保护，禁止自动追加 `--yes`。

所有 Shell/PowerShell 包装脚本都按普通文件打包并由解释器调用，不依赖 ZIP
保留可执行位。插件包不内置平台二进制或用户凭据。

## 能力

包含 26 个 Skills，覆盖审批、考勤、多维表格、日历、通讯录、云文档、云空间、
实时事件、即时通讯、邮箱、Markdown、妙记、会议纪要、OKR、电子表格、幻灯片、
任务、视频会议、画板、知识库以及会议纪要和开工摘要工作流。

Skills 和 CLI 来自官方
[larksuite/cli](https://github.com/larksuite/cli)，使用 MIT 许可证；原始许可证
保留在插件根目录。

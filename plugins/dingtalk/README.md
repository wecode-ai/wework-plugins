# 钉钉插件

该插件将 DingTalk Workspace CLI 的完整 DWS 单体技能资料适配为
Wegent/WeWork 可发布的 Codex 插件。

## 运行方式

- 插件不配置 Wegent Backend Connector，也不把用户 Token 存到服务端。
- 首次使用时，Skill 通过 `scripts/ensure-dws-ready.*` 检查本机 CLI。
- 如果 PATH 中没有可用的 `dws`，脚本会把经过 SHA-256 校验的官方
  DWS CLI 1.0.46 安装到当前用户目录：
  - macOS/Linux：`~/.wegent-executor/tools/dws/1.0.46/<platform>/dws`
  - Windows：`%LOCALAPPDATA%\Wegent\tools\dws\1.0.46\win32-x64\dws.exe`
- 如果尚未登录，脚本执行 `dws auth login --recommend --format table`。DWS 会先
  打开本机浏览器完成 OAuth 回调，再打开推荐权限确认页，并持续轮询到用户批准、
  拒绝或授权超时；凭据和自动刷新状态由 DWS 在本机管理。这里特意使用 table
  模式，因为 JSON 模式会把 `PAT_BATCH_AUTH_PENDING` 立即交还宿主，无法等待第二段
  浏览器授权完成。
- 准备脚本不会只相信 `auth status` 的退出码（该命令在未登录时也可能返回
  0）；它会解析 `authenticated` 字段，并用 `contact user get-self` 做一次
  不修改数据的鉴权探针。只有明确的认证错误才会触发重新登录。
- 后续业务请求通过 `scripts/run-dws.*` 调用 CLI。技能自带的复合 Python
  脚本通过 `scripts/run-python.*` 执行，以便自动注入本地 DWS 路径。

发布包没有内置任何用户凭据或平台专用二进制。下载地址和 SHA-256 来自
官方 DWS CLI 1.0.46 binary manifest。

## 来源与许可

`skills/dws/` 基于 DingTalk Workspace CLI 发行的 Apache-2.0 Skill 资料，
为适配 Wegent 本地安装和认证流程修改了 `SKILL.md`。原始 `LICENSE` 和
`NOTICE` 均保留在该目录。

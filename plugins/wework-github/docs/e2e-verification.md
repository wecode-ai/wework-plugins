# Wework 官方入口真实端到端验收

## 验证对象

- 日期：2026-11-26，以下时间均为北京时间（UTC+8）。
- 环境：macOS 26.1 arm64，正在运行的 Wework 开发实例 `feature/plugin-skills-mcp · ee3c`。
- 插件：`wework-github` 0.2.0，功能代码提交 `238c3dc1081a8519264b421ceb182ddcab22e20d`。
- 测试 ZIP 的 SHA-256：`dbf62d91a5ef9fba442dab1da3890768f1216dad24a7935c4a967ae4bcbfedaa`。已安装目录的 25 个文件逐一与该功能提交核对，内容全部一致。
- 提供方：真实 GitHub 网站及 GitHub CLI；使用真实账号 `luckjun529-lang`。本轮未使用 fake gh、模拟 OAuth 响应或预置业务结果。

本记录覆盖官方入口中的实际操作、Wework 对话结果及对应工具调用/原生 RPC 记录。原始日志保留在测试设备；本文件只记录必要的时间、工具名、状态和公开查询结果。

## 成功授权 → MCP 业务调用 → 再次退出 → 健康状态

1. 在 Wework 插件详情打开 **GitHub CLI 账号** 官方连接入口。先退出既有账号，使入口恢复为“登录”，再从该入口发起新的真实授权。
2. 通过本机授权页及 GitHub 官方页面完成授权。17:44，授权页显示“GitHub 已连接”；Wework 官方入口显示“已连接 · GitHub CLI 账号”和“退出登录”。17:44:22 的官方 `runtime.local_connector_auth.health` RPC 返回 HTTP 200。
3. 从该插件详情点击“立即对话”，创建带 `wework-github@wework-personal` 插件上下文的新任务 `runtime-239817085`。请求只读查询当前身份与公开仓库，指定使用所选 MCP 工具。
4. 实际调用 `wework_github__get_me`：17:46:29 发起，17:46:31 返回 `login=luckjun529-lang`。
5. 实际调用 `wework_github__search_repositories`：17:46:37 发起，参数 `q="repo:wecode-ai/wework-plugins"`、`per_page=1`；17:46:38 返回 `full_name=wecode-ai/wework-plugins`、`html_url=https://github.com/wecode-ai/wework-plugins`。Wework 对话显示这两个工具名和查询结果。
6. 上述两个调用已在该新任务的执行记录中核对：编排调用分别执行 `tools.wework_github__get_me`、`tools.wework_github__search_repositories`，结果来自实际工具输出；没有用终端 gh 查询替代 Wework MCP 业务环节。对应工具调用 ID 为 `call_1379088d3fef4440a6d87765536d6e23`、`call_e78d10c797ea483e98ecff6f22141e72`。
7. 查询结束后返回插件详情，再次执行官方退出并确认。17:48:24，`runtime.local_connector_auth.logout` 返回 HTTP 200，耗时 862 ms；入口恢复为“登录”。
8. 17:48:25 的官方健康检查返回 HTTP 200。刷新整个 Wework 界面、重新打开插件详情，17:48:56 再次执行官方健康检查，入口仍为“登录”，没有沿用退出前的已连接缓存。
9. 对同一已安装插件执行 `health` 命令交叉核对，返回 `status=need_login`、`code=github_auth_required`，stderr 为空。命令退出码 0 表示健康检查协议执行完成，认证状态以 `status` 为准。

结果：上述完整成功链路在该 macOS Wework 实例通过；最后一次退出后的健康状态为未登录。

## 取消、拒绝及恢复

- **官方取消**：16:18，在真实 gh 设备授权已经启动、验证码已显示后，于 Wework 官方卡片取消。`runtime.local_connector_auth.cancel` 返回 HTTP 200，耗时 225 ms；Wework 显示“已取消授权”，入口保持“登录”，本机页关闭且验证码隐藏。
- **提供方拒绝**：在 GitHub 官方“Authorize GitHub CLI”页面点击 Cancel。GitHub 显示“You have denied GitHub CLI access”；Wework 显示授权失败和“重新授权”，没有显示已连接。
- **失败后恢复**：重新授权能够创建新的真实设备授权流程；随后按上面的成功链路完成实际授权、业务查询、再次退出与健康核对。
- **首次真实退出**：16:16 的官方退出 RPC 返回 HTTP 200，耗时 3158 ms，入口从“管理连接”变为“登录”。此项独立于成功链路后的再次退出。

## 复验方法与范围

在目标 Wework 中导入相同插件包，从插件详情的官方认证连接开始，依次执行：登录、实际 MCP 身份/仓库查询、官方退出、刷新后健康核对。取消与提供方拒绝分别在 Wework 和 GitHub 官方页操作。应同时检查 UI 与实际工具调用记录，不能只依据模型文字或命令退出码判定认证成功。

本记录属于 macOS 上的一次真实桌面验收。跨平台 CI 中的 fake CLI 协议集成验证仍是另一类证据；Windows 原生浏览器、多账号切换、无图形桌面、云端执行与跨设备凭据同步不由本记录覆盖。插件未声明 `accountAuth`，不能据此宣称云端托管认证已验证。

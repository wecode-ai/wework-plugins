# GitHub（Wework）

`wework-github` 提供仓库、Issue、PR 和 Actions 的本地 MCP 工具，复用执行设备上的 **GitHub CLI 官方授权**。用户无需注册 OAuth App、填写 Client ID 或保存 Secret。插件的授权与 GitHub 业务调用直接使用 GitHub，不依赖 ChatGPT connector 或 OpenAI 接口。

业务链路是 `宿主 → wework_github MCP → gh api → GitHub`。登录、验证码、共享账号说明、退出确认和结果展示都由插件自己的本机浏览器页负责，不要求宿主提供原生授权卡或特殊授权协议。

## 使用与登录

实际执行插件的设备需要 GitHub CLI (`gh`)、Node.js 18+ 和 Python 3.10+；Python 无第三方依赖。

启动器会自动查找 Python；Windows 优先使用 `py -3`，再尝试 `python` 和 `python3`。下方验证命令在 Windows 可将 `python3` 替换为 `py -3`。

1. 安装并启用 `GitHub（Wework）`。已有 `gh` 活动账号时，可直接调用业务工具。
2. 需要连接时，调用 `open_account_manager`，参数为 `{"action":"login"}`。插件在**执行设备的默认浏览器**打开自己的临时本机页面。
3. 在页面核对共享账号说明并确认继续。插件运行 GitHub CLI 官方浏览器授权，在本机页展示一次性验证码和经过校验的 GitHub 官方入口；用户到 GitHub 页面完成授权。
4. 回到本机页查看真实结果。模型通过 `get_account_manager_status` 使用返回的 `sessionId` 检查安全状态；登录确认成功后，再用 `get_me` 核对身份并继续原工作流。

`open_account_manager` 返回 `browser_opened` 只表示浏览器启动请求被接受，**不表示登录、退出或连接验证成功**。详细交互和结果在页面展示；状态工具只返回不含秘密的进度或终态。工具不会把本机页面的秘密地址、验证码或 Token 返回给模型。业务请求遇到 401 或 CLI 认证缺失时返回 `github_auth_required`，应打开本插件的账号管理页；403、404、网络和限流错误不能当作未登录。

`get_account_manager_status` 的 `wait_seconds` 为 0–20 秒，未完成时返回 `waiting_for_user`；可以有限等待，不应无限轮询。用户要求取消时调用 `close_account_manager(sessionId)`，不会把已完成的登录或退出倒回原状态。

浏览器打开在执行设备，而非必然在当前查看聊天的设备。远程或无图形界面的执行环境不保证可用；无法打开时会返回明确的浏览器启动错误，不能声称页面已经可见，也不会创建公网回调、索取 Token 或把验证码转发到聊天。

## 共享账号与退出

本插件与同一执行设备、同一配置下的终端 `gh`、原 GitHub 入口**共享活动账号**。登录、切换和退出会影响这些入口；卸载插件不会退出共享账号。

需要退出时，调用 `open_account_manager`，参数为 `{"action":"logout"}`，在本机页面核对影响并确认。退出移除执行时活动账号的本地 CLI 凭据；其他已保存账号会保留，并可能成为新的活动账号。因此页面可能显示“所选账号已退出，但设备仍有其他账号连接”。这不等于全部账号已退出，也不撤销 GitHub 服务端授权；服务端撤销应由用户在 [GitHub Settings → Applications](https://github.com/settings/applications) 操作。

确认前取消不会开始账号操作；退出提交后不能保证撤回。若凭据已移除而后续健康检查失败，页面保留已修改的事实，提示重新检查连接，不能通过反复退出删除下一账号。等待操作期间请勿在其他入口切换账号。

凭据保存和账户切换由 `gh` 管理。GitHub CLI 通常使用系统凭据库；凭据库不可用时，其官方实现可能回退到自身配置文件。插件不额外创建令牌存储、不运行 `gh auth token`、不读取认证文件或向用户索取 Token。

## 配置与生命周期

沿用 GitHub CLI 的环境优先级：`GH_TOKEN`、`GITHUB_TOKEN` 优先于已保存认证；`GH_CONFIG_DIR` 等配置目录变量决定使用的 profile。显式 Token 环境不能由浏览器登录替换，也不能通过退出删除；页面会说明限制，不展示变量内容。

MCP 的 `env_vars` 白名单转发上述认证变量、`XDG_CONFIG_HOME`、`APPDATA` 及大小写代理变量，使账号管理与业务工具使用一致环境。浏览器启动同时保留 `BROWSER` 及 Linux 图形会话所需的 `DISPLAY`、`WAYLAND_DISPLAY`、`XDG_RUNTIME_DIR`、`DBUS_SESSION_BUS_ADDRESS`、`XDG_CURRENT_DESKTOP`、`XDG_SESSION_TYPE`。包内只保存变量名。业务固定使用 `--hostname github.com`，调试输出变量不会转发。

账号管理会话由插件 MCP 进程持有，最长 600 秒，并通过页面心跳、取消、终态确认和进程退出清理临时 HTTP 服务及登录子进程。临时服务只监听 loopback，不部署公网服务。页面关闭通知是尽力发送，失联后由会话期限收敛；已经完成的账号变更不会被取消自动撤销。

插件按同一 gh profile 使用跨进程账号锁。这个锁只约束遵守同一锁协议的插件进程，**不覆盖外部直接运行的 `gh`、旧宿主内置登录或其他插件**；插件不会扫描或终止这些外部进程。具体接口与安全边界见 [认证架构](docs/auth-architecture.md)。

## 工具与宿主边界

- 27 个具名 REST 工具覆盖身份、仓库、文件、分支、提交、Issue、PR、Review 及 Actions；另有打开、查询和关闭账号管理页的 3 个工具，共 30 个。
- REST 工具只接受固定业务参数，不接受任意 URL、CLI 参数、`--jq` 表达式或 shell 命令，不提供 Copilot 委派工具。
- JSON 正文经 stdin 传给 `gh api --input -`。请求时间和输出有上限；列表按 `page/per_page` 分页，写操作不自动重试。
- 本版支持 `github.com`；GitHub Enterprise、云端账号托管和独立于 `gh` 的多账号连接不在本版范围。

插件使用标准 stdio MCP 和 Skill，不声明宿主 Connector 或 `localAuth`。插件名及 Skill 为 `wework-github`，MCP server key 为 `wework_github`；安装、更新与卸载独立于上游 GitHub 插件。

宿主若用自身全局 GitHub 规则覆盖用户选择的本插件，仍需宿主另行修正路由；本仓库不会改变这些规则，也不能用切换到其他连接器来冒充本插件调用成功。真实 GitHub 授权和各宿主端到端流程需分别验收。

## 验证与上游

从仓库根目录运行：

```text
python3 -m unittest discover -s tests -p "test_wework_github*.py" -v
python3 -m unittest discover -s tests -v
```

测试使用隔离临时目录和 fake CLI，覆盖工具路由、请求边界、页面安全、取消、超时及自有进程清理，不修改开发者的真实 `gh` 账号。单测和 Windows 结构检查不能代替真实平台浏览器与 GitHub 授权验收。

工作流参考 [openai/plugins 的 github 插件](https://github.com/openai/plugins/tree/0722921d5542fc593105c27bd52630babd8b8c2a/plugins/github)，基线 commit `0722921d5542fc593105c27bd52630babd8b8c2a`、版本 `0.1.12`。本插件移除其 ChatGPT 应用绑定和公共 OAuth 配置，使用自有图标与本仓 Apache-2.0 许可。

官方说明：[GitHub CLI 登录](https://cli.github.com/manual/gh_auth_login)、[API 调用](https://cli.github.com/manual/gh_api)、[环境优先级](https://cli.github.com/manual/gh_help_environment)、[本机退出](https://cli.github.com/manual/gh_auth_logout)。

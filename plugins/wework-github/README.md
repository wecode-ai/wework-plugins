# GitHub（Wework）

`wework-github` 通过 Wework 官方本机认证连接管理 GitHub CLI 账号，通过标准 stdio MCP 提供仓库、代码、Issue、PR、Review 和 Actions 工具。认证使用 GitHub CLI 官方 OAuth，业务调用经 `gh api` 直连 GitHub，无需申请或配置 Client ID，不经过 ChatGPT/OpenAI Connector。

## 连接与使用

执行设备需要 GitHub CLI（`gh`）、Node.js 18+、Python 3.10+ 和可用的默认浏览器。Python 无第三方依赖；Windows 优先探测 `py -3`，实际运行解析得到的 Python 可执行文件。

1. 导入并启用 **GitHub（Wework）**。插件声明标准 `connectors[].localAuth`，连接器为 `wework-github-account`，按需授权（`on_use`）。
2. 在插件详情的 **GitHub CLI 账号** 连接入口点击连接。业务工具发现认证缺失时，也会返回标准 `connector_auth_required` 和准确的插件/连接器身份，由 Wework 显示授权卡。
3. Wework 执行 `connect`。已有可用 gh 账号时直接复用；需要登录时打开执行设备上的临时本机授权页。核对共享账号说明、确认继续，点击“复制验证码”，前往 GitHub 官方页面粘贴并完成授权。浏览器不允许自动复制时，页面会选中验证码并提示复制快捷键，轮询不会打断选区。
4. 插件等待真实授权终态，只在验证与清理完成后输出一份 JSON。Wework 再运行健康检查并更新连接状态；授权成功后，从原阻塞步骤继续，并用 `get_me` 核对当前身份。

仅打开浏览器不代表登录成功。取消使用 Wework 的官方取消入口或本机页取消按钮；取消会结束该次授权并清理插件自有子进程，不撤销已保存的登录。失败与网络异常不能显示为已连接，也不能诱导用户反复登录。

403、404、限流和网络异常保留实际业务错误；只有 CLI 明确认证缺失或 HTTP 401 才请求宿主授权。插件不提供模型可调用的登录、退出、验证码或凭据导出工具，MCP 仅暴露 27 个具名业务工具。

## 官方认证协议

- 连接器：`wework-github-account`；插件 key：`wework-github`。
- `localAuth.kind`：`browser_oauth`。
- 健康检查：`scripts/local-auth.sh health`。
- 连接：`scripts/local-auth.sh connect`；最长 600 秒，预留初次健康检查与清理预算。
- 退出：`scripts/local-auth.sh disconnect`；由 Wework 的退出操作发起，不再打开第二个退出确认页。
- Windows 由宿主标准规则选择同名 `local-auth.ps1`；JSON 通过 UTF-8 stdout 返回，空 stdin 不解释为取消。
- `logoutOnUninstall: false`：卸载不退出共享账号。

本机浏览器页用于 GitHub 设备码展示与共享账号确认；连接入口、轮询、健康检查、取消和退出接入 Wework 标准认证连接。无需修改 Wework 主仓库或注册 GitHub 专用宿主路由，也不使用私有流式授权事件协议。

本版仅支持执行设备本机连接，未声明跨设备凭据同步的 `accountAuth`，也不提供云端凭据托管。不要把本机 `localAuth` 接入等同于跨设备账号同步。

## 共享账号与退出

插件与同一设备、同一 gh 配置下的终端及其他 GitHub 入口共享活动账号。登录、切换或退出会影响这些入口；在操作期间避免在其他入口切换账号。

在 Wework 连接入口退出时，只移除执行时活动账号的本机凭据，保留其他已保存账号。其他账号可能接替成为活动账号，因此退出后的健康检查仍可能显示已连接；应刷新并核对身份，不要反复退出而删除下一账号。退出不撤销 GitHub 服务端 OAuth 授权，卸载也不会自动退出。

凭据存储由 gh 管理。插件不读取认证文件、导出 `gh auth token` 或额外保存 Token。`GH_TOKEN`、`GITHUB_TOKEN` 优先于已保存凭据；显式环境 Token 可用于业务与健康检查，但不能通过本机退出删除，也不会由浏览器登录覆盖。

认证命令和 MCP 沿用相同的 gh 环境与 profile；`GH_CONFIG_DIR`、`XDG_CONFIG_HOME`、`APPDATA` 决定配置目录。MCP 白名单只声明这些变量名及浏览器、图形会话和代理变量名，不打包变量值。

## 验证与兼容边界

从仓库根目录运行：

```text
python3 -m unittest discover -s tests -p "test_wework_github*.py" -v
python3 -m unittest discover -s tests -v
```

协议集成测试使用 fake CLI、临时 profile 和真实 Node/Python/HTTP，覆盖 manifest 认证身份、连接成功/失败/取消、重复连接、业务工具调用、网络失败不触发登录、退出后健康状态、标准授权错误及自有进程树清理。它们不等于真实 GitHub OAuth 或 Wework GUI 端到端验收。

0.2.0 将 0.1.x 的三个 MCP 账号管理工具迁到 Wework 官方认证连接，更新安装后需重新加载插件运行时。已有 gh 凭据保留。单独手工排障仍可运行 `scripts/local-auth.sh login` 或 `logout`，这两个命令使用本机页，不是模型工具，也不是 Wework 声明的认证入口。

2026-11-26 已在 macOS Wework 官方入口完成真实授权、MCP 身份与仓库查询、再次退出及刷新后健康状态核对；取消和提供方拒绝也已验证，见 [真实端到端验收记录](docs/e2e-verification.md)。Windows 原生浏览器及其他执行环境仍需复验。认证细节见 [认证架构](docs/auth-architecture.md)。本次 PR 只包含 GitHub，Figma 不在交付范围。

工作流参考 [openai/plugins 的 github 插件](https://github.com/openai/plugins/tree/0722921d5542fc593105c27bd52630babd8b8c2a/plugins/github)，基线 commit `0722921d5542fc593105c27bd52630babd8b8c2a`、版本 `0.1.12`。本插件移除其 ChatGPT 应用绑定和公共 OAuth 配置，使用自有图标与本仓 Apache-2.0 许可。

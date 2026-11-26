# GitHub 账号管理架构

`wework-github` 接入 Wework 官方本机认证连接：在插件清单声明 `connectors[].localAuth`，由 Wework 负责连接入口、授权会话、轮询、健康检查、取消和退出。GitHub 授权仍由 gh 官方 OAuth 完成，本机页只展示设备码、共享账号说明和授权结果。业务通过标准 stdio MCP 调用 `gh api`，不经过 ChatGPT/OpenAI Connector。

## 官方连接器与调用链

连接器 slug 为 `wework-github-account`，插件 key 为 `wework-github`，采用 `on_use` 授权策略。清单声明 `browser_oauth` 及包内相对命令：`health`、`connect`、`disconnect`。Windows 由宿主标准解析规则选择同名 PowerShell 入口。

`connect` 先检查当前 gh profile。已连接则直接返回 `ok`；网络、权限或 CLI 异常直接返回安全错误，不启动额外登录。仅 `need_login` 时启动本机授权页。内部页面总时限为 540 秒，给宿主 600 秒命令预算预留初次健康检查、浏览器启动、进程清理和结果输出时间。stdout 只输出一份终态 JSON，Wework 随后再执行 `health`，不能将打开页面视为认证成功。

业务遇到明确的认证缺失或 HTTP 401，MCP 返回 `connector_auth_required`，同时包含 `pluginKey=wework-github` 与 `connectorSlug=wework-github-account`。模型保留这组错误身份并结束本轮，由宿主解析已安装插件清单并展示官方授权卡。完成授权后只恢复原阻塞步骤；403、404、限流与网络异常不得改成登录错误。

MCP 只暴露 27 个业务工具，不再暴露 `open_account_manager`、`get_account_manager_status` 或 `close_account_manager`。登录、取消和退出通过官方连接器完成，不要求模型操作账号管理工具、读取 Token 或转发验证码。

`disconnect` 由 Wework 的显式退出操作触发，直接调用有界的精确账号注销逻辑，不再要求第二次浏览器确认。结果保留 `changed/accountRevoked/connected` 等本机事实，并通过 `hint` 说明其他账号仍可能连接。Wework 可重新执行 `health` 核对当前连接。

本机连接采用公开的 `localAuth` 协议，不依赖 GitHub 专用宿主代码或私有事件流。本版未声明 `accountAuth`，不上传或同步 gh 凭据，不代表云端托管认证可用。

## 授权与会话所有权

登录调用 GitHub CLI 官方 OAuth 应用的浏览器授权；验证码只来自受限解析器，且必须与固定的 `https://github.com/login/device` 地址配对。令牌获取和保存由 gh 负责。插件不运行 `gh auth token`，不读取认证文件，不复制客户端参数，也不新增令牌库。

Wework 的 browser_oauth 会话持有认证命令。命令同步等待本机页与授权终态；宿主取消或退出时，启动器/父进程监视收敛页面、账号操作及插件自有子进程。不依赖 MCP 进程持有认证页，也不把认证命令的空 stdin 当作取消。

本机页继续提供显式确认、取消、心跳与终态确认。失联容忍期为 120 秒，总期限始终有效。终态确认或有界期限后关闭 HTTP 服务；页面关闭通知仅为尽力发送。确认前取消不得修改账号；已保存的登录或已完成的退出不因取消自动撤销。

成功必须来自真实 gh 执行与健康检查，页面不能通过任意请求伪造成功。取消、失败或意外断开后应重新检查当前身份，不能据页面状态推断账号未发生变化。

## HTTP 与展示边界

临时服务仅监听 `127.0.0.1` 的系统分配端口；每次会话有不可猜测的随机路径和独立 CSRF Token。它不是公网 OAuth 回调，也不接收 GitHub access token。[本机 loopback 的安全原则](https://www.rfc-editor.org/rfc/rfc8252#section-7.3)

- 校验精确 Host 和本机 Origin，拒绝伪造主机、外部来源及不支持的请求路径。
- 确认、取消和终态确认使用受保护的 POST，限制正文与头部大小，拒绝不支持的传输格式；不开放跨域读写。
- 页面仅使用包内资源，设置 CSP、禁止缓存和 Referrer 泄漏，不能被其他站点嵌入。
- Provider 字段只作为受限纯文本展示；禁止插入可执行 HTML。GitHub 外链只来自固定校验，不能由模型传入任意地址。
- HTTP 日志、错误、MCP 结果和遥测不记录本机秘密路径、验证码或原始 CLI 输出。

Origin 校验与 CSRF Token 共同约束浏览器操作；随机端口和路径不能代替这些检查。[OWASP CSRF 指南](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html)

默认浏览器实际位于执行设备。没有交互桌面、远程执行器或浏览器启动失败时，应返回明确错误并清理会话，不能生成公网地址、把验证码转交模型，或声称用户当前设备已经打开网页。系统接受浏览器打开请求也不等于用户已经看到或完成操作。

MCP 白名单显式保留用户的 `BROWSER` 配置及 Linux 图形会话变量（`DISPLAY`、`WAYLAND_DISPLAY`、`XDG_RUNTIME_DIR`、`DBUS_SESSION_BUS_ADDRESS`、`XDG_CURRENT_DESKTOP`、`XDG_SESSION_TYPE`）。浏览器启动通过有时限的隔离子进程完成，标准流接到空设备，并移除 GitHub Token 环境变量；浏览器程序不会加入 gh 授权进程组或 Job Object。

## 共享账号与退出

账号管理和业务 MCP 继承同一执行设备的 gh 配置。`GH_TOKEN`、`GITHUB_TOKEN` 优先于已保存凭据；`GH_CONFIG_DIR`、`XDG_CONFIG_HOME` 及平台目录变量定位 profile。MCP 白名单只保存这些变量的名称与代理变量名称，不保存或展示实际值。显式 Token 环境阻止浏览器登录和注销，不由插件删除变量。[GitHub CLI 环境说明](https://cli.github.com/manual/gh_help_environment)

登录和退出使用同一 profile 的跨进程锁，覆盖账号操作、验证及自有子进程清理。锁只约束遵守该协议的插件进程，不能约束外部直接运行的 gh、旧宿主内置登录或其他插件；插件不能扫描或结束这些外部进程。

官方退出操作只处理执行时的活动账号；等待期间避免在外部入口切换账号。手工排障的退出页仍提供额外确认，但官方 `disconnect` 不启动该页面。实际删除使用明确的 `--hostname github.com --user <active-login>`，只移除该账号的本地 CLI 凭据，保留其他已保存账号，不撤销服务端 OAuth grant。其他账号可能接替活动状态，因此注销一个账号后 `connected` 仍可为 true。[GitHub CLI 退出说明](https://cli.github.com/manual/gh_auth_logout)

内部退出结果的 `changed=true`、`accountRevoked=true` 表示 gh 已报告移除所选账号的本地凭据，后者只是历史字段名，不代表撤销 GitHub 服务端授权或全局断开。后续健康检查失败仍保留修改标记，并提示检查连接；不能诱导重试注销而删除下一账号。变更前失败带 false；健康未知时不填 `connected=false`。这些安全结果由认证命令返回；标准宿主可能只保留 status/hint，因此 hint 也明确说明剩余账号和已生效的变更，当前连接以重新执行 health 为准。

登录创建的 gh 进程在 POSIX 使用独立进程组，在 Windows 使用 Job Object；清理涵盖主进程先退出、后代仍占用管道的情况。不使用全局 gh 进程查杀。卸载插件不会注销共享账号。

## 验证范围

协议集成测试使用 fake gh、隔离 profile 与真实 Node/Python/HTTP，不读取开发者凭据。覆盖声明的连接器身份、health/connect/disconnect、成功/取消/失败终态、已登录快速返回、网络失败不启动登录、MCP 实际业务路径与授权错误、退出后状态、自有子进程树清理和 Windows 启动器。

原有 HTTP 安全与账号状态测试继续覆盖 Host/Origin/CSRF、CSP、恶意字段、环境 Token、活动账号精确删除、其他账号保留、注销后的健康失败等边界。删除的是由 MCP 账号管理工具承担的旧入口，业务认证现由官方连接器触发。

上述协议测试与真实桌面验收分别记录。2026-11-26 已在 macOS Wework 官方入口完成真实授权成功、两个 MCP 业务查询、再次退出及刷新后健康状态核对，并验证官方取消与 GitHub 提供方拒绝。完整步骤、测试包哈希、调用名与时间见 [真实端到端验收记录](e2e-verification.md)。

该记录不覆盖 Windows 原生浏览器或云端/跨设备认证。若旧会话缓存了 0.1.x MCP，需重新加载插件运行时；宿主全局路由覆盖用户选择时应报告限制，不能换另一入口冒充验收通过。

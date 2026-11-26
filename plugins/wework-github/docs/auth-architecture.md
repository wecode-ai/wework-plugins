# GitHub 账号管理架构

`wework-github` 是标准 stdio MCP 插件。业务工具通过 `gh api` 直连 GitHub，账号管理通过插件自己的本机浏览器页完成。不声明宿主 Connector、`localAuth`、原生授权卡或私有授权协议环境变量，不要求配置 Client ID，也不部署中转 OAuth 服务。

## 调用与结果语义

MCP 工具 `open_account_manager` 接受 `action=login` 或 `action=logout`。它创建短期 HTTP 会话，打开执行设备的默认浏览器，然后立即返回 `status=browser_opened`。返回值可以带非秘密 sessionId、标题和说明，不能包含 loopback 秘密地址、验证码、Token 或原生 CLI 诊断。

`browser_opened` 只表示浏览器启动请求被接受，不是账号操作的终态。用户在插件页面核对共享账号说明并确认后，才启动登录或退出；进展、验证码和详细结果在该页面显示。业务工具不会等待这项人工操作。

`get_account_manager_status(sessionId, wait_seconds=0..20)` 返回 `waiting_for_user` 或安全终态，可包含 status/hint/code、changed/accountRevoked/connected，不含验证码或本机秘密地址。模型可以有限等待；登录终态为 `ok` 后，再用 `get_me` 核对身份并恢复原工作流。`close_account_manager(sessionId)` 只在用户要求取消时调用，不能用关闭页面来查询结果，也不能撤销已完成的变更。

业务请求仅在 HTTP 401 或 GitHub CLI 明确认证缺失时返回 `github_auth_required`，提示打开管理页，不携带宿主 Connector 身份或请求原生卡片。403、404、限流、网络及输出上限保留各自错误含义，不能借机切换认证入口。

登录调用 GitHub CLI 官方 OAuth 应用的浏览器授权；验证码必须来自受限解析器，并只允许配对的 `https://github.com/login/device` 地址。令牌获取和保存由 gh 负责。插件不导出 `gh auth token`、不读取认证配置文件、不复制 OAuth 客户端参数，也不新增令牌库。[GitHub CLI 登录说明](https://cli.github.com/manual/gh_auth_login)

## 会话所有权

HTTP 会话依附创建它的 MCP 进程，最长 600 秒，不依赖宿主的登录或退出 RPC 超时。MCP 进程退出、会话关闭、取消或总时限到期时，由插件收敛服务和自有进程。不得启动失去监督的永久后台服务。

内部 `browser_auth.start(...)` 返回会话对象：`public_result()` 只生成可返回模型的启动结果，`outcome()` 在操作完成后立即提供安全终态，`wait()` 等待整个会话关闭，`close()` 取消并清理。状态查询不需要等待页面确认；HTTP 服务仍保留到页面确认或有界期限。页面读取状态续约 lease，失联容忍期为 120 秒，以容忍用户切到 GitHub 标签页后浏览器限制后台计时器的情况；绝对总时限仍然有效。[Chrome 后台计时器说明](https://developer.chrome.com/blog/timer-throttling-in-chrome-88/)

账号 CLI 不要求特殊协议参数或环境握手，也不把 null stdin 当作用户取消。MCP 的 stdin EOF 则表示宿主连接结束，MCP shutdown 或终止信号负责关闭其持有的账号会话。

页面关闭通知是尽力发送，不能保证立即送达；lease、总时限和 MCP 生命周期是后备清理机制。确认前取消不得开始账号变更。登录已保存的结果不会因取消自动撤销；退出一旦提交，也不能把关闭页面解释为恢复已删除的凭据。

页面先渲染终态，再确认已收到结果；未确认时，服务最多保留到完成后 120 秒或会话总时限，以较早者为准。成功依据真实的 gh 执行和连接验证，页面不能通过任意请求把服务器状态改成成功。页面意外失联后，应重新检查 `get_me`，不能据网络错误推断账号未发生变化。

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

退出页确认操作执行时的活动账号，不展示另一个账号的快照作为承诺；等待期间避免在外部入口切换账号。实际删除使用明确的 `--hostname github.com --user <active-login>`，只移除该账号的本地 CLI 凭据，保留其他已保存账号，不撤销服务端 OAuth grant。其他账号可能接替活动状态，因此注销一个账号后 `connected` 仍可为 true。[GitHub CLI 退出说明](https://cli.github.com/manual/gh_auth_logout)

内部退出结果的 `changed=true`、`accountRevoked=true` 表示 gh 已报告移除所选账号的本地凭据，后者只是历史字段名，不代表撤销 GitHub 服务端授权或全局断开。后续健康检查失败仍保留修改标记，并提示检查连接；不能诱导重试注销而删除下一账号。变更前失败带 false；健康未知时不填 `connected=false`。这些安全结果供页面与状态工具消费，不依赖宿主授权结果归一化。

登录创建的 gh 进程在 POSIX 使用独立进程组，在 Windows 使用 Job Object；清理涵盖主进程先退出、后代仍占用管道的情况。不使用全局 gh 进程查杀。卸载插件不会注销共享账号。

## 验证范围

使用 fake CLI、临时 profile 和本机 HTTP fixture 验证以下行为，不操作开发者真实账号：

| 范围 | 验收点 |
| --- | --- |
| MCP | 打开管理页立即返回 browser_opened，查询返回等待或安全终态；两者均不含秘密地址或验证码，详细交互在本机页完成。 |
| HTTP | loopback、Host/Origin、CSRF、路径、方法、正文上限、缓存与 CSP；恶意输入不能造成账号操作或脚本执行。 |
| 生命周期 | 确认前取消、页面失联、绝对时限、终态送达、MCP 退出及自有子孙进程清理。 |
| 账号 | 登录/退出锁、环境 Token 限制、只移除活动账号、其他账号接替、变更后健康失败仍保留事实。 |
| 平台 | Windows/macOS/Linux 启动器和默认浏览器分别验证；结构 mock 不等于实际 Windows WinAPI 或浏览器验收。 |

真实 GitHub 浏览器授权、无图形界面失败体验以及各宿主对用户所选 MCP 的路由都需另行验收。宿主全局规则若抢占用户选中的插件，应明确报告路由限制；本仓库不修改宿主，也不能用另一入口成功代替本插件验收。

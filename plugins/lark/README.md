# 飞书 / Lark

通过原生 Lark CLI 使用文档、消息、日历、审批等能力，支持 Wegent 本地登录与云端托管认证。

## 连接与身份

- **飞书用户（lark）**：在来源设备完成原生浏览器授权。Wegent 接管 OAuth 刷新权后，本地和云端调用都使用托管连接，不需要在云端再次扫码。交接只删除所选账户的本地用户令牌，不调用会撤销服务端授权的 CLI logout。
- **飞书应用（lark-app）**：单独同步 App ID 和 App Secret，用于 `--as bot`。适配器在私有进程中换取应用令牌，原 CLI 业务命令仅接收令牌。用户连接不会自动授予应用身份权限。

云端默认使用用户身份；应用调用必须显式指定 `--as bot` 并授权应用连接。本地保留配置的默认身份。品牌由本地配置决定，支持 Feishu 和 Lark。

始终通过 `scripts/run-lark-cli.sh` 或 Windows 的 `scripts/run-lark-cli.ps1` 调用业务命令。托管模式禁止 CLI 自行登录、修改配置或切换 profile。需要新增 scope 时，在来源设备通过 wrapper 发起 `auth login --scope ...` 或 `--domain ...`，再在 Wegent 原生连接界面重新连接。云端遇到权限不足时返回来源设备处理。写操作保留原 CLI 的确认规则。

事件总线订阅依赖本地常驻进程及应用密钥，应在来源设备使用 `--as bot` 运行。托管调用仅开放 `event list` 和 `event schema`；不在云端启动后台事件进程。托管请求正文请使用参数或 `@文件`，不通过 stdin 传入。

## 认证边界

用户刷新令牌与应用密钥仅进入私有适配器，不通过命令行、环境变量或业务输出传递。托管业务命令使用内存凭据提供器，禁用本地 keychain、用户插件与配置覆盖；携带凭据的 HTTP 请求只允许对应品牌的官方 HTTPS API。

交接通过来源锁、凭据指纹和持久化回执支持恢复，凭据发生变化时停止交接。独立运行的裸 `lark-cli` 不遵守 Wegent 来源锁：交接期间请关闭它；交接后如需独立使用，应采用独立配置与授权，避免共用同一 OAuth grant。

## 构建与验证

安装包自带基于官方 CLI **v1.0.68** 构建的原生适配器，支持 macOS amd64/arm64、Linux amd64/arm64、Windows amd64。上游源码与构建产物均校验 SHA-256，二进制以 XZ 存储，运行时校验后解压到宿主执行目录。运行需要 Python 3.9+；源码目录必须先执行插件构建。

```sh
uv run --no-project python .wework-build/lark-auth/package.py --plugin . --output build/lark-package.zip
```

`.wework-build.json` 声明完整构建入口，插件目录包含所需 SDK、构建与验证脚本。CI 在三个操作系统上构建五个平台产物并验证打包后的认证路由、私有通道及错误退出码；测试使用合成凭据，不接触真实账户。真实账号授权与云端运行仍需在配套的 Wegent Backend、Executor 已部署后验收。

上游许可见安装包 `scripts/native/<platform>/UPSTREAM-LICENSE`，适配器 SDK 许可见 `.wework-build/plugin-auth-go/`。

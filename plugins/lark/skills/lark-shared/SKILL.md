---
name: lark-shared
description: "用于 lark-cli 的配置与授权任务：auth login/status/logout、用户与机器人身份、业务域权限（--domain，含 all/docs/drive）、缺失 scope、撤销授权，或处理 _notice JSON。"
---

## Wegent 本地与云端运行

- 当前 `SKILL.md` 所在目录的 `../..` 是插件根目录。首次调用前，macOS/Linux 运行 `sh "<插件根目录>/scripts/ensure-lark-ready.sh"`；Windows 运行 `powershell -NoProfile -ExecutionPolicy Bypass -File "<插件根目录>\scripts\ensure-lark-ready.ps1"`。
- 下文的 `lark-cli ...` 是逻辑命令。实际执行时，macOS/Linux 使用 `sh "<插件根目录>/scripts/run-lark-cli.sh" ...`；Windows 使用 `powershell -NoProfile -ExecutionPolicy Bypass -File "<插件根目录>\scripts\run-lark-cli.ps1" ...`。
- 应用配置和首次用户 OAuth 在本机原连接入口完成；Wegent 在后台托管用户 OAuth（`lark`）及应用凭据（`lark-app`）。云端准备脚本只检查托管认证，不安装 CLI 或发起登录。实际命令必须经过上述包装器，不能直接调用裸 CLI、读取认证文件或索取 Token。用户调用默认 `--as user`，应用调用显式 `--as bot`，两种云端授权独立管理。托管请求正文通过参数或相对路径文件传入，不使用 stdin。托管状态失效或需要增量授权时，在本机原连接入口重新连接；不要在云端执行 auth/config/profile 命令。

# lark-cli 共享规则

本技能指导你如何通过lark-cli操作飞书资源, 以及有哪些注意事项。

## 跨平台命令硬规则

- 参考文档中的反斜杠换行是 macOS/Linux 的展示写法；Windows PowerShell 执行时必须合并成单行，不能把 `\` 当续行符。
- 禁止依赖 heredoc、`cat`、`grep`、`head`、`tail`、`sed`、`awk` 或 `jq`。使用工作区文件工具生成输入文件，使用 CLI 的 JSON 输出并在内存中解析。
- `--file`、`--output`、`--output-dir`、`@file` 等路径仍遵守 CLI 的安全限制：只传当前工作目录下的相对路径。不要使用 `/tmp`、盘符绝对路径或 PowerShell 临时目录作为参数。
- JSON、Markdown、XML 等多行输入先由工作区文件工具写入当前目录的相对文件，再传 `@./file` 或对应 `--*-file ./file` 参数；不要把 Bash 重定向或管道原样交给 Windows。
- 路径和含空格参数必须保持为独立参数并完整引用，不得拼接成一段命令字符串后执行。
- 参考文档中的 `python3` 在 macOS/Linux 原样使用；Windows PowerShell 必须替换为 `py -3`，或使用 workspace dependency loader 返回的绝对 `python.exe`。

## 配置与认证

在 Wegent 原连接入口完成应用配置和用户浏览器 OAuth。浏览器链接由本机原生脚本打开，认证完成后自动交接；不要从模型进程读取、显示或上传密钥与 Token。

- `auth status` 在托管模式下返回 `status` 和 `accountId`，不沿用原 CLI 的 `identities` JSON 结构。
- 用户身份为 `lark` OAuth 连接，应用身份为 `lark-app` 密码类连接。使用 `--as bot` 时必须获得该应用连接的云端授权，不能借用户 OAuth 提权。
- 本机托管状态下执行 `auth login --scope <scope>` 或 `auth login --domain <domain>` 只记录非敏感的授权范围，并返回需要重新连接。随后在原连接入口重新连接，完成指定范围的浏览器授权。云端不执行此操作，需回到源设备处理。
- 应用 scope 在开发者后台开通；不要为 bot 执行用户 OAuth。
- 用户退出在原连接入口执行，由宿主断开云端，再清理本机用户 Token。撤销由宿主私有回调处理，不使用裸 CLI 的 logout。
- `--profile`、`--workspace`、认证/代理覆盖、调试插件等不允许进入托管业务执行；本次同步原默认配置所选的应用与用户，切换账号需在本机完成并重新连接。
- 原 CLI 的写操作确认规则保持有效。不得自动追加 `--yes`；只有用户明确授权相应写操作时，按 CLI 的要求传入确认。

### 身份类型

两种身份类型，通过 `--as` 切换：

| 身份 | 标识 | 获取方式 | 适用场景 |
|------|------|---------|---------|
| user 用户身份 | `--as user` | `lark-cli auth login` 等 | 访问用户自己的资源（日历、云空间/云盘/云存储等） |
| bot 应用身份 | `--as bot` | 自动，只需 appId + appSecret | 应用级操作,访问bot自己的资源 |

### 身份选择原则

输出的 `[identity: bot/user]` 代表当前身份。bot 与 user 表现差异很大，需确认身份符合目标需求：

- **Bot 看不到用户资源**：无法访问用户的日历、云空间（云盘/云存储）文档、邮箱等个人资源。例如 `--as bot` 查日程返回 bot 自己的（空）日历
- **Bot 无法代表用户操作**：发消息以应用名义发送，创建文档归属 bot
- **Bot 权限**：只需在飞书开发者后台开通 scope，无需 `auth login`
- **User 权限**：后台开通 scope + 用户通过 `auth login` 授权，两层都要满足


### 权限不足处理

遇到权限相关错误时，**根据当前身份类型采取不同解决方案**。

错误响应中包含关键信息：
- `permission_violations`：列出缺失的 scope (N选1)
- `console_url`：飞书开发者后台的权限配置链接
- `hint`：建议的修复命令

#### Bot 身份（`--as bot`）

将错误中的 `console_url` 原样提供给用户，引导去后台开通 scope。**禁止**对 bot 执行 `auth login`。

#### User 身份（`--as user`）

```bash
lark-cli auth login --domain <domain>           # 按业务域授权
lark-cli auth login --scope "<missing_scope>"   # 按具体 scope 授权（推荐,符合最小权限原则）
```

**规则**：auth login 必须指定范围（`--domain` 或 `--scope`）。多次 login 的 scope 会累积（增量授权）。

#### 托管状态下增量授权

本机按上面的 `auth login --scope/--domain` 记录范围后，通过 Wegent 原连接入口重新连接。云端只报告缺失 scope 与开发者后台链接，不发起第二份授权、不请求 device code 或 Token。

## 更新检查

lark-cli 命令执行后，如果检测到新版本，JSON 输出中会包含 `_notice.update` 字段（含 `message`、`command` 等）。

除非用户正在询问更新、版本或 notice，否则不要把 `_notice` 原样复制为当前任务的主要答案，也不要为了 notice 中断当前任务去反复查 help。

需要稳定 JSON 给脚本或机器读取时，继续通过插件包装器执行；包装器会在
macOS、Linux 和 Windows 上统一设置通知环境变量：

```bash
<lark-cli command>
```

当你在输出中看到 `_notice.update` 时，先完成用户当前请求；如仍相关，再简短告知可运行：

```bash
lark-cli update
```

**重要**：始终使用 `lark-cli update` 更新，它会同时更新 CLI 和 AI Skills。

## JSON 输出契约

`--format json`（默认）下，成功与错误的信封结构不同：

成功信封写入 **stdout**（退出码 0）：

```json
{ "ok": true, "identity": "user", "data": { "guid": "..." }, "meta": { "count": 1 } }
```

错误信封写入 **stderr**（退出码非 0）：

```json
{ "ok": false, "identity": "user", "error": { "type": "api", "subtype": "...", "code": 99991679, "message": "...", "hint": "..." } }
```

**判断成功必须用 `ok == true`（或进程退出码 0），不要用 `code == 0`**：成功信封没有顶层 `code` / `msg` 字段，`code` 只出现在错误信封的 `error` 内，含义是上游 OpenAPI 的 numeric code。按 OpenAPI 老格式 `{"code": 0, "msg": "ok"}` 判断会把所有成功调用误判为失败；封装写入类命令（如 `task +create`）时尤其危险，误判会绕过幂等逻辑导致重复创建。

## 安全规则

- **禁止输出密钥**（appSecret、accessToken）到终端明文。
- **写入/删除操作前必须确认用户意图**。
- 用 `--dry-run` 预览危险请求。
- **文件路径只接受相对路径**：`--file`、`--output`、`--output-dir`、`@file` 等路径参数只接受 cwd 下的相对路径，传绝对路径会报 `unsafe file path`。数据输入（大 JSON）先写入工作目录的相对文件，再用 `@file` 传入。托管调用不支持 stdin 正文。

## 高风险操作的审批协议（exit 10）

lark-cli 对高风险写操作（`risk: "high-risk-write"`）有强制确认门禁。当你不带 `--yes` 调用这类命令时，CLI 会退出码 `10`、并在 stderr 返回如下结构化 envelope：

```json
{
  "ok": false,
  "error": {
    "type": "confirmation_required",
    "message": "drive +delete requires confirmation",
    "hint": "add --yes to confirm",
    "risk": {
      "level": "high-risk-write",
      "action": "drive +delete"
    }
  }
}
```

**遇到这种情况，不要当普通错误放弃。** 按以下流程处理：

1. **识别**：看到子进程 exit code = `10` 且 stderr JSON 里 `error.type == "confirmation_required"`
2. **向用户确认**：把 `error.risk.action` 和关键参数展示给用户，明确告知"这是高风险操作"，等待用户显式同意
3. **用户同意** → 在你**原始 argv 的末尾追加 `--yes`** 后重试
4. **用户拒绝** → 终止流程，不要擅自改写参数或跳过门禁

**绝对不允许**：
- 看到 exit 10 就默认加 `--yes` 静默重试（这等于禁用门禁）
- 把 `confirmation_required` 当网络错误/权限错误处理
- 在用户没明确同意的前提下追加 `--yes` 重试
- 用 `sh -c` 等 shell 方式拼接命令重试——用 `exec.Command(argv...)` 参数数组形式，避免 shell 解析把用户参数当作语法

提前预判：想先让用户 review 危险操作的具体请求，调用时加 `--dry-run`——它不触发门禁，会打印完整请求详情（URL / body / params），你可以把这个预览给用户看过再去真正执行。

### 如何识别一条命令是高风险

- shortcut：`lark-cli <service> +<cmd> --help` 顶部会显示 `Risk: high-risk-write`
- service 命令：`lark-cli schema <service>.<resource>.<method> --format json` 的返回值里 `"risk": "high-risk-write"`

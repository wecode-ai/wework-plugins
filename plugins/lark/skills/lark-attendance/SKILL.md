---
name: lark-attendance
description: "飞书考勤打卡：查询自己的考勤打卡记录"
---

## Wegent 本地与云端运行

- 当前 `SKILL.md` 所在目录的 `../..` 是插件根目录。首次调用前，macOS/Linux 运行 `sh "<插件根目录>/scripts/ensure-lark-ready.sh"`；Windows 运行 `powershell -NoProfile -ExecutionPolicy Bypass -File "<插件根目录>\scripts\ensure-lark-ready.ps1"`。
- 下文的 `lark-cli ...` 是逻辑命令。实际执行时，macOS/Linux 使用 `sh "<插件根目录>/scripts/run-lark-cli.sh" ...`；Windows 使用 `powershell -NoProfile -ExecutionPolicy Bypass -File "<插件根目录>\scripts\run-lark-cli.ps1" ...`。
- 应用配置和首次用户 OAuth 在本机原连接入口完成；Wegent 在后台托管用户 OAuth（`lark`）及应用凭据（`lark-app`）。云端准备脚本只检查托管认证，不安装 CLI 或发起登录。实际命令必须经过上述包装器，不能直接调用裸 CLI、读取认证文件或索取 Token。用户调用默认 `--as user`，应用调用显式 `--as bot`，两种云端授权独立管理。托管请求正文通过参数或相对路径文件传入，不使用 stdin。托管状态失效或需要增量授权时，在本机原连接入口重新连接；不要在云端执行 auth/config/profile 命令。

# attendance (v1)

**CRITICAL — 开始前 MUST 先用 Read 工具读取 [`../lark-shared/SKILL.md`](../lark-shared/SKILL.md)，其中包含认证、权限处理**

## 默认参数自动填充规则

调用任何 API 时，以下参数 **必须自动填充，禁止向用户询问**：

| 参数 | 固定值 | 说明                                 |
|------|--------|------------------------------------|
| `employee_type` | `"employee_no"` | `employee_type`始终等于`"employee_no"` |
| `user_ids` | `[]`（空数组） | `user_ids`始终等于`[]`                 |

### 填充示例

当构建 `--params` 参数时，自动注入上述字段：
- `employee_type` 保持 `"employee_no"` 不变

当构建 `--data` 参数时，自动注入上述字段：
```json
{
  "user_ids": [],
  ...用户提供的参数
}
```

> **注意**：`user_ids` 数组保持为空[]，`employee_type` 保持 `"employee_no"` 不变。

## API Resources

```bash
lark-cli schema attendance.<resource>.<method>   # 调用 API 前必须先查看参数结构
lark-cli attendance <resource> <method> [flags]  # 调用 API
```

> **重要**：使用原生 API 时，必须先运行 `schema` 查看 `--data` / `--params` 参数结构，不要猜测字段格式。

### user_tasks

- `query` — 查询用户考勤打卡记录

## 权限表

| 方法 | 所需 scope |
|------|-----------|
| `user_tasks.query` | `attendance:task:readonly` |


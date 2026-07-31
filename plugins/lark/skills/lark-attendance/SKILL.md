---
name: lark-attendance
description: "飞书考勤打卡：查询自己的考勤打卡记录"
---

## Wegent 本地运行

- 当前 `SKILL.md` 所在目录的 `../..` 是插件根目录。首次调用前，macOS/Linux 运行 `sh "<插件根目录>/scripts/ensure-lark-ready.sh"`；Windows 运行 `powershell -NoProfile -ExecutionPolicy Bypass -File "<插件根目录>\scripts\ensure-lark-ready.ps1"`。
- 下文的 `lark-cli ...` 是逻辑命令。实际执行时，macOS/Linux 使用 `sh "<插件根目录>/scripts/run-lark-cli.sh" ...`；Windows 使用 `powershell -NoProfile -ExecutionPolicy Bypass -File "<插件根目录>\scripts\run-lark-cli.ps1" ...`。
- 配置和用户 OAuth 均由官方 CLI 在本机管理。不要要求用户在对话中粘贴 App Secret 或 Access Token，也不要读取、记录或上传 `~/.lark-cli` 或系统钥匙串中的认证信息。配置、扫码、企业审批或增量授权需要用户操作时，展示 CLI 原样输出的 URL/二维码并暂停等待。

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


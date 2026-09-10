# 企业微信

本地通过官方 CLI 扫码连接机器人，Wegent 同步认证后，云端可以直接使用消息、联系人、会议、日程、待办和文档能力。

## 认证与调用

连接采用 `password` 类型：用户名为 Bot ID，密码为 Bot Secret。它不是 OAuth，不需要迁移刷新权；本地原认证继续可用。适配器只通过私有认证通道导出和接收凭据。

在 Wegent 的原生连接界面完成扫码。业务命令通过 `scripts/run-wecom-cli.sh` 或 Windows 的 `scripts/run-wecom-cli.ps1` 执行。云端入口直接调用宿主 broker，不安装 CLI、不启动扫码、不读取来源设备文件。认证失效或权限不足时，在来源设备重新连接。

云端保留官方 CLI 0.1.9 的命令树和文档 helpers，通过内存机器人信息获取最新 MCP 授权地址。机器人凭据、加密密钥与 MCP 授权地址不会写入云端认证缓存；禁用日志、环境代理、重定向和工作目录 `.env` 加载。媒体下载仍正常生成用户请求的文件。

适配读取 CLI 0.1.9 的 `bot.enc` 和 `.encryption_key` 格式，可通过 `WECOM_CLI_CONFIG_DIR` 指定来源目录。使用其他独立 CLI 版本的账号应先在本插件原生界面连接，避免跨版本修改认证文件。

## 原生构建与打包

原生适配器基于官方源码提交 `72e14f7695f34d28f1ff23ea504ddd2210a87c13`（0.1.9），使用 Rust 1.94.1。构建入口校验上游归档 SHA-256，并在 macOS、Linux 和 Windows 的真实构建机上生成五个平台产物。

CI 验证成功的 XZ 压缩产物保存在 `scripts/native/<platform>/`，同时记录上游版本、适配源码摘要和二进制 SHA-256。修改适配源码后，必须重新生成五个平台产物；打包器拒绝源码与产物不匹配的包。运行时仅需 Python 3.9+，不需要安装 Rust 或 Node.js。

```sh
uv run --no-project python .wework-build/wecom-auth/build.py --output build/native
uv run --no-project python .wework-build/wecom-auth/package.py --plugin . --output build/wecom-package.zip
```

构建命令只生成当前主机对应的产物。通过 `WeCom account authentication package` 工作流汇总五个平台结果，校验成功后更新 `scripts/native` 并提交。`.wework-build.json` 提供自包含打包入口，市场发布阶段无需另行下载临时 CI 文件。

验证使用合成凭据和隔离目录，覆盖原认证文件解密、私有通道、内存业务请求、云端路由和打包后的真实子进程退出码。真实企业账号与云端联调仍需在配套 Wegent 服务部署后验收。上游许可证与每个平台产物一同保存。

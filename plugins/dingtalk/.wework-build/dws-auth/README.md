---
sidebar_position: 1
---

# DWS 本地刷新与云端访问快照

从 DWS `v1.0.58` 固定源码与插件内扩展编译原生 companion。源码归档 SHA-256：
`f6b2dcf16b34492d7be25ce63fc81c7155d6a857af21c0604ae16bf4fa96f1e2`。
共享私有通道来自 `../plugin-auth-go`，保留其 nonce、帧校验及秘密输出隔离。

## 认证边界（0.3.4）

- `dingtalk-local` 只有本地浏览器登录，正常业务直接调用本地 DWS，不依赖 Broker。
- `dingtalk-access` 是可选 bearer 连接，只通过私有通道导出访问快照，无第二次 OAuth。
- 不声明 `exportMode` 或 `oauth2`，不注册 detach、refresh、revoke。
- 唯一白名单为 token、expires_at、corp_id、user_id、source、client_id。
  不序列化完整 TokenData，refresh token、persistent code 等永不上传。
- 本机使用官方双层锁、刷新前持久化预检和刷新保存逻辑。刷新结果先保存到本机，
  与后续 Backend 上传成败无关；同步失败不删除凭据或标记 profile 失效。
- 云端拒绝未知/私密字段和无效到期时间；剩余有效期不超过 30 秒时返回
  `dingtalk_cloud_token_expired` 业务 JSON，不调用上游、不刷新、不回退本地。
  公共包装器将此结果转换为非零退出码，辅助脚本不能把它当成成功。
- 业务通过官方 edition.Hooks 的只读内存 TokenProvider 执行，隔离本机配置和钥匙串。
  限制仍只支持官方 MCP OAuth；自定义 mcp_url 与 direct client secret 不支持同步。

`accountAuth.localEnvironment` 声明本机存储目录与钥匙串配置，经宿主校验后只提供给
本机导出。令牌不放进 stdout、命令行参数、普通环境变量或聊天上下文。
后台同步周期由 WeWork 0.5.4 控制，同一连接约 60 秒冷却，不承诺即时同步。

## 构建与验证

从本仓库根目录运行：

```bash
uv run --no-project python plugins/dingtalk/.wework-build/dws-auth/vendor.py .
uv run --no-project python plugins/dingtalk/.wework-build/dws-auth/vendor.py --check .
uv run --no-project python plugins/dingtalk/.wework-build/dws-auth/package.py --plugin plugins/dingtalk --output .ci-artifacts/dingtalk-account-auth.zip
```

此目录现由插件仓库维护；不要用 Wegent 的旧独占迁移适配器覆盖它。
可通过 `--source-archive` 提供已下载的固定哈希源码包。打包器准备固定 Go 工具链，
在临时副本生成 macOS arm64/amd64、Linux arm64/amd64 和 Windows amd64 原生文件，
检查来源/二进制哈希，并在宿主平台执行真实私有通道检查。
Windows Python 启动器必须等待子进程并传播退出码，不能改回 os.execv。

`--test` 使用隔离的合成安全存储及 HTTP 服务，不读取用户钥匙串。覆盖本地保留、
刷新落盘、并发刷新、刷新失败不破坏状态、云端过期拒绝、字段白名单和官方业务命令。
旧 `wegent_transfer*` 源码及测试仅保留作历史协议回归；新可执行入口不注册交接操作。

产物含二进制、LICENSE、NOTICE、编译源哈希和平台信息。上传/解压限制仍为
50 MiB / 200 MiB。源码不含用户状态，构建不得读取发布凭据。

## 升级与发布限制

0.3.3 已交接用户不能无条件自动升级：在旧定义仍安装时，先通过平台支持的流程
停止同步、退役旧托管连接并处理在途迁移/撤销，再恢复本地 DWS 登录，然后安装新包。
否则旧刷新所有者可能残留，且新版本无法代替旧定义完成撤销。不得删除回执假装恢复。
本地仍有凭据的 0.2.9 用户无需第二次 OAuth。

本地退出不等于云端快照立即失效；停止云端使用需断开云端连接，离线时只能保证
快照到期后不可使用。已断开的连接不会被普通周期导出重新启用。

插件根目录 `.wework-build.json` 继续接入现有 package/test/release 流程；
本次未修改 WeWork 源码，也不自动提交、发布或操作真实连接。
合成测试和交叉编译不代替 WeWork 0.5.4 真机 UI、系统钥匙串、周期同步及真实云端验收。

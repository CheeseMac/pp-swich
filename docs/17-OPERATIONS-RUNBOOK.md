# 17 · 运行手册：凭证、连接、事故与故障排查

## 1. 日常操作边界

管理人员可注册 Service、审核 Endpoint、管理授权关系和轮换凭证，但**不能通过普通 Control API 下载或查看明文 Secret**。如 Vault 管理员具备技术上的读密钥能力，必须走单独双人审批和审计流程。

## 2. 新增 API 的上线 SOP

1. 管理员确认来源和合法授权。
2. 由被授权用户导入经本地脱敏的 HAR/OpenAPI 或数据库元数据。
3. 系统生成 Candidate Operation；人工确认 method/path/参数/风险。
4. 为目标 Service 创建受审批 Endpoint（域名、协议、端口及网络访问规则）。
5. 创建 Connection（凭证通过受信渠道录入，不从浏览器普通表单返回明文）。
6. 创建 Profile，设置 personal/team/deployment 作用域。
7. 发布 Capability 新版本，要求确定输出白名单、策略、测试证据。
8. 用 mock 或授权测试环境验证工具；确认符合预算与审计要求。
9. 开放 MCP/HTTP Tool 列表，人工抽查 Agent 可见字段无秘密。

## 3. 凭证轮换 SOP

1. 创建新的下游凭证（使用系统官方旋转或 OAuth 流程）。
2. 将新密钥通过 SecretProvider 的可信写入路径加入新版本/租约。
3. 使用受限测试任务验证新凭证；只检查是否成功，不回显 Key。
4. 原子调整 Connection 凭证指向，保留必要的审批和审计。
5. 清理旧缓存/租约，撤销旧凭证；检查是否有仍在运行的 Invocation。
6. 执行蜜罐外泄测试、错误日志检查和目标授权范围复验。
7. 完成审计记录及通知；没有用户批准时不要跨账号自动切换。

## 4. 事故：疑似 API Key 进入模型上下文

**优先级：P0 安全事件**
1. 暂停相关 Agent / Broker / Connection 的生产权限，不再发送新的模型请求；
2. 立即在目标服务撤销/轮换疑似泄漏凭证；
3. 阻止含敏感数据的日志、追踪、HAR 被进一步传播；
4. 关联 `invocation_id / request_id` 调查泄漏链：源码、Agent 上下文、工具结果、异常、日志和 HTTP 请求；
5. 按企业数据事件策略处理报告、通知和证据保全，遵守适用法律；
6. 修复根因（例如多余的响应 Header / 不当环境变量注入 / 跨域代理）；
7. 加入回归测试，再恢复最小权限。

**删除 `.env` 或取消模型页面中的可见输出，不等于撤销已经泄漏的 Key。**

## 5. 事故：跨用户 Profile 误切换

1. 禁用错误的 ActiveBinding / Profile。
2. 校验 `principal_id/workspace_id/service_id/context_key` 的授权验证。
3. 审计错误窗口内的调用，确认是否使用了错误 Connection。
4. 修复 Profile FK / 事务 / 缓存隔离，并增加并发测试。
5. 在确认审计与范围后逐步恢复。

## 6. 事故：未知 API 变更

当 Discovery 发现契约 drift：
1. 标记 Operation 为 `change_detected`（不直接覆盖已发布版本）。
2. 通知 Service Owner、Reviewer。
3. 在 mock/测试环境确认变更是否兼容。
4. 发布新的 Operation/Capability Version，必要时弃用旧版本。
5. 若存在明显敏感数据风险，先禁用对应 Capability。

## 7. 常见故障与响应

| 现象 | 排查 | 安全处理 |
|---|---|---|
| `SECRET_PROVIDER_UNAVAILABLE` | Vault/Keyring 认证、连通性、租约 | 拒绝执行，不回退读取环境密钥 |
| `PROFILE_UNAVAILABLE` | Profile/Connection 过期或撤销 | 告知用户重新授权 |
| `PROFILE_VERSION_CONFLICT` | 双窗口并发切换 | 刷新版本、重试人工选择 |
| `UPSTREAM_TIMEOUT` | DNS/TLS/代理/目标慢 | 不盲目重试写操作 |
| `CAPABILITY_NOT_FOUND` | 发布状态/权限/工具目录缓存 | 不泄露隐藏工具元数据 |
| Browser HAR 无法导入 | 清洗版本/字段结构/大小限制 | 不提示上传原始敏感 HAR |
| MCP 工具无法调用 | SDK 兼容性、JWT audience、Schema | 禁止跳过认证绕过故障 |

## 8. 演练周期

- 每次发布：P0 泄漏和越权 E2E 测试；
- 每次引入新的 SecretProvider：备份/恢复/轮换/撤销演练；
- 每次 MCP 协议升级：Token audience 与工具可见性测试；
- 每月/季度（按组织规定）：审计权限和账户清理、凭证轮换策略检查；
- 重大事件后：风险复盘与 ADR 修订。

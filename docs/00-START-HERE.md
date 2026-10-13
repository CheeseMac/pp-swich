# 00 · 开发启动指南

## 0. 本文档的角色

这是团队 / AI 编码助手开始开发 `pp switch` 时的**唯一启动顺序**。各文档是“目标规范”，不是已存在的软件；代码、迁移、配置、许可证尚需根据本文档创建。

**版本：v0.1.0（2026-10-13）**

## 1. 当前已经决定的事（避免反复讨论）

- 品牌 `pp switch`，GitHub 仓库建议 `pp-switch`。
- 范围：API / 数据库发现、注册、权限治理、Profile 一键切换、HTTP + MCP 安全执行。
- 暂不支持 GUI / RPA。网页相关仅发现**经过授权的网络 API**，不自动控制网页。
- API 是**候选发现**，不是“抓到就能用”；发布需要确认接口契约、合法授权、风险及可执行性。
- 内部主抽象为 `Capability`，MCP 是出口之一，不让基础业务代码依赖 MCP。
- API 定义可多人共享；用户的连接授权与密钥**不能共享或自动继承**。
- 不假定 Kubernetes。首先支持本机、Docker Compose、局域网/外网 HTTPS（有合法访问权）。
- 一键切换只切换**经过授权的 Profile 绑定**，不切换浏览器 Cookie，不向模型提供账号密码。

## 2. 从零开发的推荐顺序

| 顺序 | 任务 | 完成判据 |
|---:|---|---|
| 1 | 建立 Monorepo、CI、Python/TS 工具链 | lint、typecheck、pytest、前端 build 运行 |
| 2 | 定义 Pydantic 模型和数据库迁移 | `workspaces/services/operations/profiles/connections` 可 CRUD |
| 3 | 导入脱敏 HAR 并生成只读候选操作 | 合并路径、参数和响应 Schema |
| 4 | 操作审核、发布为 Capability | 未审核接口无法执行 |
| 5 | Mock Connector + SecretProvider（开发态） | 无需真实密钥完成执行链路 |
| 6 | Profile CRUD + 一键切换 | 切换只影响后续调用，带版本并发控制 |
| 7 | Policy + Invocation Executor | 身份验证、租户隔离、白名单目标、脱敏结果 |
| 8 | MCP Adapter | list/call 只暴露当前身份可见的工具 |
| 9 | 安全基线和端到端测试 | 所有 P0 泄露/越权用例通过 |
| 10 | Chrome Collector（可选第二阶段） | 域名授权、启停可见、本地脱敏、人工审核 |

## 3. 48 小时内应完成的“第一张竖切”

不要先做炫酷仪表盘或自动 AI 猜接口。先完成一个全链路任务：

```text
operator imports sanitized HAR
    ↓
normalizer proposes GET /api/customers/{id}
    ↓
reviewer publishes crm.get_customer:v1
    ↓
user creates profile "CRM Demo A" (no real credentials)
    ↓
user clicks "Activate"
    ↓
authorized agent calls capability by HTTP
    ↓
policy allows + mock adapter executes
    ↓
only output allowlist fields return
    ↓
auditable invocation record
```

**可验收的 10 个场景**

- [ ] 输入含 `Authorization`、`Cookie`、`token` 字段的 HAR → 中央库不保存值
- [ ] 导入 3 个不同 `customer_id` → 合并为 1 个候选 `GET /api/customers/{id}`
- [ ] 尝试发布未经审核的候选 API → HTTP 409/403
- [ ] 直接让 Agent 调用未发布工具 → 404（不暴露是否存在）
- [ ] 无权限用户切换他人的 Profile → 403
- [ ] 切换活动 Profile 后新调用选新档案，已开始调用仍使用旧快照
- [ ] 试图在模型参数中传 `Authorization` / `target_url` → 422
- [ ] 目标返回 `Set-Cookie` / `access_token` → 输出白名单中不可见
- [ ] 企业 B 的用户尝试读企业 A 的操作 → 404 / 403 且无信息泄漏
- [ ] 无法解析或命中私网禁区的未知主机 → 未授权执行器拒绝

## 4. 核心术语词典

| 术语 | 定义 | 明确不是 |
|---|---|---|
| `Service` | 某个逻辑 API 提供方，如 CRM / GitLab | 一个具体账号 |
| `Endpoint` | Service 的受审网络访问地址和环境（prod/stage） | 模型可自由输入的 URL |
| `Observation` | 经采集并脱敏的单次接口行为线索 | 可直接调用的工具 |
| `Operation` | 去重后的方法/路由/参数/响应契约 | 已审批的业务能力 |
| `Capability` | 对 Agent 可见、版本化、经发布的业务工具 | 原始 HTTP 请求 |
| `Connection` | 经过授权的某主体连接某服务的身份绑定 | 可共享的明文凭证 |
| `Profile` | Connection + Endpoint + 策略上下文的命名配置 | 浏览器登录状态复制 |
| `ActiveProfile` | 当前用户/工作区/服务/上下文选择的档案 | 全局进程环境变量 |
| `AgentGrant` | 某 Agent 被授权调用的能力/范围 | 模型自己声明的权限 |
| `Invocation` | 一次不可变的授权执行快照 | 允许执行中随意换账号 |
| `Executor` | 在受信环境调用后端的进程/服务 | 将密钥提供给 MCP client |
| `SecretProvider` | 受控密钥访问抽象 | 公共 `get_secret` API |

## 5. 协作上的红线

- 首个 PR 不得引入通过通用 `URL + headers + body` 直接访问任意互联网/内网接口的公共工具。
- 不得以模型输出的 `tenant_id`、`user_id`、`agent_id` 作为身份依据。
- 不允许在单元测试样例中出现真实凭证；用带标记的合成蜜罐值。
- 浏览器扩展不能默认拥有对所有站点的静默持续监控。
- 凭证只由运行 Executor 的受信组件获得，**管理 API 和模型不应拥有 decrypt 权限**。
- `GET` ≠ 天然安全；危险性由受审业务语义定义。
- 允许 AI 提供工具描述草稿，但 AI 不能自行批准生产发布。

## 6. 代码启动验收（完成后再开展下一阶段）

项目所有服务至少提供：
- 健康检查与 readiness；
- 强类型输入/输出；
- `request_id` 和统一错误码；
- 安全默认配置（`PUBLIC_DEMO=false`、默认拒绝外部目标）；
- 配置示例 `*.example`，不提交 `.env`；
- 基础 CI 断言（lint / typecheck / unit test / secret scan）；
- 关键行为的测试用例和文档链接。

参考实际目录：[08-REPOSITORY-STRUCTURE](08-REPOSITORY-STRUCTURE.md)；按任务推进：[09-IMPLEMENTATION-PLAN](09-IMPLEMENTATION-PLAN.md)。

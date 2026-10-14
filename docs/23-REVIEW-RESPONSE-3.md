# 23 · 第三轮评审回应（Review3 Response · v0.1.3 Authorization Boundary Fix）

> 对应评审：《pp-switch 第三轮技术复审》（2026-10-14，有条件通过）。
> 本轮全部 6 项 P0（SEC-043..048）+ 2 项顺带落地 + 评审第八节的综合攻击场景
> 均已实现并测试锁定。回应格式延续 docs/21、22。

## 0. 总览

| 评审项 | 核实 | 处理 |
| --- | --- | --- |
| 幂等缓存跨委托用户复用（新 P0） | 属实 | 回放身份绑定扩展 `delegating_user_id`，跨委托 409（SEC-043） |
| scopes 非空交集≠授权（新 P0） | 属实 | Capability `required_scopes` + 执行时全覆盖校验，空链不满足（SEC-044） |
| 目录泄漏 Draft 版本/execution/过期授权（新 P0） | 属实 | `_public_tool_view` 与管理视图拆分 + 过期 grant 过滤（SEC-045） |
| 委托人由参数选择（新 P0） | 属实 | **签名式 Delegation Context 票据**，参数选身份语法废弃（SEC-046 / ADR-005） |
| 审批人资格过宽（新 P0） | 属实 | `approvals.decide`、`endpoints.approve` 提至 reviewer（SEC-047） |
| 数组 items 宽松透传（新 P0） | 属实 | 无有效 items / `$ref` schema → 整体拒绝（SEC-048） |
| HTTP 明文凭证（v0.2 门槛） | 属实 | endpoint 默认强制 HTTPS（demo/显式开关豁免），PR-DEP-14 应用侧先行 |
| 手动创建 Capability 元数据清洗（v0.2 门槛） | 属实 | 创建时过与导入相同的 `sanitize_schema`（PR-NET-13 前置） |
| 188 测试未独立复跑 | 接受 | 本地 202 passed 全绿；CI 双工作流可复核；PostgreSQL 矩阵维持 PR-QA-06 排期 |

全仓 **202 测试**、ruff、mypy、密钥扫描全绿。迁移链新增
`c8d31a90f4e2`（capability_versions.required_scopes）。

## 1. 六项 P0 的实现

### SEC-043 幂等缓存绑定委托用户

评审推导的攻击链成立：回放身份四元组（principal/capability/version/digest）
全部与委托人无关，共享 Agent 换委托上下文重放会拿到另一位用户的缓存结果。

修复：回放身份增加第五维 —— `profile_snapshot.delegating_user_id`（v0.1.2
起每次调用都会写入审计快照）。不一致 → `409 IDEMPOTENCY_KEY_CONFLICT`，
绝无跨用户缓存读取。同委托重试语义不变（仍返回原结果）。

关于评审问的"授权版本/撤销状态纳入回放"：v0.1.2 已把回放放在 grant 与
delegation **活跃性校验之后**（SEC-037），撤销/过期在回放前就 403；
本轮补齐的是身份归属维度。Profile 一致性按产品语义**不**参与回放
（用户切换 Profile 后的重试应拿到原请求的结果——这正是幂等的含义），
已在 ADR-005 的边界小节记录。

### SEC-044 Capability required_scopes

采纳"操作级必需权限"设计（迁移 5）：

- `CapabilityCreate` / `capability_versions` 新增 `required_scopes: list[str]`，
  发布时随版本冻结；
- 执行时在三层交集（grant ∩ connection ∩ delegation）之后追加校验：
  **required_scopes 非空的能力，必须被有效交集完全覆盖**；
- 关键语义收紧（回应评审"空 scope 不宜默认等于全部权限"）：交集为
  `None`（整条链路无任何 scope 声明）对 scoped 能力是**拒绝**——显式授权
  是强制项，不是可选项。无 required_scopes 的能力维持原语义不受影响。

测试三方向：`orders.read` 链路调 `refund.execute` 能力 → 403；全空链路调
scoped 能力 → 403（显式授权缺失）；覆盖性 scopes → 200。

资源级授权（"涉及某条订单、某个客户"）确认为 v0.2 RBAC 深化范围，
见 §4。

### SEC-045 公开工具目录净化

评审指出的序列化问题属实：Capability 级过滤正确，但 `_capability_view`
把所有版本（含 draft）和内部 `execution`/`allow_fields` 一并返回。

修复：视图拆分——

- `_public_tool_view`（Agent 的 HTTP/SDK 目录）：仅 **published** 版本，
  字段只含 name/display_name/description/status +
  {version, risk_level, status, input_schema}。draft 数据、execution
  映射、allow_fields、output 守卫配置一概不出；
- `_capability_view`（管理视图）：维持全量，供控制台与审核流程；
- `GET /capabilities/{id}` 对 Agent 同样走公共视图 + published-only；
- grant 过期（`expires_at`）的目录剔除同步落地。

MCP 侧本来就只取 published 版本注册工具，行为不变。

### SEC-046 / ADR-005：Per-invocation Delegated Identity（签名票据）

采纳评审建议的"可信会话层签发短期 Delegation Context"，以最小落地形态
实现（不动 Delegation 表结构）：

- 新端点 `POST /api/v1/delegations/{id}/context`（**用户身份**专用）：
  签发 HMAC-SHA256 签名票据，内容含 delegation/user/agent/scopes/
  **10 分钟过期**/nonce，密钥为服务端 `internal_token`；
- 共享 Agent（多活动委托）调用时，执行身份**只能**由有效票据决定：
  验签 → 过期检查 → workspace/agent 匹配 → 对应 delegation 仍活跃；
  任一不满足 → 403。无票据 → 409 提示获取票据；
- **`agent:<id>:<user>` 参数选身份语法彻底废弃**（schema pattern 拒绝），
  模型可填的任何 user_id 都不再具有身份语义——票据伪造不了（无密钥）、
  改一个字节签名即失效（测试含篡改 user_id 的票据 → 403）；
- 单委托场景不需要票据（唯一身份，无选择权问题），旧 SDK 零改动可用；
- SDK 新增 `issue_delegation_context()`（用户侧）与
  `invoke(delegation_context=...)`（Agent 侧）。

ADR-005 正式记录于 §3。

### SEC-047 审批人资格

- `approvals.decide`（高风险调用审批）与 `endpoints.approve`（网络目标
  审批）在权限矩阵中提升为 **reviewer 及以上**；
- 现有"审核≠创建人/请求人"校验保持不变，两者叠加；
- "业务资源授权的审批人"（如订单线审批人）确认为 v0.2 RBAC 深化项。

### SEC-048 输出 Schema 严格化

- schema 声明 `array` 但 `items` 缺失/非对象/既无 `type` 又无 `properties`
  → **整个数组拒绝**（None + `<items_unspecified>`），不再"正则脱敏后放行"；
- 任何 schema 节点含 `$ref`（未解析引用）→ 该值整体拒绝
  （`<unresolved_ref>`），对象/数组/标量一视同仁——引用解析排期
  PR-NET-13，解析完成前引用就是不可解释契约。

## 2. 顺带落地的两项 v0.2 门槛

| 项 | 实现 |
| --- | --- |
| HTTP 明文凭证 | endpoint 创建默认仅接受 `https://`；`PP_SWITCH_ALLOW_PLAIN_HTTP=1`（显式运维决策）或 demo 模式才放行 http。应用侧先行，网络级出口约束仍归 PR-DEP-14 |
| 手动创建 Capability 元数据 | `create_capability` 对 description/input_schema/output_schema 过与 OpenAPI 导入相同的 `sanitize_schema`——canary/密钥形状字符串经手写路径同样进不了库和目录 |

## 3. ADR 补录

### ADR-005：Per-invocation Delegated Identity（本轮新增）
执行身份 = 签名票据（用户签发）> 唯一活动委托（无歧义）。参数声明的
身份恒无效。票据短寿命、一次性语义靠 delegation 活跃性复查兜底。
**边界**：Profile 切换不破坏同委托的幂等重试（重放返回原请求结果）；
跨委托永远是 409。未来接入 OIDC 后票据签发可换为会话令牌派生，接口不变。

### ADR-006：R-12 数据留存风险接受记录（补编号）
v0.1.2 §3.4 的 R-12 决定正式编号为 ADR-006：**接受延期，不接受跳过**。
PR-DATA-12（数据分级/TTL/加密/清理）是任何真实生产凭证接入的硬前置。

## 4. 路线图（v0.1.3 → v0.2 不变）

```
已完成: v0.1.3 Authorization Boundary Fix（本文件）
下一步: PR-DATA-12  结果留存/分级/TTL/加密/清理（ADR-006 硬前置）
        PR-NET-13   $ref 受限解析 + 引用循环/深度限制
        PR-DEP-14   部署面出口强制约束（防火墙/代理段）
        PR-QA-06    PostgreSQL CI 矩阵（回应评审第六节）
        PR-FEAT-07/08 OIDC/OpenBao/资源级 RBAC/Collector
```

企业真实密钥接入前置条件不变且更严格：上述全部完成 + 独立渗透测试。

## 5. 验证记录

- `uv run -m pytest`：**202 passed**（新增 SEC-043..048 六项 +
  共享 Agent 综合攻击场景套件；本轮共 +14）
- `uv run ruff check .` / `uv run -m mypy`（六个源码树 42 文件）：通过
- `scripts/check-no-secrets.py`：clean
- 迁移链：`… → b21c4f7e0d15 → c8d31a90f4e2`

综合攻击场景（评审第八节原话场景）已固化为
`apps/control-api/tests/test_security_matrix3.py::TestSharedAgentAttackScenario`：
双用户双档案共享 Agent 下，票据换身份 ✓、幂等键跨用户 409 ✓、
提示词注入参数（target_url/user_id 混入）被 schema 拒绝 ✓、
撤销委托即刻失效且不影响他人 ✓、grant 过期全员失效 ✓。

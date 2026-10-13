# 21 · 技术评审回复与安全加固记录（v0.1.1）

> **面向读者**：评审人（_blueprint 作者_）。
> **输入**：《pp swich v0.1.0 项目技术评审》（有条件通过）。
> **输出**：本文档 + 提交 `review-fixes` 系列（PR-SEC-01…05 全部落地）。
> **验证基线**：145 个自动化测试（新增 23 个攻击组合测试）、ruff/mypy 零错误、CI 双 OS 全绿。

---

## 0. 结论对照

| 评审结论 | 本轮响应 |
|---|---|
| 6 项 P0 源码问题 | ✅ 全部修复并配攻击组合测试（SEC-021…030） |
| 2 项架构隐患 | ✅ 1 项修复（快照脱敏）+ 1 项采纳架构决定（Delegation，见 §3） |
| 9 项偏差审核意见 | ✅ 全部按意见执行（D-3 收紧、D-5 进入重设计） |
| 6 个问题拍板 | ✅ 全部按决定执行 |
| 测试理念转向"攻击组合" | ✅ 新增 10 条 SEC-021…030，全部通过 |
| 下一轮优先级 PR-SEC-01…05 | ✅ 本轮已完成（原计划"下一轮"），PR-QA-06/07/08 保持排队 |

额外收获：修复过程中发现并修复了一个**真实缺陷**——SQLite 读回的过期时间为 naive
datetime 被按本地时区解释，导致审批提前 8 小时失效（评审第 3 项排查时暴露）。

## 1. P0 逐项修复对照

### P0-1 Connection 所有权（评审 §一.1）→ PR-SEC-01

| 要求 | 落地 |
|---|---|
| 创建/激活/执行三阶段校验 Connection 所有权 | `create_profile`：Connection 必须属于创建人（SEC-021：carol 用 alice 的 connection 建 profile → 403）；`activate_binding`：`connection.owner == profile.owner` 复检；执行链沿用 profile→binding 所有权 |
| Endpoint 不能由普通创建者自行批准 | 非 demo 模式：创建即 approved 被拒（`ENDPOINT_NOT_APPROVABLE`），新增 `POST /endpoints/{id}/approve`，审批人 ≠ 创建人且必须 human（SEC-030 断言自批 403）；demo 模式保留直接 approved 以维持演示 |

### P0-2 Agent 自授权（§一.2）→ PR-SEC-01

| 要求 | 落地 |
|---|---|
| 禁止 Agent 自行创建 AgentGrant | 新增 `require_user` 依赖：**所有管理类 API（grants / review / verify / publish / endpoint approve / approvals）一律拒绝 agent 主体**（403，SEC-022/023） |
| 审核人资格 | `capabilities.created_by` 落库（迁移 3）；review/verify/publish 要求 **调用人 ≠ 创建人**（alice 发草稿，carol 审核发布；创建人自发布 → 403） |
| 操作级权限 | 本轮以 "human-only + 异人审核" 实现；完整 RBAC（`capability.review` 等角色）列入 PR-FEAT-07 |

### P0-3 审批绑定（§一.3）→ PR-SEC-02

审批现在绑定不可变 Proposal：`requester_id + capability_version_id + profile_id +
arguments_digest + expires_at(15min)`（迁移 3 新列）。执行时**全量核验**后以
`UPDATE … WHERE status='approved'` 原子消费（防并发双花）。审批人必须 human 且
≠ 请求人（agent 审批 → 403，SEC-023）。跨请求人复用 → 409（SEC-024）。

### P0-4 幂等键越权（§一.4）→ PR-SEC-02

幂等检查移到能力解析之后，且重放条件绑定 `principal + capability + version +
arguments_digest` 四元组：任一不符 → 409 `IDEMPOTENCY_KEY_CONFLICT`，**绝不返回
他人结果**（SEC-025：carol 重放 alice 的 key → 409）。`GET /invocations` 与
`GET /invocations/{id}` 增加 `principal_id` 所有权过滤（SEC-029：carol 读
alice 的调用 → 404）。

### P0-5 URL 组装与网络目标（§一.5）→ PR-SEC-03

- 路径段值必须匹配 `^[A-Za-z0-9._~-]{1,128}$` 且不含 `..`（`_segment_value`），
  9 类恶意路径（`../../admin`、`a/b`、`%2e%2e%2f`、`?`/`#` 注入等）全部拒绝
  （SEC-026）。
- **强制主机白名单**：所有目标（含公网）必须精确命中 `approved_hosts`，公网不再
  隐式放行（SEC-027）。
- **不可覆盖拒绝集**：link-local/云元数据段（169.254.0.0/16 等）永远拒绝，即使
  被误加入白名单（SEC-027 断言）。
- **最终 URL 复检**：参数代入后的完整 URL 再次过网络策略，模板检查不再单独可信。
- **请求时重解析**：`NetworkValidatingTransport` 在发送前重新解析并校验目标
  （SEC-008 钉扎的实用实现：检查与连接之间的 DNS 变化被拦截，有 rebinding 模拟
  测试）；残余窗口为传输层内部，已在代码注释声明。
- 响应改为**流式读取 + 64KB 块累计硬上限**，超限即断（替代一次性 `response.content`）。

### P0-6 响应守卫递归投影（§一.6）→ PR-SEC-04

`project_response` 重写为**输出 Schema 驱动的递归白名单投影**：仅保留 schema
声明字段（所有层级）、敏感名（token/api_key/…）在任何深度直接丢弃（即使 schema
允许）、标量类型不符置空、深度 ≤12 / 数组 ≤1000 / 字节预算不变；正则脱敏降级为
最后补充层。无 `properties` 的旧 schema 回落 `allow_fields` + 递归清洗，不会比
之前更宽。SEC-028：嵌套 `customer.api_key = "普通字符串"` 被剥离（旧正则抓不到
的值也防住）。

## 2. 架构隐患响应

**执行记录瘦身（评审 §二.2）**：`invocation.profile_snapshot` 不再保存完整
Plan——现仅存 `profile_id/label、connection_id、endpoint_id、能力名+版本、
policy_version、arguments_digest、risk_level`；**不含 arguments、secret_ref、
目标地址**。完整 Plan 只存在于本次分发的内存中（SEC-017 快照语义不变）。

**Agent 委托（评审 §二.1 + D-5）——采纳为正式架构决定**：

> **ADR-001（2026-10-13）**：Agent 永不拥有用户凭证。Agent 通过**有时效、可撤销、
> 绑定具体资源范围**的 Delegation 使用用户的 Connection；实际执行必须同时满足
> 「用户拥有 Connection ∧ Delegation 有效 ∧ Agent 获授权 ∧ Capability 允许 ∧
> 资源范围匹配」。Agent 的活动绑定按 (user, agent) 隔离，不再共享 agent 全局
> personal 作用域。

过渡期（本轮已做）：agent 上下文绑定仍需**拥有该 Profile 的人类用户**写入，agent
自身无法获得绑定；`AgentGrant.scopes` / `Connection.scopes` 参与策略求值列入
PR-FEAT-07。数据库/MCP Session/UI 的 Delegation 改造按路线图 v0.5。

## 3. 偏差审核意见执行情况

| 偏差 | 评审意见 | 执行 |
|---|---|---|
| D-1 Multipart 导入 | 有条件批准 + 读取限额 | ✅ 分块读取上限 5MB + 深层 JSON 拒绝（RecursionError → 400）；异步化留待批量导入需求 |
| D-2 review/verify 端点 | 批准 + 审核角色 | ✅ 已加 human-only + 异人审核（完整角色系统 PR-FEAT-07） |
| D-3 Dev 目录免鉴权 | 必须收紧 | ✅ demo 默认**关闭**（`PP_SWICH_DEMO_MODE` 默认 false）；非 demo 下端点 404（SEC-030）；服务仅绑 127.0.0.1 |
| D-4 确定性 Mock | 仅限 Mock 用途 | ✅ `MockSecretProvider` 注释明令禁止真实凭证/生产网络；非 demo 下执行器拒绝一切 `mock://` 凭证引用与 mock 传输（SEC-030） |
| D-5 Agent 绑定 | 不批准 | ✅ 转入 ADR-001 重设计（§2）；过渡期护栏如上 |
| D-6 审批流 | 架构批准代码待修 | ✅ 本轮完成角色 + 全量绑定 + 过期 + 原子消费 |
| D-7 复合外键 | 批准 + Connection 所有权 DB 约束 | ✅ 所有权在服务层三处校验（应用层）；DB 级 Connection-owner 复合外键列 PR-QA-06（需评估跨表约束对 PG/SQLite 双库成本） |
| D-8 内部 Token | 仅开发环境 | ✅ 非 demo + 默认合成 token → **启动即拒绝**（两端，SEC-030 断言 RuntimeError） |
| D-9 MCP 静态目录 | MVP 可暂缓 | ✅ 保持静态注册；**已补权限过滤**（下条） |

**Q5 补充（tools/list 权限过滤）**：`GET /api/v1/capabilities` 现对 **agent 主体
只返回其被授权的能力**（按 AgentGrant 过滤）——HTTP 与 MCP 走同一端点，故 MCP
`tools/list` 自动同权（未授权 agent 列目录为空）。

**Q1/Q2/Q3/Q6** 均已按决定执行（Q6 的 IP 钉扎见 P0-5）。

## 4. SEC-021…030 落地证据

| 编号 | 断言 | 测试位置 | 结果 |
|---|---|---|---|
| 021 | 不能用他人 Connection 组 Profile | `test_security_matrix.py::TestSEC021` | ✅ 403 |
| 022 | Agent 不能自授 Grant / review / publish | `TestSEC022` | ✅ 403×3 |
| 023 | Agent 不能批准高风险调用 | `TestSEC023` | ✅ 403 |
| 024 | 审批不能跨请求人复用 | `TestSEC024` | ✅ 409 |
| 025 | 同工作区异主体同幂等键 ≠ 他人结果 | `TestSEC025` | ✅ 409 冲突 |
| 026 | 恶意路径参数不能改变批准路径 | executor `TestSEC026`（9 用例） | ✅ 拒绝 |
| 027 | 未批准备公网/私网/元数据全拒 | executor `TestSEC027` | ✅ 三态断言 |
| 028 | 嵌套对象不泄漏普通字符串密钥 | executor `TestSEC028` | ✅ 剥离 |
| 029 | 他人执行结果不可见 | `TestSEC029` | ✅ 404/列表不可见 |
| 030 | 非 demo 无开发目录/mock 凭证/默认 token | `TestSEC030` + 启动断言 | ✅ |

## 5. 遗留与下一步（与评审路线图对齐）

- **PR-QA-06（P1）**：PostgreSQL CI 矩阵（事务/并发/FK/迁移）、依赖漏洞扫描、
  Connection 所有权 DB 级约束评估。
- **PR-FEAT-07（P1，安全修复后）**：OIDC/JWT audience、Keyring/OpenBao Provider、
  完整操作级 RBAC、Delegation 数据模型与按 (user, agent) 隔离绑定、scopes 参与
  策略求值、凭证轮换。
- **PR-FEAT-08（P2）**：MCP 动态目录/变更通知、浏览器 Collector、drift 检测。
- v0.2…v1.0 成熟度路线（评审 §七）接受为项目正式路线图。

## 6. 复核指南

```bash
git clone https://github.com/CheeseMac/pp-swich && cd pp-swich
uv sync --all-packages
uv run pytest            # 145 用例，含 SEC-021..030
uv run ruff check . && uv run mypy packages apps
# 本地演示（demo 模式需显式开启，dev-up.py 已设置）：
uv run python scripts/dev-up.py --ui
uv run python scripts/seed-demo.py
# 注意：演示流程现为双人制 —— Alice 创建能力，Carol 登录完成 review/verify/publish
```

---

*本回复与代码同仓提交；每项声明均可在上述测试与 CI 运行记录中复核。*

# 24 · 第四轮评审回应（Review4 Response · v0.1.3.1 边界补丁）

> 对应评审：《pp-switch v0.1.3 第四轮安全复审》（2026-10-14，开发阶段有条件通过）。
> 六项 P0 通过验收；本轮处理三个剩余边界问题 SEC-049/050/051 与票据语义澄清，
> 并把评审收敛的**撤销不变量**固化为回归测试。

## 0. 总览

| 评审项 | 核实 | 处理 |
| --- | --- | --- |
| SEC-049 幂等回放绕过 scope 收紧 | 属实 | 回放前重验完整 scope 链 + connection 存活（共享守卫函数） |
| SEC-050 历史记录未按委托用户隔离 | 属实 | Agent 读历史必须带票据 + 委托活跃，按 `delegating_user_id` 过滤 |
| SEC-051 顶层 `$ref` 进入宽松回退 | 属实 | `project_response` 根节点 `$ref` 一律拒绝 |
| 票据非一次性语义 | 属实 | ADR-005 修订 + 生产强制独立签名密钥 |
| 撤销不变量（验收句） | 采纳 | `TestRevocationInvariant`：新调用/幂等缓存/历史查询/Schema 回退四路全拒 |

全仓 **209 测试**全绿，ruff / mypy / 密钥扫描通过。

## 1. 三项边界修复

### SEC-049 幂等缓存尊重授权收紧

指控属实：回放返回发生在 scope 交集与 `required_scopes` 校验之前——缩小
delegation scopes（仍 active）后，新调用被拒而旧幂等键仍能取回缓存。

修复方式采纳评审建议的第一种（检查前移），以**共享守卫**实现避免两处
逻辑漂移：

- 新增 `_authorize_scope_chain(grant_scopes, connection_scopes,
  delegation_scopes, required_scopes)`——scope 交集 + required 覆盖的
  唯一权威实现；
- **实时路径**与**回放路径**都调用它；
- 回放前额外重载原调用的 connection（审计快照中的 `connection_id`），
  其 status 非 `active`（已撤销）同样拒绝回放——撤销不变量覆盖
  connection 维度；
- 拒绝时写 `replay_scope_revoked` / `replay_connection_revoked` 审计。
- "切换 Profile 不破坏合法重试"的既定语义保持不变（Profile 不参与回放）。

测试覆盖评审原文场景：delegation 从 `orders.read` 缩到 `orders.note`
后，新调用与旧幂等键**双双 403**；connection 撤销后回放同样 403。

### SEC-050 历史记录按委托用户隔离

指控属实：`principal_id == agent_id` 的过滤对共享 Agent 意味着任何
委托用户的 `result` 都可读，绕过了上轮修复的幂等隔离。

修复采用评审的第一方案（保留 Agent 的 `invocations.read`，但加票据）：

- Agent 读历史（列表与详情）必须携带 `X-Delegation-Context` 票据：
  验签 → 过期 → workspace/agent 匹配 → **对应 delegation 当前仍活跃**
  （票据证明签发，不证明持续有效——撤销实时复查）；
- 结果按审计快照 `delegating_user_id` 过滤，跨委托用户的记录 404
  （存在性不泄露）；
- 用户 principal 的查询行为不变（仍按自身 principal_id 过滤）；
- 未来第三方 Agent 场景：Agent 凭据本身从此读不到任何历史——
  读历史永远需要某位用户此刻的有效授权。

### SEC-051 顶层 `$ref` 拒绝

指控属实：`_project()` 的 `$ref` 拒绝只覆盖了"顶层 schema 带
properties"的分支；顶层 `{"$ref": ...}` 会落入 allow+scrub 兼容路径。

修复（采纳评审"正式 Schema 根节点合法性检查"）：

- `project_response` 在任何分支之前检查：**output_schema 非空且含
  `$ref` → 整个响应拒绝**（fail closed，错误码带
  `root_schema_unresolved_ref`）；
- 兼容路径语义同时明确（ADR-007，见 §3）：无 properties 的
  `{"type":"object"}` + `allow_fields` 属于**显式兼容路径**——它仍是
  顶层字段白名单 + 递归敏感名剔除 + 密钥形状脱敏，不是宽松透传；
  $ref 则是"自称有契约却不可解释"，两者处理不同。

## 2. 票据语义澄清（ADR-005 修订）

评审观察正确：票据含 `nonce` 但无消费记录——它是**10 分钟内可重复
使用的签名 Bearer 票据**，不是一次性票据。修订 ADR-005：

1. **明确语义**：短期可复用签名凭证；`nonce` 仅用于审计关联
  （把一次调用关联到一次签发），不承担防重放；
2. **持有者边界**：票据只由可信 Agent Orchestrator 持有与转发，
   不进入模型工具参数、提示词或日志；
3. **独立信任根**（代码落地）：新增 `PP_SWITCH_DELEGATION_SIGNING_KEY`，
   生产模式（非 demo）启动时强制要求独立密钥且不得等于
   `internal_token`——票据验证与执行器内部认证从密钥层面分离
   （本地/demo 自动回退 internal_token，不影响开发流）；
4. 一次性票据（`jti` + 原子消费）确认为 PR-FEAT-07 OIDC 会话派生
   票据时一并实现，当前语义下撤销由 delegation 活跃性复查兜底
   （撤销即刻生效，已被 SEC-050 测试锁定）。

## 3. ADR 补录

### ADR-005 修订（如上）
### ADR-007：输出 Schema 的三条路径（本轮新增）
1. 有 `properties` → 严格递归投影（类型门先行、未知对象拒绝）；
2. 显式兼容（无 properties 的正式 schema + `allow_fields`）→ 顶层
   白名单 + 递归 scrub——**字段集合仍被白名单强制**；
3. 不可解释（含 `$ref`）→ 拒绝。路径 2 是历史能力的兼容承诺，
   新能力应一律走路径 1。

## 4. 路线图

维持评审建议顺序，本轮为短周期补丁（v0.1.3.1），接下来：

```
PR-DATA-12  结果留存/分级/TTL/加密/清理（ADR-006 硬前置，优先）
PR-NET-13   $ref 受限解析（SEC-051 是其前置补丁，工程本体仍待做）
PR-DEP-14   部署面出口强制约束
PR-QA-06    PostgreSQL CI 矩阵 + 并发事务验证
此后: OpenBao/Keyring → OIDC/资源级授权 → Collector
```

## 5. 验证记录

- `uv run -m pytest`：**209 passed**（新增 SEC-049/050/051 与
  `TestRevocationInvariant`，共 +7）
- ruff / mypy（42 源文件）/ `check-no-secrets.py`：全部通过
- 撤销不变量四路径（评审验收句）全部锁定：
  新调用 403 ✓ 幂等缓存 403 ✓ 历史查询 403/404 ✓ Schema 回退 raise ✓

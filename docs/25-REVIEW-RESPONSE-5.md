# 25 · 第五轮评审回应（Review5 Response · PR-DATA-12 + SEC-052/053）

> 对应评审：《pp-switch v0.1.3.1 第五轮复审》（2026-10-14，开发验收通过，
> 批准进入 PR-DATA-12）。SEC-049/050/051 正式关闭；本轮交付：
> SEC-052/053 修复 + PR-DATA-12（数据分级、保留期、静态加密、清理）
> + PR-NET-13 组合结构前置拒绝。

## 0. 总览

| 评审项 | 核实 | 处理 |
| --- | --- | --- |
| SEC-052 缩小未撤销时历史仍可读 | 属实（含评审指出的测试盲区） | 历史读取按**记录**重校验完整授权链 |
| SEC-053 撤销→重授权旧票据复活 | 属实 | Delegation `generation` 代际，票据绑定并双向校验 |
| PR-DATA-12 | 批准开工 | 分级 + TTL + Fernet 静态加密 + 清理（启动 + admin API） |
| PR-NET-13 组合结构 | 建议补拒绝 | `allOf/oneOf/anyOf/not` 全路径 fail-closed |

全仓 **217 测试**全绿（+8），ruff / mypy（44 文件）/ 密钥扫描通过。
迁移链新增 `d4e7b91c6a30`（delegations.generation、invocations 留存列）。

## 1. SEC-052：历史读取的记录级重授权

评审准确指出两件事：`_agent_invocation_scope` 只校验票据与 delegation
活跃性，不重比对 scope；且第四轮的不变量测试在测历史**之前**撤销了
delegation，"缩小但未撤销"的路径未被独立证明。

修复（按评审建议"重新校验当前 Grant、Delegation、原 Connection 与
Capability 必需 Scope"）：

- 新增 `_history_record_authorized(session, agent, delegation, row)`：
  对**每条**候选历史记录，重查该 capability 版本的 `required_scopes`、
  原调用 connection 的存活与 scopes、Agent 当前有效 grant 的 scopes，
  全部过 `_authorize_scope_chain`（与实时调用/幂等回放同一个守卫）；
- 列表：未通过校验的记录直接不出现；详情：404（与不存在不可区分）；
- 新测试 `TestSEC052`：**不撤销**、仅把 scopes 从 `orders.read` 缩到
  `orders.note` → 历史列表为空、详情 404；同时确认记录归属仍为执行
  principal（委托用户不能绕过 Agent 经用户路径直读，SEC-029 语义）。

读取权限分开控制的另一半（用户 vs Agent 路径）由既有结构保证：
用户走 principal-scoped 路径，Agent 走票据 + 重授权路径，互不相交。

## 2. SEC-053：委托代际（generation）

指控属实：`create_delegation` 复用同一行（ID 不变），票据绑定
`delegation_id` 无代际——撤销后 10 分钟内重授权，旧票据复活。

修复（采纳评审的 generation 方案，迁移 6）：

- `delegations.generation` 单调递增，默认 1；
- **撤销**、**重新授权**、**scopes 变更**（reactivate-in-place）都会
  `generation += 1`——任何授权状态变化都是新代际；
- 票据 payload 写入签发时的 `gen`；
- 验证端（调用路径与历史路径）都要求 `payload.gen ==` 数据库当前值，
  不一致 → 403 "previous authorization generation"；
- 语义保留：仍是 10 分钟可复用签名 Bearer 票据（ADR-005），代际只是
  把"撤销"从活跃性检查延伸到了已签发票据。

测试：撤销 → 同 scopes 重授权 → 旧票据调用 403 / 历史 403，新票据
正常 200——评审描述的状态转换被完整覆盖。

## 3. PR-DATA-12：结果数据生命周期

按评审验收要点逐条落地：

| 要求 | 实现 |
| --- | --- |
| 存储前数据分级 | `classify_result(risk_level)`：high/critical → `restricted`，其余 `normal`；分级写入审计快照 `data_class` 并随记录返回 |
| 默认最小化保存 | `restricted` 结果默认 **metadata-only**（`store_high_risk_results` 显式开启才存）；调用方仍在即时响应里拿到一次完整脱敏结果 |
| 明确保留期限 | `result_retention_days`（默认 7，env 可配）；存储时写 `result_expires_at`，过期后读取一律 `None`（与从未存储不可区分） |
| 加密存储 | `PP_SWITCH_RESULT_ENCRYPTION_KEY`（Fernet）配置后结果密文入 `result_encrypted`，明文列恒 NULL；**生产模式无有效密钥拒绝启动** |
| 过期清理 | 启动时自动清理 + `POST /api/v1/admin/purge-results`（admin，审计留痕）供调度；清理只抹数据载荷，元数据（状态/能力/时间）保留 |
| 用户与 Agent 读取分开 | 用户 = principal-scoped；Agent = 票据 + SEC-052 记录级重授权；幂等回放走同一 `stored_result`（留存/解密）+ SEC-049 重授权 |
| 加密不替代实时授权 | 明确写入模块 docstring 与 ADR-008：授权检查永远先于解密 |

ADR-008（本轮新增）：**结果生命周期的单一读出口**——所有读取路径
（详情、幂等回放）都经 `stored_result()`，留存判定与解密只有一处实现。

## 4. PR-NET-13 前置：组合结构 fail-closed

在受限 `$ref` 解析器（PR-NET-13 本体）落地前，`allOf / oneOf / anyOf /
not` 与 `$ref` 同等对待为**不可解释 schema**：

- 任何 schema 节点含组合关键字 → 值整体拒绝（`<uninterpretable_schema>`）；
- 根节点含组合关键字 → 整个响应拒绝（同 SEC-051 根 `$ref`）；
- 数组 `items` 含组合关键字 → 数组拒绝；
- 测试覆盖三层（根 / items / 嵌套节点）。

PR-NET-13 完成受限解析器后，这些路径转为真正的解析语义。

## 5. 路线图（更新）

```
已完成: v0.1.3.1 + 本轮（SEC-052/053、PR-DATA-12、NET-13 前置拒绝）
下一步: PR-NET-13   $ref/allOf/oneOf/anyOf 受限解析 + 循环/深度约束
        PR-DEP-14   部署面出口强制约束文档与配置
        PR-QA-06    PostgreSQL CI 矩阵 + 并发事务验证
        全链路综合审计（存储/授权/网络/真实凭证替代物）
此后:   OpenBao/Keyring → OIDC/资源级授权 → Collector
```

同意评审的方向判断：小补丁循环到此为止，剩余工作按上述四个整块推进，
然后做一次覆盖存储、授权、网络与真实凭证全链路的综合审计。

## 6. 验证记录

- `uv run -m pytest`：**217 passed**（SEC-052/053 + PR-DATA-12 生命周期
  + 组合结构 + SEC-030 生产密钥校验扩展，共 +8）
- ruff / mypy（44 源文件）/ `check-no-secrets.py`：全部通过
- 迁移链：`… → c8d31a90f4e2 → d4e7b91c6a30`
- 撤销不变量现状：新调用 / 幂等缓存 / 历史查询（含"缩小未撤销"）/
  Schema 回退（含组合结构）四路径全部拒绝

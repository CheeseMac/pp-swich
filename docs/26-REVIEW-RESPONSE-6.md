# 26 · 第六轮评审回应（Review6 Response · DATA-15..17 + PR-NET-13）

> 对应评审：《pp-switch 第六轮复审：PR-DATA-12 数据生命周期验收》
> （2026-10-14，开发验收通过，批准进入 PR-NET-13）。
> 本轮交付：DATA-15/16/17 三项生产就绪修复 + PR-NET-13 受限解析器本体
> + 评审第三节要求的加密全链路综合测试。

## 0. 总览

| 评审项 | 核实 | 处理 |
| --- | --- | --- |
| DATA-15 旧明文不迁移不过期 | 属实 | 迁移 `f3a62c8e1b47` 清空存量明文 + 运行时兜底清扫 |
| DATA-16 清理无租户边界 / 吞错 | 属实 | purge 按 workspace 隔离 + 启动失败告警日志 |
| DATA-17 密钥轮换未设计 | 属实 | MultiFernet 读写窗口 + re-encrypt API + 失败区分 |
| 加密链路综合测试缺口 | 属实 | 真实调用→密文落库→HTTP 读取→TTL→清理全链路测试 |
| PR-NET-13 | 批准开工 | 本地 `$ref` 受限解析（循环/深度/外部引用拒绝）+ guard `allOf` 受限合并 |

全仓 **226 测试**全绿（+9），ruff / mypy（44 文件）/ 密钥扫描通过。

## 1. DATA-15：存量明文的升级路径

采纳评审的"安全优先"方案，双保险：

1. **迁移 `f3a62c8e1b47`**：`UPDATE invocations SET result = NULL WHERE
   result IS NOT NULL`——从 v0.1.3.1 数据库升级的瞬间，所有历史明文
   结果清空（仅保留执行元数据）；downgrade 刻意不可逆（清掉的明文
   不应被"恢复"）；
2. **运行时兜底**：`purge_expired_results` 的清扫条件扩展为
   "有载荷 且（已过期 **或** 无保留期限）"——新代码写入的行必然带
   `result_expires_at`，所以"无期限的明文"只可能是旧数据或异常路径
   产物，任何一次清理（启动/租户 API）都会抹掉它们。

验收标准达成：升级后不存在无期限、可直接读取的历史明文。

## 2. DATA-16：清理的租户边界与可观测性

- `purge_expired_results(session, workspace_id=None)`：admin API 固定
  传 `auth.workspace_id`（租户内清理）；不带 scope 的全局清扫仅保留
  给启动时的内部维护任务——正是评审建议的双入口模型；
- SQL 改写为 SQLAlchemy Core 表达式（typed columns，无字符串拼接）；
- 启动清理失败从 `suppress(Exception)` 改为**记录告警**
  （`ppswitch.control.results` logger：`startup purge FAILED - expired
  payloads remain stored`），成功记 info——评审担心的"读取已拒绝但
  密文滞留"现在可被发现；
- 测试验证：A 租户 admin 的清理动不了 B 租户的行；全局清扫可以；
  member 调用清理 API 403。

## 3. DATA-17：密钥轮换

按评审建议的"MultiFernet + 重加密 + 失败区分"落地：

- `PP_SWITCH_RESULT_ENCRYPTION_PREVIOUS_KEYS`（逗号分隔）：旧密钥读
  窗口；写入恒用 primary（`result_encryption_key`），读取依次尝试
  primary → previous——轮换不孤儿化存量密文；
- `POST /api/v1/admin/re-encrypt-results`（admin，租户内）：批量把
  密文重加密到 primary，返回 `{rekeyed, failed}` 并审计——轮换完成
  确认的依据，之后可撤下旧密钥；
- 解密失败在 `stored_result` 记录 `result_decrypt_failed`（分类
  decrypt_error），与过期（读 None，无日志）、未保存（无载荷）、
  legacy 明文（`legacy_plaintext_result_read`）区分——API 响应对
  四者一视同仁（都是 None），只有日志能区分，符合"不给调用方探测
  通道"的原则；
- 测试覆盖完整轮换周期：KEY_A 写入 → KEY_B primary + KEY_A 读窗口
  → 旧密文可读 → re-encrypt → 撤掉 KEY_A 仍可读、KEY_A 单独解不开。

评审提的 `key_id` 密文版本标识：Fernet token 自带版本头且
MultiFernet 的顺序语义已覆盖需求，独立 key_id 前缀记录为审计阶段的
可选增强（docs/26 §5）。

## 4. PR-NET-13：受限解析器（本体）

### 4.1 导入侧：本地 `$ref` 有界解析（`openapi.py`）

- `resolve_local_refs`：仅解析 `#/...` 文档内指针（JSON Pointer 含
  `~0/~1` 转义）；**外部/URL 引用永不解引用**（不发起任何网络）；
- **循环**：解析栈命中即失败；**深度**：上限 8 层；
- 解析失败的 operation → 契约置空 + `needs_review=True`（进入人工
  审核队列，绝不静默入库坏契约）；
- 解析先于清洗：内联后的 schema 仍过 `sanitize_schema`。
- 测试：嵌套 `$ref`（Customer→Tier）完全内联且 enum 保留；循环与
  外部引用都落 needs_review。

### 4.2 执行侧：`allOf` 受限合并（`response_guard.py`）

- 统一的 `_normalized_schema()`：`$ref`/`oneOf`/`anyOf`/`not` 仍为
  不可解释（值驱动分支选择 = 猜测，保持拒绝）；
- `allOf` **仅**支持"纯对象分支全合并"（每支仅
  `{type: object, properties, required}`，无嵌套组合/引用，无兄弟
  键）→ properties 并集 + required 并集，随后走严格投影；
- 其余 allOf 形状（嵌套组合、非对象分支、带兄弟键）维持拒绝；
- 修复过程中发现并纠正了一个真 bug：根节点归一化结果此前没有
  替换后续分支判断的输入，导致合并 schema 掉进 legacy 白名单路径
  ——现有测试矩阵（含 SEC-035 类型门）全部复验通过；
- 测试：合并投影 + 敏感名（secret）在合并后依然任意深度拒绝；
  嵌套 `anyOf` 的 allOf 分支保持拒绝。

## 5. ADR 补录与待办

- **ADR-009（本轮）**：结果读取的四种 None（过期/未保存/解密失败/
  legacy）对外不可区分，仅在服务端日志分类——调用方不应有探测通道。
- 待办（进入综合审计阶段）：`classify_result` 目前以 capability
  risk_level 为代理（评审已指出低风险工具也可能返回敏感数据），
  独立的结果内容分级策略与密文 key_id 前缀列为审计阶段增强项。

## 6. 路线图

```
已完成: 本轮（DATA-15..17、PR-NET-13、加密全链路测试）
下一步: PR-DEP-14   部署面出口强制约束（评审已把 DATA-16 的告警部分
                    归入本块 - 告警日志已就位，出口控制待做）
        PR-QA-06    PostgreSQL 矩阵 + 并发事务 + 迁移/轮换的 PG 级验证
        全链路综合审计（合成凭证全链路演练）
此后:   OpenBao/Keyring → OIDC/资源级授权 → Collector
```

## 7. 验证记录

- `uv run -m pytest`：**226 passed**（DATA-15/16/17 + NET-13 + 加密
  全链路，共 +9）
- ruff / mypy（44 源文件）/ `check-no-secrets.py`：全部通过
- 迁移链：`… → d4e7b91c6a30 → f3a62c8e1b47`
- 加密全链路测试（评审第三节场景）：真实 HTTP 调用 → 即时响应带
  结果而库中仅密文 → 历史读取透明解密 → TTL 过期读空 → purge 抹除

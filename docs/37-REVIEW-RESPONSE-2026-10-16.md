# 37 · 评审回应（2026-10-16 审批续签）

> 基线：评审基线 `3f6c6c5`。本文回应两项定向要求（连接所有权缺口、
> Agent+Keyring 组合验收），并更新 docs/36 自列待补项状态。
> 评审的两处"测试通过 ≠ 验收标准被证明"的区分完全成立，照单接受。

## 1. 指控核实（先核实再修的惯例）

**指控一：跨用户连接所有权缺口——属实。**
`activate_connection()` / `revoke_connection()` 仅检查 workspace 归属，
无 owner 校验；`GET /connections` 列出全 workspace 连接。同 workspace
的 member（connections.write 最低角色 0）确实可以找到并激活/撤销他人
连接。这是 FEAT-03 新接口的接线缺陷，此前矩阵测试只覆盖了跨
workspace（Bob）与角色维度，没有同 workspace 双人维度。

**指控二：组合验收缺失——属实。**
`TestFeat03KeyringProfiles` 用的是 Alice 人类令牌 + personal 上下文；
`TestFeat05AAgentCallCloseout` 的 agent 调用固定在 Profile A。"同一
Agent 切换两套凭证"的组合证据此前不存在。

## 2. 修复内容

### 2.1 连接所有权（定向修复，逐字落实）

- `activate_connection` / `revoke_connection`：新增
  `owner_principal_id == principal_id` 校验；非所有者与跨 workspace
  同样返回 **404 CONNECTION_NOT_FOUND（不暴露存在性）**。admin 也不
  豁免（评审原文"统一增加 owner 校验"）；
- `GET /connections`：非 admin **只返回本人连接**；admin 保留全量
  运维视图；
- **负向回归**（`test_connection_ownership.py` ×3）：同 workspace
  两人类 principal——非所有者（member 与 reviewer 均测）激活/撤销
  他人连接一律 404；member 列表只含本人连接、admin 全量；owner 自身
  流程不受影响（激活 fail-closed 语义与撤销均正常）。

### 2.2 Agent + Keyring 组合验收（评审原文剧本逐条）

新增 `TestFeat03AgentKeyringCombo`（真实服务器栈，进程内假 Keyring）：

| 剧本要求 | 断言 |
| --- | --- |
| 同一 Agent 身份 / 同一已发布 MCP 工具 / 同一授权 + 有效委托票据 | 单 agent principal + 单 grant + 单 delegation（票据签发并有效） |
| 绑定 Keyring Profile A 执行 | `crm.get_customer(C-1024)` → **Acme Industries** |
| **切换 Agent 上下文**（`agent:<id>` 绑定，版本递增）到 Profile B 再执行 | 同一工具 → **Acme Industries (QA copy)** |
| 撤销 B 后 Agent 后续调用被拒绝 | 撤销连接 B → 第三次调用失败 |
| 任何返回不含凭证 | 两次结果 JSON 无 MOCKKEY |

一次通过。

### 2.3 措辞更正（评审指出的事实精度）

- FEAT-05A 收尾证据中的 tools/call 走的是 `build_tool_function()`
  注册的函数（适配器同一路径），**非外部 stdio 客户端完整协议帧**；
  成功调用依赖单活动委托自动选择而非显式票据传参——该逻辑有既有
  设计依据，本文仅把证据措辞说准，不改变结论。外部客户端实机拉起
  仍列人工验收项；
- docs/36 §1 中 FEAT-03 相关行已按本文更新。

## 3. docs/36 自列待补项状态（应用户要求汇总）

| 待补项 | 状态 |
| --- | --- |
| ① agent 绑定切换 + 双凭证组合 e2e | ✅ 本 PR 关闭（§2.2） |
| ② UI 两 Profile 切换演示截图 | ✅ 本 PR 关闭：[connections-keyring.png](assets/feat03/connections-keyring.png)（两把真实 OS keyring 凭证连接 active，隐藏显示）、[profiles-switched-keyring-A.png](assets/feat03/profiles-switched-keyring-A.png) → [profiles-switched-keyring-B.png](assets/feat03/profiles-switched-keyring-B.png) 一键切换（真实 Windows 凭据库 + `ppswitch-secret put` 真人路径，演示后已 delete 清理） |
| ③ Chrome 实机 / Claude·Cursor 实测 | ❌ 仍待用户手动（两项一直列在待人工验收清单，未变） |

## 4. 评审记录的多人部署限制（不做本轮修复，如实登记）

- `secret_name` 即可创建 `keyring://` 引用 + executor 共享命名空间：
  **单人本地模型接受；共享执行器前必须建立密钥引用与 owner 的绑定**
  （不能以"知道名字"为使用资格）。已在 connection_service 代码注释
  与本文登记，归入多用户部署前置项（多用户部署本身"暂不批准"）。

## 5. FEAT-04

按批复进入受限设计与开发：只读 PostgreSQL 发现闭环（受信 executor
只读账号 → schema/View 元数据 → 候选操作 → 既有审核发布链 → 授权 →
参数化只读调用）；验收要求（未批准 schema 不可发现/任意 SQL 与写操作
不可执行/超时行数大小受限/撤销即拒/口令零暴露/数据库目标单独获准）
已在本文登记为 FEAT-04 首切片验收清单。本 PR 不含 FEAT-04 代码。

## 6. 测试与门禁

Python 304（300 + ownership 3 + combo 1）+ vitest 31 = 335 项；
ruff / mypy / 密钥扫描 / 双端构建 / CI 结果见提交记录。

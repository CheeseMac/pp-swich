# 36 · 评审交接包入库（2026-10-15）

> 评审方（蓝图设计师）出具的正式审批与开发交接包已于 2026-10-16
> 归拢入库：**[docs/review-handoff/pp-switch-review-handoff-2026-10-15/](review-handoff/pp-switch-review-handoff-2026-10-15/00-START-HERE.md)**
> （含 `MANIFEST.sha256`，入库后逐文件校验 12/12 OK）。
> 包内含：批复决定日志、产品状态、Gate R1 清单、架构不变量、
> **FEAT-03 九步验收剧本**、下轮评审程序与开场模板、历史批复全文
> 与历轮开发者报告源材料。

## 0. 基线差（读包必看）

**包的制作基线是 `588ee48`（FEAT-05A），包内多处写"FEAT-03 尚未交付"。**
入库时仓库 main 已前进到 `3f6c6c5`，其中已包含：

- **FEAT-05A 收尾**（包 00-START-HERE 与 05 剧末"特别说明"要求的
  tools/call 真实调用验收）：`TestFeat05AAgentCallCloseout` 在真实
  服务器栈上证明了 tools/list 精确可见性、**tools/call 真实执行**
  （agent 凭证 + 签名委托票据 → `GET /api/customers/C-1024` → 守卫
  结果）、未授权隐藏、授权过期拒绝；截图已脱敏、MCP 配置改 env 传
  令牌（docs/34 §7）；
- **FEAT-03 交付**（docs/35）：OS Keyring Provider（fail-closed）、
  `ppswitch-secret` CLI、激活前实解析、撤销断路、控制台 UI、e2e
  双凭证切换验收。

评审人请以仓库最新 main 为准（包内 00 也如此要求）。

## 1. 与包内 FEAT-03 验收剧本（05 号文档）的逐条核对

| 剧本步骤 | 仓库现状 |
| --- | --- |
| 1 两把低权限可撤销合成凭证，仅安全存储持明文 | ✅ e2e（进程内假 keyring 后端；真实 OS 后端经 CLI 手动路径）；UI 不回显 |
| 2 Connection/Profile A/B + 授权关系 | ✅ e2e 建立双连接双 Profile；授权/委托在 FEAT-05A e2e 覆盖 |
| 3 激活 A 调 `crm.get_customer` 后端确认 A 身份 | ✅ e2e（用户上下文，业务结果=数据集 A） |
| 4 UI 一键切换 B 再调同名工具，确认 B 身份 | ⚠️ **部分**：切换+双凭证已由**用户上下文** e2e 证明（Acme vs Acme QA copy）；**Agent 身份 + 切换后用 B** 的组合用例未单独覆盖（agent 绑定是独立 `agent:<id>` 上下文，切换需另 PUT；FEAT-05A e2e 的 agent 调用固定在 Profile A）→ 列为待补 |
| 5 撤销/禁用 B 后重试 | ✅ e2e（撤后切 B 直接 403，不回退 A） |
| 6 无授权/错 delegation/超 scope 拒绝 | ✅ 无授权工具 CAPABILITY_NOT_FOUND（e2e）；错 delegation/超 scope 由既有 matrix 测试（SEC-031…053）覆盖 |
| 7 Canary 全链防泄露 | ⚠️ 部分：调用结果断言无 MOCKKEY；系统性 canary 机制沿用既有测试（canary 常量与扫描），未为 FEAT-03 单独新增全链 canary |
| 8 存储不可用/版本冲突/并发 | ✅ 激活 fail-closed（503 不激活）；版本冲突/快照为既有语义（SEC-017 等） |
| 9 Chrome 或 Claude/Cursor 至少一个真实端到端 | ❌ 待用户手动（两项均在待人工验收清单） |
| 交付物：两 Profile 切换前后同名工具调用成功的演示截图 | ❌ 待补（e2e 证据已有，UI 演示截图未拍） |

**待补清单（下一 PR）**：① agent 上下文绑定切换 + 双凭证的组合 e2e
用例；② Connections/Profiles UI 的切换演示截图；③ 用户手动完成
Chrome 实机与 Claude/Cursor 实测。

> **2026-10-16 更新**：①② 已由 docs/37 的修复 PR 关闭（含组合验收
> `TestFeat03AgentKeyringCombo` 与 UI 演示截图）；③ 仍待用户手动。
> 同 PR 修复了 2026-10-16 评审发现的连接所有权缺口。

## 2. 包内其余要点（对下轮评审的提醒）

- 07-NEXT-SESSION-PROMPT.md 是**用户准备发给下一位评审人的开场
  模板**——本项目如收到按该模板发起的评审，按 docs/31 §1 的评审
  循环办理；
- 证据等级约定（正式审批/开发方报告/历史核验/待实机）——回应文档
  措辞须区分，不得把开发方报告写成独立复测；
- Gate R1 四项（DEP-04/SECRET-06/ID-07/DATA-08）维持独立上线闸门，
  FEAT-03 完成不等于 SECRET-06 生产通过（docs/35 §4 已同口径）。

## 3. 完整性

包以原始目录结构入库（含 `source/` 历史材料与 `MANIFEST.sha256`），
未做任何改写；入库后 `sha256sum -c` 全部 OK。

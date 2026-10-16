# 34 · FEAT-05A 交付记录：Agent 一键接入（MVP）

> 按 2026-10-15 批复的优先级调整：FEAT-05 提前（先于数据库发现），
> 与 FEAT-03（本地 Keyring，下一项）并行推进。本 PR 同时完成 FEAT-02
> 批复留下的两项体验修复。

## 0. 一句话

发布工具之后，控制台立刻能完成"接入 Agent"：新建/选择 Agent 身份 →
勾选授权已发布工具 → **一键生成可直接粘贴的 Claude / Cursor / 通用
MCP 配置**（含一次性令牌）→ 点"验证连接"确认该 Agent 恰好看到被授权
的工具。全程 Agent 拿不到任何底层凭证。

## 1. FEAT-02 批复的两项体验修复

1. **向导可返回**：「重新选择操作」打开选择列表且**保留未提交的表单
   配置**（同一操作显示"继续配置"，换操作才重置）——405 后不再需要
   刷新页面；
2. **修改路径清晰**：审核卡明示"改配置 → 重新选择 → 重建草稿（旧草稿
   在 Capabilities 页）；已发布版本不可变，改动需发新版本"。

## 2. FEAT-05A 交付内容

### 后端（两个端点，`agent_principal_service.py`）

- `POST /api/v1/agent-principals`（admin，矩阵新动作
  `agent_principals.write`）：创建外部 Agent 身份，返回**一次性合成
  令牌**（`dev-token-agent-` 前缀，SHA-256 落库；生产换 OIDC 受众，
  流程不变）；审计 `agent_principal.create`；
- `GET /api/v1/agent-principals`（admin）：Agent 目录（id/名称/令牌
  提示，**永不返回令牌**）。

### 前端（`AgentAccessCard` 共享组件）

挂两处：新导航页「Agent 接入」+ 发布向导第 5 步（批复要求的
"发布之后页面马上出现接入操作"）。流程：选/建 Agent → 勾选已发布
工具 → 授权 → 复制生成的配置片段（Claude Desktop / Cursor / 通用
stdio 三种，**按 mcp-adapter 真实参数生成**：`uv run ppswitch-mcp
--control-url … --token …`，非虚构 env）→「验证连接」以该 Agent
身份经与 MCP 适配器**完全相同的鉴权路径**列出可见工具。

### 测试（`test_agent_access.py` ×3）

- admin 建身份 → 一次性令牌可作为 agent principal 鉴权（空授权 →
  空清单）；目录永不回显令牌；
- reviewer/member/agent 建身份一律 403/404；
- 全链路：新建 → 授权已发布能力 → 该 Agent 清单**恰好只含被授权
  工具**。

## 3. 演示证据（真实栈）

[docs/assets/feat05/agent-one-click-access.png](assets/feat05/agent-one-click-access.png)：
新建 `cursor-local` Agent → 授权 `crm.post_api_customers_search` →
一次性令牌 + 三份 MCP 配置 → **✅ 连接成功，该 Agent 可见工具：
crm.post_api_customers_search**。向导"重新选择/继续配置"回归验证通过。

## 4. 安全边界

- 建 Agent 身份与授权都是 admin 动作；令牌一次性返回、只存哈希；
- Agent 可见性 = 已发布 ∩ 已授权（既有 SEC-045 语义，无放宽）；
- 生成的配置只含控制面地址与 Agent 令牌——**绝不包含**任何下游
  API 凭证（凭证只在受信执行器内出现，与 FEAT-01/02 一致）；
- 令牌是合成值（仓库密钥扫描白名单前缀），生产替换为 OIDC。

## 5. 已知边界（诚实清单）

- "验证连接"验证的是控制面鉴权与可见性（与 MCP 适配器同一链路的前
  半段）；**真实 Claude/Cursor 客户端拉起 stdio 适配器的端到端实测
  仍待用户执行**——生成的配置即为此准备；
- 令牌丢失只能新建 Agent（无找回——一次性语义）；
- 生产 OIDC 受众、令牌轮换归 FEAT-03/Gate R1；
- Agent 维度的调用统计页面未做（Invocations 页已有全局视图）。

## 6. 数字

Python 290（287 + agent_access 3）+ vitest 31 = 321 项测试；
ruff / mypy / 密钥扫描 / 双端构建全绿（CI 见提交记录）。


## 7. 批复收尾（2026-10-15 批复指出，同日补齐）

批复正确指出：演示授权的工具其路由在 Mock CRM 上返回 405——
"能列出工具"不等于"能调用工具"。已按批复的验收表在**真实服务器
栈**（e2e：control + executor + mock CRM 全真实进程）补齐：

| 验证项 | 结果 |
| --- | --- |
| Agent 鉴权 | ✅ 一次性令牌通过（零授权时清单为空） |
| MCP `tools/list` 语义 | ✅ 授权后清单**恰好只含**被授权工具 |
| MCP `tools/call` 语义 | ✅ 适配器注册的同一工具函数 + Agent 凭证 + 签名委托票据 → 真实 HTTP → Mock CRM → 守卫投影返回 Acme Industries |
| 未授权工具调用 | ✅ CAPABILITY_NOT_FOUND（存在性不泄露） |
| 授权过期后调用 | ✅ 拒绝 |

（tests/e2e/test_full_stack.py::TestFeat05AAgentCallCloseout）

配套整改：
- **截图脱敏**：批复提醒的令牌入截图问题已处理——docs/assets/feat05
  截图为页面级脱敏后重拍（令牌以"一次性显示·截图脱敏"占位，PNG 字节
  中无任何令牌）；
- **配置不再走命令行参数**：生成的 MCP 配置改为 env 传递
  （`PP_SWITCH_DEV_TOKEN`，适配器原生支持），进程列表/Shell 历史不再
  可见令牌；
- **页面措辞两态化**："连接与授权验证成功"（列出可见工具）与
  "工具调用验证"（需在 Claude/Cursor 实测，控制面链路已由 e2e 覆盖）
  分开表述，不再过度声明。

待用户人工验收：用 Claude Desktop 或 Cursor 按生成配置拉起一次
`ppswitch-mcp`（待办清单第一项）。

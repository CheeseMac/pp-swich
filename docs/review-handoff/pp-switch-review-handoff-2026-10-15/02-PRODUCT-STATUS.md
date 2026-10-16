# 核心产品功能现状与实现边界

产品目标：用户在日常网页及接口使用中归集 API；由人审核和发布稳定的业务工具；其他 Agent **只发现和调用自己被授权的工具**，后台认证材料不进入模型上下文。不是任意网页都能被自动变成可执行、可共享的 API。

| 模块 | 最新状态 | 主要证据线索 | 未完成 |
|---|---|---|---|
| FEAT-01 Browser API Collector | **开发验收通过** | `docs/32-FEAT-01-COLLECTOR.md`、跨 TS/Python golden 测试、Chrome MV3 / CDP | Chrome 正式安装、debugger 事件、SW 重启恢复实测 |
| FEAT-01-F1 可靠性收尾 | **开发方报告完成** | Token 转 `chrome.storage.session`、落盘再 flush、同源清空、默认当前 origin、5 个运行时测试 | 仍需要 Chrome 实测 |
| FEAT-02 发布向导 | **开发验收通过** | `ad9f5af`、`docs/33-FEAT-02-WIZARD.md`、`test_wizard_flow.py` | 旧草稿不可直接编辑；新版本替代；真实私有网页 API 不保证可执行 |
| FEAT-02 体验修复 | **开发方报告完成并浏览器回归** | 当前会话：重新选择操作保留同操作表单；修改路径提示 | 如新增回退路径要防状态污染 |
| FEAT-05A Agent 接入 MVP | **开发验收通过** | `588ee48`、`docs/34-FEAT-05A-AGENT-ACCESS.md`、Agent Access 页面实栈演示 | 真实 MCP `tools/call`、真实 Claude/Cursor stdio 实机 |
| FEAT-03 本地 Keyring + Profile | **批准立刻启动，尚未交付** | 最新批复的详细验收目标见 `05` | OS Keyring、凭证生命周期、两账号切换及合成凭证全链路 |
| FEAT-04 数据库自动发现 | **规划中** | 早期 FEAT 路线图 | PostgreSQL/MySQL 受限视图/固定查询的接口归集 |
| 企业生产能力 | **未准入** | Gate R1 `docs/30-GATE-R1-PROGRESS.md` | 企业身份、真实凭证、宿主机网络、备份恢复 |

## 已有链路（开发态）

```text
用户授权网页 / HAR / OpenAPI
  └─> 浏览器 Collector / Discovery import
       └─> Operation 候选（去重；仅结构化、脱敏观察）
            └─> 可视化 Wizard（参数、响应字段、Schema）
                 └─> 经活动 Profile + 可信 Executor 真实试调
                      └─> Review → Verify → Publish
                           └─> Agent Grant
                                └─> Public Tool Schema（只展示获授权已发布版本）
                                     └─> MCP / HTTP 调用
                                          └─> Executor / Response Guard
```

**采集 ≠ 可执行 ≠ 可发布 ≠ 任意 Agent 可见**。浏览器私有接口可能依赖 CSRF、会话及服务器部署细节，必须另行确认合法稳定的执行路径。

## FEAT-02：准确边界

- 新端点 `POST /operations/{id}/test`，属于人类权限动作 `operations.test`，不授给 Agent。
- 试调经当前调用者的 Active Profile 和已批准的 Executor，不使用绕过权限的直接请求。
- 参数白名单来自观察到的契约；输出由审核后的 schema / guard 投影；响应错误应如实显示。
- 演示成功路径：`GET /api/customers/{id}`，合成参数 `C-1024`，得到 `Acme Industries`，泄漏 canary 字段被剥除。
- `POST /api/customers/search` 曾真实返回 **405**，原因是当时 Mock CRM 未提供该路由。**不能将其在 FEAT-05A 的 `tools/list` 成功当作 `tools/call` 成功证据。**
- 发布版本不可变，修改需新草稿/新版本；最新体验修复允许返回操作选择并保留同一操作未提交表单。

## FEAT-05A：准确边界

- 新导航页“Agent 接入”，以及发布向导第 5 步共享 `AgentAccessCard`。
- 由 admin 新建 Agent Principal，一次性演示 Token（前缀 `dev-token-agent-`，持久化为哈希，后端不能重新显示原文）。
- 人类按具体 Capability 为 Agent 授权；工具目录只返回已发布且授权有效的 Public Tool View。
- 生成 Claude Desktop、Cursor 和通用 stdio 配置。开发方核对的 mcp-adapter 入口为：
  - CLI：`ppswitch-mcp --control-url … --token …`
  - 环境变量：`PP_SWITCH_CONTROL_URL`、`PP_SWITCH_DEV_TOKEN`
  - 仓库内执行示例采用 `uv run ppswitch-mcp ...`；实际使用以当前包与 UI 生成配置为准。
- “验证连接”通过相同的 Agent 身份鉴权路径，证明能列出授权工具，**不是外部 MCP 客户端真正完成 `tools/call`**。
- 避免将开发 Agent Token 误认为生产 OIDC 凭证；复制后的配置可能包含高敏感 Bearer，必须保护。

## 现阶段适合谁试

允许：项目开发者、本地开发态、Mock CRM、合成不敏感测试凭证、特意授权的可撤销低权限测试环境。  
不允许据此宣称：企业可正式托管高价值 API Key、所有第三方网页的私有接口都可稳定复现、任意 Claude/Cursor 客户端均实测兼容。

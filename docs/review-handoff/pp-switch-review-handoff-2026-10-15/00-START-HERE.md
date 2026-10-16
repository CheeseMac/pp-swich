# pp-switch 审批与开发交接总览

> **交接基线：2026-10-15（北京时间日期不作为提交时间依据）**  
> 仓库：<https://github.com/CheeseMac/pp-switch>  
> 最新一笔在当前对话中**由开发方报告**的交付：FEAT-05A，提交 `588ee48`。下一位评审人须以仓库当前 `main`/实际 HEAD 为准核实，不可默认该提交仍是最新 HEAD。

## 一分钟结论

**FEAT-01（API Collector）与 FEAT-02（发布向导）开发验收已获批准；FEAT-05A（Agent 一键接入 MVP）最新批复为「开发验收通过，可立即启动 FEAT-03」。** 对这三个模块的批准范围是开发态、合成数据、本地功能试用，**不是企业生产安全认证**。

目前已有的产品链路是：浏览器/HAR/OpenAPI 发现候选 API → Discovery 目录归集 → 可视化配置与真实试调 → 人类审核/验证/发布 → Agent 授权 → 生成 Claude Desktop、Cursor 或通用 stdio 的 MCP 配置 → 以 Agent 身份列出授权工具。

**最容易误解的差别：FEAT-05A 的「验证连接成功」目前证明 Agent 能通过鉴权并看到授权工具，不等于已证明真实 MCP `tools/call` 成功。** 上次演示选了 `crm.post_api_customers_search`，而此前 Mock CRM 的 `POST /api/customers/search` 曾返回 405；若未补路由，这个工具仍可能调用失败。先用已成功试调的 `GET /api/customers/{id}`（合成客户 `C-1024`）完成真实调用验收。

**仍未批准接入企业高敏感真实凭证**。Gate R1 仍有四项在开发方截至当时的报告中标为未完成：`DEP-04` 宿主机防火墙实测、`SECRET-06` 真实 SecretProvider、`ID-07` OIDC、`DATA-08` 多实例轮换与备份恢复。FEAT-03 可以开始研发、先用可撤销的合成测试凭证验收，但不自动解除这些生产门槛。

## 下一步正式授权

1. **FEAT-03：本地 OS Keyring + 真实 Profile 一键切换**——立即开工，使用两个独立测试凭证、两个 Profile、同名 Agent 工具，证明切换后使用正确凭证且绝无凭证明文进入工具结果/模型上下文。
2. **FEAT-05A 收尾**——真实 `tools/call`、拒绝未授权工具、撤权后拒绝；由用户亲自在 Claude Desktop/Cursor 拉起 stdio 适配器。
3. **FEAT-01 手工实机验收**——在 Chrome 安装扩展，检查 `chrome.debugger`、Service Worker 重启恢复、采集域名范围和提交结果。
4. 企业生产 Gate R1 暂不转为本轮功能开发的前置依赖；保持单独的上线闸门。

## 证据等级约定

- **正式审批**：历史或当前助手明确给出的「批准/有条件通过/暂不批准」，只在其原先声明的范围内有效。
- **开发方报告**：用户提交的功能、提交号、测试数、CI 成功、截图路径；若评审人未亲自核验，不能写成「独立复测通过」。
- **历史源码核验**：先前某一轮评审对当时提交所做的源码/CI 核验；不等于最新版本的持续保证。
- **待实机/待生产验证**：不能由 Mock、单元测试或 CI 自动推定通过。

本交接包基于当前对话中的交付报告及此前附带的历史对话制作；**生成交接包这一行为没有重新拉取 GitHub、执行 pytest、运行 Docker 或验证截图**。Windows 本地文件路径仅作仓库线索，不能视为下一个会话可直接访问的附件。

## 阅读顺序

`01-APPROVAL-DECISION-LOG.md` → `02-PRODUCT-STATUS.md` → `03-GATE-R1-AND-OPEN-ITEMS.md` → `05-FEAT03-IMPLEMENTATION-ACCEPTANCE.md` → `07-NEXT-SESSION-PROMPT.md`。

需要复核历史裁定措辞时，查阅 `source/historical-approval-fulltext.md` 与 `source/current-thread-approval-excerpts.md`；希望查看每轮开发者汇报，则查 `source/historical-developer-reports.md`。

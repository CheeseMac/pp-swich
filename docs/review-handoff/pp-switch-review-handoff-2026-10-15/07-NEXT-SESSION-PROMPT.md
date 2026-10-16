# 可直接复制给下一会话的开场消息

请担任 pp-switch 项目的**功能验收与安全边界审批人**，继续此前一系列正式评审。

仓库：<https://github.com/CheeseMac/pp-switch>。我已附上一份 `pp-switch-review-handoff-2026-10-15.zip`，请先读 `00-START-HERE.md`、`01-APPROVAL-DECISION-LOG.md`、`03-GATE-R1-AND-OPEN-ITEMS.md`、`05-FEAT03-IMPLEMENTATION-ACCEPTANCE.md`，有争议再核对 `source/` 的历史原文。不要默认本 ZIP 中报告的提交号就是仓库最新 HEAD，请以最新 main 为准。

**已经批复**：
- FEAT-01 Browser API Collector 开发验收通过；FEAT-01-F1 Service Worker 恢复修复由开发方报告已完成，仍待 Chrome 安装实测。
- FEAT-02 API 发现、可视化封装、试调、审核、发布向导开发验收通过，允许本地合成数据试用；提交 `ad9f5af` 是当时的报告。
- FEAT-05A Agent 一键接入 MVP 开发验收通过；提交 `588ee48` 是当时的报告。页面支持创建 Agent、授权 Published Tools、生成 Claude Desktop/Cursor/通用 stdio 配置，验证工具目录；但真实 stdio 客户端 `tools/call` 尚未充分实测。
- **FEAT-03 本地 Keyring + 两个真实 Profile 切换已获批准，立即开工。**

**不得误判**：`tools/list` 成功不等于 `tools/call` 成功。演示的 `crm.post_api_customers_search` 可能仍对 Mock 返回 405；优先测试之前成功的 `GET /api/customers/{id}` + `C-1024`。

**安全上线限制**：Pre-alpha/Developer Alpha 可试用；企业真实高敏感 API Key 的生产托管未获批准。Gate R1 开发方此前报告 4/8，未完成的是 DEP-04、SECRET-06、ID-07、DATA-08。FEAT-03 可以用可撤销的合成或低权限测试 Key 验收，但不是企业 SecretProvider 生产准入。

**本轮请优先做**：
1. 核对最新交付与实际代码/测试/CI，特别是已授权的 Agent 是否真实调用后端成功、未授权调用/撤权是否被拒绝；
2. 以用户体验为中心，评估 FEAT-03（OS Keyring、连接向导、Profile 一键切换、同名 Agent 工具以两个不同测试凭证成功执行、Canary 不泄露）；
3. 明确签署「开发验收/本地试用/企业生产」三个不同等级的结论；
4. 不再无休止地重复已经确认修复的历史安全问题；仅针对新功能确实出现的边界补测试。

如你无法独立访问仓库或复跑，请说明证据范围，**不要把开发者报告的 CI 绿或历史助手的源码核验当成你自己刚执行的独立验证**。最后给出简洁明确的批复和下一阶段单个最高优先级任务。

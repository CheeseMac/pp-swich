# 仓库证据与交付索引

本索引中的路径、提交号和测试数量来自历史对话/当前开发方汇报；ZIP 生成时**没有重新从 GitHub 下载文件**。相应仓库文件、截图及工作流需由下一会话独立核对。

## 项目入口

- GitHub：<https://github.com/CheeseMac/pp-switch>
- 建议从 `docs/31-HANDOVER.md` 阅读实际 HEAD 对应的交接基线，不能仅相信历史旧版 `docs/31`。
- README 是状态索引；历史报告反复要求 README 测试数与交付同步。

## 文档路径与用途

| 路径 | 历史主题 |
|---|---|
| `docs/20-IMPLEMENTATION-REVIEW.md` | 最初 9 项偏差、6 个需要拍板的问题 |
| `docs/21-...` | 第一阶段安全加固设计记录（历史仅给出编号 `docs/21`，**具体文件名未在材料中完整提供**） |
| `docs/22-REVIEW-RESPONSE-2.md` | 第二轮回应，ADR-002/003/004、审批调整 |
| `docs/23-REVIEW-RESPONSE-3.md` | `SEC-043～048` 与 ADR-005/006 |
| `docs/24-REVIEW-RESPONSE-4.md` | `SEC-049～051`、委托票据语义与 ADR-007 |
| `docs/25-REVIEW-RESPONSE-5.md` | `SEC-052/053`、PR-DATA-12、ADR-008 |
| `docs/26-REVIEW-RESPONSE-6.md` | `DATA-15/16/17`、受限解析、ADR-009 |
| `docs/27-REVIEW-RESPONSE-7.md` | NET-13-F1/F2 与 DEP-14 |
| `docs/28-DEPLOYMENT-AND-EGRESS.md` | 网络隔离部署、密钥轮换运维 |
| `docs/29-REVIEW-RESPONSE-8.md` | PostgreSQL / Docker 矩阵与 DEP-14-F1/F2 |
| `docs/30-GATE-R1-PROGRESS.md` | Production Readiness Gate R1 进度和剩余四项 |
| `docs/31-HANDOVER.md` | 开发交接总索引；应随 HEAD 更新 |
| `docs/32-FEAT-01-COLLECTOR.md` | Collector 功能、隐私模型、扩展本地安装 |
| `docs/33-FEAT-02-WIZARD.md` | 发布向导、真实试调、端到端测试 |
| `docs/34-FEAT-05A-AGENT-ACCESS.md` | Agent 一键接入、配置生成与浏览器演示 |

## 已报告关键提交（不是最新 HEAD 保证）

| 主题 | Commit |
|---|---|
| 初始 v0.1.0 | `e42e40a`（早期阶段） |
| v0.1.2 相关复核 | `627e159` |
| 六项权限边界修复 | `5448b94` |
| 撤销不变量 | `14d1744` |
| 数据生命周期 | `e28e3b9` |
| DATA 尾项/Schema 引用解析 | `4b68ef9` |
| 出口与部署 | `0f51d92` |
| QA-06 收尾 | `736f14a` |
| R1 网络 P0 交付 | `39a270f → 63638f0` |
| FEAT-01 交付 | `d207673 → 00fe7ed → 9e4f772 → d2b85db` |
| FEAT-02 交付 | `ad9f5af` |
| FEAT-05A 交付 | `588ee48` |

## 用户报告的功能证据

- FEAT-01：`docs/assets/feat01/console-discovery-collector.png`、`docs/assets/feat01/extension-popup.png`，脚本 `scripts/demo-collector-flow.py`，跨语言 golden 夹具 `tests/fixtures/collector-golden/observations.json`。
- FEAT-02：`docs/assets/feat02/wizard-configure.png`、`docs/assets/feat02/wizard-test-call.png`，端到端 `test_wizard_flow.py`。
- FEAT-05A：`docs/assets/feat05/agent-one-click-access.png`，在浏览器演示新建 `cursor-local`、授权 `crm.post_api_customers_search`、配置生成与目录验证。
- FEAT-05A 用户报告了 `290 pytest + 31 vitest`、CI 双绿；FEAT-02 报告了 `287 pytest + 31 vitest`、CI 双绿；FEAT-01 报告了 `279 pytest + 25 vitest`。
- 上述路径未在此 ZIP 内嵌截图，因**本次对话没有把相应仓库截图单独上传**。不要假设 Windows 绝对路径是可下载链接。

## 原始文本

- `source/historical-approval-fulltext.md`：从此次附带的完整历史材料中，按原文提取有实质审批内容的 assistant 回合。
- `source/historical-developer-reports.md`：从同份材料中提取项目交付汇报（不是新的事实核验）。
- `source/current-thread-approval-excerpts.md`：当前对话 FEAT-02、FEAT-05A 的裁定摘录与开发汇报摘要。

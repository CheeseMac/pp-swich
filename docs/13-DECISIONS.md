# 13 · 架构决策记录（ADR）与未定事项

此文件是架构会议的决定索引。变更时增加 ADR，不可无说明地推翻已决安全边界。

## ADR-001：使用 Capability-first，而不是 MCP-first

**状态**：Accepted  
**决定**：内部持久化 `Capability`，支持 HTTP/MCP 适配层。  
**原因**：MCP 是 Agent 调用协议，不负责自动发现未知 HTTP API，也不负责业务权限、授权凭证保管。  
**替代方案**：每个 API 都启一个 MCP Server。  
**拒绝理由**：版本治理、人员账号和统一审计难以集中管理。

## ADR-002：共享 API 定义，不共享 Connection

**状态**：Accepted  
**决定**：Observation/Operation/Capability 可以遵守可见性规则共享，`Connection`、Profile 的权限始终独立。  
**风险**：团队成员观察到同一接口不等于他们都能访问同样的数据。  
**测试**：跨用户 Connection 租借必须失败。

## ADR-003：一键切换的对象是 Profile，非浏览器 Cookie

**状态**：Accepted  
**决定**：ActivityBinding 在个人/Agent/部署的独立上下文切换，Execution 固定快照。  
**原因**：避免共享浏览器 Session、避免运行时身份混乱。  
**风险**：用户可能误以为切换 UI 即切换所有第三方站点登录；需要明确文案。

## ADR-004：首期以 HAR/OpenAPI 导入为主，不做全网主动抓包

**状态**：Accepted  
**原因**：权限较低、产品验证快、隐私风险更可控。  
**备选**：默认 mitmproxy 记录所有 HTTPS。  
**拒绝理由**：CA 权限大、隐私与安全风险高。浏览器扩展主动采集留到第二阶段。

## ADR-005：密钥留在可信 Executor，公共 SDK 不提供明文提取

**状态**：Accepted, P0  
**决定**：项目 `.env` 仅保存别名与非秘密配置；业务代码调用 pp switch Capability。  
**重要限制**：如果 AI Agent 与可访问生产的 App 同权限域，可滥用调用，需 OS/容器/VM 隔离。  
**备选**：运行时注入 env Token。  
**拒绝理由**：对能读进程环境的开发 Agent 无强密钥隔离，作为受限迁移模式而非默认。

## ADR-006：开发者无需 Kubernetes

**状态**：Accepted  
**决定**：本机与 Docker Compose 一等支持；K8s/Edge Connector 按需扩展。  
**原因**：目标系统可能是单机、局域网和纯域名服务，部署无关。

## ADR-007：单机 SQLite，团队 PostgreSQL

**状态**：Accepted with caution  
**决定**：SQLite 仅支持单机早期开发；组织/企业使用 PostgreSQL。  
**注意**：需要验证事务隔离、JSON、权限语义差异；RLS 是 PostgreSQL 企业可选增强，不替代服务端鉴权。

## ADR-008：审查前不得自动执行发现的生产写操作

**状态**：Accepted  
**决定**：AI 可以辅助生成接口草稿与说明；未经人类审核的工具不执行。  
**原因**：未知网页私有 API 可能引发业务副作用、账号风险和合同/法律问题。

## ADR-009：HTTP Executor 不支持任意目的地/任意 Header

**状态**：Accepted  
**决定**：所有执行使用已发布 Capability → 受审 Operation → 审核 Endpoint。  
**原因**：Agent 能调用任意 URL 等同把网关变成 SSRF 与数据外传渠道。

## ADR-010：官方 MCP SDK 与稳定内部契约

**状态**：Proposed  
**决定**：优先使用官方 Python SDK（固定版本），协议层与业务层隔离。  
**待确认**：使用时客户端兼容矩阵、streamable HTTP 与远程授权如何集成；由测试结果决定准确版本。

## ADR-011：企业密钥后端

**状态**：Proposed  
**候选**：OpenBao / KMS / OS Keyring；不自研加密实现。  
**未决事项**：OpenBao HA、数据恢复、密钥轮换策略、可用性与成本，需要部署模型进一步确定。

## ADR-012：企业开源策略

**状态**：Proposed  
**建议**：Apache-2.0，允许企业商业使用和二次开发。  
**待确认**：商标名可用性、LICENSE、依赖兼容、贡献者条款、治理团队。

## ADR 模板

```markdown
## ADR-NNN：标题
状态：Proposed / Accepted / Replaced
背景：
选择：
备选方案：
被拒理由：
安全影响：
可测试验收：
上线与回滚计划：
```

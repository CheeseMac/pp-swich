# 15 · 技术参考与 GitHub 研究清单

> 截至文档编写时（2026-10-13），以下为**需要进一步逐项验证版本和许可证**的技术候选及一手文档，不是最终依赖清单。每个项目是否成熟、支持何种协议，均以选型时的 Release Notes / CI 兼容测试为准。

## 1. API 发现与格式规范

| 名称 | 地址 | 用途 |
|---|---|---|
| OpenAPI | https://spec.openapis.org/oas/v3.1.1.html | 已有 HTTP 契约的规范基础 |
| HAR（HTTP Archive） | https://w3c.github.io/web-performance/specs/HAR/Overview.html | 浏览器网络记录的格式参考 |
| mitmproxy | https://github.com/mitmproxy/mitmproxy | 授权测试场景的流量代理 |
| mitmproxy2swagger | https://github.com/alufers/mitmproxy2swagger | HAR/流量到 OpenAPI 草稿 |
| Chrome DevTools Network API | https://developer.chrome.com/docs/extensions/reference/api/devtools/network | 浏览器开发者工具采集 |
| Chrome Debugger API | https://developer.chrome.com/docs/extensions/reference/api/debugger | 受用户授权的浏览器网络事件接口 |
| JSON Schema | https://json-schema.org/ | 输入/输出契约与验证 |
| SQLAlchemy Inspector | https://docs.sqlalchemy.org/en/20/core/reflection.html | 数据库结构反射 |

**备注**：HAR 和网页响应可能含有大量个人信息、Cookie、Token；所有导入组件必须做本地脱敏，不可无审查直接向模型发送源文件。

## 2. MCP / Agent 与 API Gateway

| 名称 | 地址 | 用途 |
|---|---|---|
| MCP Specification | https://modelcontextprotocol.io/specification | 模型工具调用协议 |
| Official Python MCP SDK | https://github.com/modelcontextprotocol/python-sdk | 官方 MCP Python 实现 |
| FastMCP | https://github.com/jlowin/fastmcp | Python 工具服务开发候选 |
| Agentgateway | https://github.com/agentgateway/agentgateway | 高规模 MCP/Agent 流量入口候选 |
| IBM ContextForge | https://github.com/IBM/mcp-context-forge | 工具注册/治理/联邦参考 |
| Apache APISIX | https://github.com/apache/apisix | 传统 API 网关和 OpenAPI/MCP 参考 |
| Higress | https://github.com/higress-group/higress | AI/API 网关参考 |

不把某一个大型网关作为 MVP 的强制依赖；pp swich 最独特的价值是**发现、共享、切换、秘密隔离与可信执行**。

## 3. 密钥管理与模型代理

| 名称 | 地址 | 用途 |
|---|---|---|
| OpenBao | https://github.com/openbao/openbao | 企业密钥存储、轮换、认证 |
| OpenBao AppRole | https://openbao.org/docs/auth/approle/ | 机器认证参考；SecretID 自身仍需保密 |
| LiteLLM | https://github.com/BerriAI/litellm | OpenAI 兼容模型代理与虚拟凭证参考 |
| LiteLLM Proxy Docs | https://docs.litellm.ai/docs/simple_proxy | 模型代理与模型别名 |
| Keycloak | https://github.com/keycloak/keycloak | OIDC 身份与权限集成候选 |
| OPA | https://github.com/open-policy-agent/opa | 复杂策略判断候选 |
| OWASP Secure Coding with AI | https://cheatsheetseries.owasp.org/cheatsheets/Secure_Coding_with_AI_Cheat_Sheet.html | AI 辅助开发中的密钥和沙箱边界 |

**注意**：现有模型代理通常提供“虚拟 Key”模式；虚拟 Key 是比供应商主密钥更有限的授权凭据，但仍属于秘密，若 AI 进程能读取，可能被滥用。pp swich 的默认强安全方式是受信任 Broker 在自己内部持有/使用下游凭证，公开 SDK 只发能力调用。

## 4. 其他候选

- [PostgREST](https://github.com/PostgREST/postgrest)：PostgreSQL → REST，可学习它怎样依赖数据库权限；
- [Apache Camel](https://github.com/apache/camel)：复杂异构企业系统集成，需按需决定是否引入 Java 适配服务；
- [Temporal](https://github.com/temporalio/temporal)：后期复杂审批和长时间工作流；
- [NATS](https://github.com/nats-io/nats-server)：后期跨网络安全任务通道；
- [OpenTelemetry](https://opentelemetry.io/docs/)：链路观测（日志需要脱敏）。

## 5. 借鉴与自研界限

| 应直接使用成熟组件 | 应由 pp swich 自研 |
|---|---|
| OAuth/OIDC 标准、TLS/KMS、数据库驱动 | API Observation 归一化/去重 |
| OpenAPI / JSON Schema 解析器 | 多人贡献的 Capability Registry |
| MCP SDK | Profile 一键切换/版本绑定 |
| PostgreSQL / SecretProvider 后端 | Secretless Capability SDK + Broker 契约 |
| 受信任 CLI / OS 身份工具 | UI 审核、风险管理与策略关联 |
| 依赖安全扫描和审计工具 | 连接身份共享边界和发布工作流 |

## 6. 使用前核查清单

- [ ] License 是否与 Apache-2.0 商业分发兼容；
- [ ] Release 是否维护活跃，是否有公开安全公告；
- [ ] SDK 与 Python 3.12+、OpenAPI 3.1/MCP 目标版本兼容；
- [ ] 是否会默认保存 Headers/Cookies/原始 HTTP 录制；
- [ ] 是否会将 Prompt/工具结果自动发给第三方服务；
- [ ] 是否有明确的本地部署路径与不可用降级；
- [ ] 是否需要强制 K8s 或额外复杂依赖。

# 16 · 给 AI Coding Agent / 贡献者的开发规则

> 此文件面向使用 AI 编码工具开发 `pp switch` 的贡献者。根目录的 `AGENTS.md` 应引用本文件。  
> **这些规则不能替代操作系统、容器、网络和 SecretProvider 的技术隔离。**

## 1. 开发 Agent 允许做的事

- 阅读本仓库公开源码、设计文档和合成 fixture。
- 编写 Pydantic Schema、API 路由、DB 迁移、Web UI 和 pytest 测试。
- 在 mock 服务和**没有真实密钥**的沙箱中运行测试。
- 生成 OpenAPI/MCP 工具契约草稿、技术文档和实施清单。
- 审查日志/测试时**只处理已脱敏数据**。

## 2. 禁止行为

- 不得读取 `.env`、`.env.*`、密钥/证书、OS Keyring、真实 HAR、真实浏览器 Cookie、管理员密钥面板或受信 Broker 的 secret store。
- 不得运行 `printenv`、`env`、`cat ~/.ssh/*`、查找 `**/*secret*` 等泄漏真实秘密的命令来“排错”。
- 不得修改代码或工具参数来绕过 `Capability`、授权、审批、Endpoint allowlist。
- 不得新增 `get_secret`、`export_secret`、`proxy_any_url`、`execute_any_sql`、`run_shell`。
- 不得在 IDE 自动索引、prompt、生成日志或 GitHub Issue 中包含真实凭证。
- 不得为了让测试通过就将高风险调用改为默认允许。
- 不得未经用户和目标系统所有者授权主动扫描/探测他人的系统。

## 3. 仓库内只使用非秘密配置

允许：
```dotenv
PP_SWITCH_WORKSPACE=demo
PP_SWITCH_PROFILE=crm-demo
PP_SWITCH_MODEL_ALIAS=llm.fast
```

不允许：
```dotenv
PROVIDER_API_KEY=real-value
OPENBAO_SECRET_ID=real-value
CRM_SESSION_COOKIE=real-value
```

`PP_SWITCH_PROFILE` 只是别名，不应通过它直接获得调用权限。调用必须通过可信 OS/服务身份和策略引擎。

## 4. 为功能编程时的推荐步骤

1. 阅读 `docs/00-START-HERE.md` 与对应专题。
2. 写出 3–5 条**可重复**的验收条件。
3. 先定义领域 Schema 和 JSON 契约，写单元测试。
4. 实现最小业务逻辑，避免把 MCP 语义耦合到核心领域。
5. 补充越权/跨租户/日志泄漏的反例测试。
6. 更新 OpenAPI、README 中已实现功能、ADR 和迁移。
7. 运行 CI 检查；如果环境不可用，说明具体未运行项目，不虚构结果。

## 5. 优先开发接口与拒绝项

| 请求 | 如何实现 |
|---|---|
| “让 Agent 调 CRM” | 注册 `crm.search_customer`，用 Secretless Executor 调用 |
| “自动使用不同账号” | 经授权一键切换 Profile + 固定 Invocation 快照 |
| “新增 API” | 先 Observation/Review/Publish，再可执行 |
| “把供应商 Token 交给模型” | 拒绝；使用 Capability 调用或服务端模型代理 |
| “快速提供任意 URL 请求” | 拒绝；创建有白名单的 Operation |
| “运行代码发现密码” | 拒绝；使用 synthetic fixture 和可信管理 UI |

## 6. 示例代码生成要求

- 公共 SDK 只能暴露 `invoke`、`list_capabilities`、`profile_status` 等受控操作；
- 内部 Executor 获取 SecretProvider 的代码不能被前端/CLI/MCP SDK import；
- 后端 HTTP 客户端默认关闭重定向、验证 TLS、设置超时/响应长度；
- 处理外部 JSON 时进行 Schema 验证、字段 allowlist 和日志脱敏；
- DB 连接使用最小权限 role；不暴露任意 SQL；
- 新 Capability 默认 `draft` 和 `deny`，不得默认 `published`/`allow`。

## 7. 完成开发任务后的交付格式

每个开发任务至少报告：
- 变更文件；
- 完成的行为；
- 运行过的测试及结果；
- 未运行测试的原因；
- 风险、兼容性影响和需要人工批准的事项。

不要假设示例代码已经上线或通过安全审计。

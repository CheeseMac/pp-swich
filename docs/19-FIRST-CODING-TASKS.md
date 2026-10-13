# 19 · 适合直接分配给 AI 编码助手的第一批开发任务

> 本文件可以被拆成一张张 GitHub Issue，供 Codex、Claude Code、Cursor 或人工开发者执行。  
> 这些任务**只可以在没有真实密钥的开发沙箱中执行**，不能读取或复用现有项目 `.env`。

## Task 001 — Create the Monorepo skeleton

**输入**：`docs/08-REPOSITORY-STRUCTURE.md` 和 `docs/00-START-HERE.md`。  
**期望**：创建 Python `uv` workspace、React/Vite 前端、统一 lint/pytest/mypy、CI，使用 mock 服务。  
**约束**：不安装会自动上传代码/网络请求的采集工具；不添加真实 API Key。  
**完成标准**：lint/typecheck/unit tests 能运行，CI 验证仓库没有合成蜜罐值泄漏。

## Task 002 — Define the first domain contracts

建立 `Workspace/Service/Endpoint/Observation/Operation/OperationVersion/Capability/Connection/Profile/ActiveBinding` 的 Pydantic v2 Models。
- ID 统一 UUID；
- 枚举状态明确；
- Profile 必须引用同 Service 的 Endpoint 与 Connection；
- 不在公共序列化中输出 `secret_ref`；
- 发布版能力不可变。

**完成标准**：schema 测试和跨 Service/Workspace 越权测试通过。

## Task 003 — Add migrations and repositories

参考 `docs/06-DATABASE.md`，用 SQLAlchemy 2 + Alembic 实现首批数据表。
- SQLite dev 能跑；
- PostgreSQL integration CI 能跑；
- 使用事务处理 ActiveBinding 版本冲突；
- 所有查询显式受 workspace/principal 限制。

**完成标准**：数据库从空库迁移、重复迁移和 Profile 并发测试通过。

## Task 004 — Sanitized HAR import

实现纯函数：
- `sanitize_observation(raw_request)`；
- `normalize_path(method, path)`；
- `infer_json_schema(observations)`；
- `fingerprint_operation(observation)`。

并提供合成 `tests/fixtures/synthetic-har/sample.har.json`。

**完成标准**：发现 2–3 个候选 Operation，凭证蜜罐不会存在数据库或日志中。

## Task 005 — Profile switch API

实现：
- `GET /api/v1/profiles`
- `GET /api/v1/active-bindings`
- `PUT /api/v1/active-bindings/{service_id}/{context_key}`

**完成标准**：已授权用户成功切换；并发冲突返回 409；跨用户 Profile 被拒绝；UI 显示新标签和安全状态。

## Task 006 — Trusted Mock Executor

先不要连接真实 CRM。创建 mock CRM `/api/customers/{id}` 和两个模拟账号数据作用域。
- Dispatcher 接受工具名/业务参数；
- 判断授权；
- 固定 Profile 快照；
- 执行器使用假 Connection 引用；
- 只输出 `id, display_name`；
- 写入不含秘密的审计事件。

**完成标准**：不需要任何真实 `.env` 变量即可完成调用。

## Task 007 — Secretless public SDK

设计：
```python
from ppswich import PPSwichClient

pp = PPSwichClient.from_local_broker()
result = pp.invoke("crm.get_customer", {"customer_id": "C-1024"})
```

首版使用 mock/in-memory Transport；未来加入 OS IPC 和 mTLS。
**不提供 public `get_secret` API**。清楚记录：SDK 认证仍未完成时，不能宣称生产安全。

**完成标准**：SDK 仅使用业务别名和参数，可在 mock 模式执行，包含调用错误测试。

## Task 008 — MCP adapter

使用锁定版本的官方 MCP SDK，把已发布 Capability 映射到 tool list / call。
- list 按 principal 过滤；
- call 走同一 Invocation Service；
- 拒绝额外 Header/URL 参数；
- 响应只返回白名单字段。

**完成标准**：标准 MCP 客户端调用同一 mock CRM，跨租户调用被拒绝。

## Task 009 — First UI

页面：
- `Services`；
- `Discovery`；
- `Capabilities`；
- `Connections`（不显示 raw secret）；
- `Profiles + one-click Switch`；
- `Invocation logs`。

**完成标准**：通过浏览器完成 HAR → API Draft → Capability Publish → Profile Switch → Mock Invoke。

## Task 010 — Security gates

加入 OWASP 相关 P0 测试：
- `CANARY_NOT_REAL_ABC123` 秘密值不进入模型请求/日志；
- SSRF 参数不可执行；
- Profile 权限不混淆；
- SecretProvider 失联时 fail-closed；
- 生产 Connection 不对 AI 代理沙箱可用；
- 代码仓库没有真实 `.env` 凭证依赖。

**完成标准**：CI 自动失败可泄露凭证/越权的 PR。

## 开始编码时可以给 Agent 的总任务说明

> 阅读 `AGENTS.md`、`docs/00-START-HERE.md`、`docs/02-ARCHITECTURE.md`、`docs/05-SECURITY.md`、`docs/18-SECRETLESS-DEVELOPMENT.md` 和 `docs/19-FIRST-CODING-TASKS.md`。请只执行 Task 001，然后 Task 002。使用 mock 数据，不尝试读取任何 `.env`、密钥或本机安全存储。完成后列出新增文件、测试命令、运行结果和未完成事项；遇到安全边界不清时选择拒绝默认和最小权限，不自行新增秘密导出或任意 URL 调用功能。

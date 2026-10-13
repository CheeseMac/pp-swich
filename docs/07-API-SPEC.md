# 07 · REST 与 MCP API 约定（v0.1）

## 1. 服务边界与基础协议

- 控制 API 基础路径：`/api/v1`；JSON/UTF-8；OpenAPI 自动生成后仍需人工审查。
- Agent HTTP 工具调用：`POST /api/v1/invocations`，必须具备已验证用户/Agent 身份。
- MCP 入口：`/mcp`（部署和官方 SDK 版本确认后确定准确传输路径），使用标准 `tools/list`/`tools/call` 等协议。
- 所有接口必须实现：`request_id`、统一错误码、按 principal 的访问控制和脱敏日志。
- HTTP Browser UI 使用 OIDC Session / SameSite HttpOnly Cookie + CSRF；远程 API 使用短时 OAuth Access Token，验证 JWT audience。**不要把 MCP 客户端 Token 直接转发给下游 API。**
- 本地 SDK 优先通过 OS IPC + 实际工作负载认证；不能把某个公开别名当成授权秘密。

## 2. 标准响应包装（控制 API）

成功：
```json
{
  "data": {
    "id": "op_01...",
    "status": "observed"
  },
  "request_id": "req_01..."
}
```

失败：
```json
{
  "error": {
    "code": "CAPABILITY_NOT_FOUND",
    "message": "Capability is not available",
    "details": {}
  },
  "request_id": "req_01..."
}
```

说明：上例 ID 仅为示例，不固定 ID 数据类型的具体外观；持久化数据库使用 UUID。管理 API 可提供 UUID 的字符串编码。

## 3. MVP 控制 API 列表

| Method | Route | 用途 / 角色 |
|---|---|---|
| `POST` | `/api/v1/workspaces` | 创建个人/团队工作区 |
| `GET` | `/api/v1/services` | 当前 workspace 服务目录 |
| `POST` | `/api/v1/services` | 新增服务 |
| `POST` | `/api/v1/services/{service_id}/endpoints` | 新增待审核 Endpoint |
| `POST` | `/api/v1/discoveries/imports` | 导入已脱敏 HAR/OpenAPI |
| `GET` | `/api/v1/discoveries/imports/{job_id}` | 查询导入任务 |
| `GET` | `/api/v1/operations` | 过滤候选 API |
| `GET` | `/api/v1/operations/{operation_id}` | 查看草稿契约与证据 |
| `POST` | `/api/v1/operations/{operation_id}/verify` | 记录验证结果 |
| `POST` | `/api/v1/capabilities` | 新建待审业务能力 |
| `POST` | `/api/v1/capabilities/{capability_id}/publish` | 发布签审后的不可变版本 |
| `GET` | `/api/v1/capabilities` | 有权看到的能力目录 |
| `POST` | `/api/v1/connections` | 创建 Connection 元数据；秘密由可信独立渠道写入 |
| `GET` | `/api/v1/connections` | 仅元信息，禁止返回 secret_ref/明文 |
| `POST` | `/api/v1/profiles` | 创建 Profile |
| `GET` | `/api/v1/profiles` | 当前用户有权使用的档案 |
| `GET` | `/api/v1/active-bindings` | 查看当前 Profile 选择 |
| `PUT` | `/api/v1/active-bindings/{service_id}/{context_key}` | 一键激活档案 |
| `POST` | `/api/v1/invocations` | 受控工具调用 |
| `GET` | `/api/v1/invocations/{invocation_id}` | 权限内查询结果 |
| `GET` | `/api/v1/audit-events` | 仅授权审核员查看脱敏事件 |

**明令禁止**公共管理 API：`GET /secrets/{id}/plaintext`、`POST /decrypt-any`、`POST /proxy-any-url`、`POST /execute-sql`。

## 4. 导入已脱敏 HAR

`POST /api/v1/discoveries/imports`

```json
{
  "workspace_id": "wk-demo",
  "source_type": "har",
  "service_id": "svc-crm",
  "consent": {
    "authorized_by_user": true,
    "scope": "crm.example.com",
    "share_level": "private"
  },
  "upload_handle": "opaque-staged-upload-id"
}
```

上传通过另一个受控 upload endpoint 使用大小和类型限制；文件必须先本地脱敏。`upload_handle` 不能让跨租户复用，必须设置短时有效期和一次性消费。

返回：
```json
{
  "data": {
    "job_id": "job-001",
    "state": "queued",
    "observations_created": 0
  },
  "request_id": "req-001"
}
```

解析器输出 Observation / 候选 Operation，并记录脱敏版本、源证据与评估时间。**未通过本地清洗的原始 HAR 应拒绝，而非静默接收和保留**。

## 5. Capability 发布

`POST /api/v1/capabilities/{id}/publish`

```json
{
  "version": "1.0.0",
  "operation_version_id": "opv-001",
  "policy_version": "policy-v1",
  "review_id": "review-001",
  "verified_evidence_ids": ["evidence-001"]
}
```

服务端验证 review 权限、操作验证证据、输出白名单、Endpoint 允许列表、权限策略。发布生成不可变内容摘要；风险升高需要新审批。

## 6. Profile 一键切换

`PUT /api/v1/active-bindings/{service_id}/{context_key}`

```json
{
  "profile_id": "profile-staging-001",
  "expected_version": 4
}
```

`principal_id` 和 `workspace_id` 从已验证会话取得，不由客户端传入并获得信任。

成功：
```json
{
  "data": {
    "service_id": "svc-crm",
    "context_key": "personal",
    "active_profile_id": "profile-staging-001",
    "version": 5,
    "display_label": "CRM / Staging"
  },
  "request_id": "req-100"
}
```

授权失败：`403 PROFILE_NOT_ALLOWED`；Profile 不存在或不可见：`404 PROFILE_NOT_FOUND`；并发冲突：`409 PROFILE_VERSION_CONFLICT`；切换生产需要审批：`409 APPROVAL_REQUIRED`（建议进一步使用明确的审批流程状态）。

**已开始调用**不会因为 UI 切换而改变 Profile；新调用固定新快照。

## 7. 调用已发布 Capability

`POST /api/v1/invocations`

```json
{
  "capability": "crm.get_customer",
  "version": "1.0.0",
  "context_key": "personal",
  "arguments": {
    "customer_id": "C-1024"
  },
  "idempotency_key": "unique-client-generated-key"
}
```

请求中不能出现 `token`, `Authorization`, `credential_ref`, `target_url`, `endpoint_id` 等保留敏感字段；此处 `arguments` 由各 Capability 的 `additionalProperties: false` 限制。

返回：
```json
{
  "data": {
    "invocation_id": "inv-001",
    "status": "succeeded",
    "result": {
      "id": "C-1024",
      "display_name": "Acme"
    },
    "profile_label": "CRM / Staging"
  },
  "request_id": "req-101"
}
```

高风险操作返回 `status=approval_required`、一次性 `approval_request_id`，不得立刻执行。

### 内部执行器接口（不公开）

`POST /internal/v1/execute` 仅 Control Dispatcher 对 Executor 开放；认证需要独立服务身份或签发给 Worker 的短期任务凭据。

内部请求携带：`invocation_id`、可信 principal 证明、capability_version_id、profile_snapshot_id、arguments_digest、deadline、nonce。Executor 必须二次验证，不能因为“内网地址”就信任。

## 8. MCP 对接语义

- `tools/list`：只列出当前 principal 已被授权、已发布、Profile 可用的工具。
- `tools/call`：传工具名及业务参数；不允许 model 传 Token、目标 URL 或任意 Header。
- 输出：仅经白名单投影的业务 JSON / 安全文本。
- 鉴权：对远程 MCP 使用与规范一致的 OAuth 资源受众/授权服务器设置。MCP 协议的授权要求与 Web 会话不可混用。
- 工具目录变化：提供客户端可用的刷新/变更通知机制；绝不悄悄让已停用的工具继续执行。
- 具体 MCP SDK 版本、HTTP 传输及 `tools/list` 行为，以 CI 锁定的官方 SDK 为准。

## 9. API 速率与成本控制

- 每 Principal/Agent/Service/Capability 的独立速率与日预算。
- 写操作默认单次限制和审批策略；不允许通过换 Profile 绕过额度。
- 对模型 API 代理追踪 token usage/budget，但日志不记录完整提示词，除非有明确组织授权和数据保留规则。
- 每个 Invocation 记录状态变化；超时结果不确定时不得盲目重试非幂等调用。

## 10. 错误码

| HTTP | code | 语义 |
|---:|---|---|
| 400 | BAD_REQUEST | JSON / 参数无效 |
| 401 | UNAUTHENTICATED | 未认证或凭据过期 |
| 403 | FORBIDDEN | 当前主体没有此操作权限 |
| 404 | CAPABILITY_NOT_FOUND | 工具不存在或不可见 |
| 404 | PROFILE_NOT_FOUND | Profile 不存在或不可见 |
| 409 | PROFILE_VERSION_CONFLICT | 一键切换版本冲突 |
| 409 | APPROVAL_REQUIRED | 必须进入可信审批流程 |
| 422 | SCHEMA_VIOLATION | 参数不符合输入 Schema |
| 429 | QUOTA_EXCEEDED | 超过速率/预算 |
| 502 | UPSTREAM_FAILURE | 已清洗的下游错误 |
| 503 | SECRET_PROVIDER_UNAVAILABLE | 密钥服务不可用，拒绝执行 |
| 504 | UPSTREAM_TIMEOUT | 下游超时，可能需要状态核验 |

所有外部错误消息必须为安全常量，不返回原始上游 HTML / 内部栈 / 密钥。

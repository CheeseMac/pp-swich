# 03 · API 自动发现、归集、去重和契约推断

## 0. 模块目标

将**经过合法授权的 API 观察**沉淀为可审核、可复用的 API 契约，支持个人、团队和企业持续维护同一份 Capability Registry。

**注意**：发现一个 API，不代表此 API 可在独立客户端里安全、稳定或合法地调用。网页可能依赖特定用户会话、CSRF、动态签名和浏览器上下文。未经验证的 API 只进入候选目录。

## 1. 数据源与第一阶段范围

| 来源 | 导入方式 | 阶段 | 约束 |
|---|---|---|---|
| OpenAPI 3.0/3.1 | 上传经过验证的 YAML/JSON | MVP | 不能盲信文档中的地址与密钥示例 |
| HAR | 本地清洗后上传 | MVP | 默认删除值，只保留结构；不保留 Cookie |
| Postman Collection | 解析请求结构 | MVP | environment secret 一律删除 |
| 数据库 Schema | 使用有限权限账号反射表/视图 | P1 | 只读发现；权限与列范围限制 |
| Chrome MV3 Collector | 自选 origin、主动开启、客户端脱敏 | P1 | 不默认监听全部浏览器活动 |
| 现有 RPC/IDL | 用户上传 Protobuf/IDL | P2 | 需明确执行协议和认证 |
| mitmproxy 流量代理 | 经授权测试环境人工录制 | 可选 | 不作为普通用户默认组件 |

不同数据源统一转换为不含秘密值的 `Observation`。

## 2. 隐私与授权先于 API 识别

采集前必须具有 `CaptureConsent`：
- 实施者和 workspace；
- 被批准 Service/Origin；
- 来源、时间、可见性范围；
- 明确的采集数据类型；
- 留存期限与共享许可；
- 用户可以停止进一步采集。

**不接受**未经授权批量扫描、不登录的私密资源探测、自动暴力探索隐藏端点等行为。

### 2.1 客户端最小化收集

永不传输原始 `Cookie`、`Authorization`、`Set-Cookie`、API Key、OAuth Token、密码、CSRF Token、签名和登录响应值。

对于 URL、path/query、body、GraphQL variables、response：
- 默认只保存 method、host 标识、归一化路径、字段名、类型、出现次数与有限状态码；
- Query 和 Body **只保存类型/结构**，不保存实际用户输入；
- 响应 **只保存字段结构**，不保存客户资料；
- 路径中的资源 ID 替换为模式候选；对于路径可能包含 PII 的内容采用更保守规则；
- 若发现潜在敏感字段且无法安全清洗，拒绝上传该 Observation；
- 中央接收服务再次验证脱敏版本与禁止字段。

不得假设 `mitmproxy2swagger`、普通 HAR 转换器或 Chrome 采集插件自带满足企业要求的隐私过滤器；pp swich 必须实现自己的脱敏管道并测试。

## 3. 统一 Observation 结构

```json
{
  "workspace_id": "w-demo",
  "source_type": "sanitized_har",
  "service_hint": "crm.example.com",
  "method": "GET",
  "path_template_candidate": "/api/customers/{customer_id}",
  "query_shape": {"page": "integer", "limit": "integer"},
  "request_media_type": "application/json",
  "response_status": 200,
  "response_shape": {
    "type": "object",
    "properties": {
      "id": {"type": "string"},
      "display_name": {"type": "string"}
    }
  },
  "redaction_version": "v1",
  "observed_at": "2026-10-13T10:00:00Z",
  "visibility": "private"
}
```

示例中的域名、日期、字段为虚构的结构数据。`service_hint` 和时间戳本身也可能属于组织内部信息，不能跨 Workspace 默认共享。

## 4. 归一化算法

输入：一批同 Service 的 Sanitized Observations。

### 4.1 指纹定义

建议：
```
OperationFingerprint =
  workspace_id +
  service_id +
  protocol_family +
  http_method +
  normalized_path +
  api_version +
  request_media_type +
  graphql_operation_name? 
```

不跨 Workspace 简单合并不同企业的信息。相同接口跨企业“技术上相似”不意味着有权共享其观察数据。只有对公共服务明确可共享的标准契约，才适合形成社区公共目录。

### 4.2 路径模式推断

样例：
```
GET /api/customers/120
GET /api/customers/231
GET /api/customers/843
    ↓
GET /api/customers/{customer_id}
```

规则：
1. 先按 segment 位置和 method 分组；
2. UUID、ULID、高熵 ID、符合条件的数值 ID 是参数候选；
3. `search`、`active`、`settings`、`v1` 等语义 segment 不应误判为 ID；
4. 当只有一个观察样本时，不应高置信推断动态路径；
5. 两种模板可能冲突时，标记 `needs_review`；
6. 不混合不同 HTTP Method、版本或 GraphQL `operationName`；
7. 已发布接口不会随新观察自动被覆盖。

### 4.3 Query 和 Body 归纳

- 统计字段出现次数：1 次观察不是“required”的充分证据；
- 区分不存在、null、空字符串、空数组；
- 将类型冲突标记为联合类型/待审，不强行变为 string；
- 识别分页参数候选 `page`, `limit`, `cursor`，但不直接推断业务语义；
- 认证 Header、会话 Cookie 永不进入公共 Tool Schema；
- GraphQL 依据 operationName/选择集/查询类型分组，不能所有 `/graphql` 都合并。

### 4.4 响应 Schema

只允许从**脱敏响应结构**中推断字段类型：
```json
{
  "type": "object",
  "properties": {
    "id": {"type": "string"},
    "name": {"type": "string"}
  },
  "additionalProperties": false
}
```

`additionalProperties: false` 对**最终发布的安全输出投影**适用，不必强行要求旧接口的原始响应不能增加新字段。Executor 最终输出时按 Capability allowlist 投影。

## 5. 对 AI 辅助发现的约束

可以让本地模型或受控云模型：
- 建议更好的语义工具名，例如 `crm.get_customer`；
- 依据接口字段写说明、生成契约测试草案；
- 推测疑似写操作和风险线索；
- 检测变更后可能受到影响的 Capability。

不能让模型：
- 自行把发现的 API 发布生产；
- 从名字判断 GET 一定无副作用；
- 在生产环境“试试各种参数”寻找隐藏接口；
- 接收原始 HAR、Token、Cookie、敏感业务样本；
- 建议绕过认证、CSRF、访问控制或目标协议限制。

## 6. 可执行性检查（Execution Readiness）

`Operation` 推进到 Verified 时需要有：
1. **合法授权证据**：可以调用、可以归档/分享的范围；
2. **受审 endpoint**：URL/域名/端口/环境固定；
3. **稳定契约**：参数 JSON Schema 和响应 Schema 确认；
4. **认证方式**：受支持且可用的后台授权方式；
5. **权限语义**：用户资源访问范围、Agent scope、租户边界；
6. **结果过滤**：只返回审核过的业务字段；
7. **副作用审查**：危险操作使用人工批准、幂等和金额/资源限制；
8. **测试证据**：优先 mock / sandbox / 官方文档，生产调用需严格审批。

无法脱离浏览器会话执行的 API 可保留候选记录，标记 `execution_supported=false`。**不得自动把用户 Cookie 搬给 Agent**。

## 7. 状态与数据变更

候选状态：
`observed → inferred → needs_review → verified → published`（发布实际属于 Capability）。

异常流程：
- `rejected`：无权使用或风险不可接受；
- `change_detected`：监测到 Schema 漂移，需重新审核；
- `deprecated`：旧契约即将停用；
- `disabled`：发现安全问题立即禁用发布能力。

只有 Reviewer 的签审动作才能让工具进入可调用目录。

## 8. 数据库 API 发现

- PostgreSQL/MySQL 等优先利用系统 catalog / SQLAlchemy Inspector；
- 不执行 Agent 生成的任意 SQL；
- 按管理员明确批准的 Schema/View/列暴露候选数据结构；
- 敏感列名/表名也应视为内部元数据；
- 创建只读、参数化的业务查询 Capability；
- 数据权限以数据库 RLS/视图/业务策略 + 平台 Agent 权限交集为准。

## 9. 自动化测试矩阵

| 用例 | 预期 |
|---|---|
| 3 个 `GET /users/{id}` 资源样本 | 聚合为一个候选 Operation |
| `/v1/users` 与 `/v2/users` | 不混合 |
| GraphQL 同一路径不同 operationName | 识别为不同操作 |
| 相同路径不同 HTTP Method | 不混合 |
| HAR 含 Cookie/Authorization/API Key | 中央数据库与日志无值 |
| 只有一次路径样本 | 低置信或要求审核 |
| 未知第三方私有 API | 候选，不自动发布/重放 |
| API Schema 变化 | 新版本或 change warning，不覆盖发布版 |
| 两个用户发现同一 Operation | 定义可协作，凭证绝不合并 |
| 试图上报未脱敏 HAR | 拒绝上传 |

## 10. 建议实现的纯函数

```text
sanitize_har_record(record) -> SanitizedObservation | Rejection
canonicalize_path(method, path, sibling_samples) -> PathCandidate
infer_parameter_schema(samples) -> JsonSchemaCandidate
infer_response_schema(samples) -> JsonSchemaCandidate
operation_fingerprint(observation, service_scope) -> str
merge_observations(observations) -> CandidateOperation
contract_diff(previous, observed) -> ChangeSet
```

先在 Python `packages/discovery-sdk` 实现这些纯函数，用全合成 fixture 验收；与 Chrome Extension/CLI 的采集逻辑解耦。

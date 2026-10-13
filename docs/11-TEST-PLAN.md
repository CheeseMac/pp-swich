# 11 · 测试与安全验收矩阵

## 1. 测试金字塔

- **Unit**：路径归一化、Schema 合并、Profile 作用域、Policy 决策、ResponseGuard。
- **Contract**：JSON Schema、OpenAPI、MCP tools/list & tools/call、版本兼容性。
- **Integration**：PostgreSQL 迁移、事务、Executor Mock HTTP、SecretProvider mock、权限。
- **E2E**：HAR 导入 → 审核 → 发布 → Profile 切换 → HTTP/MCP 调用 → 事件审计。
- **Security**：密钥外泄、SSRF、跨租户、越权、提示词注入、上下文混淆、凭证轮换。
- **Resilience**：超时、目标 500、Broker 失联、DB 并发、异常重试。
- **Performance**：导入大量 HAR、去重可扩展性、控制面延迟、执行器并发。

## 2. P0 测试清单（必须全部通过）

| 编号 | 风险点 | 输入场景 | 预期 |
|---|---|---|---|
| SEC-001 | 导入凭证 | HAR 中含 `Authorization: Bearer CANARY_SECRET_01` | DB/日志不含 Canary |
| SEC-002 | 浏览器 Cookie | HAR 带 Cookie/Set-Cookie | 清洗后不保存值 |
| SEC-003 | 输出泄露 | 后端响应多出 `token`、`secret` | MCP/REST 仅白名单字段 |
| SEC-004 | 身份伪造 | Tool 参数传别人的 user_id | 不参与身份决策 |
| SEC-005 | 账号借用 | 用户 B 激活用户 A 私有 Profile | 拒绝 |
| SEC-006 | 跨租户 | 组织 B 访问组织 A Capability | 不可见/拒绝 |
| SEC-007 | 端点 SSRF | 模型传 `http://169.254.169.254` | 422 或执行层拒绝 |
| SEC-008 | DNS Rebinding | 域名后解析到私网/元数据 IP | 阻断 |
| SEC-009 | 任意 Header | 模型传 `Authorization` 参数 | 422 |
| SEC-010 | 未发布调用 | 直接请求 draft Tool | 不可见/拒绝 |
| SEC-011 | 高风险操作 | 无审批调用退款工具 | `APPROVAL_REQUIRED` |
| SEC-012 | 幂等失败 | 写操作超时后无条件 retry | 不发生重复执行 |
| SEC-013 | Vault 失联 | SecretProvider 不可达 | fail closed |
| SEC-014 | Agent 读环境 | dev sandbox 中无真实 Token | 无生产秘密及网络能力 |
| SEC-015 | 进程滥用 | agent 改代码尝试调用 prod | 后端身份/权限阻断 |
| SEC-016 | Profile 切换并发 | 两个窗口同版本切换 | 一个成功，一个 409 |
| SEC-017 | 在途快照 | 执行中切换 Profile | 已开始请求不改变连接 |
| SEC-018 | 工具注入 | 恶意 API description 指挥读秘密 | 发布审核和服务端权限不升权 |
| SEC-019 | Token audience | MCP token 直接送旧 API | 不透传 |
| SEC-020 | 过度输出 | 后端返回 10MB 与敏感字段 | 超限/只保留合法字段 |

## 3. 合成敏感样本

fixture 中统一使用不可能与真实用户重合的明确标签：
```python
CANARY_API_KEY = "CANARY_NOT_REAL_ABC123"
CANARY_COOKIE = "CANARY_SESSION_NOT_REAL"
CANARY_PASSWORD = "CANARY_PASSWORD_NOT_REAL"
```

验证在下列结果中都不存在合成样本：
- 原始 central database JSONB、日志、异常、OTel Span attributes；
- MCP/HTTP 返回内容、工具描述、模型供应商请求；
- HAR 导入后的 Observation 和 OpenAPI export；
- GitHub issue/CI artifact/test snapshot。

**重要**：测试不得通过 `print(os.environ)` 或全量 Dump 生产环境来验证，使用严格构造的测试环境。

## 4. Schema / Normalization 测试

用 golden fixture 保持输入输出稳定：

| 输入 | 期望 |
|---|---|
| `/api/customer/100`, `/api/customer/200` | 合并为 `/api/customer/{id}` 候选 |
| `/api/v1/customer`, `/api/v2/customer` | 不混合 API 版本 |
| `/api/users/search` 和 `/api/users/123` | `search` 不变成任意资源 ID |
| `POST /graphql` 两种 operationName | 分开 Operation |
| 同一路由出现 int/string | 输出类型冲突标记待审 |
| 不同用户观察同一接口 | 不合并 Connection/Token |
| 请求依赖 session cookie | 标为非独立可执行，待审核 |

## 5. 性能和可靠性

建议在项目实现后确定目标，例如：
- 1 万条**合成、已脱敏** HAR Observation 的导入耗时；
- 目录搜索 P95；
- Profile 切换事务 P95；
- 10/100 并发模拟读请求，限制流量后行为；
- Broker/Executor 突然退出的状态恢复；
- 工具级配额、按用户限速、目标服务故障的熔断。

## 6. 发布准入清单

- [ ] CI 运行完整 P0 测试，任何安全红线失败阻断发布；
- [ ] 受控环境下完成秘钥轮换与撤销演练；
- [ ] 结构化审计具备关联 `request_id` 和 `invocation_id`；
- [ ] 已发布 Capability 的输入输出契约可追溯；
- [ ] 安全测试包含 Agent 修改代码/调用参数的威胁场景；
- [ ] 生产和开发档案、SecretProvider 身份明确隔离；
- [ ] README 对未实现功能没有误导性宣称。

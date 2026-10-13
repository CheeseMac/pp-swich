# 12 · 产品信息架构、页面流程与“一键 Switch”体验

## 1. 产品定位如何体现在交互上

`pp switch` 不能变成单纯的“密钥列表工具”，它应提供从**API 发现 → 管理 → 授权 → Profile 切换 → Agent 调用**的完整体验。

主导航（第一版）：
- `Dashboard`：近期发现、发布数、当前活动 Profile、异常告警；
- `Services`：域名、系统、API/数据库入口；
- `Discovery`：导入 HAR/OpenAPI、去重候选、Schema diff；
- `Capabilities`：待审核/已发布的 Agent Tool；
- `Connections`：我的授权身份（只显示标签和状态，不展示秘密）；
- `Profiles`：账户 + 环境 + Endpoint 的切换目录；
- `Agents`：工具可见性、权限、调用历史；
- `Security`：审批、凭证状态、日志与权限策略；
- `Settings`：Workspace、用户和组织治理。

## 2. 首页核心信息

Dashboard 建议有三块重要区域：
1. 顶部“一键 Profile Switch Bar”：当前工作区、所选 Service、活动档案、安全状态；
2. 中部“我的可用 Capability”：当前身份可用的只读/写入能力；
3. 右侧“新发现待审核 API”：按 Service 汇总需要批准的候选接口。

不要把 `secret_ref`、真实 API Key、认证 Header 或完整用户 Cookie 显示在任何普通 UI 视图。

## 3. 一键 Switch 交互详细规范

### 3.1 当前状态示例

```
Workspace: Personal
Service: CRM
Context: Personal
Current Profile: Work / Staging / Alice
Status: Connected
[ Switch ▼ ]
```

点开后：
```
Choose Profile
  ✓ Work / Staging / Alice
    Work / Production / Alice     [approval required]
    Demo / Sandbox                [available]
    Team Shared                   [requires membership]
```

用户选另一个 Profile：
- Web 调用 `PUT /active-bindings/{service_id}/{context_key}`；
- 请求包含 `profile_id` 与读取时获取的 `expected_version`；
- 后端校验身份、Connection 授权、Endpoint 环境、切换风险、审计；
- 返回新绑定版本和标签；
- UI 切换成功后即时刷新工具可见性/权限；
- 若 409 并发冲突，显示“绑定已被其他会话更新”，重新加载状态；
- 正在执行的工具继续使用固定的旧 Profile 快照。

### 3.2 Profile 切换边界

- `personal` 为个人默认选择，不影响其他人的 Profile。
- `agent:<id>` 需要对应 Agent 的明确授权，不允许擅自命名成别人的 ID。
- `deployment:<id>` 属于正式应用发布流程，**不能允许个人 UI 一键改变整个企业生产实例**。
- 切换高风险 `production` 环境默认弹出风险确认，并可能要求单独审批。
- UI 必须明确展示：Service、账号标签、环境、授权状态；不能仅用颜色区分生产/测试。

## 4. 新增 API 导入向导

```text
Step 1: Choose Source
  [Upload sanitized HAR] [Import OpenAPI] [Import Postman] [DB schema (future)]
    ↓
Step 2: Scope & Consent
  Choose Service, allowed origin, privacy scope, consent
    ↓
Step 3: Preview & Redaction
  Verify data minimization; secret warning must be resolved
    ↓
Step 4: Normalize / Deduplicate
  Group HTTP methods/paths/query schemas; display confidence
    ↓
Step 5: Review Contract
  Name operation, classify read/write, define params/results
    ↓
Step 6: Verification
  Owner approval, test evidence, expected target auth mechanism
    ↓
Step 7: Publish Agent Capability
  Restricted description / tool schema / policy / output allowlist
```

**录制不应默认公开**；上传前必须让用户看见有哪些域名和信息类型将被归集。所有未经审查的 API 都留在 Draft。

## 5. 新增 Connection / Secret 的体验

- 用户先选择 `Service` 和认证类型（API Key、OAuth、机器身份等）；
- `API Key` 需要通过可信密钥输入面板、系统凭证界面或企业安全流程录入；
- Control 面板只能得到 `connection_id`、授权范围与 `configured=true`，不能再显示 Key；
- 对 OAuth 使用标准的授权回调/PKCE 与用户同意；
- 不提供“复制所有凭证到 .env”功能；
- 如果是 AI 开发 Agent 正在运行的终端/IDE，显示明确警告：**不要在 Agent 可读取的终端输入生产 Key**；
- 切换到 Secretless 开发需要受信 Broker 身份验证和网络/OS 隔离。

## 6. `Secretless ✓` 状态是否显示

仅以下条件被验证时，才展示绿色“通过”：
- 源码工作区、运行配置和 Agent 工作区未检出已知真实密钥（静态扫描非充分条件）；
- 后端有明确的隔离执行身份；
- 用户/Agent 的开发身份无法获取生产 secret；
- 生产 Endpoint 与开发 sandbox 网络隔离已被验证；
- 公共 SDK 没有密钥导出 API；
- 最近一次 Canary 泄漏测试通过。

否则显示：
- `Not verified`：未验证；
- `Risk: shared execution identity`：与 AI Agent 同一权限域；
- `Risk: production connection available`：开发代理仍可调用生产服务。

此标志是产品 UI 的安全状态摘要，不是不可攻破的安全认证。

## 7. MCP Tool Explorer

每个 Capability 页显示：
- 名称、语义描述、版本、风险、输入 Schema、输出 Schema；
- 对当前用户/Agent 是否可见；
- 所需最小 Scope、是否需要人工审批；
- 接入方式：MCP / HTTP / Python SDK；
- 安全测试状态与最近一次契约验证时间。

可提供“使用 mock profile 测试”按钮；绝不能在浏览器页面直接注入后端真实 API Key。

## 8. 错误提示原则

| 错误 | 建议 UI 文案 |
|---|---|
| `PROFILE_NOT_FOUND` | 当前身份无法使用此配置档案 |
| `PROFILE_VERSION_CONFLICT` | 配置已发生变化，请重新选择 |
| `CONNECTION_EXPIRED` | 连接授权已过期，请重新授权 |
| `SECRET_PROVIDER_UNAVAILABLE` | 受保护的连接暂时不可用；未执行真实请求 |
| `CAPABILITY_NOT_FOUND` | 当前身份下该能力不可用 |
| `APPROVAL_REQUIRED` | 操作需要可信审批，提交后才能继续 |

错误消息不显示内部 Host、Token、Cookie、Request Body 或响应原文。

## 9. 国际化和可访问性

- 中文为主要启动文档语言，UI 国际化从第一天支持 `zh-CN / en-US` 词条；
- 所有按钮、审批、风险提示提供可读文本，不依赖颜色；
- 切换功能支持键盘与屏幕阅读器；
- 危险操作的身份和环境标签应在确认弹窗中重复展示。

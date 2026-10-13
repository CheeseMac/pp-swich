# 22 · 第二轮评审回应（Review2 Response）

> 对应评审：《pp-switch 最新代码复核报告》（2026-10-13 第二轮）。
> 本轮结论"安全加固有实质进展，仍不建议接入企业真实密钥"——我们接受该结论，
> 并在本轮修复中把评审列出的 5 个 P0、R-06..R-14 全部处理完毕。
> 回应格式与 docs/21 一致：逐项说明改了什么、在哪个层次改的、如何验证。

## 0. 总览

| 评审项 | 结论 | 处理 |
| --- | --- | --- |
| P0-1 管理接口权限过宽 | 属实 | 全路由权限矩阵 `require_permission`，默认拒绝 |
| P0-2 共享 Agent 绑定混用 | 属实 | Delegation 表 + 绑定写入委托用户作用域 |
| P0-3 DNS 未真正钉扎 | 属实 | `PinningNetworkBackend` 连接层钉扎已验证 IP |
| P0-4 类型不匹配仍放行 | 属实 | 类型门先行的默认拒绝投影 + allow_fields 交集 |
| P0-5 OpenAPI 示例值泄密 | 属实 | 导入期 Schema 清洗 + canary 回归 |
| R-06 scopes 不参与授权 | 属实 | grant ∩ connection ∩ delegation 交集生效 |
| R-07 幂等回放先于鉴权 | 属实 | 回放移到 grant/delegation 校验之后 |
| R-08 上传先整读后检查 | 属实 | 分块限额读取，内存上界 limit+chunk |
| R-09 MCP 签名参数顺序 | 属实（已复现） | 必填参数强制前置 |
| R-10 工具名静默覆盖 | 属实 | 发布时 409 + 注册时抛错，双端检查 |
| R-11 可见性未过滤 | 属实 | private=owner/admin，team/org=工作区 |
| R-12 调用结果留存 | 部分接受 | 见 §3.4，排期 v0.2 数据分级 |
| R-13 审核者非正式角色 | 属实 | member/reviewer/admin 角色 + 权限矩阵 |
| R-14 仅支持 JSON | 属实 | `yaml.safe_load` 受限回退 |

新增回归测试 **SEC-031..SEC-042**（12 项全部落地），全仓 188 测试、
ruff、mypy、密钥扫描全绿。

## 1. P0 逐项回应

### P0-1 管理面权限（SEC-031 / SEC-032）

采纳评审建议的 `require_permission("resource.action")` 统一模型
（`apps/control-api/src/ppswitch_control/auth.py`）：

- 每条路由声明动作，矩阵按 **principal kind + workspace role** 判定；
- **Agent 默认只允许**：`capabilities.read`（仅已授予且 published）、
  `invocations.create`、`invocations.read`（仅本人记录）——其余一律 403；
- **Service principal 目前零授权**（Edge Connector 落地时再开）；
- **User 按角色**（R-13）：member/reviewer/admin；
  - `capabilities.review|publish` ≥ reviewer；
  - `agent_grants.write`、`audit.read` = admin；
- 评审点名的 7 条路由（含 `POST /operations/{id}/verify`）全部收敛；
  另外把 `GET /endpoints`、`GET /operations`、发现导入等一并收紧
  （SEC-032：Agent 连内部 Endpoint 元数据、候选 Operation 都读不到）。

Agent 看到的能力目录额外加了 `status == published` 过滤：即使被授予，
草稿能力也 404（存在性不泄露）。

### P0-2 共享 Agent 的绑定隔离（SEC-033 / SEC-041）

三层改动（迁移 `b21c4f7e0d15`）：

1. **`delegations` 表**：`(workspace, user, agent)` 唯一，status/过期/scopes。
   新路由 `POST /api/v1/delegations`、`GET`、`POST /{id}/revoke`。
2. **绑定语义**：`agent:<id>` 上下文的绑定行**写入委托用户自己的作用域**
   （`activate_binding` 不再把键改写成 agent 的共享 personal 键）。
   Alice 与 Bob 各自维护自己的 `(user, service, "agent:<id>")` 行，
   互相不可见、不可覆盖；写该上下文要求活动委托存在。
3. **调用解析**：Agent 调用时控制面解析其**活动委托人**，用
   `(委托用户, service, "agent:<id>")` 定位绑定；无委托 → 403
   （"没有有效用户委托的 Agent 无法替用户使用 Connection" 已成立）。
   共享 Agent（多人委托）用显式 `agent:<id>:<user>` 消歧，
   未消歧时 409 而不是隐式选择；`agent:<他人id>` 一律 403。

SEC-033 测试同时验证了"Alice 重绑不影响 Bob 的 version/profile"和
"显式上下文各自命中各自 Profile"两个攻击组合。

### P0-3 真正的连接地址钉扎（SEC-034)

采纳"连接地址绑定"方案，实现为 httpcore 网络后端
（`network_policy.py::PinningNetworkBackend`）：

- 连接池的 `connect_tcp(host, port)` 内完成：解析 → 策略校验（白名单、
  link-local 硬拒）→ 以**字面量 IP** 调用 `SyncBackend.connect_tcp`；
- 传给 socket 的是字面量地址，**httpcore 与操作系统都不再有第二次
  DNS 解析**——"检查得到的 IP"与"实际连接的 IP"是同一个值；
- TLS SNI 与证书主机名校验仍绑定原始域名（httpcore 的
  `server_hostname` 取自 origin 而非连接目标），钉扎不弱化证书校验；
- 传输层的 `handle_request` 全量检查保留为 fail-fast；
  执行前 `execute()` 的最终 URL 检查保留。

SEC-034 测试用"逐次翻转的假 DNS"验证了两个方向：
连接目标恒等于**连接时那次**被校验的解析结果（检查后翻转无法改道）；
连接时翻成 link-local 则直接 `NetworkPolicyViolation` 且**零 socket**。

> 评审建议的"出口防火墙/代理强制约束"属于部署面，记入
> docs/05 部署章节的待办（见 §4 路线图）。

### P0-4 Response Guard 严格 Schema 校验（SEC-035）

`response_guard.py` 重写投影逻辑，评审指出的两个洞都封上：

1. **类型门先行**：声明的 `type` 在任何结构遍历**之前**判定。
   schema 声明 `string`、实际收到 `object` → 该字段整体拒绝
   （值置 None + `<type:string>` dropped 标记），不再落入宽松分支；
2. **未知对象结构默认拒绝**：reviewed-schema 模式下，schema 未用
   `properties` 描述的对象层直接产出 `{}`（`<unspecified_object>`），
   不再 scrub-through；
3. **唯一权威允许集**：`output_schema.properties ∩ allow_fields`
   （两者都有时取交集，只有一方时以该方为准）——不再是并集语义；
4. 无 schema 的 legacy 能力保持旧的 allow+scrub 行为，暴露面只减不增。

### P0-5 OpenAPI 元数据隐私（SEC-036）

`parsers/openapi.py` 新增 `sanitize_schema`，导入期生效：

- 递归剥离 `example / examples / default / const / x-example / x-default`；
- `enum`：值含密钥形状/超长/非原始类型 → 整个 enum 丢弃；
- 所有字符串（含 description）过密钥形状正则（`sk-`、`CANARY_`、
  `api_key=...` 等）→ `<REDACTED>`；
- 清洗后的 schema 才进 `operation_versions`，`GET /capabilities`、
  capability 详情与 MCP 工具目录消费的都是清洗后的数据。

HAR 侧同步补了路径 PII 抽象：邮箱段 → `{email}`、JWT 形状段 → `{token}`
（`normalizer.py`），与既有 ID 抽象一致。

SEC-036 集成测试三面验证：API 返回的操作契约、数据库
`operation_versions` 原始行、（经 capabilities 面）工具目录均无 canary。

评审提的 `$ref` 受限解析/循环检测：当前导入器本就不跟随 `$ref`
（只复制内联 schema），外部网络引用不会发生；完整 `$ref` 支持排期
v0.2（见 §4）。

## 2. R-06..R-14

| 项 | 实现 |
| --- | --- |
| R-06 scopes 交集 | `invocation_service`：活动 grant scopes ∪（任一空=不限）∩ connection.scopes ∩ delegation.scopes，空集 → 403；有效 scopes 与委托人写入审计快照（SEC-041 三组用例：相交换行/交集放行/收窄拒绝） |
| R-07 幂等回放 | 回放检查移到 grant + delegation 校验**之后**（SEC-037：grant 过期后旧 key 得 403 而非缓存结果）。绑定/策略不重查是幂等语义的一部分，已在 docs 记录边界 |
| R-08 上传限额 | `read_upload_bounded`：64KB 块流式读取，超限即断，内存上界 `limit + chunk`（SEC-038 单元 + API 双测） |
| R-09 签名排序 | `build_tool_function` 必填参数稳定前置（SEC-039 用"可选在前"的 schema 验证签名合法） |
| R-10 名字冲突 | 发布时（`publish_capability`）与注册时（`register_capability_tools` 抛 `ToolNameConflict`）双端检查，共享 `ppswitch_core.naming.mcp_tool_name`；测试验证第二个映射 409/抛错且第一个不被遮蔽 |
| R-11 可见性 | `list/get services、capabilities`：`private` → owner 或 admin，`team/organization` → 工作区可见（SEC-042）；Agent 目录仍叠加"已授予+published"过滤 |
| R-12 结果留存 | 本轮未改代码（已是脱敏投影）；数据分级/保留期/加密排期 v0.2，见 §3.4 |
| R-13 角色 | `principals.role`（迁移 4）+ 权限矩阵；seed：alice=admin、carol=reviewer；legacy NULL 视为 member |
| R-14 YAML | JSON 解析失败 → `yaml.safe_load`（安全子集、必须 mapping），新增 YAML OpenAPI 导入测试 |

## 3. 设计决定与边界（ADR 风格）

### 3.1 ADR-002：Agent 的 "personal" 上下文服务端映射
Agent 用默认 `personal` 上下文调用时，服务端映射为自己的
`agent:<id>` 上下文（按委托人解析绑定）。这保证旧 SDK/MCP 客户端
零改动即安全；用户 principal 携带 `agent:` 上下文 → 403。

### 3.2 ADR-003：共享 Agent 的显式消歧
多个活动委托并存且上下文未消歧 → 409。拒绝隐式选择"某一个委托人"，
因为那会让审计主体不可预测。

### 3.3 ADR-004：钉扎层级
选择 httpcore backend 层（而非 URL 改写）实现钉扎，原因是 URL 改写
会破坏证书主机名校验，而 backend 层天然保留 origin hostname 的
SNI/验证。局限：出口防火墙等环境级约束不在应用可达范围，见路线图。

### 3.4 R-12 的边界说明
`invocations.result` 目前存的是 Guard 投影后的白名单数据（非原始响应），
但"数据分级 + 保留期限 + 静态加密 + 清理任务"是一套独立机制，与权限
边界正交，盲目在本轮塞入会扩大变更面。排期 v0.2（`PR-DATA-12`）。

## 4. 路线图调整（v0.1.2 → v0.2）

按评审建议开 `v0.1.2 Security Hardening`（本文件即其交付物），
随后 v0.2：

- PR-DATA-12：调用结果数据分级、保留期、加密与清理（R-12）
- PR-NET-13：`$ref` 受限本地解析 + 循环/深度限制（P0-5 遗留项）
- PR-DEP-14：部署面网络约束文档（出口防火墙/代理强制段，P0-3 遗留项）
- PR-QA-06（保留）：PostgreSQL CI 矩阵
- PR-FEAT-07/08（保留）：OIDC/OpenBao/RBAC 深化、Chrome Collector

企业真实密钥接入的前置条件维持评审结论：PR-DATA-12 + 真实
SecretProvider（Keyring/OpenBao）+ OIDC 之后。

## 5. 验证记录

- `uv run -m pytest`：**188 passed**（含 SEC-031..042 共 12 项新回归）
- `uv run ruff check .`：通过
- `uv run -m mypy`（control/executor/mcp/core/discovery 源码）：通过
- `scripts/check-no-secrets.py`：clean（仓内仅有合成 canary/MOCKKEY）
- 迁移链：`f7aa00164b41 → 7f07c5b36bb0 → efec0ab1653a → b21c4f7e0d15`

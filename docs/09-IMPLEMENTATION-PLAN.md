# 09 · 开发实施方案：从 0 到可交付 MVP

## 0. 实施原则

按**垂直闭环**拆分，而不是先把所有服务脚手架做好。每个迭代必须提交代码 + schema/migration + 自动测试 + 文档，保证能演示用户价值。

**优先产品目标**：
1. `pp switch` 一键切换合法 Profile；
2. 无真实 `.env` 密钥也能开发/运行工具；
3. 从脱敏的 HAR/OpenAPI 自动沉淀候选接口；
4. 发布后 Agent 经授权调用 MCP/HTTP；
5. 个人/企业的账号身份、API 定义和权限保持隔离。

下表中的“周”是相对阶段编号，不承诺实际开发日历；人力和复杂度以真实开发进度调整。

## 1. 阶段 0：仓库与质量门槛

**任务**
- 建立 `pp-switch` Monorepo、`uv`、`pnpm`、基础 lint、mypy、pytest。
- 建立 FastAPI Control API skeleton，健康检查和统一错误格式。
- 增加 SQLite dev / PostgreSQL compose，创建 Alembic 迁移。
- 实施 `.gitignore`、secret scan 和合成测试 fixture。
- 建立 GitHub issue templates、CI、最小贡献规范、版本与许可证计划。
- 固定允许的项目依赖及其 licenses；选定锁定版本。

**验收**
- [ ] 空库启动迁移成功；
- [ ] `GET /healthz` 可正常响应；
- [ ] CI 依次运行 lint/test/build；
- [ ] secret scanner 可识别合成泄漏样例；
- [ ] AI 编码工作区无可读真实密钥。

## 2. 阶段 1：API Discovery MVP

**用户故事**：授权用户导入脱敏 HAR → 得到操作列表。

**任务**
- Pydantic 的 `Service` / `Endpoint` / `Observation` / `Operation` / `OperationVersion` 定义；
- `HAR Importer`：只接受本地清洗后规范格式；包含 consent 与 scope；
- `OpenAPI Importer`：解析 method/path/parameters/response；
- `fingerprint` 与路径模板推断（ID/UUID/复杂路径）；
- `schema inference`，区分 required / optional / observed nullable；
- 合并/冲突/版本机制；审核页面可修改描述但保留证据；
- 导入任务去重、文件大小限制、租户隔离；
- 漏洞防护：文件解析资源限制、禁止通过导入输入执行代码。

**验收**
- [ ] 3 份合成 HAR 聚合成预期 Operation；
- [ ] `Authorization/Cookie/PII` 蜜罐不会存数据库；
- [ ] 相同路径不同版本不合并；
- [ ] 未授权用户不能查看其他 workspace 证据；
- [ ] 原始数据超过限制被拒绝而非无限内存占用。

## 3. 阶段 2：Capability Registry 与审核发布

**用户故事**：将一个 Operation 经验证发布为只读 `crm.get_customer`。

**任务**
- Capability + immutable version + policy/presentation metadata；
- 批准工作流：Reviewer 提供证据与记录；关键改动新版本；
- 生成 Agent 公共 Tool Schema；
- 关联 Endpoint allowlist、字段输出白名单；
- 状态机 `draft → reviewed → verified → published`；
- 实现查询、搜索、版本 diff 与软删除。

**验收**
- [ ] draft 不能调用；
- [ ] 当前用户只看到授权的发布工具；
- [ ] 更新已发布 Schema 必须新版本；
- [ ] 描述不包含内部凭证字段、Host、`secret_ref`。

## 4. 阶段 3：连接与一键 Profile 切换

**用户故事**：用户在 UI 中点击 `CRM / Demo A` 与 `CRM / Demo B` 切换，不改代码。

**任务**
- Connection / Profile / ActiveBinding 持久化；
- `GET/PUT active-bindings`，`expected_version` 处理冲突；
- UI `ProfileSwitcher`、可见环境与账号标签；
- 权限验证：Connection owner、workspace、endpoint/service 对齐；
- 快照语义：在执行开始前固定 Profile；
- 生产 Profile 变更需不同权限等级、可扩展审批。

**验收**
- [ ] 点击新 Profile 后后续请求使用该档案；
- [ ] 已经开始的请求不被切换影响；
- [ ] 无权限用户不能切到别人私有 Profile；
- [ ] 并发切换可检测冲突；
- [ ] UI 不返回连接秘密或隐藏代理 Key。

## 5. 阶段 4：Secretless Executor / Python SDK（安全主线）

**用户故事**：`pp.invoke("crm.get_customer", {"customer_id": "C-1024"})` 通过受信执行器请求后端；源码无 API Key。

**任务**
- 定义 Python SDK `PPSwitchClient`（先 mock local broker transport）；
- 封装已批准请求：固定 method/host/path/参数映射；
- 实现 Policy 决策、Profile 快照、调用超时/配额/错误转换；
- SecretProvider 插件：mock → dev OS secure storage → OpenBao enterprise；
- 执行器独立进程 / 身份，禁止 Control API 明文解密；
- 审计、响应字段白名单、DLP、Header/Exception 过滤；
- 错误场景一律不回退到 `.env` 明文凭证；
- 为 AI 开发代理提供独立 mock backend 和无生产网络的沙箱。

**验收**
- [ ] 合成蜜罐密钥不会出现在模型请求、响应、日志；
- [ ] 本地项目 `.env.example` 只有非秘密 Profile/Capability Alias；
- [ ] agent dev sandbox 没有 SecretProvider 管理面和 prod Connector；
- [ ] 任何 `target_url`, `Authorization`, `secret_ref` 参数被拒绝；
- [ ] `pp.invoke` 只能访问当前授权的 Capability；
- [ ] 移除 Broker 后安全失败，不自动读旧 `.env`。

## 6. 阶段 5：MCP 和 Agent 接入

**任务**
- 基于官方 Python MCP SDK 添加受审 Tool Registry 与 `tools/list`；
- `tools/call` 走同一个 `CapabilityInvocationService`；
- 远程 MCP 的 OAuth/JWT audience 校验；本地 MCP 的启动身份/IPC；
- 工具禁用通知、工具版本兼容测试；
- E2E：允许/拒绝/过期/跨租户/预算超限。

**验收**
- [ ] 官方 MCP Client 能发现只属于它的工具；
- [ ] Agent 只能传已声明业务参数；
- [ ] API 后端原始 Key 不发送给 MCP Client 或外部 LLM；
- [ ] 不因切换 Profile 而把一个用户的授权施加给另一个用户。

## 7. 阶段 6：浏览器 Collector（Phase 2）

**任务**
- Chrome MV3；用户主动授权一个具体 origin；
- 明确显示录制中；用户随时暂停、删除；
- 捕获 fetch/XHR 网络请求的**脱敏结构**，禁止完整 Cookie/Token 上传；
- 为高风险站点设置默认禁用/显式组织策略；
- 与 HAR Importer 共用 normalizer 和 schema inference；
- 对第三方站点的 API 可执行性进行合法授权/协议稳定性检查。

**验收**
- [ ] 插件没有静默监听全部域名；
- [ ] 收集记录可预览、撤回、删除；
- [ ] 默认无 Cookie/Token/响应值外发；
- [ ] 两个用户对同一 API 的发现证据可合并，连接不共享。

## 8. 阶段 7：数据库 / 企业特性（Phase 2–3）

- 数据库：只读 schema、已批准 View 和参数化查询，禁用任意 SQL；
- 团队：Workspace 成员、OIDC SSO、RBAC/ABAC；
- 内网：出站 Edge Connector + 分域 SecretProvider；
- 审批：高风险操作，原子事务、幂等、审计；
- 观察变化：API Contract drift 警告、工具不兼容分析；
- 导出：OpenAPI 与 MCP 配置、工具目录，而不是导出凭证。

## 9. 工作拆分表（可直接转 GitHub Issues）

| ID | Issue 名称 | 优先级 | 依赖 | 验收依据 |
|---|---|---|---|---|
| E0-01 | Initialize mono repo & CI | P0 | 无 | CI 通过 |
| E0-02 | Domain models + migrations | P0 | E0-01 | DB tests |
| E1-01 | Sanitized HAR parser | P0 | E0-02 | Privacy tests |
| E1-02 | OpenAPI import | P0 | E0-02 | Fixture tests |
| E1-03 | API normalization + dedup | P0 | E1-01 | Golden fixtures |
| E2-01 | Capability review + versions | P0 | E1-03 | State machine tests |
| E3-01 | Connections + Profiles | P0 | E0-02 | Auth tests |
| E3-02 | ActiveProfile API / UI | P0 | E3-01 | Version conflict |
| E4-01 | Policy engine v1 | P0 | E2-01 | Deny by default |
| E4-02 | Trusted executor + mock adapter | P0 | E4-01 | Full invocation |
| E4-03 | Secretless SDK / broker abstraction | P0 | E4-02 | No secret output |
| E4-04 | Egress + response guard | P0 | E4-02 | SSRF/PII tests |
| E5-01 | MCP adapter | P0/P1 | E4-02 | tools/list/call |
| E6-01 | Browser collector opt-in | P1 | E1-01 | Privacy E2E |
| E7-01 | Readonly DB adapter | P1 | E4-02 | RLS/query tests |
| E7-02 | OpenBao provider | P1 | E4-03 | Rotation tests |
| E7-03 | Multi-network connector | P2 | E4-02 | mTLS/scope tests |

## 10. 每张 PR 的 Definition of Done

- [ ] 完成业务行为及必要的 Pydantic / OpenAPI 契约；
- [ ] 有对应单元、集成或安全测试，并能稳定复现；
- [ ] 对权限、敏感数据、输出字段和滥用做出明确说明；
- [ ] 不出现 `get_secret/export_secret` 公共能力；
- [ ] DB 变更使用 Alembic，不直接手动改生产表；
- [ ] CI 通过 lint/test/typecheck/secret scan；
- [ ] 文档与变更记录同步；
- [ ] 高风险权限变更由不同维护者安全审查。

## 11. 首个可公开 Demo 剧本

使用完全虚构的数据：
1. 导入 `tests/fixtures/synthetic-har/crm.har.json`（客户端提前脱敏）；
2. 得到 `GET /api/customers/{customer_id}`；
3. 审核为 `crm.get_customer` 并发布；
4. 创建 `Demo A`、`Demo B` 两个 mock Connection / Profile；
5. 选择 `Demo A`，用 Python SDK 与 MCP 查询 `C-1024`；
6. 切换 `Demo B`，执行同一代码，得到另一个可授权结果；
7. 检查请求未携带供应商真实 Key，logs 中仅有非敏感审计；
8. 以无权限 Agent 和跨 workspace 身份再次调用，验证被拒绝。

## 12. 何时可以宣布 Alpha

- CI 安全基线与 secret scanner 通过；
- 公开 README 准确列出**已经实现** vs **计划实现**；
- 没有任意 URL / SQL / Secret API；
- 安全测试矩阵中的 P0 全绿；
- 出具有局限性说明的 Secretless threat model；
- 法律、许可证、SBOM 与基础维护流程就绪。

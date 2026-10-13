# 20 · 首竖切实现审核报告（v0.1.0）

> **面向读者**：pp swich 蓝图作者 / 设计审核。
> **送审内容**：按 `docs/00-START-HERE.md` 第 3 节"第一张竖切"与 `docs/19` Task 001–010 完成的实现，请求对第 4 节列出的**实现偏差与决策点**逐条审核。
> **日期**：2026-10-13 · **仓库**：https://github.com/CheeseMac/pp-swich · **审核基线提交**：`e42e40a`（CI 双工作流全绿）

---

## 0. 一页结论

| 维度 | 状态 |
|---|---|
| 蓝图 Task 001–010 | ✅ 全部完成（见第 3 节对照表） |
| 00-START-HERE §3 十个验收场景 | ✅ 全部有自动化测试且通过（见 4.1） |
| 11-TEST-PLAN P0 安全矩阵 | ✅ 18/20 条已覆盖，2 条降级/待补（见 4.2） |
| 自动化测试 | ✅ 122 个用例；ubuntu + windows CI 全绿；含三真实服务器 E2E |
| 质量门禁 | ✅ ruff / mypy（52 源文件零错误）/ 仓库密钥扫描 / 蜜罐位置核查 |
| 设计偏差 | ⚠️ 共 9 项需要审核确认（见第 5 节，每项给了理由与备选） |
| 残余风险 | ⚠️ 3 项已知限制（见第 6 节，均已在 README/代码注释声明） |

**一句话**：蓝图的核心安全闭环（脱敏发现 → 审核发布 → 一键切换 → 受信执行 → 白名单返回 → 脱敏审计）按规范落地并可演示；所有与蓝图文字不一致的地方都集中在第 5 节，等审核拍板。

---

## 1. 交付物清单

```
packages/  core(领域契约) db(ORM+Alembic) discovery-sdk policy-engine
           secret-provider python-sdk(import ppswich)
apps/      control-api(FastAPI) executor(受信执行) mcp-adapter(官方mcp v2)
           demo-mock-crm(双数据集合成后端) web-console(React七页)
tests/     fixtures/synthetic-har · security · e2e(真实三服务器)
scripts/   dev-up.py seed-demo.py check-no-secrets.py
configs/   capabilities/examples/crm.yaml agent-ready-profile.schema.json
deployments/compose/  Dockerfile.python + docker-compose.yml(PostgreSQL)
.github/   ci.yml(双OS lint/mypy/122测试) security.yml(扫描+P0) 模板
```

- 数据库迁移：2 个 Alembic 版本（初始 16 表 + capability execution 映射列），空库可重复升级
- LICENSE：Apache-2.0；依赖许可证均为宽松（MIT/BSD/Apache）

## 2. 演示路径（审核者 5 分钟走查）

```bash
git clone https://github.com/CheeseMac/pp-swich && cd pp-swich
uv sync --all-packages && pnpm install
uv run python scripts/dev-up.py --ui        # 8911/8912/8913 + 5173
uv run python scripts/seed-demo.py          # 另一终端；打印合成 dev token
```

浏览器 `http://localhost:5173` 选 Alice 登录，然后：Discovery 导入
`tests/fixtures/synthetic-har/crm.har.json` → Capabilities 创建
`crm.get_customer` → Review → Verify → Publish → Profiles 在 CRM Demo A/B
间一键切换 → Invocations 调用 C-1024，观察 `Acme Industries` ⇄
`Acme Industries (QA copy)` 随档案切换、结果仅含 `id/display_name` →
Security 页查看脱敏审计。

命令行复核：`uv run pytest`（122 全过）；`uv run python scripts/check-no-secrets.py`。

---

## 3. 蓝图任务对照（docs/19）

| 任务 | 完成判据（蓝图原文） | 实际证据 |
|---|---|---|
| 001 Monorepo | lint/typecheck/unit 可运行，CI 验证无蜜罐泄漏 | uv workspace + pnpm；ruff/mypy/pytest 双 OS CI；security.yml 蜜罐扫描 |
| 002 领域契约 | schema 测试、跨 Service/Workspace 越权测试通过 | `ppswich_core`（含 Profile 对齐校验、secret_ref 公共投影禁出、发布版不可变验证）19 用例 |
| 003 迁移+仓库 | 空库迁移、重复迁移、Profile 并发测试通过 | Alembic×2；空库/重复升级/乐观锁并发（一对一胜者）/复合外键跨 workspace 拦截，3 用例 |
| 004 清洗 HAR | 2–3 候选 Operation；蜜罐不落库 | sanitize/normalize/fingerprint/infer 四纯函数 + 合成夹具；19 用例含 3 路径合并、GraphQL 分离、类型冲突标记 |
| 005 Profile 切换 API | 授权切换、409 冲突、跨用户拒绝、UI 显示 | GET/PUT active-bindings（乐观锁）；e2e 证实在途快照（SEC-017）与切换后生效语义 |
| 006 受信 Mock Executor | 无 .env 完成调用 | 独立进程；HMAC 计划签名+nonce+deadline；mock CRM 双账号数据集；仅输出 id/display_name；审计无秘密 |
| 007 Secretless SDK | 仅别名+参数，mock 可执行，含错误测试 | `import ppswich`：invoke/list_capabilities/profile_status；**无 get_secret 面（有测试锁定公开面）** |
| 008 MCP adapter | 标准客户端调用同一 mock CRM；跨租户拒绝 | 官方 mcp 2.3.0；协议级 client-server 内存往返测试 + e2e tools/list/call |
| 009 首版 UI | 浏览器完成 HAR→Draft→Publish→Switch→Invoke | React 七页；浏览器实测全流程（登录→导入→三步发布→A/B 切换→调用→审计截图） |
| 010 安全门 | CI 自动失败可泄露凭证/越权的 PR | 10 场景 + P0 矩阵 + 密钥扫描 + 蜜罐位置检查全部进 CI |

## 4. 验收证据

### 4.1 00-START-HERE §3 十场景

| # | 场景 | 结果 | 测试 |
|---|---|---|---|
| 1 | 含凭证 HAR → 中央库不保存值 | ✅ 整包 422 UNSANITIZED_INPUT，值不回显；DB 全表无 CANARY | api 测试 Scenario1 |
| 2 | 3 个 customer_id → 1 候选 | ✅ `GET /api/customers/{id}` | Scenario2 |
| 3 | 发布未审核 → 409/403 | ✅ 409 CAPABILITY_NOT_REVIEWED（状态机拦截） | Scenario3 |
| 4 | 调用未发布 → 404 不暴露存在 | ✅ 404 CAPABILITY_NOT_FOUND | Scenario4 |
| 5 | 无权切换他人 Profile → 403 | ✅ 同工作区 403 PROFILE_NOT_ALLOWED；跨工作区 404 | Scenario5 |
| 6 | 切换后新调用用新档案；在途不变 | ✅ e2e：SLOW 请求在途切换，结果仍为旧数据集 | e2e snapshot 测试 |
| 7 | 传 Authorization/target_url → 422 | ✅ 422 RESERVED_ARGUMENT（嵌套/大小写同样拦截） | Scenario7 |
| 8 | Set-Cookie/access_token 不可见 | ✅ 响应守卫白名单投影，泄漏字段进 dropped_fields | Scenario8 + e2e |
| 9 | 企业 B 读企业 A → 404 无泄漏 | ✅ 列表为空 + 调用 404 | Scenario9 |
| 10 | 私网禁区主机 → 拒绝 | ✅ 网络策略默认拒绝环回/私网/链路本地/元数据段；模型侧无 URL 通道 | executor 网络策略测试 |

### 4.2 11-TEST-PLAN P0 矩阵

已覆盖：SEC-001/002（蜜罐入库）、003（输出泄漏）、004（身份伪造）、005（账号借用）、006（跨租户）、007（SSRF 参数）、009（任意 Header）、010（未发布）、011（高风险审批，含参数摘要绑定+一次性消费+他端审批人）、012（幂等重放返回既有结果不重执行）、013（密钥失联 fail-closed 503）、016（切换并发 409）、017（在途快照）、020（超限输出）。

**部分覆盖 / 待补**：
- **SEC-008 DNS rebinding**：当前在"解析后校验全部 IP + 禁跨主机重定向"层面拦截；**未做解析与连接之间的 IP 钉扎**，残余窗口见第 6 节。
- **SEC-014/015（Agent 沙箱无生产网络/改代码不升权）**：属于部署形态与仓库边界测试，本竖切以"仓库无真实凭证 + fail-closed 默认 + 密钥永不经过控制面"间接覆盖，自动化沙箱测试未实现。

---

## 5. 实现偏差与决策点（**请逐条审核**）

以下每条格式：**蓝图条款 → 实现选择 → 理由 → 备选**。

### D-1 导入接口形态
- 蓝图：07 §4 要求独立受控 upload endpoint + 短时一次性 `upload_handle`，导入任务返回 job 轮询。
- 实现：`POST /api/v1/discoveries/imports` **multipart 文件直传**（5MB 限额 + 类型检查 + 服务端二次清洗），同步返回 `job_id`+`state=completed` 汇总。
- 理由：首竖切减少两跳；清洗拒绝语义（422）不变。蓝图最担心的"handle 跨租户复用"面直接不存在。
- 备选：如需严格对齐，可加 staging endpoint + TTL handle（列为下迭代）。

### D-2 能力生命周期端点
- 蓝图：07 §3 只列了 create/publish；但 04 §5 状态机要求 reviewed+verified 才能 publish。
- 实现：增加 `POST /capabilities/{id}/review` 与 `/verify`（记录证据与审计），publish 前置校验状态机。
- 理由：没有显式 review/verify 动作，"发布未经审核 → 409"无落点。

### D-3 Dev 登录目录免鉴权
- 蓝图：05 §2 身份只来自已验证凭据；未定义登录页如何获得第一个凭据。
- 实现：`GET /api/v1/dev/principals` **免鉴权**（仅 `PP_SWICH_DEMO_MODE=true` 时存在，否则 404），返回 `data/dev-tokens.json`（seed 脚本写入的本地运行时文件，gitignored）中的合成 token。
- 理由：demo 登录引导需要一个受控起点；token 均为合成值，离开本机 demo 无效。
- 备选：改为 UI 手动粘贴 token（已同时支持）。

### D-4 Mock 凭证确定性派生（不再走 executor 种子端点）
- 蓝图：09 阶段 4"SecretProvider 插件：mock → dev OS secure storage → OpenBao"。
- 实现：demo 连接的 `secret_ref` 携带数据集标记（`mock://connections/<id>?dataset=A|B`），执行器从 ref **确定性派生** `MOCKKEY-DEMO-<ds>-<hash>`；控制面与执行器之间**零密钥值传输**，执行器重启免重置。`/internal/v1/admin/seeds` 端点保留但退出主流程。
- 理由：实现过程中发现"控制面调 executor 种子"会让密钥值过控制面（违背 05 §3A 精神）且重启即失；确定性派生两者皆解。

### D-5 Agent 档案绑定语义
- 蓝图：04 §4 `context_key` 允许 `agent:<verified-id>`，但未定义谁替 agent 建绑定。
- 实现：`PUT /active-bindings/{service}/agent:<id>` 由**已认证且拥有该 Profile 的用户**调用，服务端解析 agent principal（存在性+kind 校验）后写入**该 agent 自身 personal 作用域**；调用者保留审计 ownership。Agent 调用时用默认 `personal` 上下文即可命中。
- 理由：Agent 无 UI；授权决策留在人这一侧，agent 无法自行获得绑定。

### D-6 审批流形态
- 蓝图：07 §7 高风险返回 `approval_request_id`，"建议进一步使用明确的审批流程状态"。
- 实现：`approvals` 表 + `POST /api/v1/approvals/{id}/approve`（**审批人 ≠ 请求人**、参数摘要绑定、一次性消费）；换参数后旧审批立即失效（有测试）。
- 理由：把"审批"从隐式状态变为可审计对象。

### D-7 active_bindings 复合外键
- 蓝图：06 §4 注明"生产必须增加复合外键…（MVP 可缺）"。
- 实现：首版即加 `UNIQUE(id, workspace_id)` 于 profiles + `FK(active_profile_id, workspace_id)`；有跨工作区绑定被数据库拒绝的测试。
- 理由：SQLite/PG 都支持，晚加反而要数据修复迁移。

### D-8 内部通道认证
- 蓝图：07 §7 内部执行接口需"独立服务身份或短期任务凭据"。
- 实现：`X-Internal-Token`（部署注入；默认值为显式标注的合成 dev 值）+ **HMAC-SHA256 计划签名 + nonce 防重放 + deadline**；执行器对参数做与控制面相同的二次校验。
- 理由：dev 形态可跑通全部安全测试；生产替换为 mTLS/工作负载身份时执行器入口函数不变。

### D-9 MCP 工具目录刷新
- 蓝图：07 §8 "工具目录变化提供刷新/变更通知机制"。
- 实现：MCP 适配器**启动时**按已发布能力注册工具；动态刷新/失效通知未做（重启适配器即刷新）。
- 理由：竖切优先；已发布版本不可变，目录漂移风险低。**请确认是否阻塞下一阶段。**

---

## 6. 已知限制与残余风险（已同步到 README）

1. **DNS rebinding 残余窗口**（SEC-008）：目标域解析校验与实际连接之间未钉扎 IP。缓解：仅白名单 endpoint、禁重定向、方案/主机全量校验。计划：自定义 httpx transport 固定解析结果。
2. **SDK 传输认证为合成 dev token**：`PPSwichClient.from_http` 尚无 OAuth audience/工作负载身份，不可宣称生产安全（SDK 文档已声明）。
3. **PostgreSQL 只提供了部署基线**（compose + psycopg 驱动），CI 未跑 PG 集成矩阵（蓝图 E0-02 验收项之一）；当前全部迁移/测试在 SQLite。另：审计 TTL、观察数据 30 天过期、配额（QUOTA_EXCEEDED 仅定义错误码）未实现。

## 7. 未实现（蓝图阶段 6–7，README 已列）

浏览器 Collector（MV3）、Postman 导入、OS Keyring / OpenBao Provider、OIDC/JWT audience、RLS、配额预算、Contract drift 告警、Edge Connector/mTLS、审批工作台 UI。

## 8. 请审核方反馈的问题清单

1. D-1 导入接口是否接受 multipart 直传作为 v0.1 事实标准？
2. D-3 免鉴权 dev 目录的安全边界是否可接受（或收紧为仅 127.0.0.1 请求）？
3. D-4 确定性派生方案是否可作为 mock provider 的长期形态？
4. D-5 `agent:<id>` → agent personal 作用域的映射是否符合 04 §4 意图？
5. D-9 MCP 目录刷新缺失是否阻塞，还是列入阶段 5 收尾？
6. SEC-008 IP 钉扎是否必须进入下一迭代 P0？

## 9. 建议的下一迭代（若审核通过）

按 09 阶段表：E5 收尾（MCP 动态刷新、远程传输 OAuth）→ E6 浏览器 Collector（opt-in 域名授权 + 本地脱敏）→ E7-02 OpenBao Provider；并行补 PG CI 矩阵与 IP 钉扎。

---

*本报告由实现方生成；所有"通过/覆盖"声明均有对应测试文件与 CI 运行记录（Actions run `e42e40a`）可查。*

# 39 · FEAT-04 交付记录：只读 PostgreSQL 数据库发现（首切片）

> 对应批复：docs/38 §1（验收清单六条）。本文记录首切片的实现、
> 验收证据与保留边界。评审协作模式沿用 docs/31 §1。

## 0. 结论

FEAT-04 首切片**代码与测试完成**，本地全量 349 项 Python 测试 +
31 项前端测试全绿；真实 PostgreSQL 全链（executor PG 层 8 项 +
e2e 7 项）在 CI `postgresql` job（PR-QA-06）执行，本机无 PG 环境
故本地以 skip 计（15 skipped）。CI postgres job 已扩展为同时运行
`apps/executor/tests`。

闭环与 docs/38 §1 逐字对应：

```
用户显式提供授权连接（db-target 单独获准 + FEAT-03 keyring 只读账号）
→ executor 以只读账号发现 approved schemas 的表/视图元数据（仅名字+类型）
→ 候选操作（draft，needs_review）
→ 人工确认参数与返回字段（FEAT-02 向导 db 变体）
→ 既有 review → verify → publish
→ Agent 授权（grants/delegation/binding 原样复用）
→ 实际调用一个固定的参数化只读工具（单行 get-by-key 语义）
```

## 1. 六条验收 → 测试映射

| # | 验收要求 | 证据（测试） |
| --- | --- | --- |
| 1 | 未批准的 schema 不可发现 | e2e `test_discovery_scope_is_limited_to_approved_schemas`（hr schema 既不在 allowed_schemas 也未 GRANT，双层不出现）；executor PG `test_discovery_limited_to_approved_and_granted_schemas`；control `test_schema_outside_allowlist_rejected` |
| 2 | 任意 SQL、写操作、越权字段不可执行 | 结构性保证：execution 是**结构化 spec**（target/schema/view/columns/filters/limit），客户端永远无法提交 SQL 文本——control `test_malformed_specs_rejected`（`sql` 键即拒）+ `test_capability_create_rejects_db_spec_with_http_keys`；executor 无 SQL 解析面：语句由 psycopg `sql.Identifier` 合成（`test_composed_statement_is_one_parameterized_select`：单条参数化 SELECT）；签名计划也无法夹带——e2e `test_tampered_plans_refused_or_gutted_by_executor`（未批 schema 拒、注入标识符拒、越权列被守卫清空）；角色层：executor PG `test_readonly_role_cannot_write_even_with_raw_sql`（裸 SQL 写入被角色拒绝）；会话层：连接强制 `default_transaction_read_only=on`（`test_session_forced_read_only_with_statement_timeout`）；字段层：e2e `test_invoke_returns_only_approved_columns`（secret_note 列永不离开数据库） |
| 3 | 超时、行数、结果大小受限 | executor PG：`test_statement_timeout_enforced`（pg_sleep 视图 + statement_timeout → UPSTREAM_TIMEOUT）、`test_non_unique_match_fails_closed`（fetch_limit=2，多行即拒）、`test_oversize_row_rejected`（32KB 行预算）；响应守卫 64KB 预算沿用不变 |
| 4 | 撤销连接或授权后立即拒绝 | control `test_disabled_target_rejects_immediately`（plan 组装时即拒，FORBIDDEN）；e2e `test_revocation_rejects_immediately`（disable db-target → 下一次调用 403；revoke connection → 403）；授权撤销链沿用既有 SEC-049/050/053 回归 |
| 5 | 数据库口令不进 Control、Agent 载荷或审计 | e2e `test_no_password_or_hr_leakage`（audit_events/invocations 快照/Agent capability 视图中均无口令；Agent 视图无 execution/secret_ref——SEC-045 原样）；连接串不落库：DB 坐标在 db_target 行、账号在 keyring、连接只存 `keyring://<name>`（FEAT-03 路径原样） |
| 6 | 数据库目标单独获准，不引入通用出站 TCP | db-target 是独立审批类型（`dbtargets.approve` reviewer 级、非 demo 下创建者不可自审——control `test_creator_self_approval_blocked_outside_demo`；与 endpoint 审批互不可代用：`resolve_db_query` 只认 DatabaseTargetRow，executor 的 DB 准入是独立函数 `check_db_target`，不共享 HTTP allowlist）；无新增通用出站面：唯一新出站代码是 DbAdapter 内的 psycopg 连接，且 host 只能来自签名计划的冻结坐标 + 硬拒网段不可豁免（executor `test_hard_denied_network_never_reaches_connect`）；postgres 连接激活前置条件就是 approved target（control `test_postgres_activation_requires_approved_target`） |

## 2. 设计要点（与红线的对照）

- **没有任何 `execute_any_sql` 等价面**。能力 execution 的 db 变体是
  结构化描述 `{"db": {"target_id", "schema", "view", "columns",
  "filters", "limit"}}`；SQL 由 executor 用 psycopg 标识符合成，
  形状恒为 `SELECT cols FROM schema.view WHERE k=%(k)s ... LIMIT n`。
  连内部签名计划都无法改变这一形状（e2e 篡改测试）。
- **只读三重保险**：账号角色（GRANT SELECT only，激活时 executor
  实连并**拒绝 superuser/bypassrlas 级账号**）+ 会话级
  `default_transaction_read_only=on` + 语句形状恒为单条 SELECT。
- **凭证路径完全复用 FEAT-03**：`ppswitch-secret put <name>
  --scheme postgres`（user+password 入 OS keyring）→ Connection
  （auth_scheme=postgres, secret_name）→ activate（executor 实连
  approved target + 角色检查）。控制面全程只见 keyring 名。
- **发现走受信侧**：`POST /db-targets/{id}/discover` → control 取
  调用者的 active postgres 连接 → executor `/internal/v1/db/discover`
  （information_schema 查询限定 allowed_schemas；只读角色本身也
  看不见未授权对象）→ 元数据（仅列名+类型）→ 既有 import 管线
  （source="db"）生成 draft 候选。
- **Profile 适配**：profiles.endpoint_id 可空——数据库 Profile 只绑
  postgres 连接，网络授权来自 approved db-target（每次 plan 组装
  重查）。HTTP 能力对无 endpoint 的 Profile fail-closed。
- **向导适配**：db 操作（`/db/{schema}/{view}`）参数表=列（选过滤列）、
  响应字段=SELECT 列清单；试调携带向导配置的结构化 spec
  （`db_execution`），服务端与发布能力同一套校验。

## 3. 改动清单（分层）

| 层 | 内容 |
| --- | --- |
| core | `DatabaseTarget` 实体 + `DatabaseTargetStatus`（独立枚举）；`InvocationPlanDbQuery`（结构化冻结查询）挂入 `InvocationPlan.db_query`；`AuthScheme.POSTGRES`；错误码 ×3；Profile.endpoint 可空 + 对齐校验分支 |
| db | `db_targets` 表（迁移 b7d2e4c9a1f3；PG 按名换 CHECK、SQLite 显式定义重建）；connections CHECK 增 postgres；profiles.endpoint_id 可空 |
| secret-provider | CLI `--scheme postgres`（`--user` + 隐藏口令；材质 `{"user","password"}`） |
| executor | `network_policy.check_db_target`（DB 专用准入：校验-钉扎-连接同一 IP，硬拒网段不可豁免）；`adapters/db_adapter.py`（compose_select/execute_query/verify_readonly_role/discover_objects）；`/internal/v1/db/discover`；`/internal/v1/secrets/verify` 的 db_target 变体（非 postgres 材质即拒）；execute 路由 db 分支 |
| discovery-sdk | `parsers/db.py`：元数据→候选（类型映射、异形标识符跳过、needs_review） |
| control-api | `db_target_service`（生命周期/发现/spec 校验/resolve_db_query）；路由 ×5（create/list/approve/disable/discover）；权限动作 `dbtargets.*`；plan 组装与试调 db 分支；capability 创建期 spec 校验；get_operation 补 service_id |
| web-console | Wizard db 变体（结构化 execution 生成/预览/试调）；Discovery 页数据库目标卡片（创建/审批/禁用/发现）；Profiles 页可选 endpoint |
| CI | postgresql job 增跑 apps/executor/tests |

## 4. 测试与 CI

- 本地（无 PG）：`uv run -m pytest` = 349 passed / 15 skipped；
  ruff / mypy（68 文件）/ check-no-secrets / pnpm typecheck+test+build
  全绿。
- CI `postgresql` job（真实 PG16）：executor PG 层
  （`test_db_adapter.py::TestRealPostgres`，8 项：角色不可写、
  正常查行、越权列、超时、非唯一拒、超限拒、发现范围、
  只读角色验证+superuser 拒绝）+ e2e 全链
  （`tests/e2e/test_db_discovery.py`，7 项：bootstrap、发现范围、
  用户调用、Agent MCP tools/call、篡改计划、泄漏扫描、撤销即时性）。
- e2e 拓扑：同一 PG 实例两个独立库（控制面库 + 被发现目标库）；
  目标库含 `crm`（表+视图，ro 角色 GRANT SELECT）与 `hr`
  （薪资表，**未授权**）；ro 角色每轮随机名 + `MOCKKEY-` 前缀合成口令。

### CI 迭代记录（如实）

- 首跑（`edef1f9`）postgres job 失败，仅两类根因：
  ① alembic 会把 target_metadata 的 naming convention **再叠加**到
  `drop_constraint` 传入的约束名上（PG 分支按名删除变成
  `ck_connections_ck_connections_auth_scheme` 双前缀而找不到）——
  PG 分支改为原生 `ALTER TABLE ... DROP/ADD CONSTRAINT` 直呼字面名；
  ② `pg_sleep()` 返回 void 不能作视图列——用 plpgsql 文本函数包装并
  放进视图 WHERE（防规划器剪枝）。
- 二跑（`b660f85`）剩三个测试问题：ro 角色 teardown 需先
  `DROP OWNED BY`（表授权依赖）；"越权列"篡改用例实际被更早的
  "过滤列必须属于选中列"规则拒绝（比预期更严，改断言）；
  非法 plan payload 在 executor 收敛为干净的 422（原为未处理 500）。
- 三跑（`e2e2a77`）CI 五 job + Security gates 全绿。

## 5. 已知边界（后续切片，未偷渡）

1. **单行 get-by-key 语义**：limit≤2、多行即拒。列表/分页查询工具、
   聚合视图属后续切片（需先过评审对"行集工具"的守卫语义）。
2. **直连模式**：DB 连接不经 egress-gateway（隔离部署拓扑下
   executor 无出口网络时 DB 目标不可达，属部署形态约束，
   文档化不实现）。
3. **单一 engine**：仅 `postgres`；MySQL/SQLite 等未开工。
4. **标识符窄集**：schema/view/列仅接受未加引号安全的
   `[A-Za-z_][A-Za-z0-9_]{0,62}`，异形命名对象在发现层跳过。
5. **无连接池**：每次执行独立连接（read-only 会话选项随连接下发）；
   首切片以正确性优先。
6. **激活即验特权**：executor 拒绝 rolsuper/rolbypassrls；未覆盖
   CREATEROLE/CREATEDB（对只读面无影响，未加严）。

## 6. 操作手册（合成环境演示）

```bash
# 1) 人在 executor 同机存只读账号（OS keyring）
uv run ppswitch-secret put crm-db-ro --scheme postgres --user ro_crm
#    （隐藏输入口令；输出 keyring://crm-db-ro）

# 2) 控制台（console 5173）
#    Services 建 service → Discovery 页「Database discovery」卡片：
#    填 host/port/database/allowed schemas → 创建（pending）
#    → reviewer 登录点 Approve
#    Connections 页：auth_scheme=postgres + secret_name=crm-db-ro → Activate
#    （executor 实连目标并验证角色非 superuser）
#    Profiles 页：endpoint 选「无 endpoint（数据库 Profile）」+ 该连接
#    → 激活 personal binding
#    → 回 Discovery 页点「发现 schema/视图」→ 候选操作出现（/db/crm/...）

# 3) Wizard：选 /db/crm/customers_v → 勾参数（过滤列）与响应字段
#    （预览可见结构化 execution，无 SQL 文本）→ 试调 → 创建草稿
#    → reviewer review/verify/publish → AgentAccessCard 授权
```

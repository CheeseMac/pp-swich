# 38 · 交接文档（面向 FEAT-04 会话）

> 写给下一个开发会话。FEAT-03 已于 2026-10-16 获**无保留开发态验收**
> 并正式关闭；**FEAT-04（只读 PostgreSQL 数据库自动发现）已批复进入
> 受限研发**。本文告诉你：项目现状、FEAT-04 批了什么/没批什么、
> 从哪里接入、怎么干活。通用架构地图/踩坑/红线仍见
> [docs/31-HANDOVER.md](31-HANDOVER.md)（其状态快照已过时，本文为准）。

## 0. 状态快照

- **main = `2f147c3`**（本 PR 追加 reviewer 断言小修后为最新提交），
  CI 五 job + Security gates 双绿；**Python 304 + vitest 31**。
- 功能批复状态（全部为**开发态/合成数据 Alpha**，非企业生产）：
  - FEAT-01 浏览器 Collector + F1 可靠性收尾：✅ 开发验收通过
    （Chrome 实机采集待用户手动验收）；
  - FEAT-02 发布向导：✅ 通过；
  - FEAT-05A Agent 一键接入：✅ 通过（Claude/Cursor 真机拉起
    `ppswitch-mcp` 待用户手动验收）；
  - FEAT-03 本地 Keyring + Profile 切换：✅ **无保留开发态验收**
    （2026-10-16，基线 `2f147c3`；连接所有权缺口已修复关闭、
    Agent 双 Keyring 组合验收已通过）。
- 保留边界（不阻塞 FEAT-04，不得偷渡）：共享执行器的 Keyring
  多用户所有权隔离（部署前置项）；企业生产 Gate R1 四项
  （DEP-04/SECRET-06/ID-07/DATA-08）独立执行。
- 评审协作模式：先核实指控再修→测试→docs/NN-REVIEW-RESPONSE 逐项
  回应→README→push→盯 CI→记忆。最新评审原文均已入库
  （docs/review-handoff/ 为评审方交接包，36/37 为入库与回应）。

## 1. FEAT-04 批了什么（验收清单，批复原文归纳）

只读 PostgreSQL 数据库自动发现**首切片**，形成闭环：

```
用户显式提供授权连接（数据库目标单独获准）
→ 受信 executor 以只读账号发现允许的 schema/View 元数据
→ 生成待审查候选操作
→ 人工确认参数与返回字段（复用 FEAT-02 向导）
→ 既有 review → verify → publish 流程
→ Agent 授权
→ 实际调用一个固定的参数化只读工具
```

**验收必须同时证明**：
1. 未批准的 schema 不可发现；
2. 任意 SQL、写操作、越权字段不可执行；
3. 超时、行数、结果大小受限；
4. 撤销连接或授权后立即拒绝；
5. 数据库口令不进入 Control、Agent 可见载荷或审计日志；
6. 数据库访问目标单独获准，**不得借 FEAT-04 引入通用出站 TCP 能力**。

**明确不做**：任意 SQL 工具（红线 `execute_any_sql` 等价面）、写操作、
动态发现未批准 schema、通用网关能力扩张。

## 2. 技术落点建议（读这些文件再动手）

| 关注点 | 位置 |
| --- | --- |
| 只读发现的执行位置 | `apps/executor/`（受信侧；参考 `network_policy.py` 的目标准入模式——数据库目标必须走**独立**的批准类型，不复用 HTTP endpoint 审批） |
| 凭证 | FEAT-03 已有全套：`ppswitch-secret put`（OS keyring）→ connection `secret_name` → activate（executor 实解析验证）。DB 口令同路径，`auth_scheme` 需要 DB 变体或连接串口令拆分（注意：连接串整体入库等于口令入库——只存 keyring 名） |
| 候选操作生成 | 复用 discovery 管线：`packages/discovery-sdk/parsers/merge.py`（sanitize→normalize→fingerprint→infer）+ collector 模式的 source（`parsers/collector.py` 是范例）；DB 表/视图 → 类似"观察"的形状（表名→模板、列→字段类型） |
| 人工确认与发布 | FEAT-02 向导原样复用（`apps/web-console/src/pages/Wizard.tsx`）；capability 的 `execution` 映射加 `db` 变体（executor 新 adapter：固定参数化查询 + 只读连接 + 超时/行数/大小限额） |
| 授权与调用 | AgentAccessCard / agent-principals / agent-grants 原样；调用走既有 invocation 链（授权/委托/审计/守卫全继承） |
| 只读角色 | 用户在 PG 侧自备只读账号（GRANT SELECT）；executor 连接即用该账号——"只读"由数据库角色保证，executor 再加查询白名单双保险 |

**测试基线**：本机无 PG 时用 CI 的 postgresql job 模式
（`PP_SWITCH_TEST_DB_URL`，见 `.github/workflows/ci.yml` 的
PR-QA-06 job 与 `packages/db/src/ppswitch_db/testdb.py`）；e2e 仿照
`tests/e2e/test_full_stack.py` 的真实进程栈。

## 3. 日常操作（速查，详单见 docs/31 §4）

```bash
uv sync --all-packages        # 必须 --all-packages
uv run -m pytest              # 304 tests
uv run ruff check . --fix && uv run -m mypy packages apps
uv run python scripts/check-no-secrets.py
pnpm typecheck && pnpm test && pnpm build   # web-console + browser-extension
uv run python scripts/dev-up.py --ui        # 本地全栈（console 5173）
git -c user.name="CheeseMac" -c user.email="841995886@qq.com" commit …
```

新合成 token 前缀要加 `scripts/check-no-secrets.py` 白名单
（现有：MOCKKEY-/MOCK-/dev-token-/dev-internal-token-synthetic/
CANARY_/ci-*）。

## 4. 待用户手动验收清单（长期挂着，别忘）

1. Chrome 安装扩展（`apps/browser-extension` 构建 + `chrome://extensions`
   加载 dist/）：实机采集、SW 重启恢复、域名范围、提交；
2. Claude Desktop 或 Cursor 按 Agent 接入页生成的配置（env 传令牌）
   拉起 `ppswitch-mcp`，完成一次真实 tools/call。

## 5. 本轮尾巴（已完成，知悉即可）

- 2026-10-16 复核指出的 docs/37 措辞宽于断言：已补 reviewer 激活
  负向断言并更正措辞（本提交）；
- docs/34 §7 / docs/35 / docs/37 为最近三份回应与交付记录；
- 演示用 keyring 条目已清理（demo-key-a/b 已 delete）。

## 6. 文档索引

蓝图 00-20；评审回应 21-37；交接包 docs/review-handoff/2026-10-15/
（含历史批复全文）；本文件 38。功能序列：01/02/05A/03 已关，
**04 进行中**，05 完整版（MCP 配置已交付，剩真机项）。

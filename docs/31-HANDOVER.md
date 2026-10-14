# 31 · 交接文档（Handover）

> 写给下一个开发会话/开发者。读完这一份就能接手：项目是什么、
> 到哪了、下一步做什么、怎么干活、哪些坑不用再踩。
> 状态快照：**main = `63638f0`，255 测试全绿，CI 五 job 全绿，
> Gate R1 进度 4/8，真实企业凭证仍未获准入。**

## 0. 项目是什么

**pp switch**：通用 API 发现、连接切换（Profile）、凭证保护与 Agent
安全能力平台。一句话卖点：Agent 可以使用经过授权的 API 能力，但
**拿不到凭证**——凭证只在受信执行器内出现，模型全程只见脱敏结果。

- 本地：`C:\Users\84199\Documents\workspace\pp-switch`
- GitHub：`https://github.com/CheeseMac/pp-switch`（公开，Apache-2.0）
- 蓝图来源：`pp_swich_starter_20261013.zip` 的 20 篇文档在 `docs/`
  （编号 00-20 是蓝图，21-31 是评审回应与本文件；docs 是规范来源）
- 用户：基金公司开发侧，舒华；评审由"蓝图设计师"出具（外部角色，
  每轮以 markdown 复审意见的形式送达）

## 1. 这个项目怎么干活（重要：协作模式）

开发节奏是**评审驱动**的固定循环，已经跑了八轮评审 + 一轮生产就绪
综合审计，每轮都走同一个流程：

```
收到评审意见 → 逐项核实指控（先复现再修）→ 实施修复 →
补回归测试（评审会给编号：SEC-xxx / R-xx / DATA-xx / AUD-xxx）→
写 docs/NN-REVIEW-RESPONSE 文档逐项回应 → 更新 README →
commit + push → 盯 CI 到全绿（迭代修复）→ 更新持久记忆
```

关键习惯：
- **评审说的每一条都先核实**——九轮里指控 100% 属实，但也要独立复现
  （有过测试盲区被评审抓住的先例：第四轮的不变量测试在测历史前
  撤销了 delegation，"缩小未撤销"路径没被证明）；
- **如实记录失败**：CI 首跑失败与迭代过程写进回应文档（设计师明确
  认可这种诚实，"CI 通过≠生产通过"是第六/七轮的核心批评）；
- 每轮结束更新 `docs/` 回应文档 + README + 用户级持久记忆；
- 评审明确要求（第七轮起）：**不再逐轮零散补丁**，按 Gate 整块推进。

评审历史速查：
| 轮 | 主题 | 产出 |
| --- | --- | --- |
| 1 | 6 P0 + 偏差裁定 | PR-SEC-01..05、SEC-021..030、docs/21 |
| 2 | 5 P0 + R-06..14 | 权限矩阵/委托/IP钉扎/Schema清洗、SEC-031..042、docs/22 |
| 3 | 6 P0 | 幂等绑委托人/required_scopes/公共目录/签名票据、SEC-043..048、docs/23 |
| 4 | 3 边界 | 撤销不变量四路径、SEC-049..051、docs/24 |
| 5 | DATA 三项+NET-13 | 代际票据/历史重授权/结果生命周期、docs/25 |
| 6 | DATA 收口+NET-13 本体 | 存量明文清空/轮换窗口/$ref解析、docs/26 |
| 7 | NET-13 收尾+DEP-14 | allOf 重复拒绝/解析预算/NAT64/compose、docs/27 |
| 8 | DEP-14 收尾+QA-06 | egress-gateway/PG矩阵/docker验收、docs/29 |
| 审计 | Gate R1 提出 | 网络边界三项+部署全链、docs/30 |

## 2. 架构地图

```
apps/
  control-api/   控制面（FastAPI, :8911）——发现/能力/Profile/委托/审批/审计
  executor/      受信执行器（:8912）——唯一接触凭证的组件
  egress-gateway/ 受控出口（CONNECT-only 代理, :8914）——第八轮新增
  mcp-adapter/   MCP stdio 出口（tools/list + tools/call 走同一调用链）
  demo-mock-crm/ 演示后端（双数据集 A/B）
  web-console/   React 前端（pnpm）
packages/
  core/          领域模型/错误码/状态机/mcp_tool_name
  db/            SQLAlchemy 模型 + Alembic 迁移（7 个）+ testdb 助手
  discovery-sdk/ HAR/OpenAPI 解析（$ref 受限解析器/清洗/归一化）
  policy-engine/ 授权策略求值
  secret-provider/ SecretProvider 接口 + Mock 实现
  python-sdk/    `import ppswitch` secretless SDK
deploy/          Dockerfile + docker-compose.yml（隔离拓扑）
                 + docker-compose.postgres.yml + verify-deployment.sh
scripts/         dev-up.py（本地全栈）/ seed-demo.py / check-no-secrets.py
tests/e2e/       三服务真实进程端到端
```

### 关键安全机制（都在哪）

| 机制 | 位置 | 测试 |
| --- | --- | --- |
| 全路由权限矩阵（agent 默认拒绝；member/reviewer/admin） | `control-api/.../auth.py` | matrix2 SEC-031 |
| User→Agent 委托 + 代际票据（HMAC，10min） | `delegation_service.py` | matrix4/5 SEC-053 |
| 授权链：grant∩connection∩delegation scopes + required_scopes | `invocation_service.py::_authorize_scope_chain` | matrix3/4 |
| 幂等回放重授权（含 scope 收紧/连接撤销） | 同上 | SEC-037/043/049 |
| 历史 reads 票据 + 按记录重授权 | `router.py::_agent_invocation_scope/_history_record_authorized` | SEC-050/052 |
| 结果生命周期：分级(restricted=metadata-only)/TTL/Fernet/清理 | `result_vault.py`+`result_read.py` | matrix5/6 |
| IP 钉扎（直连模式）：解析→校验→连字面量 IP | `network_policy.py::PinningNetworkBackend` | SEC-034 |
| URL 策略与 DNS 拆分（网关模式 DNS 归网关） | `network_policy.py::check_url_policy` | 部署验收 |
| 永久拒绝集（含非 link-local 云元数据） | `network_policy.py::PERMANENTLY_DENIED_NETWORKS` | audit tests |
| 网关：认证(Proxy-Auth)/端口边界(裸名=443)/配额 | `egress-gateway/.../main.py` | test_audit_egress.py |
| 响应守卫：类型门先行/默认拒绝/组合 schema fail-closed/allOf 不相交合并 | `response_guard.py` | matrix3/5/6/7 |
| 发现层清洗：$ref 有界解析/预算/敏感注解剥离 | `discovery-sdk/.../openapi.py` | 同左 |

### 部署拓扑（deploy/docker-compose.yml）

```
internal 网（无出口路由）: control(仅此一网!), executor, mock-crm(demo)
egress 网（真实出口）:     egress-gateway（双宿）
executor 出站 = 经网关（PP_SWITCH_EGRESS_PROXY 带 token）
control 持有三把主密钥，零公网出口（审计后收敛）
容器加固：read_only/cap_drop ALL/no-new-privileges/限额
```

## 3. Gate R1 剩余任务（下一步的活，按评审拍板顺序）

进度 4/8（EGRESS-01/02/03 + E2E-05 部署腿已完成，docs/30）：

| # | 任务 | 验收标准 | 备注 |
| --- | --- | --- | --- |
| AUD-DEP-04 | 真实宿主机网络策略实测 | Linux 主机 DOCKER-USER/nftables forward 规则实测，覆盖 IPv6/容器重启地址漂移/DNAT-SNAT | 样例在 docs/28 §1.3；**需要一台真实 Linux 主机**，CI 只覆盖了容器级 |
| AUD-SECRET-06 | 真实 SecretProvider | OpenBao 或 OS Keyring 实现 `SecretProvider` 接口 + 合成密钥泄露全链测试 | 接口在 `packages/secret-provider`；Mock 之外要新增实现；这是接入真实凭证的核心前置 |
| AUD-ID-07 | OIDC/身份集成 | OIDC/JWT audience 替换 dev token；身份委托与多租户隔离复测 | 现在是 DB token 哈希（`auth.py`），AuthContext 形状已预留生产替换 |
| AUD-DATA-08 | 多实例/恢复演练 | 多实例混合密钥并存轮换、PG 备份恢复、加密备份 | runbook 在 docs/28 §4；re-encrypt 已分页 |

四项全过 → 做一次统一准入判定（评审原话：用完整验收矩阵判定，不再逐轮追加）。**真实凭证准入前不得宣称企业生产就绪。**

之后的产品方向（评审同意转向）：浏览器 API Collector（Chrome MV3）、
数据库能力归集、持续采集、Agent 工具市场——蓝图 docs/09 阶段 6-7。

## 4. 日常操作速查

```bash
# 环境（Windows + Git Bash）
uv sync --all-packages          # 必须 --all-packages！见坑#2
uv run -m pytest                # 255 tests（testpaths 在 pyproject）
uv run ruff check . --fix
uv run -m mypy packages apps    # 57 文件零错误是基线
uv run python scripts/check-no-secrets.py

# 本地起全栈（control 8911/executor 8912/mock 8913/console 5173）
uv run python scripts/dev-up.py --ui
uv run python scripts/seed-demo.py

# 部署验收（CI 的 docker-deploy job 就是跑它；本机需 docker daemon）
bash deploy/verify-deployment.sh

# CI：.github/workflows/ci.yml 五个 job
#   python(双OS) / frontend / postgresql(PG16) / docker-deploy
# .github/workflows/security.yml：密钥扫描 + P0 测试 + canary 位置检查

# 提交（用户身份固定）
git -c user.name="CheeseMac" -c user.email="841995886@qq.com" commit ...
git push   # 远端 git@github.com:CheeseMac/pp-switch.git（SSH 免密）
```

盯 CI 的方法（GitHub API，凭证瞬态取用绝不打印）：
```bash
cred=$(printf "protocol=https\nhost=github.com\n\n" | git credential fill)
token=$(echo "$cred" | grep "^password=" | cut -d= -f2-)
curl -s -H "Authorization: Bearer $token" \
  "https://api.github.com/repos/CheeseMac/pp-switch/actions/runs?per_page=2"
# 日志：jobs/{id}/logs 会 302 到预签名 blob —— 先取 redirect_url 再裸取，
# 带 Authorization 头去取 blob 会 401（坑#9）
```

## 5. 踩坑清单（下个会话必读，都付过学费）

1. **本机系统代理拦截 127.0.0.1**：httpx 默认 trust_env=True 读 Windows
   系统代理 → localhost 测试栈 502/空 body。所有出站 client 一律
   `trust_env=False`（e2e/seed/executor/SDK 测试都已处理；新写 client 记得）。
2. **`uv sync` 默认剪掉非 root workspace 成员** → 一堆
   ModuleNotFoundError。永远 `uv sync --all-packages`。
3. **Bash heredoc 截断/转义坑**：复杂 Python 编辑脚本经 heredoc 传参时
   反斜杠转义与截断会静默破坏内容（本会话踩了两次：`\\r\\n` 变实际字符、
   条件断言中途失败导致整批替换没写入）。**改用 Write 工具写临时 .py
   脚本文件再执行**；文件编辑优先用 Edit 工具。
4. **SQLite naive datetime**：读回的 datetime 无 tzinfo，
   `.timestamp()` 按本地时区（UTC+8）解释 → 判过期提前 8 小时。
   统一走 `_epoch()`（calendar.timegm）。新代码比较时间别直接 .timestamp()。
5. **`.gitignore` 通配吞夹具**：`*.har.json` 曾静默吞掉
   `tests/fixtures/**/*.har.json` → 本地绿 CI 挂。加夹具要核对
   `git ls-files`，需要时加 `!` 白名单。
6. **目录改名后旧 `__pycache__`**：rename 不改 mtime → pyc 里嵌的旧路径
   （pp-swich）出现在 traceback。清理：`find . -name __pycache__ -exec rm -rf`。
7. **PG 测试三坑**（第八轮 CI 迭代）：共享 PG 库导致 token_hash 唯一
   冲突 → 用 `ppswitch_db.testdb.provision_test_database` 每测试建独立库；
   PG 强制 FK 而 SQLite raw SQL 静默容忍 → 测试种子必须补全父链；
   SQLite 方言函数（group_concat）PG 不存在 → 用 ORM 遍历替代。
8. **无路由容器的 DNS 现实**（Gate R1 最大教训）：`internal: true` 网络
   容器无法解析外部域名。网关模式下 executor 只做 URL 策略
   （check_url_policy），解析+IP 策略归网关——不要把全量校验塞回 executor。
9. **GitHub Actions 日志下载**：见 §4（302 预签名 + 401 陷阱）。
10. **canary 位置检查**：新增含 CANARY_/sk- 正则的**脱敏代码路径**必须
    同步更新 `.github/workflows/security.yml` 的豁免 grep；新增合成
    token 前缀要加进 `scripts/check-no-secrets.py` 的
    ALLOWED_VALUE_PREFIXES（现有：MOCKKEY-/MOCK-/dev-token-/
    dev-internal-token-synthetic/CANARY_/ci-internal-token-/
    ci-ticket-key-/ci-egress-token-）。
11. **部署 CI 脚本**：可达性对照不能用固定公网 IP（httpbin 换过 IP，
    对照腿超时误报）——运行时从网关解析；探针必须带网关认证 token
    （否则 401 被误当目标策略拒绝）；镜像不含 scripts/ 且只读根文件
    系统 → 测试数据经应用自身模块在容器内播种。
12. **httpcore 1.0.9 钉扎要点**：自定义 backend 继承
    `httpcore.SyncBackend`、覆写 connect_tcp 传字面量 IP；`SyncStream`
    不在顶层导出；TLS server_hostname 由 connection.py 取自 origin
    （与连接目标解耦，这正是钉扎不破坏证书校验的原因）。
13. **SQLite 的 Uuid 列**存无连字符 hex：raw SQL 参数用 `uuid.hex` 不是
    `str(uuid)`（生产代码里 approvals 消费就是这么写的）。
14. **Windows IAB 浏览器自动化**（如果又需要）：Playwright 注入式 click
    全部超时，用页面内 `element.click()`（evaluate）最可靠。
15. **评审节奏**：每轮评审的验收句会变成下一轮的检查点——把安全不变量
    写成测试（如 TestRevocationInvariant），别只改代码。

## 6. 红线（任何改动不得违反）

- **不提供** `get_secret`/`proxy_any_url`/`run_shell`/`execute_any_sql`
  任何等价面；SDK 公共 API 清单有测试锁定。
- **身份绝不信 LLM 参数**：一切身份来自服务端验证（token/签名票据）；
  模型可填的参数没有身份语义（`agent:<id>:<user>` 参数形式已废弃）。
- 仓库只允许**合成凭证**（CANARY_*/MOCKKEY-*/dev-token-*/ci-*）；
  `check-no-secrets.py` 必须保持 clean。
- GitHub 凭证经 `git credential fill` 瞬态使用，**绝不打印/落盘**。
- fail-closed 默认：不可解释的 schema/未知对象/过期授权/解密失败
  一律拒绝，宁可不可用不可泄露。
- demo 模式默认关闭；非 demo 启动强制三把独立密钥
  （internal/delegation/result-encryption），缺一拒启。

## 7. 关键人物与沟通

- 用户：舒华（基金公司开发侧）。提交身份 CheeseeseMac <841995886@qq.com>
  （注意是 CheeseMac）。
- 评审方："蓝图设计师"——每轮以 markdown 文档送达，语气专业、逐条给
  编号和验收标准；回应文档也是写给 TA 看的交付物。
- 汇报习惯：中文、先结论后细节、如实报告失败与迭代。

## 8. 文档索引

- 蓝图：docs/00-20（规范来源）；测试计划 docs/11（canary 常量）
- 评审回应：docs/21-30（每轮一份）+ 本文件 31
- 部署：docs/28（三层出口模型/防火墙样例/轮换 runbook/升级不可逆）
- ADR：002-009 散布在 docs/21-26 各 §3（personal 映射/显式消歧/钉扎
  层级/数据留存/委托身份/轮换/schema 三路径/结果读取单出口）

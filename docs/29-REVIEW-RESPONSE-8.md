# 29 · 第八轮评审回应（Review8 Response · PR-QA-06 部署与 PG 矩阵）

> 对应评审：《pp-switch 第八轮技术复审》（2026-10-14，NET-13-F1/F2 通过，
> 批准进入 PR-QA-06；DEP-14-F1/F2 并入本轮一次性解决）。
> 本轮交付：**egress-gateway 受控出口**（F1 架构修复）、防火墙路径修正
> （F2）、部署默认值加固、PostgreSQL CI 矩阵与 Docker 部署验收流水线。

## 0. 总览

| 评审项 | 核实 | 处理 |
| --- | --- | --- |
| DEP-14-F1 出口可用性断裂 | 属实（internal 网络阻断一切业务出站） | 新组件 **egress-gateway**（CONNECT-only 受控出口）+ executor 网关模式 |
| DEP-14-F2 防火墙挂错链 | 属实（OUTPUT ≠ 容器 FORWARD 路径） | DOCKER-USER / nftables forward 样例 + 验证清单 |
| control 公网默认监听 | 属实 | `127.0.0.1:8911:8911`，暴露需显式反代 |
| mock-crm 默认启动 | 属实 | `profiles: [demo]`，生产默认不启 |
| QA-PG / QA-DEP 矩阵 | 采纳为验收标准 | CI 两个新 job + 参数化测试数据库 |

全仓 **247 测试**全绿（+10），ruff / mypy（57 文件）/ 密钥扫描通过。

## 1. DEP-14-F1：egress-gateway（受控业务出口）

评审的架构判断成立：无路由隔离把"用密钥安全调用外部 API"这一核心
价值一起切断了。修复 = **隔离保留 + 专用受控出口**：

- 新应用 `apps/egress-gateway`（端口 8914）：**CONNECT-only 正向代理**，
  只隧道 HTTPS；明文 HTTP 转发一律 403（与 endpoint 默认 https-only
  闭环）；
- **独立 allowlist**（`EGRESS_ALLOWED_HOSTS` / `_FILE`，由运维与
  endpoints 的 approved_hosts 同步维护）——executor 应用层校验与网关
  校验是两道独立信任根；
- 每个目标解析 IP 过 `canonical_ip`（v4-mapped/NAT64 归一），
  link-local/元数据段**即使被列入 allowlist 也硬拒**；网关连接已验证
  的 IP 字面量（钉扎纪律与 executor 相同）；
- **TLS 端到端**：网关只转发密文，凭证仍只在 executor；
- 每次 allow/deny 写审计日志；
- executor 新增 `PP_SWITCH_EGRESS_PROXY`：配置后出站 transport 走
  网关（应用层 approved_hosts 校验仍先执行）；不配置保持直连 + 钉扎
  （本地开发模式不变）。

本地测试（无需 docker）：真实自签 TLS 服务 + 网关全链路 7 项——
隧道端到端调用、未批准拒绝、明文拒绝、元数据/NAT64 硬拒、allowlist
文件加载、**executor 网关模式端到端调用**（plan→checker→代理→网关→
TLS 源站）。

Compose 拓扑：`executor(internal only)` → `egress-gateway(internal+egress
双宿)` → 外部；QA-DEP-02 的两种后端都覆盖：外部 HTTPS（经网关）与
同网内部对端（直连，隔离网内的可信内网语义）。

## 2. DEP-14-F2 与部署默认值

- docs/28 防火墙样例改为 **FORWARD 路径**：iptables 用 `DOCKER-USER`
  链、nftables 挂 `forward` hook；明确标注必须实测的验证面（IPv6、
  容器重启地址漂移、DNAT/SNAT）——样例在 QA-DEP-04 通过前不作为
  生产依据；
- compose：control 端口默认 `127.0.0.1` 绑定；mock-crm 进 `demo`
  profile（`docker compose config` 验证：默认三服务，`--profile demo`
  四服务）。

## 3. PR-QA-06 验收矩阵（CI 落地）

评审 §7 的矩阵逐项映射：

| 矩阵项 | 载体 |
| --- | --- |
| QA-PG-01 迁移升级 | `packages/db/tests/test_qa_pg_matrix.py`：从 pre-wipe 版本升级 → 存量明文清零、元数据/FK 完整 |
| QA-PG-02 多租户 | 既有 matrix6 租户隔离测试在 PG job 下运行 |
| QA-PG-03 并发 | 新增：8 线程并发消费同一审批**恰好一个成功**；6 线程并发插同幂等键**恰好一个成功** |
| QA-PG-04 轮换 | 既有轮换测试（matrix6）+ runbook 并存窗口要求（§4 评审说明已在 docs/28 §4） |
| QA-PG-05 生命周期 | 既有加密 E2E（matrix6）在 PG 下运行 |
| QA-DEP-01..05 | `deploy/verify-deployment.sh`（CI `docker-deploy` job）：构建→健康检查→外部 HTTPS 经网关→内部对端直连→未批准/元数据/明文拒绝→容器级直连阻断→凭证不入日志 |

CI 新增两个 job（`ci.yml`）：

- **postgresql**：PG 16 service，`PP_SWITCH_TEST_DB_URL` 注入，跑
  db + control-api + e2e 全套（conftest 已参数化，SQLite 默认不变）；
- **docker-deploy**：ubuntu runner 实机构建镜像并执行验收脚本。

本机无 Docker daemon，docker-deploy job 首跑结果以 CI 为准（本文件
随 CI 结果更新——如实记录，不预支结论）。

## 4. 已知边界与后续

- docker-deploy 首跑可能需要按真实环境微调（httpbin 外部依赖、runner
  拉镜像耗时）——迭代到绿为止，不宣称未验证的结论；
- QA-DEP-04 的宿主机防火墙（DOCKER-USER）部分：CI 覆盖容器级阻断；
  宿主机层规则需在真实主机实测（样例已修正路径）；
- 多实例轮换的并存窗口：runbook 流程已在 docs/28 §4 固化，分布式
  协调（实例状态表/锁）留待企业试点按需引入（评审同意不引入队列）。

## 5. 验证记录

- `uv run -m pytest`：**247 passed**（egress-gateway 7 + QA-PG 矩阵 3 +
  既有全量复验；testpaths 补录 egress-gateway）
- ruff / mypy（57 文件）/ `check-no-secrets.py`（新增 `ci-internal-token-`/
  `ci-ticket-key-` 合成前缀）：全部通过
- `docker compose config`：默认排除 mock-crm、127.0.0.1 绑定、
  egress-gateway 双宿拓扑确认
- CI：postgresql 与 docker-deploy 两个新 job 随本提交首跑

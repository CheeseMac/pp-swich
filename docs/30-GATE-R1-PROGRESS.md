# 30 · 生产就绪综合审计回应（Gate R1 进度）

> 对应评审：《pp-switch 生产就绪综合审计（第一版结论）》（2026-10-14，
> 有条件通过：Pre-alpha 基线获批，真实凭证暂不批准）。
> 评审建议的 **Production Readiness Gate R1**（8 项）作为统一发布门槛，
> 本轮完成其中可在代码与 CI 内闭环的 4 项（EGRESS-01/02/03 + E2E-05
> 的部署链部分），其余如实标记未完成。

## 0. Gate R1 进度

| Gate 项 | 状态 | 本轮交付 |
| --- | --- | --- |
| AUD-EGRESS-01 元数据/回环/私网目标策略 | ✅ 完成 | `PERMANENTLY_DENIED_NETWORKS` 独立策略集（executor + gateway 共享） |
| AUD-EGRESS-02 CONNECT 端口与协议边界 | ✅ 完成 | 裸主机名仅 443；非标准端口需显式 `host:port` |
| AUD-EGRESS-03 出口代理认证与配额 | ✅ 完成 | Proxy-Authorization（Bearer/Basic）+ 并发/速率/时长配额 |
| AUD-E2E-05 真实部署全链路测试 | ✅ 完成（CI） | 部署验收新增完整 Agent→Executor→Gateway→Backend→Guard 链 |
| AUD-DEP-04 真实宿主机网络策略 | ⏳ 未完成 | 需真实 Linux 主机实测（样例已就绪） |
| AUD-SECRET-06 真实 SecretProvider + 泄露测试 | ⏳ 未完成 | 依赖 OpenBao/Keyring 实现 |
| AUD-ID-07 OIDC/身份集成 | ⏳ 未完成 | PR-FEAT-07 |
| AUD-DATA-08 多实例轮换/PG 恢复/加密备份演练 | ⏳ 未完成 | runbook 已有，需真实多实例环境 |

全仓 **255 测试**全绿（+8），ruff / mypy / 密钥扫描通过。

## 1. AUD-EGRESS-01：永久拒绝策略集

评审对 `is_link_local` 的批评成立——阿里云 `100.100.100.200` 与 AWS
IPv6 `fd00:ec2::254` 都不在 link-local 段。修复：显式策略集
`PERMANENTLY_DENIED_NETWORKS`（`network_policy.py`，executor 与网关
共享同一实现）：

- `169.254.0.0/16`（经典 IMDS）、`100.100.100.200/32`（阿里 ECS）、
  `fd00:ec2::254/128`（AWS IPv6 IMDS）、`fe80::/10`、`0.0.0.0/8`、
  `::/128` + `is_unspecified`；
- **allowlist 列入也无效**（测试逐项锁定，含包装形态
  `::ffff:100.100.100.200`）；
- RFC1918 私网维持"显式批准/allowlist 才放行"——支持企业内网 API 的
  既定语义，但与元数据硬拒分层清晰。

## 2. AUD-EGRESS-02：端口边界

- allowlist 裸主机名 → **仅 443**；
- 非标准 TLS 端口必须显式 `host:port` 条目授权；
- 端到端测试：主机在 allowlist（仅显式端口条目）时 CONNECT 443 被拒；
- 文档明确：**CONNECT 不证明 TLS**——端到端 TLS 归属客户端（executor）
  的证书校验，网关只保证目标与端口边界。

## 3. AUD-EGRESS-03：工作负载认证与配额

- **认证**：`EGRESS_PROXY_TOKEN`——CONNECT 请求必须携带
  `Proxy-Authorization`（Bearer 或 Basic，httpx 的 URL 内嵌凭据即 Basic）；
  未认证 → 407 + 审计 `classification=unauthorized`。空 token = 本地
  测试模式（compose 生产模板强制注入）；executor 的代理 URL 内嵌同一
  token；
- **配额**（per-peer）：并发隧道 32、连接速率 60/10s、隧道时长 300s
  （超时断开并记审计）——网关不再可能被当作无界通用 TCP 出口；
- 测试：407 / 认证成功全链路 / 429 洪水拒绝。

## 4. 部署拓扑与验收升级

按评审 §4 收敛：

- **control 移出 egress 网络**（仅 internal）——高密钥组件不再拥有任何
  公网出口侧；
- 容器加固：`read_only` 根文件系统、`cap_drop: ALL`、
  `no-new-privileges`、tmpfs、CPU/内存限额（compose `x-hardening`）；
- **PG 部署模板** `deploy/docker-compose.postgres.yml`（独立 postgres
  服务 + healthcheck + control 切换 `postgresql+psycopg://`）；
- **部署验收脚本升级**（`deploy/verify-deployment.sh`）：
  - **非 demo 生产配置启动腿**：三密钥强制、healthz、dev 目录 404；
  - **AUD-E2E-05 全链路腿**（评审点名的证据盲区）：容器内经 API 走
    **完整链**——dev 登录 → HAR 发现导入 → capability → 双人审核发布 →
    connection（合成凭证）/profile/binding → **invoke** → executor →
    凭证派发 → egress gateway（认证）→ 外部 HTTPS 后端
    （httpbin.org/uuid）→ response guard → 结果断言；
  - **拒绝测试防误判**：未批准主机拒绝后必须命中网关审计日志的
    `classification=host_not_in_egress_allowlist`（不再把任意网络错误
    当作安全拒绝）；阿里云元数据地址硬拒断言；
  - **可达性对照**：先证明网关容器能连通目标 IP（对照面），再证明
    executor 直连同一 IP 被容器级阻断；
  - **全链 canary**：调用使用的合成凭证（MOCKKEY-*）不得出现在任何
    容器日志与响应负载中（后端选 /uuid——不回显请求头，避免把回显
    误当泄露源）。

## 5. 评审 §4 其余差距的处置

| 差距 | 处置 |
| --- | --- |
| 认证仍为开发态 | AUD-ID-07（未完成，PR-FEAT-07） |
| SecretProvider 为 Mock | AUD-SECRET-06（未完成） |
| 镜像固定版本 | 基础镜像已用固定 tag（python3.12-bookworm-slim）；digest 钉随 AUD-DEP-04 |
| 多实例轮换实测 | AUD-DATA-08（未完成，runbook 已有） |
| 宿主机防火墙 | AUD-DEP-04（未完成，样例已修正至 FORWARD 路径） |

## 6. 验证记录

- `uv run -m pytest`：**255 passed**（AUD-EGRESS-01/02/03 共 +8）
- ruff / mypy（58 文件）/ `check-no-secrets.py`（新增
  `ci-egress-token-` 合成前缀）：全部通过
- `docker compose config`：control 仅 internal、hardening 生效、
  PG override 四服务解析通过
- CI docker-deploy job 以升级脚本首跑（结果随提交记录）

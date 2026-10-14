# pp switch

![pp switch cover](assets/pp-switch-github-cover.png)

> **Discover APIs. Switch Profiles. Protect Secrets. Empower Agents.**
>
> 通用 API 发现、连接切换、凭证保护与 Agent 安全能力平台。

**项目状态：Pre-alpha —— 首竖切 + 四轮评审安全加固（PR-SEC-01…05、v0.1.2、v0.1.3、v0.1.3.1 撤销不变量补丁）完成（2026-10-14）**  
**本仓库启动文档版本：v0.1.0，2026-10-13**  
**品牌名称：`pp switch`；仓库 / 包名称：`pp-switch`。**

## 当前实现状态（诚实清单）

**已实现并可在本仓库运行：**

- Monorepo（`uv` Python workspace + `pnpm`）：`packages/core|db|discovery-sdk|policy-engine|secret-provider|python-sdk` 与 `apps/control-api|executor|mcp-adapter|demo-mock-crm|web-console`
- 领域契约：Pydantic v2 模型、错误码、能力状态机（draft → reviewed → verified → published，发布版不可变）
- 数据层：SQLAlchemy 2 ORM + Alembic 迁移（SQLite 开发 / PostgreSQL 生产基线），含复合外键租户约束与乐观锁并发切换
- 发现管道：清洗校验的 HAR / OpenAPI 导入、路径模板归一化、指纹去重、JSON Schema 推断；未脱敏上传直接 422 拒绝
- 能力注册与审核发布：review → verify → publish 工作流 + 输出白名单
- 连接与档案：Connection（仅存 `secret_ref`，不存明文）、Profile（同 Service 对齐校验）、一键切换（409 版本冲突保护）
- 受信执行器：HMAC 签名的执行计划 + 防重放 + 二次参数校验 + SSRF 网络策略（默认拒绝私网/元数据地址）+ 响应守卫（白名单投影、密钥形状脱敏、大小限额）
- Secretless 公开 SDK `import ppswitch`：仅 `invoke / list_capabilities / profile_status`，无任何 `get_secret` 面
- MCP 适配器（官方 `mcp` v2 SDK，stdio）：`tools/list` 按发布能力过滤，`tools/call` 走同一调用链
- React 控制台：Services / Discovery / Capabilities / Connections / Profiles（一键切换）/ Invocations / Security 七页
- 安全测试：docs/00 的 10 个验收场景、P0 安全用例（蜜罐不落库、未发布 404、保留参数 422、越权 403、密钥失联 fail-closed、在途快照等）与仓库密钥扫描脚本
- CI：lint / typecheck / 全量测试（双 OS）/ 密钥扫描（`.github/workflows`）
- 安全加固第一轮（技术评审后）：Connection 所有权三段校验、管理 API 仅限人类且审核≠创建人、审批绑定不可变 Proposal（请求人+版本+档案+参数摘要+15 分钟时效）并原子消费、幂等键绑定主体（冲突 409 而非泄漏）、执行器路径段校验+最终 URL 复检+强制主机白名单+元数据段不可豁免+请求时 DNS 复验+流式限长、响应守卫输出 Schema 递归白名单投影、执行快照脱敏（不存参数/secret_ref/目标地址）、demo 模式默认关闭且生产配置不完整拒绝启动（对应攻击组合测试 SEC-021…030）
- 安全加固第二轮 v0.1.2（复核后）：全路由权限矩阵（Agent 默认仅可见已授予的已发布能力并可调用；member/reviewer/admin 角色）、User→Agent 委托表与按委托人隔离的 Agent Profile 绑定（共享 Agent 互不覆盖，SEC-033）、连接层 IP 钉扎（解析-校验-连接同一地址，DNS 检查后不可被二次解析改道）、响应守卫类型门先行 + 未知对象默认拒绝 + allow_fields 与 Schema 取交集、OpenAPI 导入期 Schema 清洗（example/default 等注解与密钥形状字符串不落库不进工具目录）、grant/connection/delegation scopes 交集授权、幂等回放前重新鉴权、上传分块限额读取、MCP 必填参数前置与工具名冲突双端拒绝、private/team/organization 可见性过滤、YAML 导入（对应回归测试 SEC-031…042）
- 安全加固第三轮 v0.1.3（复审后）：幂等回放绑定委托用户（共享 Agent 不能跨用户复用缓存结果）、Capability required_scopes 显式授权链（空链不满足 scoped 能力）、Agent 目录公共视图（仅 published 版本、无内部 execution/allow_fields、过期 grant 剔除）、签名式 Delegation Context 票据取代参数选身份（ADR-005）、高风险审批与 Endpoint 审批提升至 reviewer 角色、响应守卫拒绝无 items 数组与未解析 $ref、endpoint 默认强制 HTTPS、手动创建 Capability 过导入级元数据清洗（对应回归测试 SEC-043…048 与共享 Agent 综合攻击场景）
- 安全加固第四轮 v0.1.3.1（复审后）：幂等回放前重验完整 scope 链与 connection 存活（授权缩小/撤销后缓存不可再取）、Agent 历史查询强制委托票据并按委托用户过滤（撤销即刻生效）、输出 Schema 根节点 $ref 一律拒绝、委托票据生产强制独立签名密钥（ADR-005 修订/ADR-007）（对应回归测试 SEC-049…051 与撤销不变量四路径）

**计划中、尚未实现（请勿当作已有能力）：**

- 浏览器 Collector（Chrome MV3 授权采集）与 Postman 导入
- OS Keyring / OpenBao SecretProvider（当前仅确定性 Mock provider）
- OIDC / JWT audience 生产认证（当前为受控本地 dev token）
- PostgreSQL RLS、配额与预算、审批工作台 UI、Contract drift 告警、Edge Connector / mTLS
- SDK 的传输级生产认证（见 `packages/python-sdk` 文档中的安全声明）

**demo 模式默认关闭**：`PP_SWITCH_DEMO_MODE` 仅由 `scripts/dev-up.py` 显式开启；非 demo 下 dev 登录目录/mock 凭证/默认内部 token 均不可用，生产配置不完整时服务拒绝启动。

**安全边界声明：** 本仓库实现的是开发态安全闭环（synthetic 数据 + mock 凭证 + fail-closed 默认值），不构成生产安全认证。威胁模型与限制见 [docs/05](docs/05-SECURITY.md) 与 [docs/18](docs/18-SECRETLESS-DEVELOPMENT.md)。

## 快速开始（本地演示）

```bash
# 1. 安装依赖（Python 3.12+ / Node 20+）
uv sync --all-packages && pnpm install

# 2. 启动全部服务（control 8911 / executor 8912 / mock CRM 8913 / console 5173）
uv run python scripts/dev-up.py --ui

# 3. 另开终端：初始化演示数据（ principals / service / connections / profiles ）
uv run python scripts/seed-demo.py

# 4. 打开 http://localhost:5173 —— 选择 Alice 登录，然后：
#    Discovery 导入 tests/fixtures/synthetic-har/crm.har.json
#    -> Capabilities 创建 crm.get_customer（Alice 为创建人）
#    -> 退出并以 Carol 登录完成 Review -> Verify -> Publish（审核≠创建人）
#    -> 回到 Alice：Profiles 一键切换 CRM Demo A/B
#    -> Invocations 调用并观察数据集随档案切换
```

测试：`uv run pytest`（209 个用例，含三服务真实 E2E 与攻击组合矩阵 SEC-021…030、SEC-031…042、SEC-043…048 及共享 Agent 综合攻击场景）；检查：`uv run ruff check . && uv run mypy packages apps`。

## 解决什么问题

人在日常工作中使用许多网页、内部系统和 API，但这些系统的接口通常缺少统一发现、统一目录和统一授权机制。Agent 既不知道有哪些能力，也不能安全地直接持有人的密钥。

`pp switch` 希望形成以下闭环：

**授权发现 → 本地脱敏 → 接口归集 → 契约验证 → 能力发布 → 选择连接档案 → 服务端授权执行 → 安全返回 Agent**

## 核心能力

1. **Discover**：导入 OpenAPI、HAR、Postman；从经用户授权的网页网络流量中识别候选 API；发现数据库 schema。
2. **Catalog**：将重复 API 合并为版本化的 `Service → Operation → Capability` 目录，支持个人/团队/企业共享。
3. **Switch**：类似“点击切换配置档案”的桌面工具，一个点击就切换当前工作区、目标服务使用的**连接配置档案**（账号授权 + 已审核端点），但绝不向模型暴露凭证。
4. **Secure**：凭证存储在受信任的本地/服务端；用户和 Agent 双重授权；敏感操作审批；日志脱敏和网络出口约束。**Secretless Development**：源代码只引用能力别名，应用通过 pp switch Broker/Executor 调用真实 API，避免把供应商密钥放在 AI Coding Agent 可读取的 `.env`。
5. **Expose**：把获批 Capability 通过受控 HTTP API 和 MCP 暴露给 Agent；未来兼容更多 Agent 工具协议。
6. **Extend**：新系统优先提供 OpenAPI + 标准身份协议 + 可选 `x-pp-switch` 扩展，实现低成本接入。

## 一键切换的准确含义

- 用户在 UI 中选择 **Workspace / Service / Profile**，例如 `CRM → 我的工作账号` 或 `CRM → 测试账号`。
- 后台只修改经过权限验证的**活动配置绑定**；不会把 Cookie、API Key 或明文密码复制进聊天上下文。
- 一次工具执行开始时固定其使用的档案快照。切换影响**后续**调用，不会改变已在运行的任务。
- 这里的 Profile 是 `endpoint + connection + policy scope`，不是绕过网页登录、替其他人登录或修改浏览器 Cookie 的工具。

## 明确的非目标（v0.1）

- 不做 GUI / RPA / 桌面软件操作。
- 不静默监听用户访问的所有网站，不窃取浏览器会话。
- 不保证发现的私有 HTTP 接口都可长期、合法地独立调用。
- 不给 Agent 提供 `read_secret`、`execute_any_sql`、`call_any_url`、`run_shell`。
- 不开发新的加密算法，不替代 OAuth/OIDC、OpenBao 或成熟 KMS。

## 文档导航

| 想了解的内容 | 文件 |
|---|---|
| 先从哪里开始编码 | [00-START-HERE](docs/00-START-HERE.md) |
| 产品理念、用户角色、需求与范围 | [01-PRODUCT-VISION-PRD](docs/01-PRODUCT-VISION-PRD.md) |
| 核心架构、信任边界、部署形态 | [02-ARCHITECTURE](docs/02-ARCHITECTURE.md) |
| 网页 API 自动发现、HAR / 数据库处理 | [03-API-DISCOVERY](docs/03-API-DISCOVERY.md) |
| 能力模型、契约格式、状态机 | [04-CAPABILITY-CONTRACT](docs/04-CAPABILITY-CONTRACT.md) |
| 密钥、权限、威胁模型与安全规则 | [05-SECURITY](docs/05-SECURITY.md) |
| 数据库 ERD、SQL 结构与多租户规则 | [06-DATABASE](docs/06-DATABASE.md) |
| REST / MCP 接口与错误码 | [07-API-SPEC](docs/07-API-SPEC.md) |
| Monorepo 目录结构与模块职责 | [08-REPOSITORY-STRUCTURE](docs/08-REPOSITORY-STRUCTURE.md) |
| 阶段任务、依赖、开发顺序与验收 | [09-IMPLEMENTATION-PLAN](docs/09-IMPLEMENTATION-PLAN.md) |
| 本地、内网和服务器部署 | [10-DEPLOYMENT](docs/10-DEPLOYMENT.md) |
| 测试矩阵与可验证安全目标 | [11-TEST-PLAN](docs/11-TEST-PLAN.md) |
| UI 交互、一键切换与接入向导 | [12-UX-FLOWS](docs/12-UX-FLOWS.md) |
| 技术决策、待确认事项、权衡 | [13-DECISIONS](docs/13-DECISIONS.md) |
| 开源协作与企业采纳 | [14-OPEN-SOURCE](docs/14-OPEN-SOURCE.md) |
| 一手资料与相关开源项目 | [15-REFERENCES](docs/15-REFERENCES.md) |
| 给开发 Agent 的执行规则 | [16-AGENT-DEVELOPMENT-RULES](docs/16-AGENT-DEVELOPMENT-RULES.md) |
| 故障与操作手册 | [17-OPERATIONS-RUNBOOK](docs/17-OPERATIONS-RUNBOOK.md) |
| **AI 编码助手与 `.env` 密钥隔离（核心）** | **[18-SECRETLESS-DEVELOPMENT](docs/18-SECRETLESS-DEVELOPMENT.md)** |
| 首批可直接交给编码 Agent 的任务 | [19-FIRST-CODING-TASKS](docs/19-FIRST-CODING-TASKS.md) |
| **v0.1.0 首竖切实现审核报告（送审）** | **[20-IMPLEMENTATION-REVIEW](docs/20-IMPLEMENTATION-REVIEW.md)** |
| **技术评审回复与安全加固记录（v0.1.1）** | **[21-REVIEW-RESPONSE](docs/21-REVIEW-RESPONSE.md)** |
| **第二轮复核回复与 v0.1.2 加固记录** | **[22-REVIEW-RESPONSE-2](docs/22-REVIEW-RESPONSE-2.md)** |
| **第三轮复审回复与 v0.1.3 加固记录** | **[23-REVIEW-RESPONSE-3](docs/23-REVIEW-RESPONSE-3.md)** |
| **第四轮复审回复与撤销不变量补丁** | **[24-REVIEW-RESPONSE-4](docs/24-REVIEW-RESPONSE-4.md)** |

**优先阅读顺序**：00 → 01 → 02 → 04 → 05 → **18** → 06 → 07 → 08 → 09 → 19。网页采集开发前加读 03；UI 开发前加读 12。

## 技术基线（提案）

- **Backend**：Python 3.12+、FastAPI、Pydantic v2、SQLAlchemy 2、Alembic、httpx。
- **Web UI**：TypeScript、React、Vite。
- **Discovery browser extension**：TypeScript、Chrome Manifest V3；先做 HAR 导入，再做授权采集。
- **Storage**：SQLite 用于单机开发和 demo；PostgreSQL 为团队/企业正式部署基线。
- **Secrets**：可插拔 `SecretProvider`；单机 OS Keyring / 企业 OpenBao 或托管 KMS。控制平面不能获取明文。
- **Agent**：官方 MCP Python SDK（v2 系列）实现 MCP Adapter，HTTP 统一调用保持为内部稳定契约。
- **Optional**：Redis/NATS/OPA 仅在具体规模和治理需求出现后引入。

以上为**计划依赖**，不是已经安装或实现的功能；首次落地应锁定经过 CI 验证的具体版本。

## 安全的初始化配置样例

仓库内附带 [`.env.example`](.env.example)、[`pp-switch.example.yaml`](pp-switch.example.yaml) 和 [`.gitignore`](.gitignore)。这些**只是非秘密配置示例**；`.gitignore` 只能降低误提交风险，不能阻止拥有文件系统权限的 AI 编码助手读取文件。因此真正的生产 Key 仍必须留在独立 Broker/Executor 的 SecretProvider 中。

## 给 AI 编码助手的入口

使用根目录 [`AGENTS.md`](AGENTS.md) 作为开发规则入口。具体编码分工看 [19-FIRST-CODING-TASKS](docs/19-FIRST-CODING-TASKS.md)；真实密钥迁移和隔离策略看 [18-SECRETLESS-DEVELOPMENT](docs/18-SECRETLESS-DEVELOPMENT.md)。AI 编码环境只使用 mock Profile，不拥有生产 Broker 身份。

## 推荐的第一个演示

使用一个不含真实客户数据的模拟 CRM：

1. 在 UI 中导入两条脱敏的 HAR API 观察记录；
2. 识别出 `GET /api/customers/{customer_id}`，生成候选操作；
3. 审核发布 `crm.get_customer`；
4. 创建两个不同的模拟连接档案；
5. 点击切换活动档案；
6. Agent 通过 MCP 调用 `crm.get_customer`，Executor 使用所选档案访问 mock backend；
7. 执行日志记录 `profile_id` 等非秘密标识，但模型输出无凭证；
8. 换成无权限的 Agent 时必须得到拒绝。

## 文档承诺与限制

`docs/` 交付的是完整开发蓝图与规范；本仓库已按蓝图实现上述"当前实现状态"清单中的第一竖切。示例 SQL / JSON / YAML 中尚未落地的部分（浏览器采集、企业特性）仍需配套代码、迁移和测试之后才能投产。安全边界需要独立测试验证，不能仅凭文档宣称"绝对不会泄漏"。

欢迎贡献，但不得上传真实凭证、真实用户 HAR、客户数据或未获授权的第三方私有接口样本。

## License

Apache-2.0（见 [LICENSE](LICENSE)）。依赖均为宽松许可证（MIT/BSD/Apache）：FastAPI、Pydantic、SQLAlchemy、Alembic、httpx、jsonschema、mcp、React、Vite。

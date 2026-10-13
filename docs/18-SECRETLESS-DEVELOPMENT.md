# 18 · 不把密钥交给 AI 编码助手：无 `.env` 凭证开发模式

> **新增 P0 产品能力**：这是 pp swich 与普通 API 目录/MCP 网关的关键差异。
>
> 目标：项目代码和 `.env.example` 只写**能力别名、档案名称与非秘密配置**；实际带凭证的 HTTP 请求通过 pp swich 可信服务发往目标。Agent 能参与开发、测试，但不能读到供应商 API Key / 数据库密码 / OAuth Token。

## 1. 为什么只是把密钥搬出 `.env` 还不够？

危险的原始代码：
```dotenv
OPENAI_API_KEY=sk-real-key-here
CRM_TOKEN=real-crm-token
DATABASE_URL=postgresql://admin:real-password@db.example/production
```

危险并不仅是 Git 泄漏。AI 编码助手可能读取 `.env`、shell history、进程环境、调试输出、IDE 索引或容器环境，并把内容加入模型上下文或执行外发代码。

把它们改成：
```dotenv
CRM_TOKEN=pp-swich://secret/crm-prod
```
**仍不够**：如果 SDK 能解析出真正的 `CRM_TOKEN` 并将其返回到应用进程，而 AI Agent 有权读取应用进程/执行任意代码，仍可能取得原始 Token。

必须分清：
- **Alias**：标识业务能力/连接，公开不代表拥有权限。
- **Authentication**：调用者证明真实身份，不能只知道 Alias 就能调用。
- **Authorization**：允许这个调用者在何种环境执行什么业务动作。
- **Execution**：只有受信任 Executor 接触后端密钥。
- **Isolation**：AI 开发代理所在 OS 用户/沙箱不能直接访问生产 Executor 的管理口、密钥或生产网络。

## 2. 推荐默认：Secretless API Proxy（真正的无密钥编程）

### 2.1 项目 `.env.example` 中只有非秘密配置

```dotenv
PP_SWICH_MODE=local
PP_SWICH_BROKER_SOCKET=/run/user/1000/pp-swich/consumer.sock
PP_SWICH_WORKSPACE=personal
PP_SWICH_PROFILE=crm-dev
PP_SWICH_MODEL_ALIAS=llm.fast
```

路径仅为示例；跨平台实际通过 SDK 选择 Unix socket / Windows Named Pipe / HTTPS（工作负载身份）。这些字段是**标识，不是授权凭据**。使用该配置运行的应用必须被可信系统认证且拥有独立授权。

### 2.2 Python SDK 目标用法（拟开发 API）

```python
from ppswich import PPSwichClient

pp = PPSwichClient.from_local_broker()

# SDK 只传能力名称和业务参数；没有 get_secret 方法
customer = pp.invoke(
    capability="crm.get_customer",
    arguments={"customer_id": "C-1024"},
)
print(customer["display_name"])
```

执行路径：
```text
Application
  └─ pp.invoke("crm.get_customer", args)
       └─ authenticated pp swich Broker
            ├─ verify caller identity, user/agent grants
            ├─ resolve selected profile and immutable snapshot
            ├─ enforce target/resource policy and budget
            ├─ worker fetches downstream credential privately
            ├─ HTTP request to allowlisted CRM endpoint
            └─ project/validate response → return safe fields only
```

**不会返回 CRM_KEY 或原始 Header。** AI 只看到 SDK 类型和 Capability Schema。

### 2.3 模型厂商 API Key 的兼容

可以建立一种受控 `ModelGateway`：
```python
reply = pp.invoke(
    capability="llm.chat",
    arguments={
        "model": "llm.fast",
        "messages": [{"role": "user", "content": "Hello"}]
    }
)
```

若第三方 SDK 要求 OpenAI-compatible `base_url`，可提供兼容代理，例如：
`http://127.0.0.1:8765/compat/openai/v1`。

但是：
- `base_url` 不是凭据；仍须通过经过认证的本地 IPC、受限代理身份或短期 Scoped Credential 来鉴权。
- 如果 SDK 必须设置 `api_key`，**不要将原始供应商 Key 填进去**；可只在受信应用中使用短期、仅限模型/预算的 pp swich 代理 Token。
- 代理 Token **同样是可滥用的 Bearer 凭据**。它不暴露供应商主密钥，但如果 AI 能读它，仍可能产生调用费用/数据访问。严格安全模式下禁止 AI Agent 获取该 Token，使用隔离执行进程和工作负载身份。

可以参考 [LiteLLM Proxy](https://docs.litellm.ai/docs/simple_proxy) 的“虚拟 Key / 模型别名”理念，但 pp swich 的默认模型应优先采用**不向调用进程返回后端密钥**的能力代理。

## 3. 一键切换如何影响项目代码

例：在 UI 中选择当前工作区的 `CRM / staging-qa` Profile。代码保持不变：
```python
pp.invoke("crm.get_customer", {"customer_id": "C-1024"})
```

控制平面只更新：
`ActiveBinding(workspace_id, principal_id, service_id, context_key) → profile_id`。

已开始的 Invocation 固定自己绑定的 `profile_snapshot_id`；后续调用读取新 Profile。切换的作用域必须显式，不允许把一个用户的 UI 状态误用到整个生产 Worker。

生产服务建议**固定部署级 Profile**，禁止个人一键切换直接改变已上线的所有流量；在 UI 中提供 `personal`、`agent`、`deployment` 独立上下文，生产切换走变更审批。

## 4. 可信 Broker 的本地与远程认证方式

| 环境 | 推荐机制 | 风险提示 |
|---|---|---|
| 个人本地 macOS/Linux | 独立服务进程 + Unix domain socket + OS peer identity + 用户确认 | 同 UID 的任意进程可能复用身份；不能抵御同用户全权 Agent |
| Windows | 独立服务/用户身份 + ACL 保护 Named Pipe | 检查 token/用户 SID 与访问控制 |
| 企业应用服务器 | mTLS + 专用工作负载身份 / 短期证书 | 证书私钥由平台安全提供，不放源码目录 |
| Cloud/K8s | 原生 workload identity + 服务网格/身份代理 | 不是依赖 K8s 的唯一方式 |
| CI Runner | OIDC 到 Broker 的短期范围绑定 | 避免长期 `PP_TOKEN` 存在 CI Secrets 中 |
| AI 编码 Agent 沙箱 | 仅 mock Broker 和隔离网络 | 不接入 production socket 或敏感网络 |

**本地同 OS 身份限制很关键**。Unix socket `chmod 0600` 只能隔离其他 UID；如果 Agent 能以同一 UID 执行任意代码，它通常也能调用该 socket。要强隔离就需要不同 OS 用户、容器/VM、权限边界或远程服务批准。

## 5. `pp-swich` CLI 预期功能

拟设计而非已实现：
```bash
ppswich init
ppswich doctor
ppswich service list
ppswich profile list --service crm
ppswich profile use crm/staging-qa --context personal
ppswich capability list
ppswich capability test crm.get_customer --fixture demo.json
ppswich migrate env --scan-only
```

管理员可以通过**受信 GUI / OS 凭证输入渠道**存入凭证：
`ppswich connection add` 仅引导打开安全输入界面，默认不允许 `--password ...` 或在 AI 可记录的终端提示明文。不打印凭证、不使用终端命令参数传 Key（shell history/process list 风险）。

迁移命令 `migrate env --scan-only` 仅报告 key 名称和位置，**永不打印 value**；真正迁移必须由可信用户在隔离环境完成。

## 6. 对原有 `.env` 项目的迁移流程

### 阶段 0：风险扫描和冻结
1. 暂停把真实 `.env` 上传、复制到 AI Chat 或提交到仓库。
2. 扫描代码仓库、提交历史和 CI 历史是否泄漏过凭证。
3. 发现已经外泄的凭证必须**立即撤销/轮换**；仅删除 Git 文件不能撤销曾泄漏的密钥。
4. 创建 `.env.example`，只保留别名和非秘密配置。
5. 为 Agent 设置文件忽略规则和 sandbox；注意 `.gitignore` 不会禁止读取文件。

### 阶段 1：建 Capability / Profile
1. 在 pp swich 注册 `Service`、允许的 `Endpoint` 和 Operation。
2. 为每种调用创建 `Capability` 和输入/输出白名单。
3. 通过可信本地 GUI / 企业 Vault 建立真实 Connection。
4. 为 dev/staging/prod 创建独立 Profile；为 production 默认启用审批。
5. 在调用运行时之外存储和轮换供应商主密钥。

### 阶段 2：改造代码
**迁移前：**
```python
import os, httpx
token = os.environ["CRM_TOKEN"]
response = httpx.get(
    "https://crm.example.com/api/customers/C-1024",
    headers={"Authorization": f"Bearer {token}"},
)
```

**迁移后：**
```python
from ppswich import PPSwichClient
pp = PPSwichClient.from_local_broker()
response = pp.invoke(
    "crm.get_customer",
    {"customer_id": "C-1024"},
)
```

旧代码若需要流式响应、文件上传、WebSocket，应扩展**明确受限且版本化**的能力适配器，不能退回为“任意目标通用 HTTP + 动态 Header”。

### 阶段 3：验证
- 代码中不再引用真实 `*_KEY`, `*_TOKEN`，仅有 `PP_SWICH_*` 非秘密变量。
- AI 开发沙箱不能读真实 Broker socket、Keyring 和 `.env` 历史备份。
- `ppswich doctor` 检查当前运行环境是否存在意外密钥环境变量（只报告**名称**，不回显值）。
- 用合成蜜罐 Key 执行调用，确保日志、异常、追踪、MCP 工具结果和**发往模型的请求**不含 Key。
- 模拟 AI 修改调用参数，检查权限、限流和资源作用域始终在服务端执行。
- 关闭旧 Key 和废弃的长期秘钥分发通道。

## 7. 与 `.env` 相关的用户体验

### 配置演示（不包含真实秘密）

```yaml
# pp-swich.yaml (commit-safe)
project: my-crm-tool
workspace: my-team
bindings:
  crm:
    capability_namespace: crm
    profile: crm-dev
  llm:
    capability_namespace: llm
    profile: llm-fast
runtime:
  broker: local
  isolation: required
```

`pp-swich.yaml` 可以在代码仓库里分享；Profile/Endpoint 的可见性仍受企业目录权限控制。

### 提示性 UI

- `Secretless ✓`：未发现可被编码代理读取的真实密钥，受信执行器权限校验成功。
- `Risk: shared user identity`：Broker 与 AI Agent 同 UID，不能保证 Agent 无法滥用连接。
- `Profile: crm-dev`：当前测试环境。
- `Production profile requires approval`：切换生产 Profile 需审批。

**不要只靠检查某个文件是否存在就显示“安全”勾选**。需对实际身份/权限/网络进行审查；无法检查时显示“未验证”。

## 8. 权限与预算

典型策略：
```yaml
principal: developer-agent
environment: development
capabilities: [crm.get_customer, llm.chat]
profile: demo-only
max_calls_per_hour: 60
max_tokens_per_day: 100000
allowed_resources: [demo-*]
deny:
  - production
  - secrets.read
  - arbitrary_http
```

如果你的项目要连接企业数据库、邮件、财务系统，Agent 的可用能力应进一步缩小，并可要求具体每次调用批准。

## 9. 失效与应急策略

- Broker 不可用：拒绝真实调用，不能自动退回读取本地 `.env`。
- 密钥撤销：Connection 标记 `revoked`，阻止新调用，谨慎处理在途请求。
- Profile 失效：前端显示身份/环境不可用并禁止“静默降级”到其他身份。
- 开发 Agent 要求导出 Key：拒绝并推荐通过已发布的能力调用。
- 环境变量中仍有 Key：报警，但不能只因删除 `.env` 就认定 Key 已从 shell、CI、镜像层、Git 历史消失。
- 必须保留临时兼容路径：在独立进程和安全边界中运行，标记不符合 secretless 强安全等级。

## 10. P0 产品验收（新增）

| 编号 | 断言 |
|---|---|
| SLD-001 | 示例代码和 `.env.example` 没有真实密钥与可解密引用 |
| SLD-002 | SDK 仅提供 `invoke`，无 public `get_secret/export_secret` |
| SLD-003 | 模型可见工具信息不存在 `secret_ref` / 目标 Header |
| SLD-004 | mock Worker 可调用，生产凭证不需出现在本地项目 |
| SLD-005 | 恶意输入 `target_url`, `Authorization`, `credential_ref` 被拒 |
| SLD-006 | 同 UID AI Agent 环境未隔离时显示安全风险，不宣称无泄漏 |
| SLD-007 | Dev Agent 身份不可调用 prod Connection |
| SLD-008 | 模型请求/日志/执行结果无蜜罐值 |
| SLD-009 | pp swich 可点击切换 dev Profile，进行中的请求绑定仍不变 |
| SLD-010 | 旧凭证泄漏后可执行轮换、撤销并完成访问审计 |

## 11. 相关参考

- [OWASP Secure Coding with AI](https://cheatsheetseries.owasp.org/cheatsheets/Secure_Coding_with_AI_Cheat_Sheet.html)
- [LiteLLM Proxy / Virtual Keys](https://docs.litellm.ai/docs/proxy/virtual_keys)
- [OpenBao Auth Methods](https://openbao.org/docs/auth/approle/)

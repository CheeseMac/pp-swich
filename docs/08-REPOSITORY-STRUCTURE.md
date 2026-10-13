# 08 · Monorepo 代码目录与模块职责

## 1. 目录总体建议

为了让 Python、Web UI、浏览器扩展、CLI、测试和协议适配一起迭代，第一版采用 Monorepo。以下为**待创建的目录结构**，不是当前文档包已存在的源码。

```text
pp-swich/
├── README.md
├── LICENSE                         # 开源前确认 Apache-2.0 并真正加入正文
├── SECURITY.md
├── CONTRIBUTING.md
├── AGENTS.md                       # AI 编码助手执行守则；无生产秘密
├── pyproject.toml                  # root tooling / uv workspace / Ruff / mypy / pytest
├── uv.lock
├── pnpm-workspace.yaml
├── package.json
├── pnpm-lock.yaml
├── .env.example                    # 只有非秘密配置
├── pp-swich.example.yaml           # commit-safe 能力别名配置
├── .gitignore
├── .github/
│   ├── workflows/
│   │   ├── ci.yml
│   │   ├── security.yml
│   │   └── release.yml
│   ├── ISSUE_TEMPLATE/
│   └── PULL_REQUEST_TEMPLATE.md
│
├── apps/
│   ├── control-api/
│   │   ├── pyproject.toml
│   │   ├── src/ppswich_control/
│   │   │   ├── main.py
│   │   │   ├── api/v1/
│   │   │   │   ├── services.py
│   │   │   │   ├── discoveries.py
│   │   │   │   ├── operations.py
│   │   │   │   ├── capabilities.py
│   │   │   │   ├── connections.py
│   │   │   │   ├── profiles.py
│   │   │   │   └── invocations.py
│   │   │   ├── domain/
│   │   │   │   ├── entities.py
│   │   │   │   └── errors.py
│   │   │   ├── services/
│   │   │   │   ├── capability_service.py
│   │   │   │   ├── profile_service.py
│   │   │   │   └── review_service.py
│   │   │   ├── repositories/
│   │   │   ├── auth/
│   │   │   └── settings.py
│   │   └── tests/
│   ├── executor/
│   │   ├── pyproject.toml
│   │   ├── src/ppswich_executor/
│   │   │   ├── main.py
│   │   │   ├── task_verify.py
│   │   │   ├── dispatch.py
│   │   │   ├── adapters/
│   │   │   │   ├── http_adapter.py
│   │   │   │   ├── database_adapter.py
│   │   │   │   └── mock_adapter.py
│   │   │   ├── response_guard.py
│   │   │   ├── secret_provider.py
│   │   │   └── network_policy.py
│   │   └── tests/
│   ├── mcp-adapter/
│   │   ├── pyproject.toml
│   │   ├── src/ppswich_mcp/
│   │   │   ├── server.py
│   │   │   ├── tool_registry.py
│   │   │   └── transport.py
│   │   └── tests/
│   ├── discovery-worker/
│   │   ├── pyproject.toml
│   │   ├── src/ppswich_discovery/
│   │   │   ├── parsers/har.py
│   │   │   ├── parsers/openapi.py
│   │   │   ├── parsers/postman.py
│   │   │   ├── normalizer.py
│   │   │   ├── schema_inference.py
│   │   │   ├── fingerprint.py
│   │   │   └── sanitizer.py
│   │   └── tests/
│   ├── web-console/
│   │   ├── package.json
│   │   ├── src/
│   │   │   ├── app/
│   │   │   ├── pages/
│   │   │   │   ├── ServicesPage.tsx
│   │   │   │   ├── DiscoveryPage.tsx
│   │   │   │   ├── CapabilitiesPage.tsx
│   │   │   │   ├── ProfilesPage.tsx
│   │   │   │   ├── ConnectionsPage.tsx
│   │   │   │   └── SecurityPage.tsx
│   │   │   ├── components/
│   │   │   │   ├── ProfileSwitcher.tsx
│   │   │   │   ├── ApiContractEditor.tsx
│   │   │   │   └── ReviewDrawer.tsx
│   │   │   ├── api/
│   │   │   └── types/
│   │   └── tests/
│   └── browser-extension/          # 第二阶段
│       ├── manifest.json
│       ├── src/
│       │   ├── background/
│       │   ├── popup/
│       │   ├── capture/
│       │   ├── sanitizer/
│       │   └── permissions/
│       └── tests/
│
├── packages/
│   ├── core/                       # domain models, typed errors, security enums
│   ├── discovery-sdk/              # reusable HAR/OpenAPI normalization
│   ├── capability-sdk/             # typed schema + protocol-independent invocation
│   ├── python-sdk/                 # `import ppswich` public client
│   ├── policy-engine/              # RBAC, scoped ABAC and delegation checks
│   ├── secret-provider/            # SecretProvider abstract API
│   │   ├── mock/
│   │   ├── keyring/
│   │   └── openbao/
│   ├── db/                         # ORM models, Alembic migration, DB utilities
│   └── protocol-contracts/         # JSON Schema/OpenAPI wire contracts
│
├── connectors/                     # 第二阶段
│   ├── local-broker/
│   ├── http-edge/
│   └── database/
│
├── configs/
│   ├── capabilities/examples/
│   ├── policies/examples/
│   └── agent-ready-profile.schema.json
│
├── deployments/
│   ├── local/
│   ├── compose/
│   ├── systemd/
│   ├── windows-service/
│   ├── reverse-proxy/
│   └── k8s/                        # 可选，非强依赖
│
├── tests/
│   ├── fixtures/synthetic-har/
│   ├── fixtures/openapi/
│   ├── fixtures/mock-crm/
│   ├── contract/
│   ├── integration/
│   ├── e2e/
│   ├── security/
│   └── performance/
│
├── scripts/
│   ├── dev-up.sh
│   ├── seed-demo.py
│   ├── check-no-secrets.py
│   └── generate-contracts.py
│
├── docs/
│   ├── 00-START-HERE.md
│   ├── ...
│   └── 18-SECRETLESS-DEVELOPMENT.md
│
└── assets/
    └── pp-swich-github-cover.png
```

## 2. 依赖规则（防循环依赖）

允许：
```
apps/control-api → packages/core, packages/db, packages/policy-engine
apps/discovery-worker → packages/discovery-sdk, packages/core
apps/mcp-adapter → packages/capability-sdk, packages/core
apps/executor → packages/core, packages/secret-provider
packages/python-sdk → packages/protocol-contracts
```

禁止：
- `packages/core` 依赖任何 apps；
- `apps/control-api` 直接 import `apps/executor.secret_provider`；
- `apps/mcp-adapter` import 或持有 SecretProvider；
- 前端调用私有 Executor 或 Secrets API；
- Discovery 自动在执行器部署/运行任意生成的代码；
- `python-sdk` 或 CLI 提供公共 `get_secret` / `export_secret` 方法；
- 把 `Connection` 和 `Profile` 合并成“一个表里存 token + target_url”。

## 3. 各模块的开发责任

| 模块 | 第一负责人任务 | 关键测试 |
|---|---|---|
| `core` | Pydantic 契约、异常、状态机 | schema 与不可变状态 |
| `db` | ORM、事务、迁移与租户约束 | FK、事务和隔离测试 |
| `discovery-sdk` | HAR/OpenAPI 转 Observation | 不保存秘密值 |
| `control-api` | CRUD、审核、Profile 切换 | 鉴权、越权、并发版本 |
| `policy-engine` | User/Agent/Resource 授权 | fail-closed |
| `executor` | HTTP mock 调用、SecretProvider、输出白名单 | SSRF、凭证不外泄 |
| `mcp-adapter` | tools/list 与 tools/call | 权限过滤/参数验证 |
| `web-console` | 仪表盘、接入向导、一键切换 | 状态回显、审批提示 |
| `python-sdk` | Secretless invoke / broker auth | 不暴露原始密钥 |
| `browser-extension` | 用户主动采集 / 本地清洗 | 权限、隐私、脱敏 |
| `local-broker` | OS IPC / 工作负载身份 | 同 UID 风险与访问拒绝 |

## 4. Python 领域接口（目标形状）

### Public SDK

```python
from typing import Any, Protocol

class CapabilityClient(Protocol):
    def invoke(self, capability: str, arguments: dict[str, Any]) -> dict[str, Any]:
        ...
```

真实实现必须带 timeout、认证、Request ID、版本和传输错误类型；此接口仅表达最小操作面。

### SecretProvider（仅内部）

```python
from typing import Protocol

class SecretProvider(Protocol):
    async def use_credential_for_request(
        self,
        credential_binding_id: str,
        request_plan: "ApprovedRequestPlan",
    ) -> "SanitizedResponse":
        ...
```

此示例刻意让公共层只返回已过滤响应。**仅执行器内部适配器**可以在受信上下文内拿到实际 Token；不允许 Public SDK 依赖该模块。

### Operation Adapter

```python
from typing import Protocol

class OperationAdapter(Protocol):
    async def execute(
        self,
        invocation: "AuthorizedInvocation",
    ) -> "SanitizedCapabilityResult":
        ...
```

`AuthorizedInvocation` 是不可变、经过来源鉴别的类型，而非让客户端任意构造的 JSON。

## 5. 前后端契约管理

- Control API 从 Pydantic / FastAPI 生成 OpenAPI；
- Web TypeScript 类型由构建步骤生成，不重复手写字段；
- 内部 Capability Contract 使用 JSON Schema 验证；
- 在 CI 里比较契约快照，防止不兼容修改静默进入主分支。

## 6. 本地开发与构建计划（待实现）

```bash
# 未来在代码仓库根目录使用：
uv sync --all-packages
pnpm install --frozen-lockfile
uv run pytest
uv run ruff check .
uv run mypy .
pnpm -r build
```

当前文档包**没有**真正的 `pyproject.toml`、`uv.lock`、`package.json` 或运行服务；这些命令是目标开发流程，不能当作现有可执行步骤。

## 7. 开发环境的安全要求

- 生产密钥在 IDE / AI 代码代理无法读取的信任边界之外；
- `.env.example` 只能包含 `PP_SWICH_*` 非秘密配置；
- 本地开发用 mock 连接和 synthetic fixture；
- 真实密钥的初始化必须通过安全 UI 或独立受信渠道进行；
- AI Agent 不应该参与运行生产密钥录入过程；
- 本地 Broker 的 OS 访问控制仅在**不同用户/沙箱**隔离的前提下成立。

# 35 · FEAT-03 交付记录：本地 Keyring + 真实 Profile 切换

> 2026-10-15 批准开工，同日交付。演示目标（批复原文）：两个测试凭证、
> 两个 Profile、一键切换、同一 Agent 工具两次调用、可信后端证据证明
> 用了不同凭证、Agent 全程拿不到 Key。

## 0. 一句话

人在本机把两把测试 Key 存进 **OS 安全存储**（Windows 凭据管理器 /
macOS 钥匙串 / Linux Secret Service）→ 控制台引用名字建两个连接 →
激活（受信执行器证明可解析后才生效）→ 两个 Profile → 点击切换 →
**同一个工具两次调用返回不同账号的业务数据**；撤销连接后切换即被拒。
密钥值从头到尾不出安全存储，控制面/模型上下文/工具 Schema 只见
`keyring://名字`。

## 1. 交付内容

### packages/secret-provider（新 keyring 模块）

- `KeyringSecretProvider`：解析 `keyring://<name>` → OS 安全存储；
  **fail-closed**——无安全后端/条目缺失/载荷损坏一律
  `SecretProviderUnavailable`，绝不退回明文文件或环境变量；
- `DispatchingSecretProvider`（executor 新默认）：按 ref 方案分派，
  `keyring://` 走 OS 存储、`mock://` 走 mock——单部署可同时服务
  demo 与真实本地连接；
- `ppswitch-secret` CLI（人用，跑在 executor 同机）：
  `put`（**隐藏输入或 stdin，绝不走命令行参数**）/`fingerprint`
  （只出 sha256[:8]，永不出值）/`delete`。

### executor

- `POST /internal/v1/secrets/verify`：激活前证明 ref 可解析，只返回
  指纹与 scheme，绝不返回材料；
- 非 demo 模式下 `mock://` 仍被既有防线拒绝（未改动）。

### control-api

- `POST /connections` 新增 `secret_name`（人键入的 keyring 名，非密钥；
  校验 `[a-z0-9][a-z0-9/_-]{2,63}`）→ 连接 `pending`；
- `POST /connections/{id}/activate`：问 executor 验证后置 `active`
  （fail-closed：存储不可达/缺条目 → 503 不激活）；
- `POST /connections/{id}/revoke`：撤销后策略引擎拒绝其调用，且
  **无法再绑定到该连接的 Profile**（e2e 验证：切换即 403）。

### 控制台

Connections 页：凭证来源二选一（demo 数据集 / OS keyring 名 +
auth scheme）、Activate/Revoke 操作、完整流程指引文案。

## 2. 验收证据（真实服务器栈 e2e）

`tests/e2e/test_full_stack.py::TestFeat03KeyringProfiles`
（进程内假 keyring 后端，真实 OS 存储不被触碰）：

1. 存两把测试 Key（MOCKKEY-DEMO-A/B-feat03，格式对 Mock CRM 有效）；
2. 建两个 keyring 连接（pending → activate 成 active）；
   未存 Key 的连接激活被拒（503 SECRET_PROVIDER_UNAVAILABLE）；
3. 两个 Profile 挂同一已审批 endpoint；
4. 切到 A 调 `crm.get_customer(C-1024)` → **Acme Industries**；
   切到 B 同一调用 → **Acme Industries (QA copy)**——不同凭证的
   业务可见证据；
5. 后端证据：两次调用的 profile/connection 各不相同；
6. Agent 可见载荷无任何 MOCKKEY；
7. 撤销 B：切到 B 直接 403（绑定期 fail-closed），A 恢复正常。

单测：keyring provider 13 项（roundtrip、缺条目/坏载荷/坏方案
fail-closed、指纹不含值、删除、分派器路由）。

## 3. 安全边界

- 密钥只进 OS 安全存储；CLI 值走隐藏输入；控制台/控制面只见名字；
- 激活 = executor 实测可解析（不信任声明）；
- 指纹是载荷 sha256[:8]（证据用，非可逆材料）；
- Agent/模型可接触面（工具 Schema、调用结果、清单）与 FEAT-01/02
  相同：零凭证。

## 4. 已知边界

- "真实企业 API Key"仍归 Gate R1 SECRET-06（OpenBao / 企业密钥管理）；
  FEAT-03 覆盖**个人本地环境**；
- keyring 后端不可用的机器上，keyring 连接无法激活（by design）；
- 凭证轮换 = `ppswitch-secret put` 同名覆盖 + 无需重建连接
  （ref 不变）；已在线调用无中断语义（下次解析生效）。

## 5. 数字

Python 300 + vitest 31 = 331 项测试；
ruff / mypy / 密钥扫描 / 双端构建全绿（CI 见提交记录）。

# 41 · SECRET-06 共享 Keyring 多用户隔离——审批修订稿

> **状态：已批复实施（2026-10-17，三项勘误为约束条件）；§8 已按勘误修订。** 本版取代 `7404fbe` 原稿（按 2026-10-16 设计审批四项裁决修订），落库于 `ff40ba9` 之后。§0–§7 为修订正文；**§8 为开发侧按 §6 要求提交的接口契约附录**（仅固定实现机制，不改变 §0–§7 安全语义）。继承此前所有已关闭 FEAT 的正式批复，不重审历史问题。
>
> 本文保留原 §0–§7、G1–G4、A1–A8，新增补充验收 T1–T8。仅针对单 control、单 executor、单共享 OS Keyring 命名空间的部署。

## 0. 结论

采用 **可证明属主的机器级名字 claim + 完整 HMAC 材质钉扎 + claim 代际撤销** 三层保障。

* **个人可信单用户模式**：原 `ppswitch-secret put → Activate` 流程不变。
* **共享执行器模式**：首次 claim 必须有独立属主证明，不能使用 first-activation-wins；未完成 claim 或钉扎者不可执行。
* **任意执行前**：executor 验证签名计划、当前 claim 代际/钉扎版本及材质完整性；不匹配 fail-closed。
* 本文是提交评审的修订设计，**不是开工许可证**。

## 1. 原始缺口与修复目标

| 编号 | 原始缺口 | 修复 |
|---|---|---|
| G1 | 任意成员可引用任意 Keyring 名创建 Connection | create/activate 校验机器级 claim；未 claim 仅允许 pending |
| G2 | B 借自己的 Connection 调用 A 的凭证 | 首次 claim 使用独立所有权证明；Agent 委托不绕过 |
| G3 | verify 短指纹被丢弃，未形成安全钉扎 | 保存完整密钥化 MAC；关联 Connection 与签名计划 |
| G4 | Keyring 重 put 后，执行可能静默使用新材质 | executor 每次使用前完整性比较、版本新鲜性检查 |

共享 OS 账户本身能直接读写 Keyring，不在本次可防御的 OS 攻击范围；本切片关闭的是经 pp-switch API 的凭证借用与静默材质替换。

## 2. 威胁模型和不可变条件

**主体**：human principal、Agent、workspace admin、独立 infra operator、可信 control/executor 和共享 Keyring。

1. 名字可读或凭证可由 executor 解析，均**不构成属主证明**。
2. claim 的唯一范围为 `(namespace_id, canonical_name)`，在单个 executor 的全部 workspace 之间生效。
3. 创建、激活、计划组装、executor 的 execute / verify / PostgreSQL discover 路径都必须统一执行授权与完整性检查。
4. 缺少 owner/claim、缺少 pin、凭证变化、旧 claim 版本、证明失效或撤销状态不可确认时，一律 fail-closed。
5. workspace admin **不能**凭本工作区角色跨工作区释放 claim；只有 claim owner 或独立的 infra operator 才有相应操作资格。
6. 配对证明、凭证与内部完整 MAC 不向普通 API、Agent、UI 或审计日志暴露。
7. 释放后新开始的执行必须拒绝旧计划；已完成最后检查、已经发起的外部请求不能保证撤回，该边界须明示和审计。

## 3. 设计

### 3.1 名字级 claim：授权层

**迁移 9：** `keyring_claims` 至少包含下列逻辑字段：

| 字段 | 含义 |
|---|---|
| `namespace_id`, `name` | 组合唯一键；单 executor 仍记录不可混淆的命名空间 ID |
| `owner_principal_id`, `owner_workspace_id` | 归属人和工作区 |
| `auth_scheme` | `postgres` 等凭证方案 |
| `generation`, `pin_version` | 单调递增的归属和材质版本 |
| `material_tag` | 完整 HMAC，视为敏感派生数据，不进入日志 |
| `status`, `claimed_at`, `updated_at` | active / pending_rotation / released / disputed 等状态及时间 |
| `proof_consumed_id` | 仅记录已消费证明的不可逆 ID，不存明文证明 |

Connection 新增关联的 `claim_generation`、`pin_version`、`secret_material_tag` 与待恢复状态；签名计划冻结 `namespace_id/name/generation/pin_version/material_tag`。删除或释放也必须保留不可回退的代际计数/墓碑，避免 ABA 旧计划复活。

**共享模式首次归属的两种合法机制：**

1. executor 受保护本机通道签发一次性配对证明：绑定 namespace、规范化名字、principal、workspace 与用途 `keyring.claim`；建议有效期 5 分钟；高熵、单次原子消费、禁止跨对象重放。获取证明必须要求真实本机操作权限，**普通远程 control API 不得替任何 principal 无条件签发证明**。
2. 独立的 infra operator 通过受保护运维入口预登记。workspace admin 不自动拥有此权限。

CLI `ppswitch-secret put` 继续保持原样；可另增 `claim-pair` 本机命令。任何证明不得出现在 argv、URL、普通日志或截图中，可通过受保护 stdin/隐藏输入提交。

**服务端执行契约：**

- `create_connection`：已有 claim 且 owner 不符时返回 `403 KEYRING_NAME_CLAIMED`，隐藏 owner；共享模式下未 claim 只允许创建不可执行 `pending_claim` Connection。迁移产生的 `pending_claim`/`disputed` 行**不授予任何所有权**：shared 模式恢复须完整双件证明（FIX-01），personal 模式仅候选 owner 本人可凭重验恢复。
- `activate_connection`：证明验真 + executor verify + 数据库原子 claim/CAS；竞争时只允许一个 winner。verify 失败不留下 active claim，证明不得二次消费。避免网络调用时长时间持数据库锁，可采用短期 reservation + CAS 最终提交。
- `plan assembly`：复查 Connection owner、claim owner/workspace、generation、pin_version、active 状态，并冻结至签名计划；继续执行已有 Agent 委托/能力权限。
- executor **最后检查**：验证计划签名，获取可信的最新 claim 代际/撤销状态，重新解析 Keyring 材质并校验 MAC，然后才能发起外部请求。单 executor 可使用受信 control 新鲜性查询或等价强一致同步；不能只验证旧签名计划或无限期缓存。
- **释放**：owner 自助 release；infra operator 强制 release。推进 generation、失效旧 Connections/Profile 绑定和计划，同步撤销信息。其他 workspace admin 不可跨域释放。新 owner 必须重新证明并 claim。
- **所有读凭证入口**（包括 FEAT-04 postgres 激活、发现、调用）使用统一校验，不增数据库能力。

### 3.2 完整性钉扎：执行层

短 `sha256[:8]` **只作 CLI 展示**，禁止参与安全决策。内部建议使用：

`HMAC-SHA-256(K_pin, encode(namespace_id, name, auth_scheme, canonical_material))`

其中 `K_pin` 仅由可信 executor / 专门密钥服务持有，与签名票据密钥分离；`encode` 必须无歧义。`canonical_material` 覆盖实际认证的所有字段（如 postgres user 和 password），不存或记录明文；完整 32 字节 MAC 使用恒时比较。

verify 将完整标签通过受保护内部协议供 control 存储；签名计划冻结标签；executor 每次材质使用前重算，不符返回 `503 SECRET_FINGERPRINT_MISMATCH` 并拒绝。MAC 视为凭证派生的敏感信息，禁止 Agent/API/审计披露；不因其为 HMAC 就公开它。

`K_pin` 遗失、轮换或 executor 重启后无法验证历史标签时，必须 fail-closed，并通过受保护密钥恢复或显式重新激活恢复；不得自动信任新材质。MAC 校验保护“被修改后不静默使用”，不阻止 OS 账户持有人直接更改存储。

**owner re-put 状态机：**

`active(vN)` → 本机 re-put → 后续执行不匹配立即拒绝 → owner 发起重新 Activate 得到 `409 SECRET_ROTATION_CONFIRMATION_REQUIRED` → owner 通过已认证的显式确认操作重新验证并原子提交 `active(vN+1)`。

轮换确认需要限时、绑定 owner/Connection/旧版本；不能允许外部提交任意新 tag。旧计划、旧 pin 和其他仍引用旧版本的 Connection 均拒绝，直到按权限重新验证。不得把材质变化自动当作换 owner 机会。

### 3.3 迁移、模式及部署

- **迁移 9**：遗留 Connection 的 tag 为 NULL，shared 模式标记 `requires_claim_recovery`，禁止调用和发现；不得保留“NULL 即跳过 pin”的回退。
- 原先按名字最早创建的 principal **仅可生成候选属主清单**，不能自动建立有效 claim。多人/多工作区引用同名时标 `disputed`，交 infra operator 调解；须明确证明并重新 verify 才恢复。
- 单用户可信部署保留现有用户体验；`mock://` 不受此安全范围影响，但正常 CI 回归必须保持全绿。
- shared 部署启动时缺少唯一 namespace、受信撤销新鲜性接口、pin key 或 claim 数据库时，拒绝启用共享 Keyring 能力。仅支持单 control/单 executor，不支持多个 control 各自维护 claim 而共享一台 executor。
- FEAT-04 PostgreSQL 的 TLS、SELECT-only 部署要求，以及 Gate R1 既有门禁保持不变。

### 3.4 错误、审计和竞态

| 场景 | 对外结果 |
|---|---|
| 他人已 claim 的名字 | `403 KEYRING_NAME_CLAIMED`，不泄露 owner |
| 材质不一致 | `503 SECRET_FINGERPRINT_MISMATCH` |
| owner 已 re-put 但未确认 | `409 SECRET_ROTATION_CONFIRMATION_REQUIRED` |
| 配对证明过期/重放或无法确认撤销新鲜性 | 拒绝；不回显证明 |
| 旧计划、已释放或未钉扎连接 | 拒绝；不得隐式恢复 |

审计 `claim.created/rejected/released/recovered/rotation_pending/rotated`，记录操作者、作用域、相关 ID、状态与时间；不记凭证明文、配对证明、完整 tag、其他 workspace 的 owner。名字必要时使用脱敏标识。释放和执行竞态以 executor 的**最终授权检查**为界：释放提交后才抵达该检查的执行必须拒绝，检查前已发起的操作不承诺收回。

## 4. 验收标准（保留原 A1–A8）

| 编号 | 必须证明 | 自动化证据 |
|---|---|---|
| A1 | B 引用 A 已 claim 名创建/激活均 403，无属主泄漏 | 双人 control 测试 |
| A2 | A 重新引用/激活、Profile 与 Agent 调用正常 | control/e2e |
| A3 | re-put 后下次调用拒绝；owner 409 显式确认后恢复 | fake Keyring/executor/e2e |
| A4 | B 投毒覆盖 A 名字，A 不会静默换凭证 | 双人 fake Keyring e2e |
| A5 | owner release / infra operator 强制 release，旧计划拒绝，新 owner 重新证明后认领 | control/executor/e2e |
| A6 | 跨 workspace 不能借用名字，ws2 admin 无权强制释放 ws1 claim | 多 workspace 回归 |
| A7 | **已修订**：shared 遗留 NULL pin 拒绝；候选/争议归属需证明恢复 | 迁移、恢复测试 |
| A8 | **已修订**：事件可审计但无材质、证明、完整 MAC 或跨工作区属主信息泄漏 | 日志/Agent/审计字节扫描 |

**新增定向验收：**

| 编号 | 验证边界 |
|---|---|
| T1 | A 已 put 未 claim，B 抢先 Activate 不得获得 A 凭证/claim |
| T2 | 同名并发 claim、证明重放/过期，只有一个合法 owner |
| T3 | release/re-claim 的 ABA 与旧签名计划重放，executor 使用 generation 阻断 |
| T4 | 截断 SHA 碰撞不能影响完整 MAC；HMAC 覆盖凭证全部字段、恒时比较 |
| T5 | postgres activate/discover/execute 三条路径均执行 claim/pin 检查 |
| T6 | 409 未确认不得自动复原；并发轮换的旧版本提交失败 |
| T7 | 重启、失去 pin key、shared 配置缺项时 fail-closed |
| T8 | Agent 已有委托也不能绕过 owner、generation、pin 约束 |

门禁：全量 pytest/前端测试、ruff/mypy、密钥扫描、CI 六 job + Security gates，以及 FEAT-03 双凭证/Agent 组合和 FEAT-04 真 PostgreSQL matrix 保持通过。CI 使用进程内 fake Keyring，不碰真实 OS 凭据库。必须人工/自动构造 `plan 已签发 → release 提交 → executor 执行` 的确定性竞态测试。

## 5. 非目标

1. 不做 OpenBao/生产 SecretProvider（SECRET-06 生产腿另立项）。
2. 不做 OS 用户/密钥环的物理隔离。
3. 不增加 FEAT-04 多行、其他数据库引擎、任意 SQL 或通用 TCP 出口。
4. 不用“名字混淆”替代权限控制。
5. 不支持多 control 共享同一 OS Keyring。
6. 本切片通过不代表 M2 人工实机或 Gate R1 企业生产通过。

## 6. 原四项开放问题——正式裁决落地

| 问题 | 裁决 |
|---|---|
| §6.1 单 control ↔ 单 executor | **同意**，为硬部署约束；namespace 显式记录 |
| §6.2 默认不加严名字抢注 | **否决**；shared 首次 claim 必须配对证明或 infra operator 预登记 |
| §6.3 owner re-put 再激活 | **同意并细化**：不匹配先拒，409 显式确认，版本推进 |
| §6.4 两个新错误码 | **同意并增加轮换 409**：`KEYRING_NAME_CLAIMED`、`SECRET_FINGERPRINT_MISMATCH`、`SECRET_ROTATION_CONFIRMATION_REQUIRED` |

残余风险：共享 OS 账户仍能直接读取和篡改凭证；可信 executor/control 被攻陷不在本切片保证之内；已经发出的外部操作无法撤销。

**实现前需在接口契约里固定**：配对证明的本机签发/传递形式；claim 的 SQL 唯一约束与原子 CAS；executor 获取最新撤销代际的方式。允许准备契约与测试设计，但改变上述安全语义须重新审批。

## 7. 获批后实施拆分

1. **core**：三个错误码、签名计划 generation/version/tag 字段和权限协议。
2. **db（迁移 9）**：机器级 claim、证明单次消费、Connection pin、遗留冲突标记、墓碑与 CAS。
3. **executor**：本机安全配对、完整 HMAC、统一材质入口、最终撤销新鲜性检查。
4. **control**：create/activate/plan 权限、显式轮换确认、owner release/infra operator 强制 release、审计。
5. **测试/文档**：A1–A8 与 T1–T8、全量回归、API-SPEC、SECURITY、升级恢复操作说明。

旧稿“10–14 文件、20 项测试”只是粗估；补上证明、代际撤销和安全迁移后，不应以该估算约束正确实现。

**申请下一步**：技术评审复核本修订稿，明确签发实施许可。**在批准前不得合入实现代码。**

## 8. 接口契约附录（实现前固定，按 §6 要求随修订稿提交）

> 本附录只固定 §6 点名的三项机制与测试映射，不改变上文任何安全
> 语义；若实现中发现需偏离，回到评审。

### 8.1 配对证明：本机签发 / 传递形式（双件兑换）

采用"控制面一次性码 + executor 本机attestation"两件套，两边都
满足才算独立属主证明（普通远程 control API 单独拿不到第二件）：

1. **意图件（control，用户认证后）**：`POST /api/v1/keyring-claims/
   pairings {name}` → 创建 `keyring_claim_pairings` 行（一次性码的
   SHA-256、绑定 principal/workspace/规范化 name、用途
   `keyring.claim`、5 分钟过期、单次消费、尝试计数熔断——复用
   FEAT-01 CollectorPairingRow 的全部防爆破机制）。码仅展示一次。
2. **本机件（executor，受 OS 权限保护的本地 IPC）**【勘误 1 修订】：
   CLI `ppswitch-secret claim-pair <name> --ticket <b64>` 在
   executor 同机运行。一次性码经**隐藏输入**（getpass）读入、随
   **请求体**提交——绝不进 argv/URL/日志；ticket 非秘密可经参数。
   本机证明 = **OS 保护的本地 IPC 通道**【定向修复 FIX-03 后的实
   现形态】：shared 模式 executor 在 0700 attest 目录内开一个
   **AF_UNIX socket**（`attest.sock`，0600；Windows 无 AF_UNIX 时为
   仅回环 TCP，随机端口写入同目录 `listener.port`，读取该文件本身
   就需要本机目录权限）。attestation **只在本地通道签发**——HTTP
   的 `/internal/v1/keyring/attest` 已移除（404），持内部令牌+偷来
   的 nonce 也无法经 HTTP 取证。一次性码经隐藏输入随通道请求体提
   交；`local.nonce`（0600）由 CLI 读出并随请求提交，**每次成功签
   发即轮换消费**（泄露值首次使用即死）。attestation =
   `HMAC(K_attest, canonical(namespace_id || name || principal_id ||
   workspace_id || proof_id || sha256(code) || purpose || exp))`，
   5 分钟、单次；K_attest 与 K_pin 分离。
3. **兑换（control activate 时原子完成）**：activate 请求携带
   pairing_code + attestation；control 校验码（哈希、TTL、单次、
   绑定本人/本名）→ 转发 attestation 到 executor
   `/internal/v1/keyring/attest/verify` 验签（恒时比较）→ 通过后
   才进入 verify 凭证 + 原子 claim 写入（§8.2）。
   attestation 全程只在 control↔executor 内部信道出现，不落
   日志/审计；**审计仅记 pairing 创建时生成的随机 proof_id**，
   不记配对码哈希（防离线猜测，勘误 1）。

个人模式（`PP_SWITCH_SHARED_KEYRING=false`，默认）：跳过 8.1 整条，
保留 put → Activate 原体验（裁决 §6.2 允许）；claim 仍建立、
generation/pin 机制不变。

### 8.2 claim 的 SQL 唯一约束与原子 CAS

- `keyring_claims`：`UNIQUE(namespace_id, name)`；`generation` 与
  `pin_version` 均 `CHECK (> 0)`；释放/争议为状态变更 + generation
  递增，**行永不清除**（防 ABA 复活）。
- 首次 claim（**仅当行从未存在**）：INSERT 竞争唯一 (namespace_id,
  name) 索引（实现为 SAVEPOINT + IntegrityError，SQLite/PG 同语义），
  唯一 winner；败者读现状按状态返回 403/409。
- **释放后重新认领【勘误 2 修订】**：released 行永不删除、也永不
  走 INSERT——重新认领是带旧代际与状态条件的原子 UPDATE：
  `UPDATE ... SET owner=..., status='active', generation=generation+1,
  pin_version=1, material_tag=:new, proof_consumed_id=:pid WHERE id=:id
  AND status='released' AND generation=:observed`；rowcount==0 即并发
  败者 409；重新认领同样要求全新配对证明（shared）或 owner 本人
  （personal）。
- 轮换确认：`UPDATE keyring_claims SET pin_version = pin_version + 1,
  material_tag = :new, status = 'active' WHERE id = :id AND
  generation = :g AND pin_version = :v AND status IN
  ('active','pending_rotation')` —— rowcount==0 即并发败者 409。
- 释放：`UPDATE ... SET status = 'released', generation =
  generation + 1 WHERE id = :id AND generation = :g`；同事务把该名字
  关联 Connections 置 `pending_claim`。
- 网络调用（executor verify/attest）一律在事务外完成，库内仅做
  短事务 CAS；不长时间持锁。

### 8.3 executor 获取最新撤销代际的方式（执行前最终检查）

- 新增反向内部查询：executor → control
  `GET /internal/v1/keyring-claims/state?name=...&namespace_id=...`
  （X-Internal-Token 共享令牌，生产换 workload identity）。control
  从 DB 实时读（不缓存），返回
  `{status, generation, pin_version}`。
- `/internal/v1/execute` 对携带 claim 字段的 keyring 计划：验签 →
  反向查询 → 与计划冻结的 generation/pin_version/status 比对 →
  重算材质 HMAC（K_pin，恒时比较）→ 全部通过才执行。任一不符
  fail-closed（503/403 族）。
- **三个入口的统一准入在 executor 侧【勘误 3 修订】**：
  `/secrets/verify` 对 keyring:// 引用必须收到 control 签发的
  purpose=verify claim ticket（60 秒预留，冻结目标 generation/
  pin_version）——仅凭内部令牌不再解析任何 keyring 材质；
  `/db/discover` 必须收到 purpose=discover ticket 并（配置了
  control_url 时）做反向新鲜性比对；`/execute` 的 keyring 计划
  必须携带 claim 上下文（缺失即拒）。首次激活因此天然是"已验证、
  短期有效的 claim reservation"：control 先签 ticket，executor 在
  预留下解析并返回材质 tag，control 随后才原子写入 claim/pin；
  verify 失败则什么都不落库（T5 覆盖）。
- 不做跨请求缓存（§3.1 "不能无限期缓存"）；本机回环调用延迟可
  接受。shared 模式启动缺 control_url 即拒启（freshness 为硬
  要求）；personal 模式未配 control_url 时跳过反向查询（单用户
  信任域；代际校验仍在 control 侧 plan 组装时执行）。

### 8.4 A/T → 测试落点（测试设计）

| 验收 | 落点 |
| --- | --- |
| A1/A2/T1/T6 | `apps/control-api/tests/test_keyring_claims.py`（双用户/双工作区 harness） |
| A3/A4 | `tests/e2e/test_keyring_isolation.py`（共享 fake keyring 双人全链） |
| A5/T3 | control 单测 + e2e（release→旧计划拒→re-claim 需新证明） |
| A6 | 多 workspace 回归（同文件） |
| A7 | `packages/db/tests` 迁移 9 回填 → shared 拒绝 → 显式恢复 |
| A8 | 审计字节扫描断言（无材质/证明/完整 MAC/跨区属主） |
| T2 | 并发 claim（线程 + CAS rowcount 断言唯一 winner）；证明重放/过期 |
| T4 | executor 单测：截断碰撞构造 + HMAC 全字段覆盖 + 恒时比较 |
| T5 | postgres activate/discover/execute 三路径 claim/pin 校验（PG matrix） |
| T7 | 配置缺项/丢 K_pin/重启 fail-closed 单测 |
| T8 | Agent 委托链上叠加 claim 约束（e2e） |
| 竞态门禁 | `plan 已签发 → release 提交 → executor 执行` 确定性时序测试（线程护栏） |

### 8.5 配置面

- `PP_SWITCH_SHARED_KEYRING`（control+executor 一致，默认 false）；
  true 时缺 `namespace_id` / `K_pin` / `K_attest` / **`K_claim_ticket`
  / `K_plan_signing`** / claim 数据库表任一项 → 拒绝启动（T7/R06）。
- `K_pin`/`K_attest` 仅 executor 侧 env（`PP_SWITCH_PIN_KEY` /
  `PP_SWITCH_ATTEST_KEY`），非 demo 缺失拒启；与内部令牌、委托
  签名密钥互不复用。
- **授权签名密钥与通道令牌分离【FIX-02】**：`PP_SWITCH_CLAIM_
  TICKET_KEY`（签/验 claim ticket 与 pairing ticket）与
  `PP_SWITCH_PLAN_SIGNING_KEY`（签/验执行计划）——control 与
  executor 两侧配置一致；shared 模式**必须显式设置**（回退内部令
  牌即拒启），个人模式回退内部令牌（单用户信任域，Alpha 无感）。
  票据签发点只存在于通过授权检查之后的服务路径（激活/轮换确认/
  发现），不存在任何通用签票接口；票据带 purpose+aud=executor。

**随修订稿一并申请：复核通过即签发实施许可，按 §7 拆分动工。**


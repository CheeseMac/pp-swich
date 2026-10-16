# 41 · 设计提案：共享执行器的 Keyring 多用户所有权隔离（SECRET-06 前置项）

> **状态：待审批**（2026-10-16 提交，基线 `f2ecc6a`）。
> 依批复流程：本提案获批前不动工实现代码。范围**不含**新的数据库
> 能力、不含 OpenBao（那是 SECRET-06 的生产 Provider 腿，需真实基础
> 设施，另案）。

## 0. 一句话结论

为 `keyring://` 凭证引入**名字级属主声明（claim）+ 激活时指纹钉扎
（fingerprint pinning）**：谁第一次成功激活一个 keyring 名字，谁就
拥有它；其他人不得通过 API 引用该名字使用其凭证；激活时记录的
材质指纹冻结进签名计划，executor 执行前重算比对——激活后任何
材质替换（无论善意恶意）立即 fail-closed。

## 1. 缺口（当前代码的精确形态）

FEAT-03 交付的 keyring 路径在**单用户个人 Alpha** 下是安全的；
在**共享执行器**（多用户、同一 OS 账户/机器 → 同一 OS keyring
命名空间 `ppswitch/<name>`）下存在横向借用：

| # | 现状 | 位置 |
| --- | --- | --- |
| G1 | 任意成员可为任意 keyring 名字建连接——`secret_name` 只做格式校验，无属主绑定 | `connection_service.create_connection`（`_KEYRING_NAME` 仅正则） |
| G2 | B 建自己的连接引用 A 存的名字 `crm-key-a`，B 激活自己的连接（owner=B 通过校验），executor 能解析到 A 的条目 → **B 以 A 的凭证调用** | 激活链无名字级授权 |
| G3 | executor verify 已返回材质指纹，但控制面**丢弃**（仅进审计）——没有任何完整性钉扎 | `connection_service.activate_connection` |
| G4 | 执行路径不校验材质指纹——激活后 keyring 条目被替换（重 put），后续调用**静默使用新材质**（投毒方向：B 覆盖 A 的名字喂入 B 的凭证，A 不知情地用 B 的身份） | executor `/execute` |

边界承认（AGENTS.md 规则 8 的口径）：共享 OS 账户本身能直接读写
OS keyring——本提案**不**声称对抗 OS 层攻击者；它关闭的是**通过
pp-switch API 的借用与投毒**（G1–G4），这是产品层可负责的部分。
OS 层隔离与生产 SecretProvider 属 SECRET-06 的 OpenBao 腿（另案）。

## 2. 威胁模型（本提案针对的攻击）

- **借用（borrow）**：成员 B 通过 API 让 executor 以 A 存储的凭证
  发起调用（G1/G2）。B 全程不需要 OS 层访问——只靠控制面 API。
- **投毒（poison）**：B 覆盖 A 已在用的 keyring 名字（B 有 OS 访问
  或共享机器上的写权限），A 的后续调用静默改用 B 的材质（G3/G4）。
  防护目标是**检测并 fail-closed**（替换必然改变指纹），不是阻止
  OS 层写入（超出产品边界）。
- **跨工作区借用**：同一控制面部署下多个 workspace 共享同一
  executor/机器——名字是机器级命名空间，claim 必须机器级生效
  （见 §3.1 部署假设）。

不在范围：OS 账户层读取/删除条目；跨机器撤销同步；控制面与
executor 之间的 mTLS（生产另有方案）。

## 3. 设计

### 3.1 属主声明（claim）——授权层

新表 `keyring_claims`（迁移 9）：

| 列 | 说明 |
| --- | --- |
| `name` | keyring 名（PK；机器级全局唯一——部署假设：一个 control 对应一个 executor，见 §6.1） |
| `owner_principal_id` | 首次成功激活者（FK principals） |
| `fingerprint` | 激活时材质指纹（sha256[:8]，与 executor verify 口径一致）；`NULL`=遗留连接回填、待 owner 重新激活钉扎 |
| `auth_scheme` | 材质声明方案（复核用） |
| `claimed_at / updated_at` | 审计时间戳 |

规则：

1. **激活时声明**：`activate_connection` 对 keyring 连接先查 claim——
   无 claim → 以当前激活者+verify 返回的指纹写入（first-activation-
   wins）；有 claim 且 owner≠激活者 → `KEYRING_NAME_CLAIMED`（403，
   消息不泄露属主身份："keyring name already claimed by another
   principal"）；有 claim 且 owner=激活者 → 校验指纹：不匹配 →
   409 要求确认（材质已变化，见 3.2 的 fail-closed 语义）。
2. **创建时预检**：`create_connection` 对已 claim 的名字仅允许
   claim owner 引用（提前失败、UX 更好；激活时仍二次校验——
   不变式在服务端两处独立成立）。
3. **调用时复核（belt+braces）**：plan 组装对 keyring 连接重查
   claim——owner≠连接 owner 或指纹与连接行不符 → 拒绝。claim
   被释放/属主变更后，他人旧连接立即失效（撤销即时性，沿用
   验收项 4 的口径）。
4. **释放**：owner 可 `POST /keyring-claims/{name}/release`（同时
   使该名字的所有既有连接走撤销语义）；admin 可
   `DELETE /api/v1/keyring-claims/{name}`（动作
   `keyring_claims.admin`，rank 2——处理离职/争议）。
5. 权限：claim 读取 `keyring_claims.read`（member，列表仅见自己
   的+名字摘要）；释放 owner 级；强制释放 admin 级。

### 3.2 指纹钉扎——完整性层

1. `ConnectionRow` 增列 `secret_fingerprint`（激活时写入；executor
   verify 已返回，落库而非丢弃）。
2. `InvocationPlan` 增字段 `secret_fingerprint: str | None`
   （签名计划冻结；仅 keyring 连接非空）。
3. executor `/execute`：resolve 后**重算**材质摘要，与
   `plan.secret_fingerprint` 不符 →
   `SECRET_FINGERPRINT_MISMATCH`（fail-closed，503 族）。这一层
   不依赖控制面正确性——即使 claim 检查被绕过（或旧连接漏网），
   材质替换也无法被静默使用。
4. 遗留语义：`NULL` 指纹（回填的旧连接）不钉扎但**仍受 claim
   授权层约束**；owner 下次重新激活即完成钉扎。新连接一律钉扎。

### 3.3 兼容与迁移

- CLI **不变**：`ppswitch-secret put <name>` 照旧（属主由首次激活
  隐式确立，人不需要知道任何 ID——这是相对"名字空间前缀"方案的
  决定性 UX 优势）。
- 单用户个人 Alpha：第一个激活者就是你，行为无感。
- 迁移 9 回填：对现存 keyring 连接按名字分组取最早 owner 写
  claim（`fingerprint=NULL`）；`secret_fingerprint` 列回填 NULL。
- mock:// demo 连接完全不受影响。

## 4. 验收标准（获批后的实施定义）

| # | 验收项 | 测试形态 |
| --- | --- | --- |
| A1 | B 引用 A 已 claim 的名字建连接/激活 → 403 `KEYRING_NAME_CLAIMED`，且不泄露属主身份 | control 单测（双用户） |
| A2 | A 自己重新引用、重新激活、调用 → 全通（同 owner 不受影响） | control 单测 |
| A3 | 激活后 keyring 条目被替换 → 下一次调用 fail-closed（executor 指纹不匹配）；重新激活（owner 本人）恢复 | executor 单测（fake keyring 换材质）+ e2e |
| A4 | 投毒方向：B 覆盖 A 的名字 → A 的调用被拒而非静默换用 B 材质 | e2e（共享 fake keyring 双用户） |
| A5 | claim 释放/admin 强制释放后：owner 旧连接调用即拒；名字可被新用户重新 claim | control 单测 + e2e |
| A6 | 跨工作区：ws2 用户引用 ws1 已 claim 的名字 → 拒绝（claim 机器级） | control 单测 |
| A7 | 遗留回填：迁移后既有 keyring 连接（fingerprint=NULL）可继续调用，但他人引用被 claim 阻止 | 迁移测试 |
| A8 | 审计：claim 建立/释放/拒绝全部落审计事件，**不含材质与指纹以外的键内容**（指纹本身非敏感） | 断言 |

回归基线：全量 pytest + CI 六 job 双绿；FEAT-03 既有 keyring e2e
（双凭证切换、Agent 组合）不回归。

## 5. 不做什么（防偷渡）

1. 不实现 OpenBao/生产 SecretProvider（SECRET-06 生产腿，需真实
   基础设施与独立验收）。
2. 不引入按用户的 OS 账户/密钥环隔离（部署形态问题）。
3. 不动 FEAT-04 任何数据库能力；不做多行查询。
4. 不给 keyring 名字做加密/混淆（名字本就不是秘密；属主关系在
   服务端）。

## 6. 风险与开放问题（请评审裁定）

1. **部署假设**：一个 control ↔ 一个 executor ↔ 一台机器的 keyring。
   若未来多 control 共享一个 executor，claim 表需上移或同步——
   本提案按单控制面部署假设实现并在文档声明。
2. **名字抢注**：恶意用户抢先激活他人**尚未存储**的名字（需先自己
   put 一个可解析条目）造成占用。缓解：这只能占用名字、得不到他人
   材质；被占用者可请 admin 强制释放（动作有审计）。可选加严：
   claim 冲突时要求新激活者证明既有连接属自己（本提案默认不加严，
   避免 UX 复杂化——请裁定）。
3. **409 重激活语义**：owner 自己 re-put 后需重新激活一次才恢复
   调用（fail-closed 的代价）。文档化为预期行为。
4. 错误码新增：`KEYRING_NAME_CLAIMED`（403）、
   `SECRET_FINGERPRINT_MISMATCH`（503）——进 errors.py 与
   API-SPEC 对应章节。

## 7. 实施拆分（获批后）

1. core：错误码 ×2 + `InvocationPlan.secret_fingerprint`；
2. db：`keyring_claims` 表 + `connections.secret_fingerprint` 列 + 迁移 9（含回填）；
3. executor：execute 路径指纹重算比对；
4. control：claim 服务（create/activate/invoke 三处校验）+ 释放/强制释放路由 + 权限动作 ×3 + 审计；
5. 测试：A1–A8 + 全量回归；docs：041 交付记录 + 05-SECURITY 增补 + README。

预估规模：约 10–14 个文件改动 + 1 迁移 + 20 项左右新测试，
单切片可完成。

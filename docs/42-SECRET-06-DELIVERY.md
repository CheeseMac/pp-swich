# 42 · SECRET-06 交付记录：共享执行器的 Keyring 多用户所有权隔离

> 实施依据：docs/41（`b767e2b` 修订稿 + 2026-10-17 批复的三项勘误，
> 勘误已按批复回写 docs/41 §8.1–8.3）。本切片关闭的是**经 pp-switch
> API 的凭证借用与静默材质替换**（G1–G4）；OS 账户层攻击者、OpenBao
> 生产 Provider（SECRET-06 生产腿）与 Gate R1 其余门禁不在本切片。

## 0. 结论

三层保障全部落地并有测试证据：

1. **授权层——机器级名字 claim**（`keyring_claims` 表，迁移
   `c3f8d1e6a2b4`）：个人模式保留 put → Activate 原体验（首次激活
   即属主）；共享模式首次 claim / 释放后重认领必须提供**双件属主
   证明**（console 一次性码 + executor 本机 attestation）。claim 行
   永不删除，释放/重认领推进 generation（墓碑防 ABA）。
2. **完整性层——全长 HMAC 钉扎**：`HMAC-SHA-256(K_pin,
   canonical(namespace, name, auth_scheme, material))`（无歧义
   length-prefixed 编码，覆盖全部材质字段，恒时比较）；截断
   sha256[:8] 仅作 CLI 展示。激活时钉扎进 Connection 与签名计划；
   executor 每次执行前重算比对（`SECRET_FINGERPRINT_MISMATCH` 503）。
3. **撤销层——代际新鲜性**：计划冻结 generation/pin_version/
   material_tag；executor 执行前反向查询 control 实时 claim 状态
   比对（不做缓存）；shared 模式缺 control_url / K_pin / K_attest /
   namespace 任一项拒绝启动。

轮换状态机：材质变化 → 执行 503 拒绝 → owner 再激活得
`SECRET_ROTATION_CONFIRM_REQUIRED` 409 → 显式 confirm-rotation 在
预留下重验并 CAS 推进 pin_version → 恢复；引用旧 pin 的其他连接
继续拒绝直至各自重验。

## 1. 三项勘误的落实（批复约束条件）

| 勘误 | 落实 |
| --- | --- |
| ① 配对码进 argv / 内部令牌≠本机权限 | CLI `claim-pair` 隐藏输入读码、随请求体提交；本机件 = executor 0700 目录下的轮换 `local.nonce`（0600），attest 端点**同时**校验内部令牌与 nonce——仅有令牌拿不到 attestation；attestation 绑定 namespace/name/principal/workspace/proof_id/码哈希/用途/有效期；审计记随机 proof_id，不记码哈希 |
| ② released 行无法经 INSERT 重认领 | 首次 claim 才走 INSERT（SAVEPOINT + IntegrityError 可移植唯一赢家）；重认领是 `status='released' AND generation=:observed` 的条件 UPDATE，成功推进 generation；测试断言重认领后仍只有一行 |
| ③ 检查须在 executor 侧三入口统一 | `/secrets/verify` 对 keyring:// 强制 purpose=verify ticket（60s 预留）；`/db/discover` 强制 purpose=discover ticket + 反向新鲜性；`/execute` 强制计划携带 claim 上下文（缺失即拒）+ 反向查询 + MAC 重算。首次激活 = 先签预留、executor 预留下解析返回 tag、control 才原子写库——verify 失败零落库 |

## 2. 验收矩阵（A1–A8 + T1–T8 + 勘误负向）

| 项 | 落点 | 结果 |
| --- | --- | --- |
| A1 借用拒绝（无属主泄漏） | `apps/control-api/tests/test_keyring_claims.py::TestPersonalModeClaims` | ✅ 403 KEYRING_NAME_CLAIMED，无 "Alice" |
| A2 owner 无感复用 | 同上 `test_owner_re_references_and_lists` | ✅ |
| A3 轮换 409→确认→恢复 | 控制半程 `TestRotationStateMachine`；执行半程+数据集切换 `tests/e2e/test_keyring_isolation.py::test_a4_poison_swap_fails_closed_and_a3_rotation_recovers` | ✅ |
| A4 投毒不静默换凭证 | e2e 同上：覆盖条目后下一次调用 503，绝不切到 B 数据集 | ✅ |
| A5 释放/重认领 | `TestReleaseAndReclaim`（释放即清钉扎、重认领走 UPDATE、行数仍 1）+ e2e T3 | ✅ |
| A6 跨工作区 | 同上 `test_foreign_workspace_admin_cannot_release`（Bob 提权 admin 后仍 403） | ✅ |
| A7 遗留回填 | 迁移 9 回填为 `pending_claim`（单 owner）/`disputed`（多 owner），无 NULL-tag 放行——`resolve_claim_context` 对一切未钉扎 keyring 连接拒绝（个人模式同样适用） | ✅（迁移往返测试 + 代码路径） |
| A8 审计卫生 | `TestAuditHygiene`：无材质值、无 64-hex 全长 tag、无证明 | ✅ |
| T1 抢先激活 | 共享模式无证明即 403（`TestSharedModeProofs`）——B 拿不到 claim 也拿不到 A 的凭证；个人模式属主即首个激活者 | ✅ |
| T2 并发/重放 | `TestConcurrentFirstClaim`（双线程一 winner）+ 配对码单次消费 + 释放后旧证明重放 403 | ✅ |
| T3 ABA/旧计划重放 | e2e `test_t3_signed_plan_before_release_executes_after_release_refused`：先签计划→提交释放→再执行→拒 | ✅ |
| T4 截断碰撞 | `TestFullMacNotTruncated`：确定性生日搜索构造 sha256[:8] 碰撞对，全 tag 不同且比较为全长恒时 | ✅ |
| T5 三入口 | verify/discover/execute 各自的 ticket/claim 负向断言（control + e2e；PG 路径的 discover 校验在 CI postgres job） | ✅ |
| T6 未确认不复原 | 409 后不 confirm 直接调用仍拒；并发轮换 CAS rowcount=0 路径 | ✅ |
| T7 fail-closed 启动 | `TestStartupFailClosed`（shared 缺 keys / 缺 control_url 拒启） | ✅ |
| T8 Agent 不绕过 | e2e `test_t8_agent_delegation_cannot_bypass`：活委托+授权在释放后仍 403 | ✅ |
| 门禁 | 全量 pytest + ruff/mypy + check-no-secrets + CI 六 job + Security gates；FEAT-03 双凭证/Agent 组合与 FEAT-04 PG matrix 回归保持 | 见 CI |

## 3. 变更清单

| 层 | 内容 |
| --- | --- |
| core | 错误码 ×4（KEYRING_NAME_CLAIMED 403 / KEYRING_CLAIM_PROOF_INVALID 403 / SECRET_ROTATION_CONFIRM_REQUIRED 409 / SECRET_FINGERPRINT_MISMATCH 503）；`InvocationPlanClaim` 冻结上下文挂入计划 |
| db | `keyring_claims`、`keyring_claim_pairings` 表 + connections 钉扎四列；迁移 `c3f8d1e6a2b4`（含遗留候选回填：单 owner→pending_claim、多 owner→disputed，绝不自动 active） |
| executor | `claim_security.py`（ticket 签验、全长 MAC、本地 nonce 仓、attestation 签验、K 派生）；attest / attest-verify 端点；verify/discover/execute 三入口准入 + 反向新鲜性；shared 启动 fail-closed |
| control | `keyring_claim_service`（配对/兑换/CAS/轮换/释放/计划冻结）；activate 改造（active keyring 连接可再激活进入轮换流）；confirm-rotation / pairings / claims / release 路由 + 内部 state 端点；settings（shared_keyring/namespace_id）；plan 组装与试调、FEAT-04 发现全部冻结 claim |
| CLI | `ppswitch-secret claim-pair`（隐藏输入 + 本地 nonce，输出 attestation） |
| 测试 | control 21 项 + e2e 4 项 + 存量全量回归 |

## 4. 使用说明（共享模式运维）

```bash
# executor 侧环境（shared 部署）
PP_SWITCH_SHARED_KEYRING=true
PP_SWITCH_NAMESPACE_ID=<机器唯一 id>
PP_SWITCH_PIN_KEY=<32+字节随机>        # 丢失即所有钉扎失效（fail-closed，需全员重新激活）
PP_SWITCH_ATTEST_KEY=<32+字节随机>
PP_SWITCH_CONTROL_URL=http://<control>:8911
# control 侧环境
PP_SWITCH_SHARED_KEYRING=true
PP_SWITCH_NAMESPACE_ID=<同机器 id>

# 首次认领（共享模式）
# 1) console「Keyring claims」发起配对 → 得一次性码 + ticket
# 2) executor 机器上：
uv run ppswitch-secret put crm-key-a          # 存凭证（不变）
uv run ppswitch-secret claim-pair crm-key-a --ticket <b64>
#    （隐藏输入配对码；读本机 local.nonce）→ 输出 attestation
# 3) console 激活连接时粘贴 attestation → 完成 claim + 钉扎
```

K_pin 遗失的恢复路径 = 所有连接重新激活重钉（文档化的 fail-closed
代价，docs/41 §3.2）。

## 5. 边界（不变）

- 共享 OS 账户仍可直接读/改 OS keyring（防护目标=经 API 的借用与
  静默替换；OS 层隔离见部署文档）。
- 单 control ↔ 单 executor ↔ 单 keyring 命名空间（docs/41 §6.1）。
- 本切片 ≠ SECRET-06 生产 Gate 关闭（OpenBao 腿未动）；不改变
  Gate R1 其余门禁与 M2 实机待办状态。

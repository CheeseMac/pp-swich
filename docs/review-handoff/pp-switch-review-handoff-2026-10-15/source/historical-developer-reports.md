# 历史开发方汇报原文

说明：内容逐字摘自历史对话相应 user 轮次。它们是**开发方陈述与交付声明**，不是本 ZIP 生成过程中独立复验的结论。部分回合混有执行过程、文件路径和日志摘要。

---

# 历史 round 7 · 开发方原文

项目已实现并开源：[**https://github.com/CheeseMac/pp-swich**](https://github.com/CheeseMac/pp-swich) 审核请从这份报告开始：[**https://github.com/CheeseMac/pp-swich/blob/main/docs/20-IMPLEMENTATION-REVIEW.md**](https://github.com/CheeseMac/pp-swich/blob/main/docs/20-IMPLEMENTATION-REVIEW.md) 重点看第 5 节（9 项实现偏差，每条附理由）和第 8 节（6 个需要你拍板的问题）；验收证据在第 3–4 节，想本地跑一遍的话第 2 节有 5 分钟走查命令。  
你评审一下这个项目吧

---

# 历史 round 10 · 开发方原文

[CheeseMac/pp-switch: Discover APIs. Switch Profiles. Protect Secrets. Empower Agents. API discovery, connection profile switching and secretless execution for AI agents.](https://github.com/CheeseMac/pp-switch)  
现在已经修改好了，你再核验一下，看看功能上是否还有漏洞，是否符合设计要求

---

# 历史 round 11 · 开发方原文

## 给设计师的回信材料

- [image](./material-icons/markdown.svg)docs/22-REVIEW-RESPONSE-2.md：逐项回应 + 4 个 ADR 决定 + 路线图（PR-DATA-12/NET-13/DEP-14 排在真实密钥接入之前）
- README 状态行、特性清单、测试数（188）已同步更新
- 你可以继续到github上面校验一下代码

---

# 历史 round 12 · 开发方原文

第三轮复审的全部 6 项 P0 已实施完毕并推送，CI 双绿。

## 结果

**提交 ****`5448b94`****（v0.1.3 Authorization Boundary Fix）** 已推送到 `CheeseMac/pp-switch`，**202 个测试全绿**（新增 14 个）、ruff / mypy / 密钥扫描全过，CI 和 Security gates 两个工作流均 success。逐项核实先行：评审的 5 处源码指控（幂等缓存、目录版本泄漏、无 required\_scopes、审批角色、数组宽松透传）全部属实。

## 六项 P0 的处理

**SEC-043 幂等缓存跨用户**（本轮最重要发现）——回放身份从四元组扩为五元组，新增 `delegating_user_id` 比对：同一共享 Agent 用同一个幂等键、换到另一位用户的委托下重放 → `409`，绝无跨用户缓存读取；同委托重试语义不变。Profile 一致性不参与回放（这是幂等的本义），已在 docs/23 里作为产品语义明确记录。

**SEC-044 required\_scopes**——Capability 声明必需 scopes（迁移 5 冻结到版本），执行时必须被授权链交集**完全覆盖**；并按评审意见收紧语义：整条链路无任何 scope 声明（“空=不限”）对 scoped 能力是**拒绝**——显式授权是强制项。测试覆盖了评审举的例子：委托只有 `orders.read` 时调 `refund.execute` 能力 → 403。

**SEC-045 目录净化**——Agent 目录改走独立的 `_public_tool_view`：仅 published 版本、只含名称/说明/公开 Input Schema/版本/风险提示，draft 数据、`execution`、`allow_fields`、output 守卫配置一概不出；过期的 grant 让工具从目录整个消失。

**SEC-046 / ADR-005 委托身份**——落地了评审建议的短期签名票据：用户通过自己的认证调用签发 HMAC 签名、10 分钟过期的 Delegation Context，共享 Agent 多委托时的执行身份**只能**由有效票据决定。`agent:<id>:<user>` 参数选身份的语法彻底废弃（schema 层拒绝），模型可填的任何 user\_id 从此没有身份语义——测试里连“改一个字节伪造他人 user\_id 的票据”也验证了 403。SDK 同步加了签发与携带方法。

**SEC-047 审批资格**——高风险审批和 Endpoint 审批提升到 reviewer 及以上，与“审核≠创建人”叠加。

**SEC-048 输出严格化**——无有效 `items` 的数组整体拒绝；任何含 `$ref` 的未解析 schema 节点对对象/数组/标量一律拒绝。

**顺带落地了两项 v0.2 门槛**：endpoint 默认强制 HTTPS（明文需显式运维开关）；手写 Capability 与 OpenAPI 导入过同一套元数据清洗。

## 评审要的综合攻击场景

第八节那个“Alice/Bob 共享 Agent”场景已固化为专门的测试套件：换上下文各命中各的 Profile、同幂等键跨用户 409、提示词注入参数（混入 `target_url`/`user_id`）被 schema 拒绝、撤销 Dave 委托即刻失效且不影响 Alice、grant 过期全员失效——五条全部锁定。

给设计师的材料：[image](./material-icons/markdown.svg)docs/23-REVIEW-RESPONSE-3.md（含 ADR-005/006 补录和回应评审第四节表格），README 已同步到 v0.1.3 / 202 测试。关于“188 未独立复跑”：本地是 202 passed 全绿 + CI 双工作流可复核，PostgreSQL 矩阵维持 PR-QA-06 排期未动。

---

# 历史 round 13 · 开发方原文

第四轮复审：六项 P0 通过验收，新增三个边界问题（SEC-049/050/051）+ 票据语义澄清。验收不变量很清晰：“已撤销或缩小的授权，无论通过新调用、幂等缓存、历史查询还是 Schema 回退，都不能重新取得数据”。逐项核实后实施。

三项指控全部属实。开始实施。第一步：提取共享的 scope 守卫函数，回放前重验：

脚本在最后一步断言失败（原子未写入）。重新读准确文本：

SEC-049 完成。现在 SEC-050——Agent 历史查询必须带票据并按委托用户过滤：

现在做独立签名密钥（settings + 生产校验 + 统一取用）：

现在 SEC-051 — 顶层 `$ref` schema 拒绝：

补一处：历史读取的票据也要验证对应 delegation 仍然活跃（撤销后不得经历史接口重取数据）：

那段 session 处理很丑——helper 直接复用路由的 session 更干净：

现在编写第四轮回归测试（SEC-049/050/051 + 撤销不变量）：

装饰器错位到了 helper 头上——把 `@router.get("/invocations")` 移回 `list_invocations`：

SEC-030 的生产启动守卫测试需要跟上新校验——补独立密钥场景：

全绿（209 测试）。写 docs/24 回应文档：

第四轮复审的三个边界问题已全部修复并推送，CI 双绿。

## 结果

**提交 `14d1744`（v0.1.3.1 撤销不变量补丁）**已推送，**209 个测试全绿**（新增 7 个），ruff / mypy / 密钥扫描通过，CI 与 Security gates 双工作流 success。三项指控先核实、后修复——全部属实。

## 三项边界修复

**SEC-049 幂等缓存绕过 scope 收紧**——按评审建议把检查前移，实现方式是提取共享守卫 `_authorize_scope_chain()`：scope 交集 + required_scopes 覆盖只有这一个权威实现，实时路径和回放路径都调用它，两处永远不会漂移。回放前还会从审计快照重载原调用的 connection 并要求其仍为 active——所以评审举的场景（delegation 从 `orders.read` 缩到 `orders.note`）现在**新调用和旧幂等键双双 403**，connection 撤销同样封死缓存。“切换 Profile 不破坏合法重试”的既定语义原样保留。

**SEC-050 历史记录按委托用户隔离**——采纳评审的第一方案：Agent 读历史（列表和详情）必须带 `X-Delegation-Context` 票据，验签之外还要求**对应 delegation 当前仍活跃**（票据证明签发、不证明持续有效，撤销实时复查），结果按快照里的 `delegating_user_id` 过滤，跨委托用户的记录 404。测试验证了评审的场景：Agent 凭据裸查 403、Alice 的票据看不到 Dave 的记录、撤销 Alice 委托后她的有效票据也读不了历史。

**SEC-051 顶层 `$ref` 宽松回退**——`project_response` 现在在任何分支之前检查根节点：output_schema 非空且含 `$ref` → 整个响应拒绝（fail closed）。同时用 ADR-007 把三条路径的语义写死：有 properties → 严格投影；无 properties 的兼容路径 → 仍是 allow_fields 白名单（不是宽松透传）；不可解释（$ref）→ 拒绝。

## 票据语义澄清（ADR-005 修订）

评审观察准确——它是 10 分钟可复用的签名 Bearer 票据，不是一次性的。修订后明确：`nonce` 仅用于审计关联；持有者边界写为仅可信 orchestrator；**代码落地了独立信任根**：新增 `PP_SWITCH_DELEGATION_SIGNING_KEY`，生产模式启动强制要求独立密钥且不得等于 internal_token（缺省或重复都拒绝启动），本地 demo 自动回退不受影响。一次性票据（jti + 原子消费）按评审建议留到 OIDC 会话派生时实现。

## 撤销不变量

评审收敛的那句验收目标已固化为 `TestRevocationInvariant`，四条路径在同一个流程里全部验证：授权缩小后**新调用 403、幂等缓存 403、历史查询 403、Schema 回退路径 raise**。这条不变量从此有回归测试看守。

给设计师的材料：[docs/24-REVIEW-RESPONSE-4.md](C:\Users\84199\Documents\workspace\pp-switch\docs\24-REVIEW-RESPONSE-4.md)（含 ADR-005 修订与 ADR-007），README 已同步到 v0.1.3.1 / 209 测试。下一步按既定顺序进入 PR-DATA-12（结果数据分级/TTL/加密/清理）。

---

# 历史 round 14 · 开发方原文

第五轮复审：SEC-049/050/051 正式关闭，批准进入 PR-DATA-12；新增 SEC-052（历史读取未重校验 scope）、SEC-053（撤销重授权后旧票据复活），并要求补 allOf/oneOf/anyOf 组合结构处理、启动数据留存工程。按评审安排实施：SEC-053 独立修复 + SEC-052 并入 PR-DATA-12 + PR-DATA-12 本体（分级/TTL/加密/清理/最小化保存）。

指控全部属实（cryptography 已可用）。开始实施——先 DB 层（迁移 6）：

现在是 delegation_service 的代际逻辑：

现在接线代际比较 + SEC-052 历史重校验。先改 invocation 路径：

现在 router 侧：`_agent_invocation_scope` 返回 delegation 对象 + 代际校验，再加 SEC-052 记录级重校验：

现在实施 PR-DATA-12 的结果存储策略。先建 result_vault 模块和 settings：

现在将 storage policy 接入 invocation_service（写入侧）以及 router（读取侧）：

现有全绿。写第五轮回归测试：

两处问题：测试断言错（记录 owner 是 Agent principal，用户直读本就 404——这正是 SEC-029 语义）；根节点组合键检查漏了。修两处，再看 SEC-053/critical 失败：

SEC-053 生效但错误信息不够明确——把代际检查改成独立分支：

demo 已有绑定，重放 expected_version=0 冲突。改为按需绑定：

找到原因：即时响应返回的是持久化列（restricted 下为 None），而调用方应当当场拿到一次结果。修复：

fixture 应该放进 conftest 共享，而不是跨测试模块导入。移动：

fixture 依赖三个助手——把它们一起搬进 conftest：

217 全绿。写 docs/25 并更新 README：

第五轮复审的两项边界修复 + 批准开工的 PR-DATA-12 已全部交付并推送，CI 双绿。

## 结果

**提交 `e28e3b9`** 已推送，**217 个测试全绿**（新增 8 个），ruff / mypy（44 源文件）/ 密钥扫描通过，CI 与 Security gates 均 success。SEC-052/053 指控核实后均属实，按评审的分类实施：SEC-053 独立小修复、SEC-052 并入 PR-DATA-12 一并落地。

## 两项边界修复

**SEC-052 历史读取重授权**——评审对测试盲区的批评很准：第四轮的不变量测试在测历史前撤销了 delegation，“缩小但未撤销”确实没被独立证明。修复按评审建议做全：每条候选历史记录都要重查**当前** grant（含过期）、**当前** delegation scopes、原调用 connection 的存活与 scopes、该 capability 版本的 required_scopes，全部通过那个与实时调用/幂等回放共享的 `_authorize_scope_chain` 守卫。新测试专门保持 delegation active、只缩小 scopes——历史列表为空、详情 404。

**SEC-053 委托代际**——采纳评审的 generation 方案（迁移 6）：`delegations.generation` 单调递增，撤销、重新授权、scopes 变更三种状态转换都会 bump；票据签发时写入代际，调用路径和历史路径都要求与数据库当前值完全一致。测试完整覆盖评审描述的转换：撤销→同 scopes 重授权→旧票据（密码学上完好、仍在 10 分钟窗口内）调用 403、历史 403，新票据正常。

## PR-DATA-12 结果生命周期

评审的验收要点逐条落地：**分级**（high/critical → restricted，默认 metadata-only，但调用方在即时响应里仍完整拿到一次脱敏结果）；**保留期**（`result_retention_days` 默认 7，过期读取一律为空，与从未存储不可区分）；**静态加密**（Fernet，密文入新列、明文列恒 NULL，生产模式无有效密钥拒绝启动）；**清理**（启动自动 + admin API 供调度，只抹数据载荷保留元数据）；**读取权限分开**（用户 principal-scoped，Agent 票据 + SEC-052 重授权）；并用 ADR-008 固化“单一读出口”——所有读取路径都走 `stored_result()`，**授权检查永远先于解密**。

另按评审表格补了 PR-NET-13 前置：`allOf/oneOf/anyOf/not` 在受限解析器落地前与 `$ref` 同等对待，根节点/items/嵌套节点三层全部 fail-closed。

## 关于“停止小补丁循环”

接受评审的方向判断，docs/25 已把路线图收敛为整块推进：PR-NET-13（受限解析器本体）→ PR-DEP-14 → PR-QA-06（PostgreSQL 矩阵）→ 覆盖存储/授权/网络/真实凭证的全链路综合审计，之后才谈 OpenBao 和企业密钥。给设计师的材料是 [docs/25-REVIEW-RESPONSE-5.md](C:\Users\84199\Documents\workspace\pp-switch\docs\25-REVIEW-RESPONSE-5.md)，README 已同步（217 测试、PR-DATA-12 完成状态）。

---

# 历史 round 15 · 开发方原文

第六轮复审：PR-DATA-12 开发验收通过、批准进入 PR-NET-13，新增 DATA-15/16/17 三个生产就绪问题（旧明文不迁移、清理无租户边界、密钥轮换缺失）+ 加密链路综合测试缺口。按评审顺序本轮交付：DATA 三项收尾 + PR-NET-13 受限解析器本体。

三项属实。先做 DATA-15/16（迁移 7 + 清理隔离与兜底）：

启动失败还要真正记录告警（suppress 吞掉前打日志）：

DATA 组完成。现在做 PR-NET-13 本体——先查测试里用到的 marker 名，然后改造两端：

Discovery 侧的受限 `$ref` 解析器：

核心改动完成。写第六轮测试（DATA-15/16/17 + NET-13 + 加密全链路）：

发现真 bug：根节点 allOf 通过了"可解释"门，但 `project_response` 没把**归一化后的 schema** 用于后续投影，掉进了 legacy 路径。修复 + 测试 token 修正：

FK 约束要用真实 capability_version_id；allOf 泄漏直接调试看：

`schema_properties` 在归一化**之前**就取了。移到之后：

`demo` fixture 没有调用记录——先经 API 真实调用一条作为模板行：

外“租户”行应放进 bob 的工作区（FK 各自独立）：

改用 SQLAlchemy Core 表达式（顺带消掉 S608），拆长行：

226 全绿。写 docs/26、更新 README、推送：

第六轮复审的三项生产就绪问题 + 批准的 PR-NET-13 已全部交付并推送，CI 双绿。

## 结果

**提交 `4b68ef9`** 已推送，**226 个测试全绿**（新增 9 个），ruff / mypy / 密钥扫描通过，CI 与 Security gates 双 success。DATA-15/16/17 逐项核实均属实，按评审指定的归属处理，并顺手完成 PR-NET-13 本体。

## DATA 三项生产就绪修复

**DATA-15 存量明文**——采纳评审的安全优先方案，双保险：迁移 `f3a62c8e1b47` 在升级瞬间清空所有历史明文结果（元数据保留，downgrade 刻意不可逆）；运行时清理的清扫条件扩展为“无保留期限的载荷一并抹除”——新代码写的行必带期限，所以无期限明文只可能是旧数据，任何一次清理都会兜掉。验收标准达成：升级后不存在可读的历史明文。

**DATA-16 清理租户边界**——按评审的双入口模型：admin API 固定传 `auth.workspace_id`（测试验证 A 租户的清理动不了 B 租户的行），不带 scope 的全局清扫仅限启动时内部任务；启动清理失败从静默吞掉改为记录告警日志（评审担心的“读取已拒绝但密文滞留”现在可发现）；SQL 顺带改写成 SQLAlchemy Core 类型化表达式。

**DATA-17 密钥轮换**——`PP_SWITCH_RESULT_ENCRYPTION_PREVIOUS_KEYS` 提供 MultiFernet 读窗口（写入恒用 primary，轮换不孤儿化存量密文）；新增租户内 `POST /admin/re-encrypt-results` 批量重加密并返回 `{rekeyed, failed}`（轮换完成确认依据）；解密失败在服务端日志单独分类，而 API 响应里四种 None（过期/未保存/解密失败/legacy）对调用方不可区分（ADR-009）——不给探测通道。测试覆盖完整轮换周期：换 key 后旧密文可读 → 重加密 → 撤掉旧 key 仍可读。

## PR-NET-13 受限解析器

导入侧：本地 `#/...` `$ref` 有界解析（JSON Pointer 转义、循环检测、深度上限 8），**外部引用永不解引用**，解析失败的 operation 进 `needs_review` 人工审核队列。执行侧：`allOf` 仅支持纯对象分支的受限合并，`oneOf/anyOf/not` 保持拒绝（值驱动分支选择等于猜测）。过程中还揪出一个真 bug：根节点归一化结果此前没有替换后续分支的输入，合并 schema 会掉进 legacy 白名单路径——已修复并被新测试锁定。

## 评审要的综合测试

第三节的缺口已补：`TestEncryptedLifecycleEndToEnd` 走完整链路——真实 HTTP 调用（即时响应带结果）→ 库中仅密文无明文 → 历史读取透明解密 → TTL 过期读空 → purge 抹除。PG 级验证按评审安排留给 PR-QA-06。

给设计师的材料：[docs/26-REVIEW-RESPONSE-6.md](C:\Users\84199\Documents\workspace\pp-switch\docs\26-REVIEW-RESPONSE-6.md)（含 ADR-009 与两项记录在案的审计阶段增强：独立内容分级、密文 key_id 前缀）。README 已同步到 226 测试。下一块按既定顺序是 PR-DEP-14（部署面出口强制约束）。

---

# 历史 round 16 · 开发方原文

第七轮复审：DATA 三项开发级通过、PR-NET-13 主体完成留两项收尾（allOf 重复属性覆盖 P0、解析器资源预算 P1），批准进入 PR-DEP-14 主线。按评审安排：NET-13-F1/F2 收尾 + PR-DEP-14（出口强制 + 部署方案 + 轮换运维流程）一并交付。

两项指控都实锤复现（private_note 泄露 + RecursionError）。修复 F1——重复属性一律拒绝：

F1 生效（重复属性 → 整体拒绝）。现在 F2——解析器四类预算 + `$ref` 兄弟键拒绝：

F2 生效。现在 DEP-14 应用层强化——IPv4-mapped IPv6 归一化（封 `::ffff:169.254.169.254` 这类绕过）：

NAT64 前缀 `64:ff9b::/96` 内嵌私网地址确实绕过 `is_private`。加归一化：

真实路径验证：已批准主机名解析到 NAT64 包装的元数据地址 → 硬拒。现在 reencrypt 分页 + PR-DEP-14 部署物：

分页循环有死循环 bug（同一批会被反复选中）——改 keyset 分页：

现在进行 PR-DEP-14 的部署配置（Dockerfile + 隔离网络 compose + 部署文档）：

现在开始第七轮测试（F1 重复属性、F2 预算、v6 包装归一化）：

`:?` 必填守卫按预期生效。用占位值验证完整配置：

Compose 拓扑验证通过（executor 仅在 internal 网络）。尝试构建镜像做最终验证：

Docker daemon 未运行（CLI 客户端解析有效，镜像构建留待有 daemon 的环境——如实记录）。写 docs/27 回应：

第七轮复审的两项 NET-13 收尾 + 批准的 PR-DEP-14 已全部交付并推送，CI 双绿。

## 结果

**提交 `0f51d92`** 已推送，**237 个测试全绿**（新增 11 个），ruff / mypy / 密钥扫描通过，CI 与 Security gates 双 success。两项指控先独立复现再修复——评审的 `private_note` 泄露示例和 5000 层嵌套的 RecursionError 都在本地重现确认。

## NET-13 两项收尾

**F1 重复属性覆盖**——评审示例完全复现后，采纳其“最简可靠”方案：不做交集计算，**分支间属性名重叠的 allOf 一律拒绝**（根级拒绝整个响应、嵌套级拒绝该字段）。完整 JSON-Schema 交集语义明确排除在范围外，disjoint union 是唯一受支持的形状。评审原文的攻击示例现在拿不到任何数据。

**F2 解析预算**——四类独立预算全部落地：`$ref` 链 8、普通结构深度 64（修复了真实的递归爆栈）、总节点 2 万、数组 256、内联体积 512KB，超限统一走 `needs_review` 绝不自动发布；`$ref` 携带兄弟关键字（评审指出会被静默丢弃语义）也改为拒绝。

## PR-DEP-14 主体

**应用层强化**：探测中发现了一个评审没点名但真实存在的绕过——NAT64 前缀 `64:ff9b::/96` 内嵌私网地址时 Python 标准库不判 private。`canonical_ip()` 现在把 IPv4-mapped 和 NAT64 包装统一归一为内嵌 IPv4 再判定：已批准主机名解析到 `64:ff9b::a9fe:a9fe`（云元数据的 NAT64 形态）→ 硬拒。评审点名的六个验证面（DNS 切换/代理变量/重定向/IPv6/内网/元数据）逐项有测试看守。

**三层出口隔离**：`deploy/` 下 Dockerfile + docker-compose——executor 只挂 `internal: true` 网络（容器级无出口路由，应用层被绕过也出不了站）、代理环境变量清空、三个必需密钥用 `.env` 必填守卫（缺失直接拒绝起服务）。`docker compose config` 已全量验证拓扑正确；**镜像构建因本机 Docker daemon 未运行而没有执行**——docs/27 如实记录，不虚报。

**运维文档**（docs/28）：nftables 主机白名单样例、多实例密钥轮换 runbook（评审的五步流程：兼容读窗口→统一切写→分页重加密→`failed=0` 后撤旧 key→备份恢复演练）、升级不可逆性显著标注、WAL/备份物理残留与加密备份要求。`reencrypt_results` 顺带改为 keyset 分页逐批提交。

给设计师的材料：[docs/27-REVIEW-RESPONSE-7.md](C:\Users\84199\Documents\workspace\pp-switch\docs\27-REVIEW-RESPONSE-7.md) + [docs/28-DEPLOYMENT-AND-EGRESS.md](C:\Users\84199\Documents\workspace\pp-switch\docs\28-DEPLOYMENT-AND-EGRESS.md)。评审“QA-06 后做一次生产就绪综合评审、不再逐轮零散补丁”的建议已采纳并写入路线图——下一块是 PR-QA-06（PostgreSQL CI 矩阵 + PG 级多租户/迁移/并发/轮换/生命周期验证）。

---

# 历史 round 17 · 开发方原文

第八轮复审指出了一个真架构问题：`internal: true` 隔离把 executor 的**业务出站能力也切断了**——出口隔离与“安全调用外部 API”这一核心价值冲突。按评审安排：DEP-14-F1（受控出口网关）+ F2（防火墙路径修正）+ 部署默认值，全部并入 PR-QA-06 一次性解决。开始实施。

网关策略路径正常（沙箱无该域名 DNS）。现在给 executor 加代理模式：

网关的端到端测试（本地 asyncio，TLS 隧道穿真实自签服务）：

项目没有 pytest-asyncio——把 fixture 改成后台线程 + 同步测试：

网关 7 项全绿（含 executor→网关→TLS 源站端到端）。现在修 compose 与部署默认值：

Compose 拓扑全部正确。修正 docs/28 的防火墙路径与出口模型：

现在参数化测试数据库并加 PG 矩阵测试。先看迁移入口：

两处：raw SQL 缺 `policy_ref` 列、并发测试缺 workspace 父行（FK）。修复：

`operation_version_id` 是真外键——补全 ORM 链（service→operation→operation_version）：

SQLite 的 Uuid 列存无连字符的 hex——参数要用 `.hex`（与生产代码一致）：

policy_ref 修复当时随断言失败一起丢了。补上 + 幂等测试复用真实外键链：

db 测试全绿。现在加 CI 的 PostgreSQL 与 docker-deploy 两个 job：

脚本有几处逻辑别扭（if/else 吞退出码、内部直连语义混着网关）。重写更严谨的版本：

核对脚本依赖的几个端点与镜像细节：

密钥扫描逮住了写死的 Fernet key（好事）——改用纯标准库生成；顺手清 lint/mypy：

noqa 要贴在 S607 实际报告的行（argv 行）：

240 passed——核对一下新网关测试是否被全量收集：

247 全绿。写 docs/29 回应文档并更新 README，提交推送：

主矩阵双绿，两个新 job 首跑失败（预期内）。取日志修复：

docker job 是 compose 文件路径问题。看 PG job 失败点：

根因清楚：PG 下所有测试共享一个库，`token_hash` 唯一约束冲突（SQLite 是每测试一个文件）。方案：每个测试动态建独立 PG 库。修两处：

conftest 已改，e2e 文本不匹配中断了后两步。查看实际文本并补齐：

**docker-deploy 首跑成功**（QA-DEP-01..05 全过）。只剩 PG job：

两处 PG 特有问题：QA-PG-01 的 raw SQL 在 PG 下 FK 真实强制（要补全父链）；某测试用了 SQLite 的 `group_concat`。修：

**五个 job 全绿**——包括 PG 矩阵与 Docker 部署验收。更新 docs/29 验证记录与记忆：

第八轮复审的全部项目已交付：DEP-14-F1/F2 修复 + PR-QA-06 验收矩阵在 CI 实跑通过，最终**五个 job 全绿**。

## 结果

**提交链 `15b7bf4 → 8ee019b → 95c0be1 → 736f14a`**（迭代三次到全绿，过程如实记录在 docs/29）。本地 **247 测试全绿**；CI 最终状态：双 OS 测试、前端构建、**PR-QA-06 postgres matrix**、**PR-QA-06 docker deploy matrix** 五个 job 全部 success。

## DEP-14-F1：受控出口网关（架构修复）

评审的判断成立——无路由隔离把核心价值一起切断了。修复是新增 `apps/egress-gateway` 组件：**CONNECT-only 正向代理**（端口 8914），executor 保持完全隔离，所有真实 API 通信经它出站。它有**独立于 executor 的 allowlist**（两道独立信任根）、每个目标 IP 过 `canonical_ip` 归一且元数据段即使被列入 allowlist 也硬拒、连接已验证的 IP 字面量（钉扎纪律一致）、**TLS 端到端不解密**（凭证仍只在 executor）、全量 allow/deny 审计日志。明文 HTTP 转发一律 403，与 https-only 策略闭环。executor 加 `PP_SWITCH_EGRESS_PROXY` 配置走网关模式（应用层校验仍先执行），本地开发默认直连钉扎不变。

评审“QA-DEP-02 与 04 必须同时通过”的要求在 CI 实测达成：**外部 HTTPS 经网关成功调用**（httpbin）+ **executor 容器直连外网被容器级阻断**——可用性与隔离同时成立。

## DEP-14-F2 与默认值

防火墙样例修正到 **FORWARD 路径**（iptables `DOCKER-USER` / nftables forward hook），并标注必须实测的验证面（IPv6、容器重启地址漂移、DNAT/SNAT）——CI 已验证容器级阻断，宿主机层留待真实主机；control 默认只绑 `127.0.0.1`；mock-crm 移入 `demo` profile（生产默认不启动）。

## PR-QA-06 矩阵（CI 实跑）

评审 §7 的十项全部落地并跑通：**QA-PG-01**（从 pre-wipe 版本升级 → 存量明文清零、FK 完整——PG 强制外键反而让这个测试测到了 SQLite 测不到的真完整性）、**QA-PG-03**（8 线程并发消费审批恰好一个成功、6 线程幂等插入恰好一个成功）、QA-PG-02/04/05（既有租户/轮换/生命周期测试在 PG 上运行）；**QA-DEP-01..05**（`deploy/verify-deployment.sh`：构建、健康检查、外部 HTTPS 经网关、内部对端直连、三类拒绝、容器级直连阻断、凭证不入日志）。

首跑失败与修复过程（共享 PG 库的唯一键冲突 → 每测试独立建库；PG 强制 FK → 种子补全父链；SQLite `group_concat` 方言差异）都如实写进了 docs/29 §4。

给设计师的材料：[docs/29-REVIEW-RESPONSE-8.md](C:\Users\84199\Documents\workspace\pp-switch\docs\29-REVIEW-RESPONSE-8.md)。按评审约定，下一步是**生产就绪综合审计**（一份完整验收矩阵统一判定真实凭证开放）——宿主机防火墙层的真实主机实测是其剩余输入项。

---

# 历史 round 18 · 开发方原文

综合审计结论：Pre-alpha 基线获批、真实凭证暂不批准，三个网络 P0（元数据硬拒不完整、CONNECT≠TLS 端口边界、网关无调用方认证）+ 测试证据盲区，收敛为 Production Readiness Gate R1。本轮交付：三个 P0 修复 + 部署验收升级为完整 Agent 链路 + Control 网络收敛。

executor 侧生效。现在改 gateway：共享硬拒集 + 端口策略 + 认证与配额：

现在对连接处理器进行改造（认证头解析 + 配额执行 + 隧道时长上限）：

一处替换未命中，检查现状：

脚本断言中止导致整批未写入。逐段看准确文本重做：

heredoc 转义又坑了。改用脚本文件 + raw 字符串：

现有测试仍绿。补审计回归测试：

fixture 在另一个测试文件里——移到共享 conftest：

httpx 发的是 Basic 认证——网关同时支持 Basic 与 Bearer：

网关 15 项全绿。现在改 compose（Control 移出 egress、网关令牌、容器加固、mock-crm 宿主映射支撑全链测试）：

compose 验证通过。现在写生产 PG override 和升级版验收脚本（完整 Agent 链 + 防误判 + 全链 canary）：

一刀切替换误伤了用 port 的测试。精确修复：

255 全绿。写 docs/30（Gate R1 进度）、更新 README，提交推送并盯 CI：

全链腿缺 principals（容器没跑 seed）。改为容器内直接播种（镜像里没有 scripts/，用应用自身模块）：

架构问题浮现：网关模式下 executor（无路由网络）自己做不了外部 DNS——解析职责应归网关。把 URL 策略与 DNS 校验拆开，网关模式 executor 只查 URL/白名单：

还在解析——查 main.py 是否有第二处调用：

第二处在 execute 端点里。改为网关模式用 URL-only：

全链已通！卡在日志断言的字符串格式（Deny reason 用空格不是下划线）。修：

测试客户端没带认证 token——网关回的是 407 unauthorized 而非 allowlist 拒绝。给 QA-DEP-03 的代理 URL 带上 token：

替换引入了缩进错误，修正：

token 不在 executor 的环境里（嵌在代理 URL 中）。从 URL 解析：

探针通过了，新的失败是超时——定位在哪一段：

QA-DEP-04 的对照腿用了过时固定 IP。改为动态解析 httpbin 的实际 IP：

**CI 全绿**——升级后的部署验收（生产启动腿 + 完整 Agent 链 + 审计日志断言 + 可达性对照 + 全链 canary）实跑通过。收尾更新：

生产就绪综合审计的第一批阻断项已全部交付：Gate R1 进度 **4/8**，CI 五个 job 全绿（含升级后的部署验收）。

## 结果

**提交链 `39a270f → 63638f0`**（七次迭代到全绿，过程完整记录在 docs/30）。本地 **255 个测试全绿**（新增 8 个审计回归），CI 最终：双 OS 测试、前端、PG 矩阵、**升级后的 docker-deploy**（非 demo 生产启动 + 完整 Agent 链 + 审计断言）全部 success。

## 三个网络 P0 的修复

**AUD-EGRESS-01 元数据硬拒不完整**——评审正确：阿里云 `100.100.100.200` 和 AWS IPv6 `fd00:ec2::254` 都不在 link-local 段。现在有显式的 `PERMANENTLY_DENIED_NETWORKS` 策略集（executor 与网关共享同一实现），列入 allowlist 也拒；RFC1918 维持“显式批准”层以支持企业内网 API——与评审要求的分层一致。

**AUD-EGRESS-02 端口边界**——allowlist 裸主机名现在只授权 443；非标准 TLS 端口必须显式 `host:port` 条目。文档明确：CONNECT 不证明 TLS，端到端 TLS 归属客户端证书校验。

**AUD-EGRESS-03 认证与配额**——网关要求 `Proxy-Authorization`（Bearer/Basic，executor 的代理 URL 内嵌 token），未认证 407 + 审计；per-peer 配额：32 并发隧道、60 连接/10 秒、300 秒隧道时长上限，超限 429 + 审计——网关不再可能被当作无界通用 TCP 出口。

## 证据盲区的补齐（评审第三节）

部署验收脚本按三点批评全部升级并实跑通过：**完整 Agent 链**（容器内播种 principals → HAR 发现导入 → 双人审核发布 → 合成凭证调用 → 认证网关 → 外部 HTTPS 后端 httpbin → 响应守卫 → 结果断言）；**拒绝测试防误判**（必须命中网关审计日志的 allowlist 拒绝分类；对照腿先动态解析后端当前 IP 证明网关可达，再证 executor 直连被阻断——固定 IP 已过时，正好踩了评审预言的坑）；**全链 canary**（调用实际使用的 MOCKKEY 凭证不出现在任何日志与响应）。

**迭代中揪出一个真架构问题**：无路由容器做不了外部 DNS——网关模式下 executor 的解析职责正式移交网关（`check_url_policy` 与 DNS/IP 校验拆分，两端各自完整），这正是“CI 通过≠生产通过”的实证。

## 评审 §4 的部署收敛

control 移出 egress 网络（高密钥组件零公网出口）；容器加固（只读根文件系统、cap_drop ALL、no-new-privileges、资源限额）；PostgreSQL 部署模板 `docker-compose.postgres.yml`。

Gate R1 剩余四项（DEP-04 宿主机防火墙实测、SECRET-06 真实 SecretProvider、ID-07 OIDC、DATA-08 多实例轮换/备份演练）都需要真实环境或新模块，docs/30 如实标记未完成——按评审约定，这四项走完才做真实凭证的统一准入判定。给设计师的材料：[docs/30-GATE-R1-PROGRESS.md](C:\Users\84199\Documents\workspace\pp-switch\docs\30-GATE-R1-PROGRESS.md)。
快点推进核心功能，不要老是搞这个破架构

---

# 历史 round 19 · 开发方原文

继续修扩展的 TypeScript 报错。先看 engine.ts 报错处的实际代码：

三处问题确认：`onDebuggerEvent` 应为 `chrome.debugger.onEvent`；popup 按钮元素缺类型；engine 的收窄写法换成早返回。逐一修复：

刚才的写法不对，换成显式辅助方法 + 显式返回类型，彻底避开收窄问题：

根因找到了：存储的分组类型误用了 `CandidatePreview & {...}` 交叉类型（要求带派生字段，而字面量没有）。换独立的 `PreviewGroup` 接口：

现在修复 service worker 中 debugger 的 event API 名称，以及 popup 按钮的 types：

构建过了，但 popup.html 落在 `dist/src/popup/popup.html`，与 manifest 引用的根路径 `popup.html` 不符。把 popup.html 提到扩展根目录：

dist 布局正确（popup.html / popup.js / background.js / manifest / icons）。扩展完成。现在做 web-console 的 Discovery 页面增强：

console 通过。接线 CI：

检查 .gitignore 不会吞掉扩展的图标（交接文档坑 #5）：

`dist/` 被忽略（构建产物）、图标正常纳入。跑全量 JS 验证：

JS 全绿。补一个关键工件：**跨语言黄金契约测试**——用 vitest 固化引擎输出，Python 侧同一份夹具喂给 collector 解析器，锁定 TS/Python 两端的载荷契约：

夹具已由 TS 引擎真实输出固化。跑 Python 契约测试，按真实行为校准断言：

符合预期的分叉：`{id}` 与 `{uuid}` 本来就是不同模板（与 HAR 管线行为一致）。修正契约测试：

契约测试通过。全量验证：

279 全绿。修 ruff B023（把循环变量作为参数传入闭包）：

golden 测试需要 Node 类型。加 `@types/node`，并把 engine 的 postData 类型放宽为可空：

全部本地门禁绿了（279 Python 测试 + 25 JS 测试 + ruff/mypy/密钥扫描 + 双端构建）。现在做演示证据。先看 dev-up 脚本用法：

给 popup 加一个诚实的 demo 模式（无扩展上下文时渲染罐头状态，用于截图/预览，生产路径不受影响），然后起栈：

栈已就绪（vite 在 localhost:5173）。写可复用的演示脚本（瞬时取用 token，绝不落盘/打印）：

全链路真实打通：配对 → 令牌交换 → 6 条观察 → 5 个 collector 草稿（全部 `observed`，无自动发布）。现在加载浏览器技能截图取证：

登录页显示 token 未注入（种子写了文件但控制台没读到——先查一下，同时准备手动粘贴路径）：

种子跑了两遍产生两套 principal（seed-demo 对已有库不幂等）。重置数据库重来一遍干净状态：

DB 被占用——dev-up 还活着。按端口找 PID 精确杀掉：

干净状态就绪。再补一个 HAR 导入让观察来源表同时展示 har + collector 两种来源，然后进浏览器截图：

登录成功。进入 Discovery 页：

页面完全符合设计（7 候选 = 2 har + 5 collector；`{id}` 因 query 参数集不同正确分为两条）。选择服务展示配对列表后截图：

配对列表需要触发一次 load——正好点“生成配对码”演示完整交互：

配对卡完整呈现（一次性码 254QMNWU + paired 会话带 6 条观察）。截图取证：

控制台截图完成。现在起静态服务托管扩展 dist，截 popup 演示模式：

弹窗渲染正确。为演示效果调整 demo 状态（提交后又采集了 14 条），重建后截图：

两张演示截图都已落盘。清理演示进程，写交付文档：

更新 README（文档表 + 功能描述 + 测试计数）：

最终门禁全量重跑（此后不再改代码）：

数字核对：Python 279（255 基线 + 24 新增）+ vitest 25 = 304。修正文档里的计数笔误：

50 个文件，全部符合预期（无 dist/data 泄漏）。提交并推送：

CI 两条工作流已在跑。等待期间更新持久记忆：

Security gates 已绿。CI 还在跑（含较慢的 docker-deploy 与双 OS 矩阵）：

PG 矩阵腿挂了（本地 SQLite 全绿——典型的 PG 方言差异，交接文档坑 #7 预言过）。取日志定位：

根因明确：**PG 连接耗尽**（`too many clients already`）——每个测试建独立库+engine 但从不 dispose，我的 24 个新测试把并发连接推过了 PG 的 max_connections。修 harness 的 engine 生命周期：

修 control-api harness：engine 用完即 dispose（此前每个测试泄漏一个连接池，我新增 24 个测试后越过 PG max_connections）：

确认没有其他每测试建 PG engine 的位置：

Alembic 用的是短生命周期 engine，不受影响。本地验证 conftest 改动后提交：

连接耗尽已修复（其余 277 项全过），剩 2 个 e2e 用例仅在 PG 上挂。取完整 traceback：

仍然连接耗尽，但这次只有 e2e 两个用例挂——单位测试的泄漏已修，剩余泄漏在别处累积。不再猜，本地起 PG 容器复现：

Docker 未启动。先读 engine 工厂实现，弄清池参数再定论：

`stack` 是模块级——e2e 自身有界。泄漏来自更早的套件。全面搜每测试建 engine 的位置：

关键发现：PG job 只跑 db + control-api + e2e 三个目录；每个 control-api 测试里 `create_app` 内部还会建**第二个** engine（应用自己的），我的 dispose 只释放了 harness 的那个——每测试漏 ~1 个连接 × 77 测试正好越界。确认 app 的 engine 挂在哪：

`app.state.engine` 存在。在 harness teardown 一并释放；顺手确认 executor app 是否也建 PG engine：

executor 无 DB engine（符合架构——DB 只归 control）。补上 control app engine 的释放：

本地全过。提交推送：

**CI 全绿**（`9e4f772`：CI + Security gates 双成功）。按仓库惯例把 CI 迭代过程如实补进交付文档，收尾：

# FEAT-01 交付完成：浏览器 API Collector 已合入 main，CI 全绿

交接照旧从 [docs/31-HANDOVER.md](C:\Users\84199\Documents\workspace\pp-switch\docs\31-HANDOVER.md) 进入（读文档→核对仓库状态一致），随后按你的开发命令完整实现了 FEAT-01。最终提交链：`d207673`（功能主体）→ `00fe7ed`、`9e4f772`（CI 迭代修复）→ `d2b85db`（交付记录收尾），CI 五 job + Security gates 双工作流全绿。

## 做出来的东西（按验收标准对照）

**Chrome 扩展**（`apps/browser-extension/`，pnpm workspace 新成员，25 项 vitest）：MV3 + TypeScript。用户在弹窗里粘贴控制台生成的一次性配对码连接；对**明确附加调试的单个标签页**（Chrome 常驻“正在调试”横幅）通过 CDP 监听 Fetch/XHR；弹窗实时预览候选 API（次数/状态码/参数名/响应字段），一键提交；断线（关标签页/取消横幅）后观察缓冲保留可续采。

**隐私边界是设计出来的，不是事后过滤**：所有 JSON 值在浏览器内就被替换成类型标签（零值样本）——上传的只有方法、粗归一路径模板、参数名、字段结构，**头一个都不传**。服务端复用 discovery-sdk 的 sanitize→normalize→fingerprint→infer 共享管线（从 har.py 抽出到 `merge.py`）再做 fail-closed 二次防御：扩展有 bug 真发出 secret 形状的值，整批拒绝。跨语言契约有黄金夹具锁死——TS 引擎的真实输出与 Python 解析器吃的是同一份字节，`tests/fixtures/collector-golden/observations.json`。

**配对授权**：扩展绝不持有用户 Token。一次性配对码（SHA-256 落库、单次使用、10 分钟过期、复放 5 次吊销连坐已发 Token）换来的 collector token 只能调提交接口——碰其他任何接口一律 401（有测试锁定）；采集只产生草稿，发布仍走人工 review。

**控制台** Discovery 页新增配对卡（生成码、会话状态、观察计数）和来源表（har/collector 并列显示）。

## 功能演示证据

- [控制台截图](C:\Users\84199\Documents\workspace\pp-switch\docs\assets\feat01\console-discovery-collector.png)：真实栈上配对码 + paired 会话 + 7 个候选（2 HAR + 5 collector 双来源）
- [扩展弹窗截图](C:\Users\84199\Documents\workspace\pp-switch\docs\assets\feat01\extension-popup.png)：真实 UI 演示模式
- `scripts/demo-collector-flow.py`：起栈后一条命令重放扩展的完整报文流（配对→令牌→提交→草稿入库）
- 交付记录：[docs/32-FEAT-01-COLLECTOR.md](C:\Users\84199\Documents\workspace\pp-switch\docs\32-FEAT-01-COLLECTOR.md)（含安装步骤、隐私模型、CI 迭代记录）

## 如实报告：CI 跑了三次才绿

前两次 PG 矩阵腿挂——`too many clients already`。根因是 control-api 测试 harness 每个测试泄漏连接：先修了 harness engine 未 dispose，仍剩 2 个 e2e 用例挂，再排查发现 `create_app()` 内部对同一测试库还建了第二个 engine；两个都在 teardown 释放后全绿。这个坑已写进 docs/32 和持久记忆。

## 诚实的边界与下一步

无头环境装不了未打包扩展，所以 `chrome.debugger` 的 chrome.* 接线（约 100 行）还需要你在 Chrome 里按 docs/32 §4 装一次实测——这是遗留清单第一项，其余全部逻辑（含 CDP 事件流）已被纯函数测试覆盖。测试合计 304 项（Python 279 + vitest 25），按新评审方式，重点放在了功能可演示和隐私边界可证明上。下一项是 **FEAT-02：API 发现与发布向导**。

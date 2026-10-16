# 被正式认可的设计决定与不可破坏的约束

本页只总结历史审批明确支持过的方向，不新增架构要求，也不声称能取代源仓库的 ADR 原文。

## 产品级不变量

1. **能力先发现、再审核、最后按身份公开**：Discovery Candidate / Operation 与正式 Published Capability 不可混淆。
2. **模型不拿凭证**：Agent 只能提交业务参数，不能有 `get_secret`、任意目标 URL、任意 SQL、通用 Shell 或绕过执行器直接解密能力。
3. **用户凭证与 Agent 授权分离**：Connection/Profile 有明确所有者；Agent 通过有作用域、期限、可撤销的 Delegation 使用用户授权连接，不存在共享 Agent 的全局任意账号。
4. **权限计算默认拒绝**：Grant、Delegation、Connection Scopes 与 Capability `required_scopes` 需要真正满足操作要求；身份由可信会话/签名上下文确定，不由模型自由填写用户 ID。
5. **新调用、幂等回放、历史结果读取都实时核验**：缩权和撤销不能从旧缓存或历史接口重新拿到当前已无权访问的数据。
6. **输出严格按审核契约投影**：递归白名单、类型校验、未解释 `$ref`/不支持组合拒绝；受限 `allOf` 对重复属性拒绝，不能宽松透传。
7. **秘密与审计生命周期有隔离**：结果分类、默认有限 TTL、静态加密、过期清理、读取先授权再解密；原始凭证明文不落日志、审计或模型上下文。
8. **隔离不等于无法使用**：Executor 可以通过专用 Egress Gateway 访问批准的 HTTPS 目标，同时直接出口被隔离；生产的网络/身份强制控制须实测。

## 已提及的 ADR / 决策记录

| ADR 或决策 | 根据对话能明确的含义 | 进一步核对 |
|---|---|---|
| `ADR-002` | 旧 Agent `personal` 映射兼容方向，后续必须受委托上下文约束 | `docs/22`、`docs/23` |
| `ADR-003` | 共享 Agent 多委托要显式消歧，不能隐式选择用户 | `docs/22`、`docs/23` |
| `ADR-004` | httpcore 连接级 IP 钉扎，而不只是连接前 DNS 检查 | `docs/22` |
| `ADR-005` | Per-invocation Delegated Identity；10 分钟可复用签名 Bearer，独立委托签名根、generation 失效 | `docs/23`、`docs/24` |
| `ADR-006` | 开发方报告补录，但本次对话未给出可明确重述的完整 ADR 标题和条款 | **阅读源文，勿编造** |
| `ADR-007` | Response Guard：有效 schema 严格投影，无 properties 的旧路径走 allow_fields；不可解释 `$ref` 拒绝 | `docs/24` |
| `ADR-008` | `stored_result()` 作为统一结果读取出口，授权先于解密 | `docs/25` |
| `ADR-009` | 解密失败/过期/未保存等对外表现一致，避免成为探测通道；内部单独记录事件 | `docs/26` |
| Gate R1 | 将网络、SecretProvider、OIDC、数据生命周期生产验证单列，不阻塞合成数据功能开发 | `docs/30` |

## 不应该扩大化的说法

- “密钥不进入模型”不等于“业务敏感数据绝不会发给模型”。业务输出仍要经范围授权、字段投影与最小化。
- “有 MCP stdio 适配器”不等于“所有 Claude/Cursor 客户端都完成实测”。
- “一键切换 Profile 在 Mock 下通过”不等于“真实 Keyring 凭证取用与撤销完成”。
- “代码/CI 通过”不等于“真实主机级网络边界和备份恢复完成”。

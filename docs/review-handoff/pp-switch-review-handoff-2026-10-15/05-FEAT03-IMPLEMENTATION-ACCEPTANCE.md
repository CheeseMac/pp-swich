# 下轮获批主任务：FEAT-03 本地 Keyring + 真实 Profile 切换

**状态：获准立即开发，尚未交付。** 目标是让普通个人开发者在自己的可信机器上用真实的 OS 安全存储机制保存**可撤销的测试凭证**，通过 Profile 一键切换后，让 Agent 使用同名能力执行，却不看到任何凭证明文。

## MVP 交付范围

1. 实现本地安全 `SecretProvider`：优先由 OS Keyring（根据真实目标系统的 Keyring 支持情况）保管秘密；控制平面只存 `secret_ref` 等必要元数据。安全存储不可用时明确报错，**不能悄悄退回明文存储**。
2. Console 提供连接与 Profile 管理：添加/更新/撤销测试凭证，明确当前 Active Profile；不展示已保存明文、不把凭证值写到审计/错误提示。
3. 使用已有 `Connection → Profile → ActiveBinding` 模型：切换保留其所有权、权限、版本冲突与调用快照语义；不能切换到别人的连接。
4. 真实 Executor 内仅在调用时取用相应凭证，按获准 Endpoint 请求目标，不提供模型可调用的“读取密钥”工具。
5. 串联 FEAT-05A：Agent 使用同一个 Published Capability，Profile 切换后无需重新发明工具名或对外重复注册。
6. 沿用 Public Tool Schema、required_scopes、Delegation、Response Guard 与审计机制，不创建第二套平行权限系统。

## 必须跑通的验收剧本

| 步骤 | 操作 | 判定标准 |
|---|---|---|
| 1 | 准备 `TEST_KEY_A` 与 `TEST_KEY_B` 两个不同的、低权限、可撤销的合成测试凭证 | 仅安全存储后端持有明文；UI 不回显 |
| 2 | 创建 Connection A/B、Profile A/B、明确 Agent Grant/委托关系 | B 不借用 A 的 Connection |
| 3 | 激活 Profile A；调用同一个 `crm.get_customer` | 测试后端**确认**使用 A 身份，业务结果经 Guard |
| 4 | 通过 UI 一键切换到 Profile B，再调用同名工具 | 后端**确认**使用 B 身份，不需要给 Agent 暴露 Key |
| 5 | 撤销或禁用 B 凭证后重试 | 受控失败，不回退到 A，也不使用其他人的凭证 |
| 6 | 尝试无授权 Agent / 错误 delegation / 超 scope 调用 | 均拒绝；目录不会暴露超权限工具 |
| 7 | 对日志、审计、数据库持久化列、MCP tools/list、tool 返回值及模拟模型请求做 Canary 检查 | 任何地方都不能出现测试凭证明文 |
| 8 | 安全存储不可用/解密失败、Profile 版本冲突、并发切换 | 明确失败或按现有快照语义执行；不悄悄明文回退 |
| 9 | Chrome 和 Claude/Cursor 手工路径中至少完成一个真实 Agent 端到端调用 | 凭证隔离与实际可用性同时成立 |

**特别说明**：正常 SDK `tools/list` 只证明可发现工具；必须另跑 `tools/call` 并看后端的凭证身份证据。历史 `POST /api/customers/search` 曾 405，不要把它当成成功用例。

## 明确不在本轮强推

- 企业 OIDC SSO、跨多服务器 OpenBao 大规模部署与主机防火墙综合认证。
- 桌面 GUI/RPA、任意 SQL、动态解析未知网页登录 Cookie。
- 再做一个新的 MCP 网关或重建 Registry。
- 自动把企业高价值真实 API Key 纳入仓库测试。

## 交付物与证据格式

- 可实际启动的代码、设置说明、连接/Profile UI、必要迁移。
- 功能演示截图/视频：两 Profile 切换前后同名工具调用成功。
- 核心端到端测试与 Canary 防泄露测试，包含拒绝路径。
- CI（pytest、vitest、ruff、mypy、密钥扫描、构建）真实运行结果，注明 commit 与 job。
- 清楚列出还没有实测的操作系统与 MCP 客户端类型。
- **不要把本功能完成直接标为 Gate R1 SECRET-06 生产通过**；Gate 还需要生产环境取用、轮换、撤销和隔离证据。

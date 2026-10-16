# 40 · Alpha 快速上手与实机验收剧本（M2 第一腿）

> 本轮（2026-10-16，基线 `78576c7` 之后）交付：**可独立安装验证**
> （`scripts/verify-install.py` + CI install-smoke job）、面向真实用户的
> Quickstart、以及两项 P0 实机验收的逐步剧本（Chrome 扩展 /
> Claude Desktop·Cursor）。同时把 FEAT-04 复核的两条边界说明
> （只读账号 GRANT 责任、远程 DB TLS 前置）固化到文档（本文 §5、
> docs/39 §2/§5）。
>
> 下一轮开发建议（需单独提案后动工）：共享执行器的 Keyring
> 多用户所有权隔离（Gate R1 SECRET-06 部署前置项，docs/37 登记）。

## 1. 安装与一键自检

系统要求：Python 3.12+、Node 20+、[uv](https://docs.astral.sh/uv/)、pnpm。

```bash
git clone https://github.com/CheeseMac/pp-switch && cd pp-switch
uv sync --all-packages && pnpm install

# 一键安装自检：全新数据库 → 迁移 → 真实服务 → 完整首用户旅程
# （发现→发布→双档案切换→SDK 调用→响应守卫断言→审计），仅回环流量
uv run python scripts/verify-install.py
```

预期输出末行 `INSTALL VERIFIED (9 steps, ...)`。任何一步失败即安装
不完整——先修环境再继续，不要跳过。

## 2. Quickstart：从安装到第一次 Agent 调用

```bash
# 启动本地全栈（control 8911 / executor 8912 / mock CRM 8913 / console 5173）
uv run python scripts/dev-up.py --ui

# 另开终端：初始化演示数据（合成令牌写入 data/dev-tokens.json）
uv run python scripts/seed-demo.py
```

打开 http://localhost:5173 ：

1. **登录**：选 Alice（admin）。合成令牌仅本地 demo（`PP_SWITCH_DEMO_MODE`）。
2. **发现**：Discovery 页导入 `tests/fixtures/synthetic-har/crm.har.json`
   （含密钥形状的上传会被 422 拒绝——脱敏是硬约束不是建议）。
3. **发布向导**：Wizard 页选 `GET /api/customers/{id}` → 配置参数与
   响应字段 → 试调 → 创建草稿 → **切换 Carol 登录**完成
   review → verify → publish（审核者≠创建者是硬性规则）。
4. **数据库工具（可选，FEAT-04）**：先在本机
   `uv run ppswitch-secret put crm-db-ro --scheme postgres --user <只读账号>`
   存入只读账号；Discovery 页「Database discovery」卡片建目标并由
   Carol 审批 → Connections 建 postgres 连接并激活 → Profiles 建
   无 endpoint 的数据库档案 → 发现 schema/视图 → Wizard 走同一链路。
5. **接入 Agent**：Capabilities/AgentAccess 页一键生成 MCP 配置
   （令牌经 env 传递，不走命令行参数）→ 按 §4 剧本在真实客户端拉起。

## 3. 实机验收剧本 A：Chrome 扩展采集（P0）

前置：`cd apps/browser-extension && pnpm install && pnpm build`（产物 `dist/`）。

| # | 操作 | 预期 |
| --- | --- | --- |
| A1 | Chrome 打开 `chrome://extensions` → 开发者模式 → 「加载已解压的扩展程序」→ 选 `apps/browser-extension/dist` | 扩展出现在列表，无错误 |
| A2 | console（5173）Discovery 页 → Browser collector 选 service → Generate pairing code | 显示一次性配对码（10 分钟有效，仅显示一次） |
| A3 | 点扩展图标 → 填 control 地址 `http://127.0.0.1:8911` → 粘贴配对码 → 配对 | 扩展获得仅限该 service 的短时 collector token（非用户令牌） |
| A4 | 在扩展里对目标页 Attach（chrome.debugger 授权）→ 正常浏览目标站点的 API 页面 | 页面顶部出现"正在调试"横幅（Chrome 对调试器的标准提示） |
| A5 | 扩展里提交观察 | console Discovery 页出现新候选（零值骨架：只有参数名/字段类型，无任何值） |
| A6 | 关闭并重开浏览器（Service Worker 重启恢复） | 配对与提交状态恢复，无需重新配对 |
| A7 | 断开 Attach | 采集停止；无任何后台采集 |
| A8 | 负向：在未 Attach 的标签页浏览 | 无观察产生（域名范围=显式附加的 tab） |

通过标准：A1–A8 全部符合预期，且候选操作进入向导后可正常走发布链。

## 4. 实机验收剧本 B：Claude Desktop / Cursor 拉起 MCP（P0）

前置：console 已发布至少一个能力（§2 步骤 3）。

| # | 操作 | 预期 |
| --- | --- | --- |
| B1 | console AgentAccess 页：创建 Agent 身份 | 一次性令牌仅显示一次（不落截图/日志） |
| B2 | 授权该 Agent 调用已发布的能力 | 授权列表出现该能力 |
| B3 | 复制生成的 MCP 配置（`uv run ppswitch-mcp`，令牌在 env `PP_SWITCH_DEV_TOKEN`） | 配置不含明文令牌于命令行参数 |
| B4 | 粘贴到 Claude Desktop（`claude_desktop_config.json` 的 mcpServers）或 Cursor 的 MCP 设置 → 重启/重连 | 客户端显示 ppswitch 服务器已连接 |
| B5 | 在客户端让模型列出工具 | `tools/list` **恰好只含**被授权的已发布工具 |
| B6 | 让模型调用该工具（给一个已知业务参数） | 返回守卫投影后的结果（仅允许字段，无凭证/泄漏字段） |
| B7 | console 撤销该 Agent 授权（或过期）→ 再次调用 | 调用被拒（CAPABILITY_NOT_FOUND，存在性不泄露） |
| B8 | 检查客户端侧配置与进程列表 | 无令牌进入命令行参数/Shell 历史 |

通过标准：B5–B7 的授权边界与撤权即时性在**真实客户端**复现
（控制面链路已由 e2e 覆盖；本剧本补的是外部 stdio 客户端证据）。

## 5. 已知限制与部署边界（如实）

1. **开发态/合成数据 Alpha**：demo 模式默认关闭；非 demo 下缺生产
   配置拒绝启动。真实企业凭证未准入（Gate R1）。
2. **FEAT-04 只读账号责任边界**：连接激活证明"账号可连接且非
   superuser/bypassrls 级"，**不构成对账号全部权限的审计**——部署者
   必须自备仅授予必要 SELECT 权限的数据库账号（GRANT 由 DBA 侧保证）。
3. **远程数据库 TLS 前置**：当前 DB 直连仅适用于本地可信目标；
   开放远程/共享数据库目标之前必须补强制 TLS + 服务器证书校验
   （实现建议：psycopg `hostaddr` 用已钉扎 IP、`host` 保留原始主机名
   供证书验证）+ 拒绝降级测试。
4. **共享执行器 Keyring 隔离**（部署前置项，未实现）：同一 OS 账户下
   多用户可互相引用对方 keyring 名——上线共享执行器前必须实现
   secret 引用与 owner 的强绑定（需单独提案）。
5. FEAT-04 首切片为单行 get-by-key 语义（limit≤2、多行即拒）、仅
   postgres、不经 egress-gateway——扩展需独立设计审批（docs/39 §5）。
6. 待实机项即本文 §3/§4 两剧本；通过后 M2 才能关闭。

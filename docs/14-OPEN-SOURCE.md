# 14 · 面向个人与企业的开源治理、版本发布与社区计划

## 1. 项目目标

`pp switch` 希望被个人开发者、企业平台工程师和 Agent 应用团队广泛采用：**能本地部署、可信使用、容易贡献、无供应商锁定**。

## 2. 开源许可证（提案）

建议考虑 **Apache-2.0**：
- 允许个人和企业使用、部署、修改、分发；
- 带明确的专利授权条款，较适合企业评估；
- 仍须遵守第三方组件许可和 NOTICE 要求。

**在维护者确认并真正添加 `LICENSE` 文件之前，不能宣称仓库已经以 Apache-2.0 授权。** 生成的文档不是正式法律建议。

## 3. 品牌

- 品牌展示：`pp switch`（小写空格）
- GitHub / 包与命令：`pp-switch` / `ppswitch`
- 推荐副标题：`Secure API & Secret Switching for Humans and Agents`
- README banner：`assets/pp-switch-github-cover.png`

仓库、PyPI、npm、GitHub 组织名、域名和商标是否可用，需要在正式发布前分别核对。不要假定 `pp switch` 当前具备排他商标权。

## 4. 公开透明原则

- 明确标注各功能 `Implemented`, `Experimental`, `Planned`；
- 不隐瞒当前无法保证安全的场景：同权限域 AI 编码 Agent、依赖 Cookie/CSRF 的第三方私有网页 API；
- 发现模块必须 opt-in，不默认采集浏览器全部网络数据；
- 禁止将用户真实密钥、私有 HAR、真实个人资料放到公开 Issue；
- 公开安全基线、威胁模型和安全测试覆盖率。

## 5. 建议的发布节奏（阶段，不承诺具体日期）

| 版本 | 范围 |
|---|---|
| `0.1.0` | HAR/OpenAPI 导入、基础目录、mock 执行、Profile 切换 |
| `0.2.0` | Secretless Python SDK、真实 HTTP Executor、输出过滤、严格权限 |
| `0.3.0` | MCP 适配、个人/团队工具发布 |
| `0.4.0` | 浏览器按站点授权采集、数据库只读能力 |
| `0.5.0` | 多节点内网 Connector、OIDC 企业权限、审批 |
| `1.0.0` | 安全审计、兼容性承诺、企业部署文档、升级工具 |

实际版本以经过审核的实现能力为准；不要仅以日历到期就宣布功能成熟。

## 6. 版本兼容性

- Public SDK 和 HTTP API 采用 SemVer 思路：兼容范围、弃用期及迁移指南明确。
- 已发布 Capability Schema 不可被静默覆盖；新字段/权限变化发布新版本。
- MCP 版本策略单独兼容测试，避免核心目录与具体协议 SDK 绑定。
- DB migration 升级必须有备份和回滚方案。

## 7. 贡献方式

- Issue 分类：`bug` / `security`（私下披露）/ `feature` / `docs` / `good first issue` / `integration`。
- PR 必需：需求背景、权限/安全影响、测试、截图/示例、兼容影响。
- 高风险 PR（SecretProvider、授权、Executor、网络出口）必须双人审查。
- 新 Adapter 应提供只读示例、synthetic fixture、无密钥 CI 测试和 README。
- 不提交真实服务端授权材料，Issue 附件必须先脱敏。

## 8. 企业采纳检查表

- [ ] 清晰的许可证和依赖许可证清单；
- [ ] SBOM、构建可复现、依赖 lockfile；
- [ ] 安全漏洞披露渠道与支持政策；
- [ ] Secretless 威胁模型与授权边界说明；
- [ ] OIDC/MFA、审计、备份、密钥轮换指南；
- [ ] 部署在纯内网/离线环境时的工作方式；
- [ ] 数据保留、API 观察可见性、隐私保护机制；
- [ ] API/SDK 向后兼容政策。

## 9. 开源社区的边界

API Collection 不可鼓励用户抓取无权限第三方数据、绕过网站的授权或访问控制。任何“从网页自动发现 API”的贡献，都必须符合来源的合法授权和隐私约束。

不接受“导出所有 Token 给 Agent”“通用解密工具”“任意 URL 代理到内网”等破坏基础安全边界的设计。

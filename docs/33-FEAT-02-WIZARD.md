# 33 · FEAT-02 交付记录：API 发现与发布向导

> 功能冲刺第二枪，同 PR 附带 FEAT-01-F1 可靠性收尾（评审 2026-10-15
> 提出的 Service Worker 恢复问题，三项指控核实全部属实后修复）。
> 评审方式：验收看用户能否无编程走通"发现 → 工具"闭环。

## 0. 一句话

用户在 Web Console 的「发布向导」里：选一个采集/导入发现的操作 →
勾选暴露哪些参数与响应字段（表单，不写 YAML）→ 创建草稿 →
**真实试调一次**（经活动 Profile、受信执行器、响应守卫）→
第二人 review → verify → publish → 授权 Agent → Agent 工具清单立即可见、
人可直接调用。全程零代码、零 OpenAPI。

## 1. FEAT-01-F1 可靠性收尾（先修再用）

评审指出并核实（三项全部属实，另发现一处连带 bug）：

| 指控 | 核实 | 修复 |
| --- | --- | --- |
| collector token 只在 SW 模块变量，未持久化 | 属实——SW 挂起即丢，弹窗仍显示"已连接" | token 存 `chrome.storage.session` 独立 key，绝不进 CollectorState/弹窗消息 |
| State 无可恢复令牌 | 属实 | `restore()` 重建缓冲 + 校验 token 有效期，过期即清配对（不再假装已连接） |
| loadingFinished 后 autoFlush 读未同步的旧存储 | 属实，且 `doSubmit` 提交旧列表却按引擎计数清空——丢数据路径 | 每条观察**先落盘再判断自动提交**；submit 上传与清空同源同量 |
| ——（连带发现） | | 重构成 `CollectorRuntime`（纯编排、可注入依赖） |

新增 vitest：SW 重建场景（配对→采集→销毁实例→恢复→提交，不丢不重）、
阈值自动提交、过期清理、断线保留、取消配对清除 token——扩展测试 25→31。
另外按评审建议收紧**采集域名范围**：默认只归集所附标签页自身 origin，
第三方 Fetch/XHR 丢弃（engine `allowedOrigins` + 测试 + 弹窗文案明示）。

## 2. FEAT-02 交付内容

### 后端（少量业务编排，不重建 Registry）

- `POST /api/v1/operations/{id}/test`（`services/operation_test_service.py`）：
  向导"试调"步骤。经**调用者自己的活动 Profile**（personal 上下文，与真实
  调用同一解析逻辑）+ 已审批 endpoint + 签名 plan + 受信执行器（网络策略/
  响应守卫全在执行器侧）执行一次；**不产生 InvocationRow、不消耗授权**。
  - 权限矩阵新动作 `operations.test`（member 可用；agent 永不授予）；
  - 参数白名单来自操作自身的观察契约（路径占位符 ∪ 观察 query 名 ∪
    观察 body 字段），未知/非标量/缺路径参数一律 422——试调不能夹带
    任意请求素材；
  - 恒等 execution 映射从观察契约生成（空映射意味着不发 query/body，
    这是 executor 只发送模板内参数的语义）；
  - 每次尝试留审计（含拒绝路径，先 commit 再抛错——与 pairing replay
    同一课）；
  - 结果 8KB 截断展示。

### 前端（产品主体）

新「发布向导」页（`apps/web-console/src/pages/Wizard.tsx`）：

1. **选择**：候选操作列表（含来源服务/状态），Discovery 页每行新增
   「发布为工具」直达；
2. **配置**：表单化——工具名（自动生成 `service.method_path` slug）/显示名/
   描述/风险等级；参数表（来源 path/query/body、类型、暴露/必填勾选，
   path 强制必填）；响应字段勾选（allow_fields）；生成的 input_schema 与
   execution 映射可展开预览；
3. **试调**：按参数类型生成输入表单 → 展示守卫投影后的结果 JSON；
   无活动 Profile 时明确提示去 Profiles 页；
4. **审核发布**：状态驱动（draft→review→verify→publish），明示
   **必须由非创建者操作**（demo：切 Carol 登录回到本页即可续走）；
5. **授权 Agent**（可选）：选 agent + scopes → POST /agent-grants；
   MCP 客户端一键配置留给 FEAT-05。

向导状态全部由 capability status 推导——跨会话、跨账号可续走。

## 3. 验收证据

- **全链路验收测试**（`test_wizard_flow.py`）：采集观察 → 建草稿 →
  试调 200 → Carol review/verify/publish 1.0.0 → 创建者自审 403（守卫）→
  grant → **Agent 工具清单出现新工具且只见已发布版本** → 人工直接调用
  succeeded。一次通过。
- 端点测试（`test_operation_test_api.py` ×7）：试调返回守卫投影结果
  （MockAdapter 的泄漏 canary 被剥除）、未知参数/非标量/缺路径参数 422、
  无活动 Profile 409、agent 403、允许+拒绝路径都留审计。
- 扩展 vitest 31 项（含 F1 SW 重建五连测）。
- 截图：[向导配置步骤](assets/feat02/wizard-configure.png)（表单化参数/
  响应字段勾选 + schema 预览）、[试调成功](assets/feat02/wizard-test-call.png)
  （真实 HTTP 调用 mock CRM，守卫投影后的结果与审核卡同框；405 错误
  路径同样被诚实展示）。

## 4. 安全边界（只查与新功能直接相关的部分）

- 试调是**人-only** 的动作（矩阵新条目，agent 不授予）；
- 试调参数白名单 = 该操作自己的观察契约，请求只能落在已观察到的形状内；
  endpoint 必须已审批、connection 必须 active；
- 审核发布链完全复用既有 capability 状态机（创建者不能自审自发布——
  向导只是编排，没有绕过任何一步）；
- 响应经执行器响应守卫投影（试调结果断言了 canary 剥除）；
- 扩展端 token 持久化在 session storage（浏览器关闭即清），弹窗/State
  消息永不携带。

## 5. 已知边界

- 试调结果 8KB 截断；试调不做幂等键/审批（它不是正式调用，不产生调用记录）；
- 向导创建后如需改配置，走"返回选择→重新创建草稿"（草稿不可编辑是
  既有 Registry 语义，未改动）；
- agent 目录步骤在 demo 模式用 /dev/principals；生产 OIDC 目录待 FEAT-05；
- Chrome 实机验证（含 F1 场景）仍待用户装扩展实测——遗留项与 FEAT-01 相同。

## 6. 数字

Python 287（279 + wizard 1 + endpoint 7）+ vitest 31 = 318 项测试；
ruff/mypy/密钥扫描/双端构建全绿（CI 结果见提交记录）。

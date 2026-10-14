# 32 · FEAT-01 交付记录：浏览器 API Collector

> 功能冲刺第一枪。评审方式已按用户 2026-10-14 的决定调整：验收优先看
> 实际功能演示与隐私/凭证边界，不再以测试数量为主要指标。
> 状态：**代码合入 main，全量本地门禁绿（304 项测试），CI 待跑。**

## 0. 一句话

安装 Chrome 扩展 → 控制台生成一次性配对码 → 扩展里粘贴连接 →
用户在**明确附加了调试**的标签页上正常使用网页 → 扩展把 Fetch/XHR
流量的**结构**（方法、路径模板、参数名、字段类型）上传 → 控制台出现
**草稿**候选 Operation → 走既有 verify → capability → publish 流程。

## 1. 用户可见的流程（截图）

| 环节 | 证据 |
| --- | --- |
| 控制台 Discovery 页：配对卡（一次性码 + 已配对会话）、har/collector 双来源观察表、草稿列表 | [docs/assets/feat01/console-discovery-collector.png](assets/feat01/console-discovery-collector.png)（真实栈：control-api 8911 + 真实配对/提交链路） |
| 扩展弹窗：已连接徽章、采集中状态、候选 API 预览（次数/状态码/参数/响应字段）、上次提交统计、隐私声明 | [docs/assets/feat01/extension-popup.png](assets/feat01/extension-popup.png)（popup 演示模式：真实 UI，罐头状态；`?demo=1` 仅在无扩展上下文时生效） |

演示复现：

```bash
uv run python scripts/dev-up.py --ui     # control 8911 / executor 8912 / mock 8913 / console 5173
uv run python scripts/seed-demo.py
uv run python scripts/demo-collector-flow.py   # 模拟扩展 SW 的完整 wire 行为
```

`demo-collector-flow.py` 与扩展 service worker 发的是**同一种报文**
（同一黄金夹具），所以它跑通即等于扩展提交链路跑通（TS 侧逻辑另由
25 项 vitest 固化，包括这份夹具的字节级一致）。

## 2. 隐私与安全边界（本功能的核心约束）

**扩展端——零值样本（zero-value samples）**：
- 每个请求/响应 JSON 被还原成"骨架"：叶子只留类型标签
  （`""`/`0`/`0.5`/`false`/`null`），键与嵌套保留。**任何数据值在
  浏览器内即被丢弃**，不是"上传后脱敏"；
- 路径先做粗归一（数字→`{id}`、UUID→`{uuid}`、长十六进制→`{hash}`），
  原始记录 ID 不出浏览器；
- Query 只传参数名（值一律 `""`）；**头一个都不传**（更严于 HAR 路径）；
- GraphQL 只传 operationName（API 结构，非数据）；
- 深度 8 层 / 2000 节点 / 单体 256KB 封顶；缓冲区上限 5000 条；
- 只采集用户主动附加 `chrome.debugger` 的那一个标签页——Chrome 会在
  页面顶部常驻"正在调试此标签页"横幅，授权状态用户随时可见；
- Fetch/XHR 之外的资源（Document/Script/Image/...）不采集；控制台自身
  的 origin 永不采集。

**传输与凭证——配对授权，绝不共享用户 Token**：
- 扩展从未持有任何用户管理 Token。控制台（登录用户）生成绑定单个
  service 的一次性配对码：8 字符 ~40bit 熵、SHA-256 哈希存储、
  单次使用、10 分钟过期、复放 5 次即吊销（连带吊销已发 Token）；
- 扩展用配对码换取**专属短期 collector token**（默认 8 小时，
  `PP_SWITCH_COLLECTOR_TOKEN_TTL`），该 token 在权限矩阵之外——
  它**只能**调 `POST /api/v1/collector/observations`，碰任何其他
  接口一律 401（有测试锁定）；collector 不是 principal，没有
  principals 表行；
- 令牌/配对码只在 chrome.storage.session（会话级，浏览器关闭即清，
  不进磁盘 profile 同步）。

**服务端——fail-closed 二次防御**：
- 所有观察再过一遍 `sanitize_observation`：扩展有 bug 真把
  `Bearer ...`/`sk-...` 形状的值发上来 → 整批拒绝（UNSANITIZED_INPUT，
  连同批内干净观察一起拒，有测试），永不入库；
- 每批上限 200 条（`PP_SWITCH_COLLECTOR_MAX_OBSERVATIONS`），
  解析器另有 500 条硬顶；
- 采集**只产生草稿**（observed/inferred/needs_review），不 verify、
  不发布、不执行——发布仍走人工 review 链路（capability.review 是
  reviewer 角色动作）；
- 全程审计：`collector.pairing.create / pairing.exchange / pairing.replay
  / collector.submit`（拒绝路径也留审计，与仓库既有惯例一致）。

## 3. 架构落点

```
apps/browser-extension/         Chrome MV3 + TypeScript（pnpm workspace 新成员）
  src/shared/skeleton.ts        值→零值骨架（vitest：值永不残留）
  src/shared/paths.ts           粗路径归一 / 参数名提取 / 本地预览键
  src/background/engine.ts      纯 CollectorEngine：CDP 事件→观察
                                （重定向链取末次、requestId 去重、
                                 loadingFailed 丢弃、资源类型过滤、
                                 body 逐出降级、断线缓冲保留）
  src/background/cdp.ts         chrome.debugger / Network 域封装
  src/background/index.ts       SW：消息 API、配对、自动/手动提交、断线恢复
  src/popup/*                   弹窗 UI（中文，隐私声明常驻）
  tests/*.test.ts               25 项 vitest
packages/discovery-sdk/
  parsers/merge.py              从 har.py 抽出的共享管线（sanitize→
                                normalize→fingerprint→infer）
  parsers/collector.py          FEAT-01 解析器（max 500/批）
packages/db/                    collector_pairings 表 + 迁移 e5c9a2f01d38
apps/control-api/
  services/collector_service.py 配对/交换/校验/提交
  api/v1/router.py              4 条新路由 + GET /observations
apps/web-console/               Discovery 页配对卡 + 观察来源表
tests/fixtures/collector-golden/observations.json
                                跨语言黄金契约夹具（TS 引擎真实输出）
```

**跨语言契约**：`vitest tests/golden.test.ts` 用引擎真实输出固化这份
夹具；`packages/discovery-sdk/tests/test_collector_contract.py` 把同一份
文件喂给 Python 解析器断言候选结果，并逐叶子验证"无任何数据值"。
两端谁漂移谁先红。

## 4. 本地安装步骤

```bash
cd apps/browser-extension
pnpm install && pnpm build        # 产物在 dist/
# Chrome: chrome://extensions → 开发者模式 → 加载已解压的扩展程序 → 选 dist/
# 控制台：Discovery 页选服务 → Generate pairing code
# 扩展弹窗：填控制台地址（默认 http://127.0.0.1:8911）→ 粘贴配对码 → 连接
#          打开目标网站标签页 → 开始采集 → 正常操作 → 提交
```

host_permissions 默认只含 localhost/127.0.0.1；远程控制台部署时需在
manifest 中追加对应 origin。

## 5. 测试与验证方式

| 层 | 内容 |
| --- | --- |
| vitest（25） | 骨零值不变量、粗归一、引擎全事件流（重定向/去重/失败/资源类型/体逐出/GraphQL/断线恢复/容量上限）、黄金契约 |
| discovery-sdk（13） | collector 解析：合并、指纹分裂、fail-closed 报告、`{id}` 幂等、类型冲突、容量、跨语言契约 ×2 |
| control-api（11） | 配对全流程成草稿并合并进既有 HAR 操作、单次使用、复放吊销连坐 token、collector token 非_principal（6 路径 401）、agent 建码 403、跨工作区 404、脏批整拒、批量上限 413、过期 token、无 token 401 |
| db（+1 断言） | 迁移含 collector_pairings，空库可迁可重迁 |
| 演示 | `demo-collector-flow.py` 真实栈全链路（本文 §1） |

合计 304 项测试全绿：Python 279（原 255 基线 + 新增 24）+ vitest 25。

## 6. 诚实的边界（没做的事）

- **chrome.debugger 真实浏览器联动未在本环境跑**：无头环境无法加载
  未打包扩展。CDP 事件→观察的全部逻辑是纯函数、被 vitest 以真实
  事件形状覆盖；`cdp.ts`+SW 的 chrome.* 接线（~100 行）需用户按 §4
  装进 Chrome 实测——这是下一轮验收清单第一项；
- popup 演示模式是罐头状态（真实 UI、真实渲染，非真实数据）；控制台
  截图是真实栈真实数据；
- 单标签页采集；跨标签页并行采集、采集期间的去重编辑（FEAT-02 范畴）
  未做；
- pairing 码暴力面：40bit×10min×单次有效 + 复放计数吊销。未加每 IP
  限流（留待反代层，与网关配额同批处理）。

## 7. 与红线的关系

- 无任何 `get_secret` 等价面；collector token 只能提交观察；
- 身份仍只来自服务端校验的 token（用户 token / collector token 两类，
  互不通用，均有测试）；模型可影响的参数没有任何身份语义；
- 仓库新增内容全部合成凭证（配对码/token 运行时生成，测试经 API 取得，
  无字面量）；`check-no-secrets.py` clean；
- fail-closed：脏批整拒、未知一律 401、过期即拒；
- demo 模式默认关闭不受影响（配对路由无 demo 门，生产同路径）。

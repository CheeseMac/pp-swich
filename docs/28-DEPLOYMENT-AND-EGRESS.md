# 28 · 部署与出口强制控制（PR-DEP-14）

> 目标：在本地单机、Docker Compose 与 Linux 内网多机三种模式下，
> 受信执行器（唯一接触凭证的组件）始终被**网络层 + 应用层**双重
> 约束。部署无关承诺不变：不依赖 Kubernetes。

## 1. 三层出口隔离模型

| 层 | 机制 | 失效时的兜底 |
| --- | --- | --- |
| 应用层（代码） | `approved_hosts` 显式白名单（无隐式放行）、连接层 IP 钉扎（解析-校验-连接同一字面量 IP）、link-local/元数据段不可豁免硬拒、IPv6 包装归一化（IPv4-mapped 与 NAT64 `64:ff9b::/96` 内嵌地址按其 IPv4 判定）、`trust_env=False`（无视代理环境变量）、重定向永不跟随 | —— 第一道 |
| 编排层（Compose） | executor 仅加入 `internal: true` 网络：**容器级无出口路由**，即使应用层被完全绕过也无法触达外网；control 双宿（internal + egress） | 应用层 |
| 主机层（防火墙） | 出口白名单只放行已批准目标（下附 iptables/nftables 样例）；应用层 `approved_hosts` 与主机层清单同源维护 | 编排层 |

`deploy/docker-compose.yml` 是第一版可执行方案：`docker compose up`
即得到上述拓扑；`deploy/.env.example` 列出全部必需密钥（缺失时非
demo 模式拒绝启动）。

### 主机层样例（Linux）

```bash
# executor 容器（由 cgroup/docker 隔离网段 172.20.0.0/16 表示）仅允许
# DNS(53/udp 到内网解析器) 与 internal 网段内的 control；其余出站 DROP。
# 真实目标白名单按 approved_hosts 清单逐条追加。
nft add table inet egress
nft add chain inet egress out { type filter hook output priority 0 \; policy accept \; }
nft add rule inet egress out ip saddr 172.20.0.0/16 udp dport 53 accept
nft add rule inet egress out ip saddr 172.20.0.0/16 ip daddr 172.20.0.0/16 accept
nft add rule inet egress out ip saddr 172.20.0.0/16 drop
```

iptables 等价物与 Windows/单机边界：本机开发（dev-up.py）不承载真实
凭证，应用层策略即为全部防线；Windows 主机部署需在宿主防火墙为
executor 进程配置等价出站规则（文档化承诺，样例随企业试点补充）。

### 评审点名的验证面（应用层已锁定，回归测试看守）

- **DNS 切换**：连接层钉扎（PinningNetworkBackend）——检查后二次解析无法改道；
- **代理环境变量**：全部出站 client `trust_env=False`，容器内同时清空代理 env；
- **重定向**：`follow_redirects=False`（执行器与 SDK）；
- **IPv6 / 内网 IP / 云元数据**：v4-mapped 与 NAT64 包装归一化后按内嵌
  IPv4 判定；link-local 不可豁免。

## 2. 升级说明（重要：不可逆操作）

迁移 `f3a62c8e1b47`（v0.1.3.1 → v0.1.3.2 起）会**清空全部历史明文
调用结果**（仅保留执行元数据）。这是安全优先的既定取舍（第七轮评审
支持），**不可逆**：升级前请自行确认无需通过应用读取旧结果。

同时注意：`result` 置 NULL 不等于数据库 WAL、磁盘历史页与既有备份中
的旧明文物理消失。正式企业部署要求：**备份加密（静态加密的备份介质）、
明确的备份保留期、以及覆盖"旧密钥时期备份"的恢复演练**（见 §4）。

## 3. 密钥体系

生产（非 demo）启动强制三项独立密钥（`deploy/.env.example` 附生成命令）：

| 密钥 | 用途 | 泄露影响面 |
| --- | --- | --- |
| `PP_SWITCH_INTERNAL_TOKEN` | control↔executor 计划签名 + 内部认证 | 可伪造执行计划 |
| `PP_SWITCH_DELEGATION_SIGNING_KEY` | 委托票据签名（不得与上者相同） | 可铸造委托身份 |
| `PP_SWITCH_RESULT_ENCRYPTION_KEY` | 结果静态加密（Fernet） | 可解密存量结果 |

## 4. 多实例密钥轮换流程（DATA-17 运维规程）

多台实例并存时，**严禁**在重加密完成前撤销旧密钥。标准流程：

1. **兼容窗口**：所有实例配置
   `PP_SWITCH_RESULT_ENCRYPTION_PREVIOUS_KEYS=<旧密钥>`（读窗口），
   新主密钥 `PP_SWITCH_RESULT_ENCRYPTION_KEY` 尚未切换或已切换均可读；
2. **统一切写**：逐台重启使全部实例以新主密钥写入；
3. **分页重加密**：每台执行 `POST /api/v1/admin/re-encrypt-results`
   （keyset 分页、逐批提交，大批量历史可断点续跑）；复核返回的
   `{rekeyed, failed}`，`failed>0` 时先处理解密失败行；
4. **撤销旧密钥**：确认所有实例重加密完成且 `failed=0` 后，从
   PREVIOUS_KEYS 移除旧密钥并再次重启；
5. **备份恢复验证**：用旧密钥时期的备份做一次恢复演练，确认
   PREVIOUS_KEYS 仍覆盖其密文（否则恢复的数据不可读——这是预期行为，
   需要在演练中确认业务可接受）。

## 5. 部署模式边界声明

| 模式 | 出口强制 | 凭证承载 | 适用 |
| --- | --- | --- | --- |
| 本地 dev（dev-up.py） | 应用层 | mock 合成凭证 | 开发/演示 |
| Docker Compose（deploy/） | 应用层 + 容器网络隔离 +（可选）主机防火墙 | 可载真实凭证（完成综合审计后） | 单机/小团队 |
| Linux 内网多机 | 应用层 + 主机出口防火墙（nftables/iptables 白名单） | 同上 | 企业内网 |

跨内网多服务器（Edge Connector）与 mTLS 属后续 PR-FEAT-07 范围；
在它落地前，跨机明文信道不建议承载真实凭证。

import { AgentAccessCard } from "../components/AgentAccessCard";

export function AgentAccessPage() {
  return (
    <div>
      <h1>Agent 接入</h1>
      <p className="page-sub">
        把已发布的工具安全开放给 Claude、Cursor 或自研 Agent：生成专属身份与
        MCP 配置、按工具授权、验证可见性。Agent 全程拿不到底层 API 凭证。
      </p>
      <AgentAccessCard />
    </div>
  );
}

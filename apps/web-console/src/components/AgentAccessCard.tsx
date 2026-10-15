import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "../api";
import type { Capability } from "../types";
import { Button, Card, Empty, ErrorNote, Field } from "../ui";

/**
 * FEAT-05 agent one-click access (shared by the wizard's step 5 and the
 * dedicated "Agent 接入" page):
 *
 *   pick/create an agent identity -> grant published tools -> copy the
 *   generated MCP client config (token shown ONCE) -> verify the agent
 *   sees exactly its granted tools over the same auth path the MCP
 *   adapter uses.
 */

interface AgentPrincipal {
  id: string;
  display_name: string;
  kind: string;
  token_hint?: string;
  token?: string; // only in the create response
}

const CONTROL_URL_DEFAULT = "http://127.0.0.1:8911";

function mcpConfigs(controlUrl: string, token: string) {
  const args = ["run", "ppswitch-mcp", "--control-url", controlUrl, "--token", token];
  const env = {
    PP_SWITCH_CONTROL_URL: controlUrl,
    PP_SWITCH_DEV_TOKEN: token,
  };
  return {
    generic: JSON.stringify({ mcpServers: { "pp-switch": { command: "uv", args } } }, null, 2),
    claude: JSON.stringify(
      { mcpServers: { "pp-switch": { command: "uv", args, env } } },
      null,
      2,
    ),
    cursor: JSON.stringify({ mcpServers: { "pp-switch": { command: "uv", args, env } } }, null, 2),
  };
}

export function AgentAccessCard({
  preselectedCapabilityId,
}: {
  preselectedCapabilityId?: string | null;
}) {
  const [agents, setAgents] = useState<AgentPrincipal[]>([]);
  const [published, setPublished] = useState<Capability[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  const [agentId, setAgentId] = useState("");
  const [newAgentName, setNewAgentName] = useState("");
  const [freshToken, setFreshToken] = useState<string | null>(null);
  const [selectedCaps, setSelectedCaps] = useState<string[]>(() =>
    preselectedCapabilityId ? [preselectedCapabilityId] : [],
  );
  const [controlUrl, setControlUrl] = useState(CONTROL_URL_DEFAULT);
  const [verifyResult, setVerifyResult] = useState<string[] | null>(null);

  const load = useCallback(() => {
    api.get<AgentPrincipal[]>("/api/v1/agent-principals").then(setAgents).catch(setError);
    api
      .get<Capability[]>("/api/v1/capabilities?status=published")
      .then(setPublished)
      .catch(setError);
  }, []);
  useEffect(load, []);

  const createAgent = async () => {
    setBusy(true);
    setError(null);
    setFreshToken(null);
    try {
      const created = await api.post<AgentPrincipal>("/api/v1/agent-principals", {
        display_name: newAgentName,
      });
      setAgentId(created.id);
      setFreshToken(created.token ?? null);
      setNewAgentName("");
      load();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };

  const grant = async () => {
    if (!agentId || selectedCaps.length === 0) return;
    setBusy(true);
    setError(null);
    try {
      for (const capability_id of selectedCaps) {
        await api.post("/api/v1/agent-grants", {
          agent_principal_id: agentId,
          capability_id,
          scopes: [],
        });
      }
      setVerifyResult(null);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };

  const verify = async () => {
    if (!freshToken) return;
    setBusy(true);
    setError(null);
    try {
      // same-origin proxy path like every console call; the controlUrl
      // field above is only for the GENERATED client config
      const response = await fetch("/api/v1/capabilities", {
        headers: { Authorization: `Bearer ${freshToken}` },
      });
      if (!response.ok) throw new ApiError("UNKNOWN", `HTTP ${response.status}`, {});
      const data = (await response.json()).data as Capability[];
      setVerifyResult(data.map((c) => c.name));
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };

  const configs = freshToken ? mcpConfigs(controlUrl, freshToken) : null;

  return (
    <Card title="接入 Agent（一键生成 MCP 配置）">
      <p className="muted" style={{ marginTop: 0 }}>
        选择或新建一个 Agent 身份 → 勾选要开放的已发布工具 → 复制生成的
        MCP 配置到 Claude / Cursor / 自研 Agent。令牌只在创建时显示一次；
        Agent 只能看到并被允许调用被授权的工具，拿不到任何底层凭证。
      </p>
      <ErrorNote error={error} />
      <div className="form-row">
        <Field label="已有 Agent">
          <select value={agentId} onChange={(e) => setAgentId(e.target.value)}>
            <option value="">select...</option>
            {agents.map((a) => (
              <option key={a.id} value={a.id}>
                {a.display_name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="或新建 Agent（admin）">
          <input
            value={newAgentName}
            placeholder="例如 claude-desktop / cursor-local"
            onChange={(e) => setNewAgentName(e.target.value)}
          />
        </Field>
        <Button onClick={createAgent} disabled={busy || !newAgentName.trim()}>
          新建并生成令牌
        </Button>
      </div>

      {published.length === 0 ? (
        <Empty>还没有已发布的工具——先在发布向导完成 review → verify → publish。</Empty>
      ) : (
        <p className="muted">
          授权已发布工具：
          {published.map((c) => (
            <label key={c.id} style={{ marginRight: 10 }}>
              <input
                type="checkbox"
                checked={selectedCaps.includes(c.id)}
                onChange={(e) =>
                  setSelectedCaps((prev) =>
                    e.target.checked ? [...prev, c.id] : prev.filter((x) => x !== c.id),
                  )
                }
              />{" "}
              <span className="mono">{c.name}</span>
            </label>
          ))}
        </p>
      )}
      <Button onClick={grant} disabled={busy || !agentId || selectedCaps.length === 0}>
        {busy ? "授权中..." : "授权所选工具"}
      </Button>

      {configs && (
        <div style={{ marginTop: 12 }}>
          <p className="muted" style={{ color: "#b45309" }}>
            ⚠️ Agent 令牌只显示这一次，关闭页面后无法找回（可新建 Agent 重新生成）：
            <span className="mono" style={{ userSelect: "all" }}> {freshToken}</span>
          </p>
          <Field label="控制台地址（写入配置）">
            <input value={controlUrl} onChange={(e) => setControlUrl(e.target.value)} />
          </Field>
          {(
            [
              ["Claude Desktop（claude_desktop_config.json / mcpServers）", configs.claude],
              ["Cursor（.cursor/mcp.json）", configs.cursor],
              ["通用 stdio MCP 配置", configs.generic],
            ] as const
          ).map(([label, config]) => (
            <details key={label} style={{ marginBottom: 6 }}>
              <summary className="muted">{label}</summary>
              <pre className="mono" style={{ fontSize: 11, userSelect: "all" }}>{config}</pre>
            </details>
          ))}
          <p className="muted">
            本地运行需要仓库环境：<span className="mono">uv sync --all-packages</span>，
            然后由 MCP 客户端按上面的配置启动{" "}
            <span className="mono">uv run ppswitch-mcp</span>。
          </p>
          <Button onClick={verify} disabled={busy}>
            验证连接（以该 Agent 身份列出可见工具）
          </Button>
          {verifyResult !== null && (
            <p className="muted">
              {verifyResult.length === 0
                ? "该 Agent 当前没有被授权的工具。"
                : `✅ 连接成功，该 Agent 可见工具：${verifyResult.join(", ")}`}
            </p>
          )}
        </div>
      )}
    </Card>
  );
}

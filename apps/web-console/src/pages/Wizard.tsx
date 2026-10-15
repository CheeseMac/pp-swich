import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api";
import type {
  CapabilityDetailView,
  Operation,
  OperationDetail,
  OperationTestResult,
  Principal,
  Service,
} from "../types";
import { Button, Card, Empty, ErrorNote, Field, StatusBadge } from "../ui";

/**
 * FEAT-02 publish wizard: discovered operation -> agent-callable tool,
 * without writing OpenAPI YAML or code.
 *
 * Steps are STATUS-DRIVEN (resumable across sessions and principals):
 * review/verify/publish intentionally require a principal other than the
 * creator - the wizard says so and the reviewer simply reopens this page.
 */

type ParamSource = "path" | "query" | "body";

interface ParamConfig {
  name: string;
  source: ParamSource;
  type: string;
  included: boolean;
  required: boolean;
}

function slugPart(value: string): string {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 48) || "x";
}

function pathParams(template: string): string[] {
  return [...template.matchAll(/\{([^}]+)\}/g)].map((m) => m[1]);
}

function propsOf(schema: unknown): Record<string, { type?: string }> {
  const section = (schema as { properties?: Record<string, { type?: string }> } | undefined)
    ?.properties;
  return section ?? {};
}

export function WizardPage({ seedOperationId, clearSeed }: {
  seedOperationId: string | null;
  clearSeed: () => void;
}) {
  const [operations, setOperations] = useState<Operation[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  const [detail, setDetail] = useState<OperationDetail | null>(null);
  const [serviceById, setServiceById] = useState<Record<string, Service>>({});

  // step 2: tool configuration
  const [toolName, setToolName] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [description, setDescription] = useState("");
  const [risk, setRisk] = useState("low");
  const [params, setParams] = useState<ParamConfig[]>([]);
  const [allowFields, setAllowFields] = useState<string[]>([]);
  const [allFields, setAllFields] = useState<string[]>([]);

  // step 3+: capability lifecycle
  const [capability, setCapability] = useState<CapabilityDetailView | null>(null);
  const [testArgs, setTestArgs] = useState<Record<string, string>>({});
  const [testResult, setTestResult] = useState<OperationTestResult | null>(null);
  const [publishVersion, setPublishVersion] = useState("1.0.0");
  const [evidence, setEvidence] = useState("verified against provider docs in console wizard");
  const [agents, setAgents] = useState<Principal[]>([]);
  const [agentId, setAgentId] = useState("");
  const [grantScopes, setGrantScopes] = useState("");
  const [grantDone, setGrantDone] = useState<string | null>(null);

  const load = useCallback(() => {
    api.get<Service[]>("/api/v1/services")
      .then((rows) => setServiceById(Object.fromEntries(rows.map((s) => [s.id, s]))))
      .catch(setError);
    api.get<Operation[]>("/api/v1/operations").then(setOperations).catch(setError);
  }, []);
  useEffect(load, []);

  useEffect(() => {
    // demo-mode agent directory for the grant step (production uses OIDC)
    api.get<Principal[]>("/api/v1/dev/principals")
      .then((rows) => setAgents(rows.filter((p) => p.kind === "agent")))
      .catch(() => setAgents([]));
  }, []);

  const selectOperation = useCallback(async (id: string) => {
    setError(null);
    setTestResult(null);
    setCapability(null);
    setGrantDone(null);
    try {
      const op = await api.get<OperationDetail>(`/api/v1/operations/${id}`);
      setDetail(op);
      const latest = op.versions[op.versions.length - 1];
      const queryProps = propsOf((latest?.input_schema as unknown as { query?: unknown })?.query);
      const bodyProps = propsOf((latest?.input_schema as unknown as { body?: unknown })?.body);
      const nextParams: ParamConfig[] = [
        ...pathParams(op.normalized_path).map<ParamConfig>((name) => ({
          name, source: "path", type: "string", included: true, required: true,
        })),
        ...Object.entries(queryProps).map<ParamConfig>(([name, prop]) => ({
          name, source: "query", type: prop.type ?? "string", included: true, required: false,
        })),
        ...Object.entries(bodyProps).map<ParamConfig>(([name, prop]) => ({
          name, source: "body", type: prop.type ?? "string", included: true, required: false,
        })),
      ];
      setParams(nextParams);
      setTestArgs({});
      const outputProps = propsOf(latest?.output_schema);
      setAllFields(Object.keys(outputProps));
      setAllowFields(Object.keys(outputProps));
      // default naming from the operation itself
      const seed = operations.find((o) => o.id === id);
      const namespace = seed ? slugPart(serviceById[seed.service_id]?.slug ?? "svc") : "svc";
      setToolName(`${namespace}.${slugPart(`${op.method} ${op.normalized_path}`)}`);
      setDisplayName(`${op.method} ${op.normalized_path}`);
      setDescription(`Discovered from traffic: ${op.method} ${op.normalized_path}`);
    } catch (e) {
      setError(e);
    }
  }, [operations, serviceById]);

  useEffect(() => {
    if (seedOperationId) {
      void selectOperation(seedOperationId);
      clearSeed();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seedOperationId]);

  const generated = useMemo(() => {
    const included = params.filter((p) => p.included);
    const properties: Record<string, unknown> = {};
    for (const p of included) {
      properties[p.name] =
        p.type === "integer" || p.type === "number"
          ? { type: p.type }
          : p.type === "boolean"
            ? { type: "boolean" }
            : { type: "string", minLength: 1, maxLength: 256 };
    }
    const inputSchema = {
      type: "object",
      additionalProperties: false,
      properties,
      required: included.filter((p) => p.required).map((p) => p.name),
    };
    const execution = {
      path: Object.fromEntries(
        params.filter((p) => p.source === "path" && p.included).map((p) => [p.name, p.name]),
      ),
      query: Object.fromEntries(
        params.filter((p) => p.source === "query" && p.included).map((p) => [p.name, p.name]),
      ),
      body: Object.fromEntries(
        params.filter((p) => p.source === "body" && p.included).map((p) => [p.name, p.name]),
      ),
    };
    return { inputSchema, execution };
  }, [params]);

  const createDraft = async () => {
    if (!detail) return;
    setBusy(true);
    setError(null);
    try {
      const latest = detail.versions[detail.versions.length - 1];
      const created = await api.post<{ id: string; status: string }>("/api/v1/capabilities", {
        name: toolName,
        display_name: displayName,
        description,
        operation_id: detail.id,
        input_schema: generated.inputSchema,
        output_schema: latest?.output_schema ?? {},
        allow_fields: allowFields,
        risk_level: risk,
        execution: generated.execution,
      });
      const full = await api.get<CapabilityDetailView>(`/api/v1/capabilities/${created.id}`);
      setCapability(full);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };

  const refreshCapability = async () => {
    if (!capability) return;
    try {
      setCapability(await api.get<CapabilityDetailView>(`/api/v1/capabilities/${capability.id}`));
    } catch (e) {
      setError(e);
    }
  };

  const runTest = async () => {
    if (!detail) return;
    setBusy(true);
    setError(null);
    setTestResult(null);
    try {
      const arguments_: Record<string, unknown> = {};
      for (const p of params.filter((x) => x.included)) {
        const raw = testArgs[p.name];
        if (raw === undefined || raw === "") continue;
        arguments_[p.name] =
          p.type === "integer" || p.type === "number" ? Number(raw) : raw;
      }
      setTestResult(
        await api.post<OperationTestResult>(`/api/v1/operations/${detail.id}/test`, {
          arguments: arguments_,
        }),
      );
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };

  const lifecycle = async (action: "review" | "verify" | "publish") => {
    if (!capability) return;
    setBusy(true);
    setError(null);
    try {
      if (action === "publish") {
        await api.post(`/api/v1/capabilities/${capability.id}/publish`, { version: publishVersion });
      } else {
        await api.post(`/api/v1/capabilities/${capability.id}/${action}`, { evidence });
      }
      await refreshCapability();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };

  const grantAgent = async () => {
    if (!capability || !agentId) return;
    setBusy(true);
    setError(null);
    try {
      await api.post("/api/v1/agent-grants", {
        agent_principal_id: agentId,
        capability_id: capability.id,
        scopes: grantScopes.split(",").map((s) => s.trim()).filter(Boolean),
      });
      setGrantDone(capability.name);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <h1>发布向导</h1>
      <p className="page-sub">
        把发现的 API 变成 Agent 可调用的工具：选择操作 → 配置参数 → 试调 →
        审核发布 → 授权。审核与发布必须由<b>非创建者</b>完成（demo 中：Alice
        创建后，切换 Carol 登录，在 Capabilities 页或重新进入本向导继续）。
      </p>
      <ErrorNote error={error} />
      <p className="muted">当前步骤：{capability ? (capability.status === "published" ? "5 发布完成" : "4 审核发布") : detail ? "3 试调" : "1 选择"}</p>

      {!detail && (
        <Card title={`1 · 选择候选操作 (${operations.length})`}>
          {operations.length === 0 ? (
            <Empty>还没有发现任何操作 —— 先去 Discovery 页导入或采集。</Empty>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>method</th>
                  <th>route template</th>
                  <th>service</th>
                  <th>status</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {operations.map((op) => (
                  <tr key={op.id}>
                    <td className="mono">{op.method}</td>
                    <td className="mono">{op.normalized_path}</td>
                    <td>{serviceById[op.service_id]?.slug ?? "?"}</td>
                    <td><StatusBadge status={op.status} /></td>
                    <td>
                      <Button variant="ghost" onClick={() => void selectOperation(op.id)}>
                        发布为工具
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      )}

      {detail && !capability && (
        <>
          <Card title={`2 · 配置工具 · ${detail.method} ${detail.normalized_path}`}>
            <div className="form-row">
              <Field label="工具名 (name)">
                <input value={toolName} onChange={(e) => setToolName(e.target.value)} />
              </Field>
              <Field label="显示名">
                <input value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
              </Field>
              <Field label="风险等级">
                <select value={risk} onChange={(e) => setRisk(e.target.value)}>
                  {["low", "medium", "high", "critical"].map((r) => (
                    <option key={r} value={r}>{r}</option>
                  ))}
                </select>
              </Field>
            </div>
            <Field label="描述">
              <input value={description} onChange={(e) => setDescription(e.target.value)} />
            </Field>
            <table>
              <thead>
                <tr>
                  <th>参数</th>
                  <th>来源</th>
                  <th>类型</th>
                  <th>暴露给 Agent</th>
                  <th>必填</th>
                </tr>
              </thead>
              <tbody>
                {params.map((p) => (
                  <tr key={`${p.source}:${p.name}`}>
                    <td className="mono">{p.name}</td>
                    <td>{p.source}</td>
                    <td>{p.type}</td>
                    <td>
                      <input
                        type="checkbox"
                        checked={p.included}
                        disabled={p.source === "path"}
                        onChange={(e) =>
                          setParams((prev) =>
                            prev.map((x) => (x === p ? { ...x, included: e.target.checked } : x)),
                          )
                        }
                      />
                    </td>
                    <td>
                      <input
                        type="checkbox"
                        checked={p.required}
                        disabled={p.source === "path"}
                        onChange={(e) =>
                          setParams((prev) =>
                            prev.map((x) => (x === p ? { ...x, required: e.target.checked } : x)),
                          )
                        }
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="muted" style={{ marginTop: 8 }}>
              响应字段（Agent 可见）：
              {allFields.map((f) => (
                <label key={f} style={{ marginRight: 10 }}>
                  <input
                    type="checkbox"
                    checked={allowFields.includes(f)}
                    onChange={(e) =>
                      setAllowFields((prev) =>
                        e.target.checked ? [...prev, f] : prev.filter((x) => x !== f),
                      )
                    }
                  />{" "}
                  <span className="mono">{f}</span>
                </label>
              ))}
            </p>
            <details style={{ marginTop: 8 }}>
              <summary className="muted">生成的输入 schema 与执行映射（预览）</summary>
              <pre className="mono" style={{ fontSize: 11 }}>
                {JSON.stringify(
                  { input_schema: generated.inputSchema, execution: generated.execution },
                  null,
                  2,
                )}
              </pre>
            </details>
            <div className="form-row" style={{ marginTop: 10 }}>
              <Button onClick={createDraft} disabled={busy || !toolName.includes(".")}>
                创建草稿
              </Button>
              <Button variant="ghost" onClick={() => { setDetail(null); setCapability(null); }}>
                返回选择
              </Button>
            </div>
          </Card>
        </>
      )}

      {detail && (
        <Card title="3 · 试调（经当前活动 Profile 真实执行一次）">
          {params.filter((p) => p.included).length === 0 ? (
            <Empty>没有暴露的参数。</Empty>
          ) : (
            <div className="form-row">
              {params.filter((p) => p.included).map((p) => (
                <Field key={p.name} label={`${p.name} (${p.source})`}>
                  <input
                    value={testArgs[p.name] ?? ""}
                    placeholder={p.required ? "必填" : "可选"}
                    onChange={(e) =>
                      setTestArgs((prev) => ({ ...prev, [p.name]: e.target.value }))
                    }
                  />
                </Field>
              ))}
            </div>
          )}
          <Button onClick={runTest} disabled={busy}>
            发起测试调用
          </Button>
          <p className="muted">需要该服务已激活 Profile（Profiles 页）；结果经响应守卫投影后展示。</p>
          {testResult && (
            <div style={{ marginTop: 8 }}>
              <p>
                <StatusBadge status={testResult.ok ? "succeeded" : "failed"} />{" "}
                executor {testResult.executor_status}
                {testResult.error_code ? ` · ${testResult.error_code}` : ""}
              </p>
              <pre className="mono" style={{ fontSize: 11, maxHeight: 240, overflow: "auto" }}>
                {JSON.stringify(testResult.result, null, 2)}
              </pre>
            </div>
          )}
        </Card>
      )}

      {capability && (
        <Card title={`4 · 审核与发布 · ${capability.name}`}>
          <p>
            状态：<StatusBadge status={capability.status} />
            {capability.versions.length > 0 && (
              <> · 版本 {capability.versions[capability.versions.length - 1].version}</>
            )}
          </p>
          <p className="muted">
            审核链：draft → review → verify → publish。每一步都必须由<b>另一位</b>
            负责人操作（创建者不能自审）。demo：切换 Carol 登录后回到本页。
          </p>
          <div className="form-row">
            {capability.status === "draft" && (
              <>
                <Field label="审核证据">
                  <input value={evidence} onChange={(e) => setEvidence(e.target.value)} />
                </Field>
                <Button onClick={() => void lifecycle("review")} disabled={busy}>
                  提交审核 (review)
                </Button>
              </>
            )}
            {capability.status === "reviewed" && (
              <Button onClick={() => void lifecycle("verify")} disabled={busy}>
                验证 (verify)
              </Button>
            )}
            {capability.status === "verified" && (
              <>
                <Field label="发布版本号">
                  <input value={publishVersion} onChange={(e) => setPublishVersion(e.target.value)} />
                </Field>
                <Button onClick={() => void lifecycle("publish")} disabled={busy}>
                  发布 (publish)
                </Button>
              </>
            )}
            {capability.status === "published" && (
              <p style={{ margin: 0 }}>
                ✅ 已发布为 Agent 可调用工具。在 Capabilities 页可见，MCP 名称见工具清单。
              </p>
            )}
          </div>
        </Card>
      )}

      {capability?.status === "published" && (
        <Card title="5 · 授权 Agent（可选）">
          <p className="muted" style={{ marginTop: 0 }}>
            选择要授权的 Agent 并授予权限。MCP 客户端的一键接入配置生成是 FEAT-05。
          </p>
          <div className="form-row">
            <Field label="agent">
              <select value={agentId} onChange={(e) => setAgentId(e.target.value)}>
                <option value="">select...</option>
                {agents.map((a) => (
                  <option key={a.id} value={a.id}>{a.display_name}</option>
                ))}
              </select>
            </Field>
            <Field label="scopes（逗号分隔，可空）">
              <input value={grantScopes} onChange={(e) => setGrantScopes(e.target.value)} />
            </Field>
            <Button onClick={grantAgent} disabled={busy || !agentId}>
              授权
            </Button>
          </div>
          {grantDone && (
            <p className="muted">已授权 {grantDone}。Agent 现在可以在其工具清单中看到并调用它。</p>
          )}
        </Card>
      )}
    </div>
  );
}

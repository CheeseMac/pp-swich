import { useEffect, useState } from "react";
import { api } from "../api";
import type { Connection, Service } from "../types";
import { Button, Card, Empty, ErrorNote, Field, StatusBadge } from "../ui";

export function ConnectionsPage() {
  const [connections, setConnections] = useState<Connection[]>([]);
  const [services, setServices] = useState<Service[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [serviceId, setServiceId] = useState("");
  const [dataset, setDataset] = useState("A");
  const [mode, setMode] = useState<"demo" | "keyring">("demo");
  const [secretName, setSecretName] = useState("");
  const [authScheme, setAuthScheme] = useState("api_key");

  const load = () => {
    api.get<Connection[]>("/api/v1/connections").then(setConnections).catch(setError);
    api.get<Service[]>("/api/v1/services").then(setServices).catch(setError);
  };
  useEffect(load, []);

  const create = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.post("/api/v1/connections", {
        service_id: serviceId,
        auth_scheme: mode === "keyring" ? authScheme : "api_key",
        demo_dataset: mode === "demo" ? dataset : undefined,
        secret_name: mode === "keyring" ? secretName.trim() : undefined,
      });
      setSecretName("");
      load();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };

  const activate = async (connection: Connection) => {
    setBusy(true);
    setError(null);
    try {
      await api.post(`/api/v1/connections/${connection.id}/activate`);
      load();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };

  const revoke = async (connection: Connection) => {
    setBusy(true);
    setError(null);
    try {
      await api.post(`/api/v1/connections/${connection.id}/revoke`);
      load();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <h1>Connections</h1>
      <p className="page-sub">
        Connections are authorization bindings. The console never shows or
        accepts raw secrets - real credentials are stored by you in the OS
        keyring (<span className="mono">ppswitch-secret put &lt;name&gt;</span>
        ) and referenced by name; demo connections use synthetic MOCKKEY
        values.
      </p>
      <ErrorNote error={error} />
      <Card title="Create connection">
        <div className="form-row">
          <Field label="service">
            <select value={serviceId} onChange={(e) => setServiceId(e.target.value)}>
              <option value="">select...</option>
              {services.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.slug}
                </option>
              ))}
            </select>
          </Field>
          <Field label="credential source">
            <select value={mode} onChange={(e) => setMode(e.target.value as "demo" | "keyring")}>
              <option value="demo">demo dataset (synthetic MOCKKEY)</option>
              <option value="keyring">OS keyring (real local credential)</option>
            </select>
          </Field>
          {mode === "demo" ? (
            <Field label="demo dataset (mock CRM account scope)">
              <select value={dataset} onChange={(e) => setDataset(e.target.value)}>
                <option value="A">A - Demo A dataset</option>
                <option value="B">B - Demo B (QA) dataset</option>
              </select>
            </Field>
          ) : (
            <>
              <Field label="keyring name (ppswitch-secret put 过的名字)">
                <input
                  value={secretName}
                  placeholder="例如 crm-key-a"
                  onChange={(e) => setSecretName(e.target.value)}
                />
              </Field>
              <Field label="auth scheme">
                <select value={authScheme} onChange={(e) => setAuthScheme(e.target.value)}>
                  <option value="api_key">api_key</option>
                  <option value="bearer">bearer</option>
                </select>
              </Field>
            </>
          )}
          <Button
            onClick={create}
            disabled={busy || !serviceId || (mode === "keyring" && !secretName.trim())}
          >
            Create
          </Button>
        </div>
        {mode === "keyring" && (
          <p className="muted">
            流程：先在运行 executor 的机器上执行{" "}
            <span className="mono">uv run ppswitch-secret put {secretName.trim() || "<name>"}</span>
            （值经隐藏输入，不经过控制台/网络）→ 创建此连接 → 点 Activate
            （由受信执行器验证可解析后才激活）。没有安全存储后端时明确报错，绝不退回明文。
          </p>
        )}
      </Card>
      <Card title={`Connections (${connections.length})`}>
        {connections.length === 0 ? (
          <Empty>No connections yet.</Empty>
        ) : (
          <table>
            <thead>
              <tr>
                <th>id</th>
                <th>service</th>
                <th>auth scheme</th>
                <th>status</th>
                <th>secret</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {connections.map((c) => (
                <tr key={c.id}>
                  <td className="mono">{c.id.slice(0, 8)}…</td>
                  <td className="mono">
                    {services.find((s) => s.id === c.service_id)?.slug ?? "?"}
                  </td>
                  <td>{c.auth_scheme}</td>
                  <td>
                    <StatusBadge status={c.status} />
                  </td>
                  <td>{c.secret_configured ? "configured (hidden)" : "not configured"}</td>
                  <td>
                    {c.status === "pending" && (
                      <Button variant="ghost" onClick={() => activate(c)} disabled={busy}>
                        Activate
                      </Button>
                    )}
                    {c.status === "active" && (
                      <Button variant="ghost" onClick={() => revoke(c)} disabled={busy}>
                        Revoke
                      </Button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}

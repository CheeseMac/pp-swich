import { useEffect, useState } from "react";
import { api } from "../api";
import type { Connection, Service } from "../types";
import { Button, Card, Empty, ErrorNote, Field, StatusBadge } from "../ui";

export function ConnectionsPage() {
  const [connections, setConnections] = useState<Connection[]>([]);
  const [services, setServices] = useState<Service[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [serviceId, setServiceId] = useState("");
  const [dataset, setDataset] = useState("A");

  const load = () => {
    api.get<Connection[]>("/api/v1/connections").then(setConnections).catch(setError);
    api.get<Service[]>("/api/v1/services").then(setServices).catch(setError);
  };
  useEffect(load, []);

  const create = async () => {
    setError(null);
    try {
      await api.post("/api/v1/connections", {
        service_id: serviceId,
        auth_scheme: "api_key",
        demo_dataset: dataset,
      });
      load();
    } catch (e) {
      setError(e);
    }
  };

  return (
    <div>
      <h1>Connections</h1>
      <p className="page-sub">
        Connections are authorization bindings. The console never shows or
        accepts raw secrets - credentials are provisioned through the trusted
        executor channel (demo: synthetic MOCKKEY values).
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
          <Field label="demo dataset (mock CRM account scope)">
            <select value={dataset} onChange={(e) => setDataset(e.target.value)}>
              <option value="A">A - Demo A dataset</option>
              <option value="B">B - Demo B (QA) dataset</option>
            </select>
          </Field>
          <Button onClick={create} disabled={!serviceId}>
            Create
          </Button>
        </div>
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
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}

import { useEffect, useState } from "react";
import { api } from "../api";
import type { Endpoint, Service } from "../types";
import { Button, Card, Empty, ErrorNote, Field, StatusBadge } from "../ui";

export function ServicesPage() {
  const [services, setServices] = useState<Service[]>([]);
  const [endpoints, setEndpoints] = useState<Endpoint[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [slug, setSlug] = useState("");
  const [name, setName] = useState("");
  const [env, setEnv] = useState("");
  const [baseUrl, setBaseUrl] = useState("http://127.0.0.1:8913");

  const load = () => {
    api.get<Service[]>("/api/v1/services").then(setServices).catch(setError);
    api.get<Endpoint[]>("/api/v1/endpoints").then(setEndpoints).catch(setError);
  };
  useEffect(load, []);

  const createService = async () => {
    setError(null);
    try {
      await api.post("/api/v1/services", { slug, name });
      setSlug("");
      setName("");
      load();
    } catch (e) {
      setError(e);
    }
  };

  const createEndpoint = async (serviceId: string) => {
    setError(null);
    try {
      const host = baseUrl.replace(/^https?:\/\//, "");
      await api.post(`/api/v1/services/${serviceId}/endpoints`, {
        environment: env || "demo",
        base_url: baseUrl,
        approved_hosts: [host],
        status: "approved",
      });
      setEnv("");
      load();
    } catch (e) {
      setError(e);
    }
  };

  return (
    <div>
      <h1>Services</h1>
      <p className="page-sub">
        Logical API providers. Endpoints are reviewed target addresses - the
        executor only ever calls approved hosts.
      </p>
      <ErrorNote error={error} />
      <Card title="Register service">
        <div className="form-row">
          <Field label="slug">
            <input value={slug} onChange={(e) => setSlug(e.target.value)} placeholder="crm" />
          </Field>
          <Field label="display name">
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="CRM" />
          </Field>
          <Button onClick={createService} disabled={!slug || !name}>
            Create service
          </Button>
        </div>
      </Card>
      <Card title="Add endpoint (approved host)">
        <div className="form-row">
          <Field label="service">
            <select id="ep-service">
              {services.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.slug}
                </option>
              ))}
            </select>
          </Field>
          <Field label="environment">
            <input value={env} onChange={(e) => setEnv(e.target.value)} placeholder="demo" />
          </Field>
          <Field label="base url">
            <input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} />
          </Field>
          <Button
            onClick={() => {
              const select = document.getElementById("ep-service") as HTMLSelectElement | null;
              if (select?.value) createEndpoint(select.value);
            }}
            disabled={services.length === 0}
          >
            Add endpoint
          </Button>
        </div>
      </Card>
      <Card title={`Services (${services.length})`}>
        {services.length === 0 ? (
          <Empty>No services yet.</Empty>
        ) : (
          <table>
            <thead>
              <tr>
                <th>slug</th>
                <th>name</th>
                <th>visibility</th>
                <th>endpoints</th>
              </tr>
            </thead>
            <tbody>
              {services.map((s) => (
                <tr key={s.id}>
                  <td className="mono">{s.slug}</td>
                  <td>{s.name}</td>
                  <td>{s.visibility}</td>
                  <td>
                    {endpoints
                      .filter((e) => e.service_id === s.id)
                      .map((e) => (
                        <div key={e.id}>
                          <StatusBadge status={e.status} />{" "}
                          <span className="mono">
                            {e.environment} · {e.base_url}
                          </span>
                        </div>
                      ))}
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

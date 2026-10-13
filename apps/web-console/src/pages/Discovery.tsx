import { useEffect, useState } from "react";
import { api } from "../api";
import type { ImportSummary, Operation, Service } from "../types";
import { Button, Card, Empty, ErrorNote, Field, StatusBadge } from "../ui";

export function DiscoveryPage() {
  const [services, setServices] = useState<Service[]>([]);
  const [operations, setOperations] = useState<Operation[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [summary, setSummary] = useState<ImportSummary | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [serviceId, setServiceId] = useState("");
  const [busy, setBusy] = useState(false);

  const load = () => {
    api.get<Service[]>("/api/v1/services").then(setServices).catch(setError);
    api.get<Operation[]>("/api/v1/operations").then(setOperations).catch(setError);
  };
  useEffect(load, []);

  const upload = async () => {
    if (!file || !serviceId) return;
    setBusy(true);
    setError(null);
    setSummary(null);
    try {
      const form = new FormData();
      form.append("file", file);
      form.append("source_type", file.name.endsWith(".json") && file.name.includes("openapi") ? "openapi" : "har");
      form.append("service_id", serviceId);
      const result = await api.upload<ImportSummary>("/api/v1/discoveries/imports", form);
      setSummary(result);
      load();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };

  const verify = async (operation: Operation) => {
    setError(null);
    try {
      await api.post(`/api/v1/operations/${operation.id}/verify`, {
        evidence: "verified from console",
      });
      load();
    } catch (e) {
      setError(e);
    }
  };

  return (
    <div>
      <h1>Discovery</h1>
      <p className="page-sub">
        Import locally-sanitized HAR / OpenAPI files. Raw credentials in the
        upload are rejected - nothing is stored.
      </p>
      <ErrorNote error={error} />
      <Card title="Import sanitized file">
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
          <Field label="file (har.json / openapi.json)">
            <input type="file" accept=".json" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          </Field>
          <Button onClick={upload} disabled={!file || !serviceId || busy}>
            {busy ? "Importing..." : "Import"}
          </Button>
        </div>
        {summary && (
          <p className="muted">
            Import {summary.job_id}: {summary.operations_created} operations created,{" "}
            {summary.operations_updated} updated, {summary.observations_created} observations.
          </p>
        )}
      </Card>
      <Card title={`Candidate operations (${operations.length})`}>
        {operations.length === 0 ? (
          <Empty>Nothing imported yet.</Empty>
        ) : (
          <table>
            <thead>
              <tr>
                <th>method</th>
                <th>route template</th>
                <th>status</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {operations.map((op) => (
                <tr key={op.id}>
                  <td className="mono">{op.method}</td>
                  <td className="mono">{op.normalized_path}</td>
                  <td>
                    <StatusBadge status={op.status} />
                  </td>
                  <td>
                    {op.status !== "verified" && op.status !== "rejected" && (
                      <Button variant="ghost" onClick={() => verify(op)}>
                        Verify
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

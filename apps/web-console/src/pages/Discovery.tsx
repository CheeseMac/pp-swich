import { useEffect, useState } from "react";
import { api } from "../api";
import type {
  CollectorObservation,
  CollectorPairing,
  ImportSummary,
  Operation,
  Service,
} from "../types";
import { Button, Card, Empty, ErrorNote, Field, StatusBadge } from "../ui";

export function DiscoveryPage({ publishToWizard }: { publishToWizard: (operationId: string) => void }) {
  const [services, setServices] = useState<Service[]>([]);
  const [operations, setOperations] = useState<Operation[]>([]);
  const [observations, setObservations] = useState<CollectorObservation[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [summary, setSummary] = useState<ImportSummary | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [serviceId, setServiceId] = useState("");
  const [busy, setBusy] = useState(false);

  // browser collector (FEAT-01)
  const [collectorServiceId, setCollectorServiceId] = useState("");
  const [pairings, setPairings] = useState<CollectorPairing[]>([]);
  const [freshCode, setFreshCode] = useState<CollectorPairing | null>(null);
  const [collectorBusy, setCollectorBusy] = useState(false);

  const load = () => {
    api.get<Service[]>("/api/v1/services").then(setServices).catch(setError);
    api.get<Operation[]>("/api/v1/operations").then(setOperations).catch(setError);
    api
      .get<CollectorObservation[]>("/api/v1/observations")
      .then(setObservations)
      .catch(setError);
    if (collectorServiceId) {
      api
        .get<CollectorPairing[]>(
          `/api/v1/services/${collectorServiceId}/collector/pairings`,
        )
        .then(setPairings)
        .catch(setError);
    }
  };
  useEffect(load, []); // eslint-disable-line react-hooks/exhaustive-deps

  const createPairing = async () => {
    if (!collectorServiceId) return;
    setCollectorBusy(true);
    setError(null);
    setFreshCode(null);
    try {
      const pairing = await api.post<CollectorPairing>(
        `/api/v1/services/${collectorServiceId}/collector/pairings`,
      );
      setFreshCode(pairing);
      load();
    } catch (e) {
      setError(e);
    } finally {
      setCollectorBusy(false);
    }
  };

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
        Import locally-sanitized HAR / OpenAPI files or collect APIs with the
        browser extension. Raw credentials in any upload are rejected -
        nothing is stored. Collector results are drafts: review and publish
        from here.
      </p>
      <ErrorNote error={error} />
      <Card title="Browser collector pairing">
        <p className="muted" style={{ marginTop: 0 }}>
          Install the Chrome extension (apps/browser-extension), pick the
          service to collect for, generate a one-time pairing code and paste
          it into the extension popup. The extension only uploads method,
          path templates, parameter names and field types - never values,
          cookies or auth headers.
        </p>
        <div className="form-row">
          <Field label="service">
            <select
              value={collectorServiceId}
              onChange={(e) => {
                setCollectorServiceId(e.target.value);
                setFreshCode(null);
              }}
            >
              <option value="">select...</option>
              {services.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.slug}
                </option>
              ))}
            </select>
          </Field>
          <Button onClick={createPairing} disabled={!collectorServiceId || collectorBusy}>
            {collectorBusy ? "Generating..." : "Generate pairing code"}
          </Button>
        </div>
        {freshCode?.pairing_code && (
          <p style={{ fontSize: 18, letterSpacing: 2, margin: "10px 0 0" }}>
            <span className="mono">{freshCode.pairing_code}</span>{" "}
            <span className="muted" style={{ fontSize: 12 }}>
              one-time code - shown only now, valid 10 minutes
            </span>
          </p>
        )}
        {pairings.length > 0 && (
          <table style={{ marginTop: 10 }}>
            <thead>
              <tr>
                <th>pairing</th>
                <th>status</th>
                <th>observations</th>
                <th>last submit</th>
              </tr>
            </thead>
            <tbody>
              {pairings.slice(0, 5).map((p) => (
                <tr key={p.id}>
                  <td className="mono">{p.id.slice(0, 8)}…</td>
                  <td>
                    <StatusBadge status={p.status} />
                  </td>
                  <td>{p.observation_count}</td>
                  <td className="muted">
                    {p.last_submitted_at
                      ? new Date(p.last_submitted_at).toLocaleString()
                      : "-"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
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
                  <td>
                    <Button onClick={() => publishToWizard(op.id)}>发布为工具</Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
      <Card title={`Observation sources (${observations.length})`}>
        <p className="muted" style={{ marginTop: 0 }}>
          Recent discovery evidence by source. <span className="mono">collector</span>{" "}
          rows arrived from the browser extension as zero-value skeletons.
        </p>
        {observations.length === 0 ? (
          <Empty>No observations recorded yet.</Empty>
        ) : (
          <table>
            <thead>
              <tr>
                <th>source</th>
                <th>method</th>
                <th>template</th>
                <th>samples</th>
                <th>status codes</th>
                <th>observed</th>
              </tr>
            </thead>
            <tbody>
              {observations.map((o) => (
                <tr key={o.id}>
                  <td>
                    <StatusBadge status={o.source} />
                  </td>
                  <td className="mono">{o.normalized_shape.method}</td>
                  <td className="mono">{o.normalized_shape.template}</td>
                  <td>{o.normalized_shape.sample_count}</td>
                  <td className="mono">{(o.normalized_shape.status_codes ?? []).join("/")}</td>
                  <td className="muted">
                    {o.created_at ? new Date(o.created_at).toLocaleString() : "-"}
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

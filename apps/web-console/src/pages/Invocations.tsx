import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import type { Capability, InvocationView } from "../types";
import { Button, Card, Empty, ErrorNote, Field, StatusBadge } from "../ui";

export function InvocationsPage() {
  const [capabilities, setCapabilities] = useState<Capability[]>([]);
  const [invocations, setInvocations] = useState<InvocationView[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [result, setResult] = useState<string>("");
  const [capabilityName, setCapabilityName] = useState("");
  const [customerArg, setCustomerArg] = useState("C-1024");

  const load = useCallback(() => {
    api.get<Capability[]>("/api/v1/capabilities").then(setCapabilities).catch(setError);
    api.get<InvocationView[]>("/api/v1/invocations").then(setInvocations).catch(setError);
  }, []);
  useEffect(load, [load]);

  const invoke = async () => {
    setError(null);
    setResult("");
    try {
      const response = await api.post<Record<string, unknown>>("/api/v1/invocations", {
        capability: capabilityName,
        arguments: { customer_id: customerArg },
      });
      setResult(JSON.stringify(response, null, 2));
      load();
    } catch (e) {
      setError(e);
    }
  };

  const published = capabilities.filter((c) => c.status === "published");

  return (
    <div>
      <h1>Invocations</h1>
      <p className="page-sub">
        Invoke published capabilities with business arguments only. Reserved
        arguments (Authorization, target_url, token...) are rejected with 422;
        results pass the capability's output allowlist.
      </p>
      <ErrorNote error={error} />
      <Card title="Invoke capability">
        <div className="form-row">
          <Field label="capability (published only)">
            <select value={capabilityName} onChange={(e) => setCapabilityName(e.target.value)}>
              <option value="">select...</option>
              {published.map((c) => (
                <option key={c.id} value={c.name}>
                  {c.name}
                </option>
              ))}
            </select>
          </Field>
          <Field label="customer_id">
            <input value={customerArg} onChange={(e) => setCustomerArg(e.target.value)} />
          </Field>
          <Button onClick={invoke} disabled={!capabilityName}>
            Invoke
          </Button>
        </div>
        {result && <div className="result-box">{result}</div>}
      </Card>
      <Card title={`Recent invocations (${invocations.length})`}>
        {invocations.length === 0 ? (
          <Empty>No invocations yet.</Empty>
        ) : (
          <table>
            <thead>
              <tr>
                <th>capability</th>
                <th>status</th>
                <th>profile</th>
                <th>created</th>
              </tr>
            </thead>
            <tbody>
              {invocations.map((i) => (
                <tr key={i.id}>
                  <td className="mono">{i.capability}</td>
                  <td>
                    <StatusBadge status={i.status} />
                    {i.error_code && <span className="muted"> {i.error_code}</span>}
                  </td>
                  <td>{i.profile_label}</td>
                  <td className="muted">{new Date(i.created_at).toLocaleTimeString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}

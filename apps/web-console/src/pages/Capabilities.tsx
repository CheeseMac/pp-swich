import { useEffect, useState } from "react";
import { api } from "../api";
import type { Capability, Operation, Service } from "../types";
import { Button, Card, Empty, ErrorNote, Field, StatusBadge } from "../ui";

export function CapabilitiesPage() {
  const [capabilities, setCapabilities] = useState<Capability[]>([]);
  const [services, setServices] = useState<Service[]>([]);
  const [operations, setOperations] = useState<Operation[]>([]);
  const [error, setError] = useState<unknown>(null);

  const [name, setName] = useState("crm.get_customer");
  const [displayName, setDisplayName] = useState("Get customer");
  const [operationId, setOperationId] = useState("");
  const [allowFields, setAllowFields] = useState("id, display_name");
  const [riskLevel, setRiskLevel] = useState("low");

  const load = () => {
    api.get<Capability[]>("/api/v1/capabilities").then(setCapabilities).catch(setError);
    api.get<Service[]>("/api/v1/services").then(setServices).catch(setError);
    api.get<Operation[]>("/api/v1/operations").then(setOperations).catch(setError);
  };
  useEffect(load, []);

  const create = async () => {
    setError(null);
    try {
      await api.post("/api/v1/capabilities", {
        name,
        display_name: displayName,
        description: "Created from the console demo",
        operation_id: operationId,
        input_schema: {
          type: "object",
          additionalProperties: false,
          required: ["customer_id"],
          properties: { customer_id: { type: "string", maxLength: 64 } },
        },
        output_schema: { type: "object" },
        allow_fields: allowFields.split(",").map((f) => f.trim()).filter(Boolean),
        risk_level: riskLevel,
        execution: { path: { id: "customer_id" } },
      });
      load();
    } catch (e) {
      setError(e);
    }
  };

  const step = async (capability: Capability, action: "review" | "verify" | "publish") => {
    setError(null);
    try {
      if (action === "publish") {
        await api.post(`/api/v1/capabilities/${capability.id}/publish`, { version: "1.0.0" });
      } else {
        await api.post(`/api/v1/capabilities/${capability.id}/${action}`, {
          evidence: `${action} from console`,
        });
      }
      load();
    } catch (e) {
      setError(e);
    }
  };

  return (
    <div>
      <h1>Capabilities</h1>
      <p className="page-sub">
        Capabilities are reviewed, versioned business tools. Draft → reviewed →
        verified → published; published versions are immutable.
      </p>
      <ErrorNote error={error} />
      <Card title="Create draft capability">
        <div className="form-row">
          <Field label="name (namespace.operation)">
            <input value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
          <Field label="display name">
            <input value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
          </Field>
          <Field label="operation">
            <select value={operationId} onChange={(e) => setOperationId(e.target.value)}>
              <option value="">select...</option>
              {operations.map((op) => (
                <option key={op.id} value={op.id}>
                  {op.method} {op.normalized_path}
                </option>
              ))}
            </select>
          </Field>
          <Field label="output allowlist (comma separated)">
            <input value={allowFields} onChange={(e) => setAllowFields(e.target.value)} />
          </Field>
          <Field label="risk level">
            <select value={riskLevel} onChange={(e) => setRiskLevel(e.target.value)}>
              <option>low</option>
              <option>medium</option>
              <option>high</option>
              <option>critical</option>
            </select>
          </Field>
          <Button onClick={create} disabled={!name || !operationId}>
            Create
          </Button>
        </div>
      </Card>
      <Card title={`Capabilities (${capabilities.length})`}>
        {capabilities.length === 0 ? (
          <Empty>No capabilities yet.</Empty>
        ) : (
          <table>
            <thead>
              <tr>
                <th>name</th>
                <th>status</th>
                <th>versions</th>
                <th>allow fields</th>
                <th>actions</th>
              </tr>
            </thead>
            <tbody>
              {capabilities.map((c) => (
                <tr key={c.id}>
                  <td className="mono">{c.name}</td>
                  <td>
                    <StatusBadge status={c.status} />
                  </td>
                  <td>
                    {c.versions.map((v) => (
                      <div key={v.id} className="muted">
                        {v.version} · {v.status} · risk {v.risk_level}
                      </div>
                    ))}
                  </td>
                  <td className="mono">{c.versions.at(-1)?.allow_fields.join(", ")}</td>
                  <td>
                    <div className="row">
                      {c.status === "draft" && (
                        <Button variant="ghost" onClick={() => step(c, "review")}>
                          Review
                        </Button>
                      )}
                      {c.status === "reviewed" && (
                        <Button variant="ghost" onClick={() => step(c, "verify")}>
                          Verify
                        </Button>
                      )}
                      {c.status === "verified" && (
                        <Button onClick={() => step(c, "publish")}>Publish 1.0.0</Button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
      <p className="muted">
        {services.length > 0 && "Workflow: create → review → verify → publish. Publishing an unreviewed capability is rejected with 409."}
      </p>
    </div>
  );
}

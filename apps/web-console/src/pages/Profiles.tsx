import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import type { ActiveBinding, Connection, Endpoint, Profile, Service } from "../types";
import { Button, Card, Empty, ErrorNote, Field, StatusBadge } from "../ui";

export function ProfilesPage() {
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [bindings, setBindings] = useState<ActiveBinding[]>([]);
  const [services, setServices] = useState<Service[]>([]);
  const [endpoints, setEndpoints] = useState<Endpoint[]>([]);
  const [connections, setConnections] = useState<Connection[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [notice, setNotice] = useState<string>("");

  const [serviceId, setServiceId] = useState("");
  const [endpointId, setEndpointId] = useState("");
  const [connectionId, setConnectionId] = useState("");
  const [profileName, setProfileName] = useState("");

  const load = useCallback(() => {
    api.get<Profile[]>("/api/v1/profiles").then(setProfiles).catch(setError);
    api.get<ActiveBinding[]>("/api/v1/active-bindings").then(setBindings).catch(setError);
    api.get<Service[]>("/api/v1/services").then(setServices).catch(setError);
    api.get<Endpoint[]>("/api/v1/endpoints").then(setEndpoints).catch(setError);
    api.get<Connection[]>("/api/v1/connections").then(setConnections).catch(setError);
  }, []);
  useEffect(load, [load]);

  const createProfile = async () => {
    setError(null);
    setNotice("");
    try {
      await api.post("/api/v1/profiles", {
        service_id: serviceId,
        endpoint_id: endpointId,
        connection_id: connectionId,
        name: profileName,
      });
      setProfileName("");
      load();
    } catch (e) {
      setError(e);
    }
  };

  const activate = async (service: string, profileId: string, expectedVersion: number) => {
    setError(null);
    setNotice("");
    try {
      const result = await api.put<ActiveBinding>(
        `/api/v1/active-bindings/${service}/personal`,
        { profile_id: profileId, expected_version: expectedVersion },
      );
      setNotice(`Switched to ${result.display_label} (binding v${result.version})`);
      load();
    } catch (e) {
      setError(e);
    }
  };

  const bindingFor = (serviceId: string) =>
    bindings.find((b) => b.service_id === serviceId && b.context_key === "personal");

  const serviceProfiles = (id: string) => profiles.filter((p) => p.service_id === id);

  return (
    <div>
      <h1>Profiles & one-click switch</h1>
      <p className="page-sub">
        A Profile = endpoint + connection of the same service. Switching
        changes the binding for <i>future</i> calls only; in-flight
        invocations keep their frozen snapshot.
      </p>
      <ErrorNote error={error} />
      {notice && <div className="error-note" style={{ color: "var(--ok)", borderColor: "var(--ok)" }}>{notice}</div>}

      <Card title="Active bindings">
        {services.length === 0 ? (
          <Empty>No services yet.</Empty>
        ) : (
          services.map((s) => {
            const binding = bindingFor(s.id);
            return (
              <div key={s.id} className="binding-card">
                <div>
                  <div className="binding-label">
                    <span className="mono">{s.slug}</span>{" "}
                    <span className="muted">· personal</span>
                  </div>
                  <div className="binding-sub">
                    {binding
                      ? `active: ${binding.display_label} (v${binding.version})`
                      : "no active profile"}
                  </div>
                </div>
                <div className="row">
                  <select
                    id={`switch-${s.id}`}
                    defaultValue={binding?.active_profile_id ?? ""}
                    key={binding?.version ?? 0}
                  >
                    {serviceProfiles(s.id).map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.name}
                      </option>
                    ))}
                  </select>
                  <Button
                    disabled={serviceProfiles(s.id).length === 0}
                    onClick={() => {
                      const select = document.getElementById(`switch-${s.id}`) as HTMLSelectElement | null;
                      if (select?.value) {
                        activate(s.id, select.value, binding?.version ?? 0);
                      }
                    }}
                  >
                    Switch
                  </Button>
                </div>
              </div>
            );
          })
        )}
        <p className="muted" style={{ fontSize: 12 }}>
          Conflict (409) means another window switched first - the list above
          reloads with the new version; retry.
        </p>
      </Card>

      <Card title="Create profile">
        <div className="form-row">
          <Field label="service">
            <select
              value={serviceId}
              onChange={(e) => {
                setServiceId(e.target.value);
                setEndpointId("");
                setConnectionId("");
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
          <Field label="endpoint">
            <select value={endpointId} onChange={(e) => setEndpointId(e.target.value)}>
              <option value="">select...</option>
              {endpoints
                .filter((e) => e.service_id === serviceId)
                .map((e) => (
                  <option key={e.id} value={e.id}>
                    {e.environment} · {e.base_url} ({e.status})
                  </option>
                ))}
            </select>
          </Field>
          <Field label="connection">
            <select value={connectionId} onChange={(e) => setConnectionId(e.target.value)}>
              <option value="">select...</option>
              {connections
                .filter((c) => c.service_id === serviceId)
                .map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.id.slice(0, 8)}… ({c.status})
                  </option>
                ))}
            </select>
          </Field>
          <Field label="profile name">
            <input
              value={profileName}
              onChange={(e) => setProfileName(e.target.value)}
              placeholder="CRM Demo A"
            />
          </Field>
          <Button onClick={createProfile} disabled={!serviceId || !endpointId || !connectionId || !profileName}>
            Create profile
          </Button>
        </div>
      </Card>

      <Card title={`Your profiles (${profiles.length})`}>
        {profiles.length === 0 ? (
          <Empty>No profiles yet.</Empty>
        ) : (
          <table>
            <thead>
              <tr>
                <th>name</th>
                <th>service</th>
                <th>status</th>
              </tr>
            </thead>
            <tbody>
              {profiles.map((p) => (
                <tr key={p.id}>
                  <td>{p.name}</td>
                  <td className="mono">
                    {services.find((s) => s.id === p.service_id)?.slug ?? "?"}
                  </td>
                  <td>
                    <StatusBadge status={p.status} />
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

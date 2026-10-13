import { useEffect, useState } from "react";
import { api } from "../api";
import type { AuditEvent } from "../types";
import { Card, Empty, ErrorNote } from "../ui";

export function AuditPage() {
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    api.get<AuditEvent[]>("/api/v1/audit-events").then(setEvents).catch(setError);
  }, []);

  return (
    <div>
      <h1>Security & audit</h1>
      <p className="page-sub">
        Redacted audit trail: actors, decisions and non-secret metadata only.
        Raw arguments, responses and credentials are never recorded.
      </p>
      <ErrorNote error={error} />
      <Card title={`Audit events (${events.length})`}>
        {events.length === 0 ? (
          <Empty>No events yet.</Empty>
        ) : (
          <table>
            <thead>
              <tr>
                <th>action</th>
                <th>decision</th>
                <th>invocation</th>
                <th>metadata</th>
                <th>time</th>
              </tr>
            </thead>
            <tbody>
              {events.map((e) => (
                <tr key={e.id}>
                  <td className="mono">{e.action}</td>
                  <td>{e.decision}</td>
                  <td className="mono">{e.invocation_id?.slice(0, 8) ?? "-"}</td>
                  <td className="mono muted">
                    {JSON.stringify(e.metadata).slice(0, 120)}
                  </td>
                  <td className="muted">{new Date(e.created_at).toLocaleTimeString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
      <Card title="Security invariants (what this demo enforces)">
        <ul className="muted" style={{ lineHeight: 1.9, margin: 0, paddingLeft: 18 }}>
          <li>Unsanitized HAR uploads (raw Authorization/Cookie values) are rejected 422.</li>
          <li>Draft / unpublished capabilities are invisible at invoke time (404).</li>
          <li>Reserved arguments (Authorization, target_url, token, user_id...) → 422.</li>
          <li>Response guard: only allowlisted fields return; Set-Cookie and access_token never leak.</li>
          <li>Profile switching is owner-checked, version-guarded (409 on races).</li>
          <li>High-risk capabilities require a second-principal approval (APPROVAL_REQUIRED).</li>
          <li>Secret provider outages fail closed (503), never fall back to plaintext.</li>
          <li>Cross-workspace objects do not exist for other tenants (404, no info leak).</li>
          <li>Profiles can only bind connections owned by the creating principal (no cross-user assembly).</li>
          <li>Management APIs are human-only; the capability creator cannot review/publish their own draft.</li>
          <li>Idempotency keys replay only for the same principal and arguments (409 otherwise).</li>
          <li>High-risk approvals bind requester + version + profile + arguments and expire in 15 minutes.</li>
          <li>Executor enforces an approved-host whitelist; metadata ranges can never be approved.</li>
        </ul>
      </Card>
    </div>
  );
}

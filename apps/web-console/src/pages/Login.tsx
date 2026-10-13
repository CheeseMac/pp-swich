import { useEffect, useState } from "react";
import { api, setToken } from "../api";
import type { Principal } from "../types";
import { ErrorNote } from "../ui";

export function Login({ onLogin }: { onLogin: () => void }) {
  const [principals, setPrincipals] = useState<Principal[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [manualToken, setManualToken] = useState("");

  useEffect(() => {
    api
      .get<Principal[]>("/api/v1/dev/principals")
      .then(setPrincipals)
      .catch(setError);
  }, []);

  const pick = (principal: Principal) => {
    if (!principal.dev_token) return;
    setToken(principal.dev_token);
    onLogin();
  };

  return (
    <div className="login-wrap">
      <div className="card login-card">
        <h2 style={{ fontSize: 20 }}>pp switch console</h2>
        <p className="muted" style={{ fontSize: 12 }}>
          Demo login: pick a synthetic dev principal. Tokens come from
          <code> data/dev-tokens.json</code> written by <code>scripts/seed-demo.py</code>.
          Production deployments use OIDC instead.
        </p>
        <ErrorNote error={error} />
        {principals.length === 0 && !error && (
          <p className="empty">No principals yet - run scripts/seed-demo.py first.</p>
        )}
        {principals.map((p) => (
          <div key={p.id} className="principal-row" onClick={() => pick(p)}>
            <div>
              <b>{p.display_name}</b>
              <div className="kind">
                {p.kind} · {p.token_hint || "token seeded"}
              </div>
            </div>
            {p.dev_token ? (
              <button className="btn btn-primary">Sign in</button>
            ) : (
              <span className="muted">token not seeded</span>
            )}
          </div>
        ))}
        <div className="form-row" style={{ marginTop: 16 }}>
          <label className="field" style={{ flex: 1 }}>
            <span>or paste a dev token</span>
            <input
              value={manualToken}
              onChange={(e) => setManualToken(e.target.value)}
              placeholder="dev-token-..."
            />
          </label>
          <button
            className="btn btn-ghost"
            disabled={!manualToken}
            onClick={() => {
              setToken(manualToken.trim());
              onLogin();
            }}
          >
            Use token
          </button>
        </div>
      </div>
    </div>
  );
}

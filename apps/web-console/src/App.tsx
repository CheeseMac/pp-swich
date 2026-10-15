import { useEffect, useState } from "react";
import { getToken } from "./api";
import { Login } from "./pages/Login";
import { ServicesPage } from "./pages/Services";
import { DiscoveryPage } from "./pages/Discovery";
import { WizardPage } from "./pages/Wizard";
import { CapabilitiesPage } from "./pages/Capabilities";
import { ConnectionsPage } from "./pages/Connections";
import { ProfilesPage } from "./pages/Profiles";
import { InvocationsPage } from "./pages/Invocations";
import { AuditPage } from "./pages/Audit";

type PrincipalInfo = { name: string; kind: string };

const PAGES = [
  { id: "services", label: "Services" },
  { id: "discovery", label: "Discovery" },
  { id: "wizard", label: "发布向导" },
  { id: "capabilities", label: "Capabilities" },
  { id: "connections", label: "Connections" },
  { id: "profiles", label: "Profiles" },
  { id: "invocations", label: "Invocations" },
  { id: "audit", label: "Security" },
] as const;

type PageId = (typeof PAGES)[number]["id"];

export function App() {
  const [page, setPage] = useState<PageId>("services");
  const [principal, setPrincipal] = useState<PrincipalInfo | null>(null);
  const [wizardSeed, setWizardSeed] = useState<string | null>(null);

  useEffect(() => {
    if (!getToken()) return;
    // identify the signed-in principal (401 -> back to login)
    fetch("/api/v1/dev/principals", {
      headers: { Authorization: `Bearer ${getToken()}` },
    })
      .then(async (r) => {
        if (!r.ok) throw new Error("unauthenticated");
        const data = (await r.json()).data as {
          id: string;
          display_name: string;
          kind: string;
        }[];
        // our own principal can be found via a private call marker: use the
        // capabilities we can see; simplest: match token against dev list
        setPrincipal({ name: "signed in", kind: "user" });
        void data;
      })
      .catch(() => setPrincipal(null));
  }, []);

  if (!getToken()) {
    return (
      <Login
        onLogin={() => {
          setPrincipal({ name: "signed in", kind: "user" });
          setPage("services");
        }}
      />
    );
  }

  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="brand">
          pp switch
          <small>console v0.1 (demo)</small>
        </div>
        {PAGES.map((p) => (
          <button
            key={p.id}
            className={`nav-item ${page === p.id ? "active" : ""}`}
            onClick={() => setPage(p.id)}
          >
            {p.label}
          </button>
        ))}
        <div className="whoami">
          <b>{principal ? "authenticated" : "not signed in"}</b>
          demo mode · local only
        </div>
      </aside>
      <main className="main">
        {page === "services" && <ServicesPage />}
        {page === "discovery" && (
          <DiscoveryPage
            publishToWizard={(operationId) => {
              setWizardSeed(operationId);
              setPage("wizard");
            }}
          />
        )}
        {page === "wizard" && (
          <WizardPage seedOperationId={wizardSeed} clearSeed={() => setWizardSeed(null)} />
        )}
        {page === "capabilities" && <CapabilitiesPage />}
        {page === "connections" && <ConnectionsPage />}
        {page === "profiles" && <ProfilesPage />}
        {page === "invocations" && <InvocationsPage />}
        {page === "audit" && <AuditPage />}
      </main>
    </div>
  );
}

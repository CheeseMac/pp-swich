import type { ReactNode } from "react";

export function Card({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="card">
      <h2>{title}</h2>
      {children}
    </section>
  );
}

export function Button({
  children,
  onClick,
  variant = "primary",
  disabled,
}: {
  children: ReactNode;
  onClick?: () => void;
  variant?: "primary" | "ghost" | "danger";
  disabled?: boolean;
}) {
  return (
    <button className={`btn btn-${variant}`} onClick={onClick} disabled={disabled}>
      {children}
    </button>
  );
}

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
    </label>
  );
}

const STATUS_TONE: Record<string, string> = {
  published: "ok",
  verified: "ok",
  approved: "ok",
  active: "ok",
  succeeded: "ok",
  enabled: "ok",
  observed: "info",
  inferred: "info",
  reviewed: "info",
  draft: "muted",
  pending: "muted",
  needs_review: "warn",
  approval_required: "warn",
  running: "info",
  failed: "bad",
  rejected: "bad",
  disabled: "bad",
  deprecated: "bad",
};

export function StatusBadge({ status }: { status: string }) {
  return <span className={`badge tone-${STATUS_TONE[status] ?? "muted"}`}>{status}</span>;
}

export function ErrorNote({ error }: { error: unknown }) {
  if (!error) return null;
  const message = error instanceof Error ? error.message : String(error);
  return <div className="error-note">{message}</div>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="empty">{children}</p>;
}

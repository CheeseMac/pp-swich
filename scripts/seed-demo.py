#!/usr/bin/env python
"""Seed the pp swich demo workspace (idempotent).

Creates directly in the database (trusted operator tool):
- a ``demo`` workspace with dev principals (alice / carol / agent-helper),
  writing their synthetic dev tokens to ``data/dev-tokens.json`` (gitignored
  runtime file, never committed) so the console login can list them;

then via the public API as alice:
- the ``crm`` service + approved endpoint pointing at the local mock CRM;
- two connections (Demo A / Demo B datasets) provisioned through the
  executor's trusted channel (synthetic MOCKKEY values only);
- two profiles and the initial active binding.

Requires: control-api (8911) and executor (8912) already running.

Usage:
    uv run python scripts/seed-demo.py [--control-url http://127.0.0.1:8911]
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
import uuid
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]

PRINCIPAL_SPECS = [
    ("user", "Alice (demo)", "alice"),
    ("user", "Carol (demo)", "carol"),
    ("agent", "agent-helper (demo)", "agent-helper"),
]


def die(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def seed_principals() -> str:
    """Create workspace + principals directly; returns alice's dev token."""
    from ppswich_control.auth import hash_token
    from ppswich_db.models import PrincipalRow, WorkspaceRow, utcnow
    from ppswich_db.session import create_engine_for_url, create_session_factory

    db_url = _control_db_url()
    tokens_path = REPO_ROOT / "data" / "dev-tokens.json"
    tokens_path.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[str, str] = {}
    if tokens_path.exists():
        try:
            existing = json.loads(tokens_path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            existing = {}

    engine = create_engine_for_url(db_url)
    factory = create_session_factory(engine)
    with factory() as session:
        workspace = (
            session.query(WorkspaceRow).filter_by(slug="demo").one_or_none()
        )
        if workspace is None:
            workspace = WorkspaceRow(
                id=uuid.uuid4(), slug="demo", name="Demo Workspace", kind="personal",
                created_at=utcnow(),
            )
            session.add(workspace)
            session.flush()
        tokens: dict[str, str] = {}
        principal_ids: dict[str, uuid.UUID] = {}
        for kind, name, slug in PRINCIPAL_SPECS:
            token = existing.get(slug) or f"dev-token-{slug}-{secrets.token_hex(4)}"
            tokens[slug] = token
            principal = (
                session.query(PrincipalRow)
                .filter_by(token_hash=hash_token(token))
                .one_or_none()
            )
            if principal is None:
                principal = PrincipalRow(
                    id=uuid.uuid4(),
                    workspace_id=workspace.id,
                    kind=kind,
                    display_name=name,
                    token_hash=hash_token(token),
                    token_hint=token[:16] + "...",
                    created_at=utcnow(),
                )
                session.add(principal)
                session.flush()
            principal_ids[slug] = principal.id
        session.commit()
    engine.dispose()

    # map principal ids -> tokens for the console login (demo only)
    token_catalog = {
        str(principal_ids[slug]): tokens[slug] for slug in tokens
    }
    tokens_path.write_text(json.dumps(token_catalog, indent=2), encoding="utf-8")
    return tokens["alice"]


def _control_db_url() -> str:
    import os

    return os.environ.get("PP_SWICH_DB_URL", "sqlite:///./data/pp-swich.db")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--control-url", default="http://127.0.0.1:8911")
    args = parser.parse_args(argv)

    alice_token = seed_principals()
    headers = {"Authorization": f"Bearer {alice_token}"}
    client = httpx.Client(base_url=args.control_url, timeout=30.0)

    if client.get("/healthz").status_code != 200:
        die(f"control-api not reachable at {args.control_url} (run scripts/dev-up.py first)")

    # ---- service + endpoint -------------------------------------------------
    services = client.get("/api/v1/services", headers=headers).json()["data"]
    crm = next((s for s in services if s["slug"] == "crm"), None)
    if crm is None:
        created = client.post(
            "/api/v1/services", json={"slug": "crm", "name": "CRM (mock)"}, headers=headers
        )
        if created.status_code != 201:
            die(f"create service failed: {created.text}")
        crm = created.json()["data"]
    service_id = crm["id"]

    crm_port = 8913
    endpoints = client.get(f"/api/v1/endpoints?service_id={service_id}",
        headers=headers).json()["data"]
    if not any(e["base_url"] == f"http://127.0.0.1:{crm_port}" for e in endpoints):
        created = client.post(
            f"/api/v1/services/{service_id}/endpoints",
            json={
                "environment": "demo",
                "base_url": f"http://127.0.0.1:{crm_port}",
                "approved_hosts": [f"127.0.0.1:{crm_port}"],
                "status": "approved",
            },
            headers=headers,
        )
        if created.status_code != 201:
            die(f"create endpoint failed: {created.text}")

    # ---- connections + profiles ---------------------------------------------
    profiles = client.get(f"/api/v1/profiles?service_id={service_id}",
        headers=headers).json()["data"]
    endpoint_id = next(
        e["id"] for e in client.get(f"/api/v1/endpoints?service_id={service_id}",
            headers=headers).json()["data"]
        if e["base_url"] == f"http://127.0.0.1:{crm_port}"
    )
    for dataset, profile_name in (("A", "CRM Demo A"), ("B", "CRM Demo B")):
        if any(p["name"] == profile_name for p in profiles):
            continue
        created = client.post(
            "/api/v1/connections",
            json={"service_id": service_id, "auth_scheme": "api_key",
                  "demo_dataset": dataset},
            headers=headers,
        )
        if created.status_code != 201:
            die(f"create connection {dataset} failed: {created.text}")
        connection_id = created.json()["data"]["id"]
        created = client.post(
            "/api/v1/profiles",
            json={"service_id": service_id, "endpoint_id": endpoint_id,
                  "connection_id": connection_id, "name": profile_name},
            headers=headers,
        )
        if created.status_code != 201:
            die(f"create profile {profile_name} failed: {created.text}")

    profiles = client.get(f"/api/v1/profiles?service_id={service_id}",
        headers=headers).json()["data"]
    demo_a = next((p for p in profiles if p["name"] == "CRM Demo A"), None)
    bindings = client.get("/api/v1/active-bindings", headers=headers).json()["data"]
    if demo_a is not None and not any(b["service_id"] == service_id for b in bindings):
        client.put(
            f"/api/v1/active-bindings/{service_id}/personal",
            json={"profile_id": demo_a["id"], "expected_version": 0},
            headers=headers,
        )

    print()
    print("Demo workspace seeded. Synthetic dev tokens (localhost demo only):")
    for kind, name, _slug in PRINCIPAL_SPECS:
        print(f"  {name:<24} [{kind}]")
    print()
    print("Tokens:   data/dev-tokens.json (gitignored runtime file)")
    print("Console:  http://127.0.0.1:5173  (pick a principal on the login page)")
    print("Docs API: http://127.0.0.1:8911/docs")
    print("Next:     import tests/fixtures/synthetic-har/crm.har.json from the")
    print("          Discovery page, then review -> verify -> publish the capability.")


if __name__ == "__main__":
    main()

"""Fresh-install verification smoke (M2 Alpha leg 1, docs/40).

Proves the "a real user clones the repo and it works" path on a CLEAN
environment: fresh database, programmatic migrations, real uvicorn
servers on ephemeral ports, and the full first-user journey over the
public API only -

    service -> approved endpoint -> HAR import -> operation verify ->
    capability draft -> review/verify/publish -> two demo connections ->
    profiles -> one-click switch -> secretless SDK invocation ->
    response-guard assertion (leaky CRM fields never survive)

Run after `uv sync --all-packages`:

    uv run python scripts/verify-install.py

Exit code 0 = install verified. No network access beyond loopback; no
real credentials (mock CRM datasets carry synthetic MOCKKEY values).
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
HAR_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "synthetic-har" / "crm.har.json"

ALICE_TOKEN = "dev-token-installcheck-alice"  # noqa: S105 - synthetic dev token
CAROL_TOKEN = "dev-token-installcheck-carol"  # noqa: S105 - synthetic dev token

_steps: list[tuple[str, float]] = []


def _step(name: str) -> None:
    _steps.append((name, time.time()))
    print(f"  [ok] {name}")


def _fail(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def main() -> None:
    started = time.time()
    print("pp-switch install verification (fresh database, loopback only)")

    # ---- 1) fresh database + programmatic migrations -----------------------
    tmp = tempfile.mkdtemp(prefix="ppswitch-install-")
    db_url = f"sqlite:///{tmp}/install-check.db"

    from ppswitch_db.migrate import run_upgrade

    run_upgrade(db_url)
    _step("migrations apply to a clean database")

    from ppswitch_db.session import create_engine_for_url, create_session_factory

    engine = create_engine_for_url(db_url)
    factory = create_session_factory(engine)

    from ppswitch_control.auth import hash_token
    from ppswitch_db.models import PrincipalRow, WorkspaceRow

    with factory() as session:
        workspace = WorkspaceRow(id=uuid.uuid4(), slug="install-check", name="Install Check")
        session.add(workspace)
        session.flush()
        for kind, name, token, role in (
            ("user", "Alice (install check)", ALICE_TOKEN, "admin"),
            ("user", "Carol (install check)", CAROL_TOKEN, "reviewer"),
        ):
            session.add(
                PrincipalRow(
                    id=uuid.uuid4(), workspace_id=workspace.id, kind=kind,
                    display_name=name, token_hash=hash_token(token),
                    token_hint=token[:16] + "...", role=role,
                )
            )
        session.commit()
    engine.dispose()
    _step("workspace + dev principals seeded")

    # ---- 2) real servers on ephemeral ports --------------------------------
    import uvicorn
    from ppswitch_control.app import create_app as create_control_app
    from ppswitch_control.settings import ControlSettings
    from ppswitch_executor.main import ExecutorSettings
    from ppswitch_executor.main import create_app as create_executor_app
    from ppswitch_mockcrm import app as mockcrm_app

    def start(app, name: str):
        import socket
        import threading

        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = int(sock.getsockname()[1])
        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        server = uvicorn.Server(config)

        thread = threading.Thread(target=server.run, name=name, daemon=True)
        thread.start()
        deadline = time.time() + 20
        while time.time() < deadline:
            if server.started:
                return server
            time.sleep(0.05)
        _fail(f"{name} failed to start")

    crm_server = start(mockcrm_app, "install-crm")
    crm_host = f"127.0.0.1:{crm_server.config.port}"
    executor_server = start(
        create_executor_app(ExecutorSettings(demo_mode=True)), "install-executor"
    )
    executor_url = f"http://127.0.0.1:{executor_server.config.port}"
    control_server = start(
        create_control_app(
            ControlSettings(
                db_url=db_url,
                executor_url=executor_url,
                auto_migrate=False,
                demo_mode=True,
            )
        ),
        "install-control",
    )
    control_url = f"http://127.0.0.1:{control_server.config.port}"
    _step(f"servers up: mock-crm :{crm_server.config.port}, "
          f"executor :{executor_server.config.port}, control :{control_server.config.port}")

    import httpx

    client = httpx.Client(base_url=control_url, timeout=30.0, trust_env=False)
    headers = _auth(ALICE_TOKEN)

    # ---- 3) the first-user journey, public API only ------------------------
    service = client.post("/api/v1/services", json={"slug": "crm", "name": "CRM"},
                          headers=headers)
    if service.status_code != 201:
        _fail(f"create service: {service.text}")
    service_id = service.json()["data"]["id"]

    endpoint = client.post(
        f"/api/v1/services/{service_id}/endpoints",
        json={"environment": "demo", "base_url": f"http://{crm_host}",
              "approved_hosts": [crm_host], "status": "approved"},
        headers=headers,
    )
    if endpoint.status_code != 201:
        _fail(f"create endpoint: {endpoint.text}")
    endpoint_id = endpoint.json()["data"]["id"]
    _step("service + approved endpoint")

    with open(HAR_FIXTURE, "rb") as handle:
        imported = client.post(
            "/api/v1/discoveries/imports",
            files={"file": ("crm.har.json", handle, "application/json")},
            data={"source_type": "har", "service_id": service_id},
            headers=headers,
        )
    if imported.status_code != 201:
        _fail(f"har import: {imported.text}")
    operations = client.get(f"/api/v1/operations?service_id={service_id}",
                            headers=headers).json()["data"]
    get_operation = next(o for o in operations if o["method"] == "GET")
    client.post(f"/api/v1/operations/{get_operation['id']}/verify",
                json={"evidence": "install check"}, headers=headers)
    _step(f"HAR discovery imported ({imported.json()['data']['candidates']} candidates)")

    capability = client.post(
        "/api/v1/capabilities",
        json={
            "name": "crm.get_customer",
            "display_name": "Get customer",
            "description": "Install-check capability",
            "operation_id": get_operation["id"],
            "input_schema": {
                "type": "object", "additionalProperties": False,
                "required": ["customer_id"],
                "properties": {"customer_id": {"type": "string", "maxLength": 64}},
            },
            "output_schema": {"type": "object"},
            "allow_fields": ["id", "display_name"],
            "risk_level": "low",
            "execution": {"path": {"id": "customer_id"}},
        },
        headers=headers,
    )
    if capability.status_code != 201:
        _fail(f"create capability: {capability.text}")
    capability_id = capability.json()["data"]["id"]
    for action in ("review", "verify"):
        stepped = client.post(f"/api/v1/capabilities/{capability_id}/{action}",
                              json={"evidence": f"install {action}"},
                              headers=_auth(CAROL_TOKEN))
        if stepped.status_code != 200:
            _fail(f"{action}: {stepped.text}")
    published = client.post(f"/api/v1/capabilities/{capability_id}/publish",
                            json={"version": "1.0.0"}, headers=_auth(CAROL_TOKEN))
    if published.status_code != 200:
        _fail(f"publish: {published.text}")
    _step("capability reviewed + published (creator != reviewer)")

    profiles = {}
    for dataset, profile_name in (("A", "Demo A"), ("B", "Demo B")):
        conn = client.post("/api/v1/connections",
                           json={"service_id": service_id, "auth_scheme": "api_key",
                                 "demo_dataset": dataset},
                           headers=headers)
        if conn.status_code != 201:
            _fail(f"connection {dataset}: {conn.text}")
        profile = client.post("/api/v1/profiles",
                              json={"service_id": service_id, "endpoint_id": endpoint_id,
                                    "connection_id": conn.json()["data"]["id"],
                                    "name": profile_name},
                              headers=headers)
        if profile.status_code != 201:
            _fail(f"profile {dataset}: {profile.text}")
        profiles[dataset] = profile.json()["data"]["id"]
    client.put(f"/api/v1/active-bindings/{service_id}/personal",
               json={"profile_id": profiles["A"], "expected_version": 0},
               headers=headers)
    _step("two demo connections + profiles + active binding")

    # ---- 4) secretless SDK invocation + one-click switch -------------------
    from ppswitch import PPSwitchClient

    sdk = PPSwitchClient.from_http(control_url, ALICE_TOKEN, trust_env=False)
    first = sdk.invoke("crm.get_customer", {"customer_id": "C-1024"})
    if first.status != "succeeded" or first.result.get("display_name") != "Acme Industries":
        _fail(f"dataset A invocation: {json.dumps(first.result)}")
    if set(first.result) != {"id", "display_name"}:
        _fail(f"response guard leaked fields: {sorted(first.result)}")

    client.put(f"/api/v1/active-bindings/{service_id}/personal",
               json={"profile_id": profiles["B"], "expected_version": 1},
               headers=headers)
    second = sdk.invoke("crm.get_customer", {"customer_id": "C-1024"})
    if second.status != "succeeded" or "QA copy" not in str(second.result.get("display_name")):
        _fail(f"dataset B invocation: {json.dumps(second.result)}")
    _step("SDK invocation via A, one-click switch, invocation via B")

    audit = client.get("/api/v1/audit-events", headers=headers).json()["data"]
    actions = {event["action"] for event in audit}
    if not {"capability.publish", "invocation.execute"} <= actions:
        _fail(f"audit trail incomplete: {sorted(actions)}")
    _step("audit trail records the journey")

    for server in (control_server, executor_server, crm_server):
        server.should_exit = True
    client.close()

    print()
    print("INSTALL VERIFIED "
          f"({len(_steps)} steps, {time.time() - started:.1f}s, loopback only)")
    print("Next: scripts/dev-up.py --ui, then docs/40 for the quickstart")


if __name__ == "__main__":
    main()

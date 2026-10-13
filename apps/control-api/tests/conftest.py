"""Shared harness for control-api integration tests.

In-process topology: control TestClient -> executor TestClient (shared
state, real HTTP serialization through the ASGI portal). The demo endpoint
uses the ``mock://`` transport so no external server is needed here; the
full real-server path is covered by tests/e2e.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from ppswitch_control.app import create_app
from ppswitch_control.auth import hash_token
from ppswitch_control.settings import ControlSettings
from ppswitch_db.migrate import run_upgrade
from ppswitch_db.models import PrincipalRow, WorkspaceRow
from ppswitch_db.session import create_engine_for_url, create_session_factory
from ppswitch_executor.main import ExecutorSettings
from ppswitch_executor.main import create_app as create_executor_app
from ppswitch_secrets import MockSecretProvider

REPO_ROOT = Path(__file__).resolve().parents[3]
HAR_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "synthetic-har" / "crm.har.json"

ALICE_TOKEN = "dev-token-alice-4f81"
CAROL_TOKEN = "dev-token-carol-91b3"
BOB_TOKEN = "dev-token-bob-77e0"
AGENT_TOKEN = "dev-token-agent-helper-c2d5"


@dataclass
class Demo:
    harness: Harness
    ids: dict


@pytest.fixture()
def demo(harness: Harness) -> Demo:
    """Harness with the full happy-path chain bootstrapped via the API."""
    return Demo(harness=harness, ids=bootstrap_demo(harness))


def _make_principal(session, workspace_id, kind, name, token) -> PrincipalRow:
    principal = PrincipalRow(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        kind=kind,
        display_name=name,
        token_hash=hash_token(token),
        token_hint=token[:14] + "...",
    )
    session.add(principal)
    return principal


@dataclass
class Harness:
    control: TestClient
    executor: TestClient
    db_url: str
    session_factory: object


@pytest.fixture()
def harness(tmp_path: Path) -> Harness:
    db_url = f"sqlite:///{tmp_path / 'control-test.db'}"
    run_upgrade(db_url)

    engine = create_engine_for_url(db_url)
    session_factory = create_session_factory(engine)
    with session_factory() as session:
        ws_a = WorkspaceRow(id=uuid.uuid4(), slug=f"demo-a-{uuid.uuid4().hex[:6]}", name="Demo A")
        ws_b = WorkspaceRow(id=uuid.uuid4(), slug=f"demo-b-{uuid.uuid4().hex[:6]}", name="Demo B")
        session.add_all([ws_a, ws_b])
        session.flush()
        _make_principal(session, ws_a.id, "user", "Alice (demo)", ALICE_TOKEN)
        _make_principal(session, ws_a.id, "user", "Carol (demo)", CAROL_TOKEN)
        _make_principal(session, ws_b.id, "user", "Bob (other org)", BOB_TOKEN)
        _make_principal(session, ws_a.id, "agent", "agent-helper (demo)", AGENT_TOKEN)
        session.commit()

    provider = MockSecretProvider()
    executor_app = create_executor_app(ExecutorSettings(demo_mode=True), provider=provider)
    executor = TestClient(executor_app)

    settings = ControlSettings(
        db_url=db_url,
        executor_url="http://executor.test.internal",
        auto_migrate=False,
        demo_mode=True,
    )
    control_app = create_app(settings, executor_client=executor, run_migrations=False)
    control = TestClient(control_app)
    return Harness(control=control, executor=executor, db_url=db_url,
                   session_factory=session_factory)


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def bootstrap_demo(harness: Harness) -> dict:
    """Full happy-path chain via the public API; returns created ids."""
    control = harness.control
    ids: dict = {}

    response = control.post("/api/v1/services", json={"slug": "crm", "name": "CRM"},
                            headers=auth(ALICE_TOKEN))
    assert response.status_code == 201, response.text
    ids["service_id"] = response.json()["data"]["id"]

    response = control.post(
        f"/api/v1/services/{ids['service_id']}/endpoints",
        json={"environment": "demo", "base_url": "mock://demo", "status": "approved"},
        headers=auth(ALICE_TOKEN),
    )
    assert response.status_code == 201, response.text
    ids["endpoint_id"] = response.json()["data"]["id"]

    with open(HAR_FIXTURE, "rb") as f:
        response = control.post(
            "/api/v1/discoveries/imports",
            files={"file": ("crm.har.json", f, "application/json")},
            data={"source_type": "har", "service_id": ids["service_id"]},
            headers=auth(ALICE_TOKEN),
        )
    assert response.status_code == 201, response.text
    body = response.json()["data"]
    assert body["candidates"] == 2  # get + refund
    ids["import"] = body

    response = control.get(
        f"/api/v1/operations?service_id={ids['service_id']}", headers=auth(ALICE_TOKEN)
    )
    operations = response.json()["data"]
    get_op = next(o for o in operations if o["method"] == "GET")
    refund_op = next(o for o in operations if o["method"] == "POST")
    ids["get_operation_id"] = get_op["id"]
    ids["refund_operation_id"] = refund_op["id"]

    response = control.post(
        f"/api/v1/operations/{ids['get_operation_id']}/verify",
        json={"evidence": "contract confirmed against provider docs"},
        headers=auth(ALICE_TOKEN),
    )
    assert response.status_code == 200

    response = control.post(
        "/api/v1/capabilities",
        json={
            "name": "crm.get_customer",
            "display_name": "Get customer",
            "description": "Get a customer within the caller's authorized scope",
            "operation_id": ids["get_operation_id"],
            "input_schema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["customer_id"],
                "properties": {"customer_id": {"type": "string", "minLength": 1, "maxLength": 64}},
            },
            "output_schema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "display_name"],
                "properties": {"id": {"type": "string"}, "display_name": {"type": "string"}},
            },
            "allow_fields": ["id", "display_name"],
            "risk_level": "low",
        },
        headers=auth(ALICE_TOKEN),
    )
    assert response.status_code == 201, response.text
    ids["capability_id"] = response.json()["data"]["id"]

    # review PR-SEC-01: review/verify/publish must be performed by a human
    # principal OTHER than the creator - carol is the demo reviewer
    for action in ("review", "verify"):
        response = control.post(
            f"/api/v1/capabilities/{ids['capability_id']}/{action}",
            json={"evidence": f"demo {action}"},
            headers=auth(CAROL_TOKEN),
        )
        assert response.status_code == 200, response.text

    response = control.post(
        f"/api/v1/capabilities/{ids['capability_id']}/publish",
        json={"version": "1.0.0"},
        headers=auth(CAROL_TOKEN),
    )
    assert response.status_code == 200, response.text
    ids["published"] = response.json()["data"]

    for dataset, name in (("A", "CRM Demo A"), ("B", "CRM Demo B")):
        response = control.post(
            "/api/v1/connections",
            json={"service_id": ids["service_id"], "auth_scheme": "api_key",
                  "demo_dataset": dataset},
            headers=auth(ALICE_TOKEN),
        )
        assert response.status_code == 201, response.text
        ids[f"connection_{dataset}"] = response.json()["data"]["id"]

        response = control.post(
            "/api/v1/profiles",
            json={
                "service_id": ids["service_id"],
                "endpoint_id": ids["endpoint_id"],
                "connection_id": ids[f"connection_{dataset}"],
                "name": name,
            },
            headers=auth(ALICE_TOKEN),
        )
        assert response.status_code == 201, response.text
        ids[f"profile_{dataset}"] = response.json()["data"]["id"]

    response = control.put(
        f"/api/v1/active-bindings/{ids['service_id']}/personal",
        json={"profile_id": ids["profile_A"], "expected_version": 0},
        headers=auth(ALICE_TOKEN),
    )
    assert response.status_code == 200, response.text
    ids["binding_version"] = response.json()["data"]["version"]
    return ids

"""SECRET-06 e2e: real control + executor servers WITH the executor's
revocation-freshness reverse query wired (control_url), against a fake
OS keyring.

Covers the acceptance items that need a real process pair (docs/41):

- A3 execution half: a swapped material fails CLOSED at the executor
  (503 SECRET_FINGERPRINT_MISMATCH), never silently switches, and the
  owner's 409-confirm rotation recovers the call with a visible dataset
  change;
- A4 poison: overwriting the keyring entry mid-flight cannot redirect
  the invocation to the attacker's credential;
- A5/T3 race: a plan SIGNED before a release is refused by the executor
  AFTER the release commits (generation tombstone, deterministic order);
- T8: an agent holding a live delegation + grant still cannot bypass the
  claim checks after release.
"""

from __future__ import annotations

import json
import socket
import threading
import time
import uuid
from pathlib import Path

import httpx
import pytest
import uvicorn

REPO_ROOT = Path(__file__).resolve().parents[2]
HAR_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "synthetic-har" / "crm.har.json"

ALICE_TOKEN = "dev-token-alice-iso-01"
CAROL_TOKEN = "dev-token-carol-iso-02"


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _FakeKeyringModule:
    class errors:
        class KeyringError(Exception):
            pass

        class PasswordDeleteError(KeyringError):
            pass

    def __init__(self) -> None:
        self.store: dict = {}

    def get_password(self, service, username):
        return self.store.get((service, username))

    def set_password(self, service, username, password):
        self.store[(service, username)] = password

    def delete_password(self, service, username):
        self.store.pop((service, username), None)

    def put(self, name, payload):
        self.store[(f"ppswitch/{name}", "pp-switch-executor")] = json.dumps(payload)


@pytest.fixture(scope="module")
def fake_keyring():
    import ppswitch_secrets.keyring as kr_module

    backend = _FakeKeyringModule()
    original = kr_module._keyring_module
    kr_module._keyring_module = lambda: backend
    yield backend
    kr_module._keyring_module = original


def _start(app, port: int, name: str):
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                           log_level="warning"))
    thread = threading.Thread(target=server.run, name=name, daemon=True)
    thread.start()
    deadline = time.time() + 20
    while time.time() < deadline:
        if server.started:
            return server
        time.sleep(0.05)
    raise RuntimeError(f"{name} failed to start")


@pytest.fixture(scope="module")
def iso_stack(tmp_path_factory, fake_keyring):
    from ppswitch_control.app import create_app as create_control_app
    from ppswitch_control.auth import hash_token
    from ppswitch_control.settings import ControlSettings
    from ppswitch_db.migrate import run_upgrade
    from ppswitch_db.models import PrincipalRow, WorkspaceRow
    from ppswitch_db.session import create_engine_for_url, create_session_factory
    from ppswitch_executor.main import ExecutorSettings
    from ppswitch_executor.main import create_app as create_executor_app
    from ppswitch_mockcrm import app as mockcrm_app

    db_dir = tmp_path_factory.mktemp("iso")
    db_url = f"sqlite:///{db_dir / 'iso.db'}"
    run_upgrade(db_url)
    engine = create_engine_for_url(db_url)
    factory = create_session_factory(engine)
    with factory() as session:
        workspace = WorkspaceRow(id=uuid.uuid4(), slug="iso", name="ISO")
        session.add(workspace)
        session.flush()
        for kind, name, token, role in (
            ("user", "Alice (iso)", ALICE_TOKEN, "admin"),
            ("user", "Carol (iso)", CAROL_TOKEN, "reviewer"),
        ):
            session.add(PrincipalRow(
                id=uuid.uuid4(), workspace_id=workspace.id, kind=kind,
                display_name=name, token_hash=hash_token(token),
                token_hint=token[:14] + "...", role=role,
            ))
        session.commit()
    engine.dispose()

    crm = _start(mockcrm_app, _free_port(), "iso-crm")
    # control's port is pre-picked so the executor can point its
    # revocation-freshness reverse query at it BEFORE control starts
    control_port = _free_port()
    executor = _start(
        create_executor_app(ExecutorSettings(
            demo_mode=True,
            control_url=f"http://127.0.0.1:{control_port}",
        )),
        _free_port(), "iso-executor",
    )
    control = _start(
        create_control_app(ControlSettings(
            db_url=db_url,
            executor_url=f"http://127.0.0.1:{executor.config.port}",
            auto_migrate=False,
            demo_mode=True,
        )),
        control_port, "iso-control",
    )
    yield {
        "crm": crm, "executor": executor, "control": control,
        "executor_url": f"http://127.0.0.1:{executor.config.port}",
        "control_url": f"http://127.0.0.1:{control.config.port}",
        "db_url": db_url,
    }
    control.should_exit = True
    executor.should_exit = True
    crm.should_exit = True


@pytest.fixture(scope="module")
def iso_demo(iso_stack, fake_keyring) -> dict:
    """The FEAT-03-style chain, but with a REAL keyring credential whose
    material the tests swap to simulate rotation and poisoning."""
    control_url = iso_stack["control_url"]
    crm_host = f"127.0.0.1:{iso_stack['crm'].config.port}"
    ids: dict = {}
    fake_keyring.put("iso-key", {
        "auth_scheme": "api_key",
        "material": {"api_key": "MOCKKEY-DEMO-A-feat03"},  # dataset A key
    })
    with httpx.Client(base_url=control_url, timeout=30.0, trust_env=False) as client:
        service = client.post("/api/v1/services",
                              json={"slug": "crm", "name": "CRM"},
                              headers=auth(ALICE_TOKEN)).json()["data"]
        ids["service_id"] = service["id"]
        client.post(
            f"/api/v1/services/{ids['service_id']}/endpoints",
            json={"environment": "demo", "base_url": f"http://{crm_host}",
                  "approved_hosts": [crm_host], "status": "approved"},
            headers=auth(ALICE_TOKEN),
        )
        with open(HAR_FIXTURE, "rb") as handle:
            client.post(
                "/api/v1/discoveries/imports",
                files={"file": ("crm.har.json", handle, "application/json")},
                data={"source_type": "har", "service_id": ids["service_id"]},
                headers=auth(ALICE_TOKEN),
            )
        operations = client.get(f"/api/v1/operations?service_id={ids['service_id']}",
                                headers=auth(ALICE_TOKEN)).json()["data"]
        ids["operation_id"] = next(o["id"] for o in operations if o["method"] == "GET")
        capability = client.post(
            "/api/v1/capabilities",
            json={
                "name": "crm.get_customer",
                "display_name": "Get customer",
                "operation_id": ids["operation_id"],
                "input_schema": {
                    "type": "object", "additionalProperties": False,
                    "required": ["customer_id"],
                    "properties": {"customer_id": {"type": "string", "maxLength": 64}},
                },
                "output_schema": {"type": "object"},
                "allow_fields": ["id", "display_name"],
                "execution": {"path": {"id": "customer_id"}},
            },
            headers=auth(ALICE_TOKEN),
        )
        assert capability.status_code == 201, capability.text
        ids["capability_id"] = capability.json()["data"]["id"]
        for action in ("review", "verify"):
            client.post(f"/api/v1/capabilities/{ids['capability_id']}/{action}",
                        json={"evidence": f"iso {action}"},
                        headers=auth(CAROL_TOKEN))
        client.post(f"/api/v1/capabilities/{ids['capability_id']}/publish",
                    json={"version": "1.0.0"}, headers=auth(CAROL_TOKEN))

        connection = client.post(
            "/api/v1/connections",
            json={"service_id": ids["service_id"], "auth_scheme": "api_key",
                  "secret_name": "iso-key"},
            headers=auth(ALICE_TOKEN),
        ).json()["data"]
        ids["connection_id"] = connection["id"]
        activated = client.post(f"/api/v1/connections/{ids['connection_id']}/activate",
                                headers=auth(ALICE_TOKEN))
        assert activated.status_code == 200, activated.text
        endpoints = client.get(f"/api/v1/endpoints?service_id={ids['service_id']}",
                               headers=auth(ALICE_TOKEN)).json()["data"]
        profile = client.post(
            "/api/v1/profiles",
            json={"service_id": ids["service_id"],
                  "endpoint_id": endpoints[0]["id"],
                  "connection_id": ids["connection_id"], "name": "ISO profile 2"},
            headers=auth(ALICE_TOKEN),
        )
        assert profile.status_code == 201, profile.text
        ids["profile_id"] = profile.json()["data"]["id"]
        client.put(
            f"/api/v1/active-bindings/{ids['service_id']}/personal",
            json={"profile_id": ids["profile_id"], "expected_version": 0},
            headers=auth(ALICE_TOKEN),
        )
    return ids


def _invoke(iso_stack, arguments: dict, token: str = ALICE_TOKEN) -> httpx.Response:
    with httpx.Client(base_url=iso_stack["control_url"], timeout=30.0,
                      trust_env=False) as client:
        return client.post(
            "/api/v1/invocations",
            json={"capability": "crm.get_customer", "arguments": arguments},
            headers=auth(token),
        )


class TestSecret06Isolation:
    def test_baseline_invocation_uses_dataset_a(self, iso_stack, iso_demo):
        response = _invoke(iso_stack, {"customer_id": "C-1024"})
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["status"] == "succeeded"
        assert data["result"]["display_name"] == "Acme Industries"

    def test_a4_poison_swap_fails_closed_and_a3_rotation_recovers(
        self, iso_stack, iso_demo, fake_keyring
    ):
        # A4: the entry is overwritten (attacker credential, dataset B) -
        # the very next invocation must REFUSE, never silently switch
        fake_keyring.put("iso-key", {
            "auth_scheme": "api_key",
            "material": {"api_key": "MOCKKEY-DEMO-B-feat03"},
        })
        refused = _invoke(iso_stack, {"customer_id": "C-1024"})
        assert refused.status_code == 503, refused.text
        assert refused.json()["error"]["code"] == "SECRET_FINGERPRINT_MISMATCH"

        # A3 control half: plain re-activation is a 409, not a silent repin
        with httpx.Client(base_url=iso_stack["control_url"], timeout=30.0,
                          trust_env=False) as client:
            conflict = client.post(
                f"/api/v1/connections/{iso_demo['connection_id']}/activate",
                headers=auth(ALICE_TOKEN),
            )
            assert conflict.status_code == 409, conflict.text
            assert conflict.json()["error"]["code"] == "SECRET_ROTATION_CONFIRM_REQUIRED"
            # A3 resolution: explicit confirm CAS-pins the new material
            confirmed = client.post(
                f"/api/v1/connections/{iso_demo['connection_id']}/confirm-rotation",
                headers=auth(ALICE_TOKEN),
            )
            assert confirmed.status_code == 200, confirmed.text

        # A3 execution half: recovered - and the credential REALLY changed
        # (dataset B answers with the QA copy name)
        recovered = _invoke(iso_stack, {"customer_id": "C-1024"})
        assert recovered.status_code == 200, recovered.text
        assert "QA copy" in recovered.json()["data"]["result"]["display_name"]

    def test_t3_signed_plan_before_release_executes_after_release_refused(
        self, iso_stack, iso_demo
    ):
        """Deterministic race: freeze + sign a plan, COMMIT a release,
        THEN hand the plan to the executor - the generation tombstone and
        the reverse freshness query must refuse it."""
        from ppswitch_executor.plan_signing import sign_plan

        # a valid plan exactly as the control plane would sign it now
        with httpx.Client(base_url=iso_stack["control_url"], timeout=30.0,
                          trust_env=False) as client:
            claims = client.get("/api/v1/keyring-claims",
                                headers=auth(ALICE_TOKEN)).json()["data"]
            claim = next(c for c in claims if c["name"] == "iso-key")
        plan = {
            "invocation_id": str(uuid.uuid4()),
            "capability_name": "crm.get_customer",
            "capability_version": "1.0.0",
            "capability_version_id": str(uuid.uuid4()),
            "operation": {"method": "GET", "path_template": "/api/customers/{id}",
                          "path_map": {"id": "customer_id"},
                          "query_template": {}, "body_template": {}},
            "endpoint_base_url": f"http://127.0.0.1:{iso_stack['crm'].config.port}",
            "approved_hosts": [f"127.0.0.1:{iso_stack['crm'].config.port}"],
            "auth_scheme": "api_key",
            "secret_ref": "keyring://iso-key",
            "allow_fields": ["id", "display_name"],
            "input_schema": {"type": "object"},
            "output_schema": {"type": "object"},
            "arguments": {"customer_id": "C-1024"},
            "workspace_id": str(uuid.uuid4()), "principal_id": str(uuid.uuid4()),
            "risk_level": "low", "policy_version": "race-test",
            "arguments_digest": "0" * 64,
            "deadline_epoch_s": time.time() + 60,
            "nonce": uuid.uuid4().hex + uuid.uuid4().hex[:8],
            "claim": {
                "namespace_id": "local", "name": "iso-key",
                "generation": claim["generation"],
                "pin_version": claim["pin_version"],
                "material_tag": _current_tag(iso_stack),
            },
        }
        signature = sign_plan(plan, "dev-internal-token-synthetic")

        # release commits BETWEEN signing and execution
        with httpx.Client(base_url=iso_stack["control_url"], timeout=30.0,
                          trust_env=False) as client:
            released = client.post("/api/v1/keyring-claims/iso-key/release",
                                   headers=auth(ALICE_TOKEN))
            assert released.status_code == 200, released.text

        response = httpx.post(
            f"{iso_stack['executor_url']}/internal/v1/execute",
            json={"plan": plan, "signature": signature},
            headers={"X-Internal-Token": "dev-internal-token-synthetic"},
            timeout=15.0, trust_env=False,
        )
        assert response.status_code in (401, 403, 503), response.text
        assert "Acme" not in response.text and "Globex" not in response.text

    def test_t8_agent_delegation_cannot_bypass(self, iso_stack, iso_demo, fake_keyring):
        """After the release above, an agent with a live grant and
        delegation still gets 403: claim checks bind before the
        delegation path hands out a plan."""
        with httpx.Client(base_url=iso_stack["control_url"], timeout=30.0,
                          trust_env=False) as client:
            agent = client.post("/api/v1/agent-principals",
                                json={"display_name": "iso-agent"},
                                headers=auth(ALICE_TOKEN)).json()["data"]
            delegation = client.post(
                "/api/v1/delegations",
                json={"agent_principal_id": agent["id"], "scopes": []},
                headers=auth(ALICE_TOKEN),
            ).json()["data"]
            client.post(
                f"/api/v1/delegations/{delegation['id']}/context",
                headers=auth(ALICE_TOKEN),
            )
            client.post(
                "/api/v1/agent-grants",
                json={"agent_principal_id": agent["id"],
                      "capability_id": iso_demo["capability_id"], "scopes": []},
                headers=auth(ALICE_TOKEN),
            )
            client.put(
                f"/api/v1/active-bindings/{iso_demo['service_id']}/agent:{agent['id']}",
                json={"profile_id": iso_demo["profile_id"], "expected_version": 0},
                headers=auth(ALICE_TOKEN),
            )
        denied = _invoke(iso_stack, {"customer_id": "C-1024"}, token=agent["token"])
        assert denied.status_code == 403, denied.text


def _current_tag(iso_stack) -> str:
    """Fetch the pinned material tag from the control DB (test helper;
    the tag normally never leaves the internal channel)."""
    from sqlalchemy import create_engine, text

    engine = create_engine(iso_stack["db_url"])
    try:
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT material_tag FROM keyring_claims WHERE name = 'iso-key'"
            )).one()
            return str(row[0])
    finally:
        engine.dispose()

"""Full-stack E2E: real HTTP servers for control-api, executor and the mock
CRM, plus the Python SDK and the MCP adapter.

Covers the README demo script end to end:
import HAR -> publish capability -> two profiles -> one-click switch ->
HTTP + SDK + MCP invocations through different datasets, response guard
against the leaky CRM, and the in-flight profile snapshot (SEC-017).
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path

import anyio
import httpx
import pytest

ALICE_TOKEN = "dev-token-alice-e2e-01"
CAROL_TOKEN = "dev-token-carol-e2e-02"
AGENT_TOKEN = "dev-token-agent-e2e-03"

REPO_ROOT = Path(__file__).resolve().parents[2]
HAR_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "synthetic-har" / "crm.har.json"


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def stack(tmp_path_factory, start_server_fixture):

    from ppswitch_control.app import create_app
    from ppswitch_control.auth import hash_token
    from ppswitch_control.settings import ControlSettings
    from ppswitch_db.migrate import run_upgrade
    from ppswitch_db.models import PrincipalRow, WorkspaceRow
    from ppswitch_db.session import create_engine_for_url, create_session_factory
    from ppswitch_executor.main import ExecutorSettings
    from ppswitch_executor.main import create_app as create_executor_app
    from ppswitch_mockcrm import app as mockcrm_app

    db_dir = tmp_path_factory.mktemp("e2e")
    pg_base = os.environ.get("PP_SWITCH_TEST_DB_URL")
    if pg_base:
        from ppswitch_db.testdb import provision_test_database

        db_url = provision_test_database(pg_base, prefix="ppswitch_e2e")
    else:
        db_url = f"sqlite:///{db_dir / 'e2e.db'}"
    run_upgrade(db_url)
    # share the URL with teardown-style helpers (_expire_grant)
    os.environ["PP_SWITCH_E2E_DB_URL"] = db_url
    engine = create_engine_for_url(db_url)
    factory = create_session_factory(engine)
    with factory() as session:
        workspace = WorkspaceRow(id=uuid.uuid4(), slug="e2e-demo", name="E2E Demo")
        session.add(workspace)
        session.flush()
        # review2 R-13: alice administers, carol reviews/publishes
        for kind, name, token, role in (
            ("user", "Alice (e2e)", ALICE_TOKEN, "admin"),
            ("user", "Carol (e2e)", CAROL_TOKEN, "reviewer"),
            ("agent", "agent-helper (e2e)", AGENT_TOKEN, None),
        ):
            session.add(
                PrincipalRow(
                    id=uuid.uuid4(), workspace_id=workspace.id, kind=kind,
                    display_name=name, token_hash=hash_token(token),
                    token_hint=token[:14] + "...", role=role,
                )
            )
        session.commit()

    start_server = start_server_fixture
    crm = start_server(mockcrm_app, name="mock-crm")
    executor_app = create_executor_app(ExecutorSettings(demo_mode=True))
    executor = start_server(executor_app, name="executor")

    control_app = create_app(
        ControlSettings(
            db_url=db_url,
            executor_url=executor.url,
            auto_migrate=False,
            demo_mode=True,
        )
    )
    control = start_server(control_app, name="control")

    yield {"crm": crm, "executor": executor, "control": control}

    control.stop()
    executor.stop()
    crm.stop()


@pytest.fixture(scope="module")
def demo(stack) -> dict:
    control_url = stack["control"].url
    ids: dict = {}
    with httpx.Client(base_url=control_url, timeout=15.0, trust_env=False) as client:
        created = client.post("/api/v1/services", json={"slug": "crm", "name": "CRM"},
                              headers=auth(ALICE_TOKEN))
        assert created.status_code == 201, created.text
        ids["service_id"] = created.json()["data"]["id"]

        crm_host = f"127.0.0.1:{stack['crm'].port}"
        endpoint = client.post(
            f"/api/v1/services/{ids['service_id']}/endpoints",
            json={"environment": "demo", "base_url": f"http://{crm_host}",
                  "approved_hosts": [crm_host], "status": "approved"},
            headers=auth(ALICE_TOKEN),
        )
        assert endpoint.status_code == 201, endpoint.text
        ids["endpoint_id"] = endpoint.json()["data"]["id"]

        with open(HAR_FIXTURE, "rb") as handle:
            imported = client.post(
                "/api/v1/discoveries/imports",
                files={"file": ("crm.har.json", handle, "application/json")},
                data={"source_type": "har", "service_id": ids["service_id"]},
                headers=auth(ALICE_TOKEN),
            )
        assert imported.status_code == 201, imported.text
        ids["import"] = imported.json()["data"]

        operations = client.get(
            f"/api/v1/operations?service_id={ids['service_id']}",
            headers=auth(ALICE_TOKEN),
        ).json()["data"]
        ids["get_operation_id"] = next(o["id"] for o in operations if o["method"] == "GET")
        ids["refund_operation_id"] = next(o for o in operations if o["method"] == "POST")["id"]

        client.post(f"/api/v1/operations/{ids['get_operation_id']}/verify",
                    json={"evidence": "e2e contract check"}, headers=auth(ALICE_TOKEN))

        created = client.post(
            "/api/v1/capabilities",
            json={
                "name": "crm.get_customer",
                "display_name": "Get customer",
                "description": "Get a customer within the caller's authorized scope",
                "operation_id": ids["get_operation_id"],
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
            headers=auth(ALICE_TOKEN),
        )
        assert created.status_code == 201, created.text
        ids["capability_id"] = created.json()["data"]["id"]
        for action in ("review", "verify"):
            stepped = client.post(
                f"/api/v1/capabilities/{ids['capability_id']}/{action}",
                json={"evidence": f"e2e {action}"}, headers=auth(CAROL_TOKEN),
            )
            assert stepped.status_code == 200, stepped.text
        published = client.post(
            f"/api/v1/capabilities/{ids['capability_id']}/publish",
            json={"version": "1.0.0"}, headers=auth(CAROL_TOKEN),
        )
        assert published.status_code == 200, published.text

        for dataset, name in (("A", "CRM Demo A"), ("B", "CRM Demo B")):
            conn = client.post(
                "/api/v1/connections",
                json={"service_id": ids["service_id"], "auth_scheme": "api_key",
                      "demo_dataset": dataset},
                headers=auth(ALICE_TOKEN),
            )
            assert conn.status_code == 201, conn.text
            ids[f"connection_{dataset}"] = conn.json()["data"]["id"]
            profile = client.post(
                "/api/v1/profiles",
                json={"service_id": ids["service_id"],
                      "endpoint_id": ids["endpoint_id"],
                      "connection_id": ids[f"connection_{dataset}"],
                      "name": name},
                headers=auth(ALICE_TOKEN),
            )
            assert profile.status_code == 201, profile.text
            ids[f"profile_{dataset}"] = profile.json()["data"]["id"]

        bound = client.put(
            f"/api/v1/active-bindings/{ids['service_id']}/personal",
            json={"profile_id": ids["profile_A"], "expected_version": 0},
            headers=auth(ALICE_TOKEN),
        )
        assert bound.status_code == 200, bound.text
    return ids


def binding_version(stack) -> int:
    with httpx.Client(base_url=stack["control"].url, timeout=30.0, trust_env=False) as client:
        rows = client.get("/api/v1/active-bindings", headers=auth(ALICE_TOKEN)).json()["data"]
    for row in rows:
        if row["context_key"] == "personal":
            return int(row["version"])
    raise AssertionError("no personal binding")


def switch_profile(stack, service_id: str, profile_id: str) -> httpx.Response:
    with httpx.Client(base_url=stack["control"].url, timeout=30.0, trust_env=False) as client:
        return client.put(
            f"/api/v1/active-bindings/{service_id}/personal",
            json={"profile_id": profile_id, "expected_version": binding_version(stack)},
            headers=auth(ALICE_TOKEN),
        )


def invoke(stack, arguments: dict, token: str = ALICE_TOKEN, **extra) -> httpx.Response:
    with httpx.Client(base_url=stack["control"].url, timeout=30.0, trust_env=False) as client:
        return client.post(
            "/api/v1/invocations",
            json={"capability": "crm.get_customer", "arguments": arguments, **extra},
            headers=auth(token),
        )


class TestDemoFlow:
    def test_invoke_via_dataset_a_then_switch_to_b(self, stack, demo) -> None:
        first = invoke(stack, {"customer_id": "C-1024"})
        assert first.status_code == 200, first.text
        data = first.json()["data"]
        assert data["status"] == "succeeded"
        assert data["profile_label"] == "CRM Demo A"
        assert data["result"]["display_name"] == "Acme Industries"
        # response guard: the leaky CRM fields never survive
        assert set(data["result"]) == {"id", "display_name"}
        assert "CANARY" not in first.text
        assert "access_token" not in first.text

        switched = switch_profile(stack, demo["service_id"], demo["profile_B"])
        assert switched.status_code == 200, switched.text

        second = invoke(stack, {"customer_id": "C-1024"})
        assert second.status_code == 200, second.text
        assert second.json()["data"]["profile_label"] == "CRM Demo B"
        assert second.json()["data"]["result"]["display_name"] == "Acme Industries (QA copy)"

    def test_in_flight_invocation_keeps_its_snapshot(self, stack, demo) -> None:
        # back to A for a deterministic starting point
        switched = switch_profile(stack, demo["service_id"], demo["profile_A"])
        assert switched.status_code == 200, switched.text

        results: dict = {}

        def slow_call() -> None:
            results["response"] = invoke(stack, {"customer_id": "SLOW-C-1024"})

        thread = threading.Thread(target=slow_call)
        thread.start()
        time.sleep(0.15)  # invocation is now in flight against the slow CRM path

        switched = switch_profile(stack, demo["service_id"], demo["profile_B"])
        assert switched.status_code == 200, switched.text

        thread.join(timeout=30)
        response = results["response"]
        assert response.status_code == 200, response.text
        # in-flight call still used the OLD profile (dataset A)
        assert response.json()["data"]["profile_label"] == "CRM Demo A"
        assert response.json()["data"]["result"]["display_name"] == "Acme Industries"
        # subsequent calls use the new profile
        after = invoke(stack, {"customer_id": "C-1024"})
        assert after.json()["data"]["profile_label"] == "CRM Demo B"

    def test_sdk_invokes_through_real_servers(self, stack, demo) -> None:
        from ppswitch import PPSwitchClient

        client = PPSwitchClient.from_http(stack["control"].url, ALICE_TOKEN, trust_env=False)
        result = client.invoke("crm.get_customer", {"customer_id": "C-2048"})
        assert result.status == "succeeded"
        assert result.result["display_name"] == "Globex Corporation (QA copy)"
        capabilities = client.list_capabilities()
        assert any(c["name"] == "crm.get_customer" for c in capabilities)
        bindings = client.profile_status()
        assert bindings[0]["display_label"] == "crm / CRM Demo B"

    def test_mcp_tools_list_and_call(self, stack, demo) -> None:
        from ppswitch import PPSwitchClient
        from ppswitch_mcp.server import build_mcp_server

        client = PPSwitchClient.from_http(stack["control"].url, ALICE_TOKEN, trust_env=False)
        server = build_mcp_server(stack["control"].url, ALICE_TOKEN, client=client)

        async def scenario() -> tuple[list, dict]:
            tools = await server.list_tools()
            call = await server.call_tool(
                "crm_get_customer", {"customer_id": "C-2048"}
            )
            return tools, {"content": [c.text for c in (call.content or [])]}

        tools, payload = anyio.run(scenario)
        tool_names = [t.name for t in tools]
        assert "crm_get_customer" in tool_names
        # no credential-ish parameters are ever advertised
        tool = next(t for t in tools if t.name == "crm_get_customer")
        assert "target_url" not in json.dumps(tool.input_schema)
        blob = json.dumps(payload)
        assert "Globex" in blob
        assert "CANARY" not in blob
        assert "access_token" not in blob

    def test_audit_trail_recorded(self, stack, demo) -> None:
        with httpx.Client(base_url=stack["control"].url, timeout=30.0, trust_env=False) as client:
            events = client.get("/api/v1/audit-events", headers=auth(ALICE_TOKEN))
        assert events.status_code == 200
        actions = [e["action"] for e in events.json()["data"]]
        assert "invocation.execute" in actions
        assert "profile.activate" in actions




class _FakeKeyringModule:
    """In-process keyring stand-in; the OS store is never touched."""

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


@pytest.fixture()
def fake_keyring(monkeypatch):
    backend = _FakeKeyringModule()
    import ppswitch_secrets.keyring as kr_module

    monkeypatch.setattr(kr_module, "_keyring_module", lambda: backend)
    return backend


class TestFeat03KeyringProfiles:
    """FEAT-03 acceptance: two keyring credentials, two profiles, one-click
    switch, same tool, provably different credentials, no secrets exposed."""

    def _activate(self, stack, connection_id):
        with httpx.Client(
            base_url=stack["control"].url, timeout=30.0, trust_env=False
        ) as client:
            return client.post(
                f"/api/v1/connections/{connection_id}/activate", headers=auth(ALICE_TOKEN)
            )

    def test_switch_between_two_keyring_credentials(self, stack, demo, fake_keyring) -> None:
        # 1) the human stores two TEST keys in the (faked) OS keyring
        fake_keyring.put("feat03-a", {"auth_scheme": "api_key",
                                      "material": {"api_key": "MOCKKEY-DEMO-A-feat03"}})
        fake_keyring.put("feat03-b", {"auth_scheme": "api_key",
                                      "material": {"api_key": "MOCKKEY-DEMO-B-feat03"}})

        with httpx.Client(
            base_url=stack["control"].url, timeout=30.0, trust_env=False
        ) as client:
            # 2) two pending keyring connections, activated only after the
            #    executor proves the refs resolve
            connections = {}
            for name in ("feat03-a", "feat03-b"):
                created = client.post(
                    "/api/v1/connections",
                    json={"service_id": demo["service_id"], "auth_scheme": "api_key",
                          "secret_name": name},
                    headers=auth(ALICE_TOKEN),
                ).json()["data"]
                assert created["status"] == "pending"
                activated = self._activate(stack, created["id"])
                assert activated.status_code == 200, activated.text
                connections[name] = created["id"]

            # fail-closed: a ref with no stored secret never activates
            missing = client.post(
                "/api/v1/connections",
                json={"service_id": demo["service_id"], "auth_scheme": "api_key",
                      "secret_name": "never-stored"},
                headers=auth(ALICE_TOKEN),
            ).json()["data"]
            refused = self._activate(stack, missing["id"])
            assert refused.status_code == 503
            assert refused.json()["error"]["code"] == "SECRET_PROVIDER_UNAVAILABLE"

            # 3) two profiles on the same approved endpoint
            profiles = {}
            for name, conn_id in connections.items():
                profiles[name] = client.post(
                    "/api/v1/profiles",
                    json={"service_id": demo["service_id"],
                          "endpoint_id": demo["endpoint_id"],
                          "connection_id": conn_id, "name": "keyring-" + name},
                    headers=auth(ALICE_TOKEN),
                ).json()["data"]["id"]

        # 4) switch A -> invoke -> switch B -> invoke: same tool, different
        #    credentials, DIFFERENT business results (dataset A vs B)
        assert switch_profile(stack, demo["service_id"], profiles["feat03-a"]).status_code == 200
        via_a = invoke(stack, {"customer_id": "C-1024"})
        assert via_a.status_code == 200, via_a.text
        result_a = via_a.json()["data"]["result"]
        assert result_a["display_name"] == "Acme Industries"

        assert switch_profile(stack, demo["service_id"], profiles["feat03-b"]).status_code == 200
        via_b = invoke(stack, {"customer_id": "C-1024"})
        assert via_b.status_code == 200, via_b.text
        result_b = via_b.json()["data"]["result"]
        assert result_b["display_name"] == "Acme Industries (QA copy)"

        # 5) trusted-backend evidence: different connections underneath
        with httpx.Client(
            base_url=stack["control"].url, timeout=30.0, trust_env=False
        ) as client:
            rows = client.get("/api/v1/invocations", headers=auth(ALICE_TOKEN)).json()["data"]
        keyring_rows = [r for r in rows if "feat03" in str(r.get("profile_label", ""))]
        assert len(keyring_rows) >= 2
        # two distinct active profiles => two distinct keyring connections
        labels = {r.get("profile_label") for r in keyring_rows[:2]}
        assert labels == {"keyring-feat03-a", "keyring-feat03-b"}

        # 6) agent-visible payloads contain no credential material
        assert "MOCKKEY" not in json.dumps(result_a) + json.dumps(result_b)

        # 7) revocation closes the path; switching back to A restores it
        with httpx.Client(
            base_url=stack["control"].url, timeout=30.0, trust_env=False
        ) as client:
            revoked = client.post(
                "/api/v1/connections/" + connections["feat03-b"] + "/revoke",
                headers=auth(ALICE_TOKEN),
            )
            assert revoked.status_code == 200
            assert revoked.json()["data"]["status"] == "revoked"
        # the revoked connection cannot be re-bound at all (fail-closed at
        # binding time, not first call), and A keeps working
        assert switch_profile(stack, demo["service_id"], profiles["feat03-b"]).status_code == 403
        assert switch_profile(stack, demo["service_id"], profiles["feat03-a"]).status_code == 200
        assert invoke(stack, {"customer_id": "C-1024"}).status_code == 200


class TestFeat05AAgentCallCloseout:
    """FEAT-05A closeout: the agent path actually CALLS the tool (MCP
    tools/call semantics) through the real server stack."""

    def _mint_agent(self, stack):
        with httpx.Client(
            base_url=stack["control"].url, timeout=30.0, trust_env=False
        ) as client:
            created = client.post(
                "/api/v1/agent-principals",
                json={"display_name": "e2e-closeout-agent"},
                headers=auth(ALICE_TOKEN),
            ).json()["data"]
        return created["id"], created["token"]

    def _delegate(self, stack, agent_id):
        with httpx.Client(
            base_url=stack["control"].url, timeout=30.0, trust_env=False
        ) as client:
            delegation = client.post(
                "/api/v1/delegations",
                json={"agent_principal_id": agent_id, "scopes": []},
                headers=auth(ALICE_TOKEN),
            ).json()["data"]
            ticket = client.post(
                "/api/v1/delegations/" + delegation["id"] + "/context",
                headers=auth(ALICE_TOKEN),
            ).json()["data"]["context"]
        return delegation["id"], ticket

    def test_agent_tools_list_and_call_over_real_stack(self, stack, demo) -> None:
        from ppswitch import PPSwitchClient
        from ppswitch.client import HttpTransport
        from ppswitch_mcp.registry import build_tool_function

        control_url = stack["control"].url
        agent_id, agent_token = self._mint_agent(stack)

        with httpx.Client(
            base_url=control_url, timeout=30.0, trust_env=False
        ) as client:
            # a) agent auth works, zero grants -> nothing listed (SEC-045)
            empty = client.get("/api/v1/capabilities", headers=auth(agent_token)).json()["data"]
            assert empty == []

            # b) grant exactly the published tool
            granted = client.post(
                "/api/v1/agent-grants",
                json={"agent_principal_id": agent_id,
                      "capability_id": demo["capability_id"], "scopes": []},
                headers=auth(ALICE_TOKEN),
            )
            assert granted.status_code == 201

            listed = client.get("/api/v1/capabilities", headers=auth(agent_token)).json()["data"]
            assert [c["name"] for c in listed] == ["crm.get_customer"]

        # delegation first: an agent-context binding requires it (P0-2)
        _delegation_id, ticket = self._delegate(stack, agent_id)
        with httpx.Client(
            base_url=control_url, timeout=30.0, trust_env=False
        ) as client:
            binding = client.put(
                f"/api/v1/active-bindings/{demo['service_id']}/agent:{agent_id}",
                json={"profile_id": demo["profile_A"], "expected_version": 0},
                headers=auth(ALICE_TOKEN),
            )
            assert binding.status_code == 200, binding.text

        # c) tools/call semantics: the exact function the MCP adapter
        #    registers, invoked with the agent credential + signed
        #    delegation ticket -> real HTTP -> mock CRM -> guarded result
        agent_client = PPSwitchClient(
            HttpTransport(control_url, agent_token, trust_env=False)
        )
        tool = build_tool_function(
            agent_client,
            "crm.get_customer",
            {"type": "object", "required": ["customer_id"],
             "properties": {"customer_id": {"type": "string"}}},
            "Get a customer you are authorized to view.",
        )
        outcome = tool(customer_id="C-1024")
        assert outcome["status"] == "succeeded", outcome
        assert outcome["result"]["display_name"] == "Acme Industries"
        assert "MOCKKEY" not in json.dumps(outcome["result"])

        # d) an UNGRANTED tool name is hidden from the agent entirely
        try:
            agent_client.invoke(
                capability="crm.post_api_customers_search",
                context_key="agent:" + agent_id,
                delegation_context=ticket,
                arguments={},
            )
            raised = False
        except Exception as exc:  # PPSwitchError with CAPABILITY_NOT_FOUND
            raised = "CAPABILITY_NOT_FOUND" in str(exc) or (
                getattr(exc, "code", "") == "CAPABILITY_NOT_FOUND"
            )
        assert raised

        # e) expiring the grant closes the same tool
        _expire_grant(stack, agent_id)
        try:
            tool(customer_id="C-1024")
            denied = False
        except Exception:
            denied = True
        assert denied


def _expire_grant(stack, agent_id):
    from datetime import UTC, datetime, timedelta

    from ppswitch_db.models import AgentGrantRow
    from ppswitch_db.session import create_engine_for_url, create_session_factory

    db_url = os.environ.get("PP_SWITCH_E2E_DB_URL")
    assert db_url, "stack fixture must export PP_SWITCH_E2E_DB_URL"
    engine = create_engine_for_url(db_url)
    factory = create_session_factory(engine)
    with factory() as session:
        grant = session.query(AgentGrantRow).filter_by(
            agent_principal_id=uuid.UUID(agent_id)
        ).one()
        grant.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        session.commit()
    engine.dispose()

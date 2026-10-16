"""FEAT-04 E2E: read-only PostgreSQL discovery -> wizard-shaped publish
-> agent tools/call, against REAL server processes and a REAL PostgreSQL.

Runs only when ``PP_SWITCH_TEST_DB_URL`` is set (CI postgres job). The
PostgreSQL instance plays both roles: one provisioned database backs the
control plane, a second provisioned database IS the discovered target with

- schema ``crm``: table + view (ro role has GRANT SELECT),
- schema ``hr``: a salaries table the ro role CANNOT even see and that is
  NOT in allowed_schemas (acceptance item 1, two enforcement layers).

The read-only account is stored through the FEAT-03 path (keyring entry,
in-process fake store like every other keyring e2e) and activated via the
executor's real connectivity + non-superuser check.

Acceptance mapping (docs/38 section 1):
1. unapproved schemas not discoverable            -> test_discovery_scope
2. no arbitrary SQL / writes / out-of-scope fields-> test_tampered_plans_refused,
                                                   test_invoke_returns_only_approved_columns
3. timeout / row / size caps                      -> apps/executor/tests (PG layer)
4. revocation rejects immediately                 -> test_revocation_rejects_immediately
5. db password absent from control/agent/audit    -> test_no_password_or_hr_leakage
6. db targets are a separate approval type        -> bootstrap (reviewer approve),
                                                   control-api tests
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Any

import httpx
import pytest

ALICE_TOKEN = "dev-token-alice-db-04"
CAROL_TOKEN = "dev-token-carol-db-04"

PG_URL = os.environ.get("PP_SWITCH_TEST_DB_URL", "")
RO_PASSWORD = "MOCKKEY-pg-ro-e2e-04"

pytestmark = pytest.mark.skipif(not PG_URL, reason="PP_SWITCH_TEST_DB_URL not set")


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


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


@pytest.fixture(scope="module")
def fake_keyring():
    """Module-scoped: the bootstrap fixture activates the connection (an
    executor keyring read) before any test function runs."""
    import ppswitch_secrets.keyring as kr_module

    backend = _FakeKeyringModule()
    original = kr_module._keyring_module
    kr_module._keyring_module = lambda: backend
    yield backend
    kr_module._keyring_module = original


@pytest.fixture(scope="module")
def db_stack(tmp_path_factory, start_server_fixture, fake_keyring):
    from ppswitch_control.app import create_app as create_control_app
    from ppswitch_control.auth import hash_token
    from ppswitch_control.settings import ControlSettings
    from ppswitch_db.migrate import run_upgrade
    from ppswitch_db.models import PrincipalRow, WorkspaceRow
    from ppswitch_db.session import create_engine_for_url, create_session_factory
    from ppswitch_db.testdb import provision_test_database
    from ppswitch_executor.main import ExecutorSettings
    from ppswitch_executor.main import create_app as create_executor_app
    from sqlalchemy import create_engine, text

    control_db = provision_test_database(PG_URL, prefix="ppswitch_db_e2e_ctl")
    target_db = provision_test_database(PG_URL, prefix="ppswitch_db_e2e_tgt")
    run_upgrade(control_db)

    role = f"ro_e2e_db_{uuid.uuid4().hex[:8]}"
    admin = create_engine(PG_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE ROLE "{role}" LOGIN PASSWORD \'{RO_PASSWORD}\''))
    admin.dispose()

    target = create_engine(target_db, isolation_level="AUTOCOMMIT")
    with target.connect() as conn:
        conn.execute(text("CREATE SCHEMA crm"))
        conn.execute(text("CREATE SCHEMA hr"))
        conn.execute(text(
            "CREATE TABLE crm.customers "
            "(id text PRIMARY KEY, display_name text, secret_note text)"
        ))
        conn.execute(text(
            "INSERT INTO crm.customers VALUES "
            "('C-1024', 'Acme Industries', 'CANARY-DB-INTERNAL'), "
            "('C-2048', 'Globex', 'CANARY-DB-INTERNAL')"
        ))
        conn.execute(text(
            "CREATE VIEW crm.customers_v AS "
            "SELECT id, display_name, secret_note FROM crm.customers"
        ))
        conn.execute(text("CREATE TABLE hr.salaries (emp text, amount integer)"))
        conn.execute(text("INSERT INTO hr.salaries VALUES ('alice', 999999)"))
        conn.execute(text(f'GRANT USAGE ON SCHEMA crm TO "{role}"'))
        conn.execute(text(f'GRANT SELECT ON ALL TABLES IN SCHEMA crm TO "{role}"'))
        # hr deliberately stays ungranted: the read-only role cannot see it
    target.dispose()

    from sqlalchemy.engine import make_url

    target_url = make_url(target_db)

    engine = create_engine_for_url(control_db)
    factory = create_session_factory(engine)
    with factory() as session:
        workspace = WorkspaceRow(id=uuid.uuid4(), slug=f"db-e2e-{uuid.uuid4().hex[:6]}",
                                 name="DB E2E")
        session.add(workspace)
        session.flush()
        for kind, name, token, role_title in (
            ("user", "Alice (db e2e)", ALICE_TOKEN, "admin"),
            ("user", "Carol (db e2e)", CAROL_TOKEN, "reviewer"),
        ):
            session.add(PrincipalRow(
                id=uuid.uuid4(), workspace_id=workspace.id, kind=kind,
                display_name=name, token_hash=hash_token(token),
                token_hint=token[:14] + "...", role=role_title,
            ))
        session.commit()
    os.environ["PP_SWITCH_DB_E2E_URL"] = control_db

    start_server = start_server_fixture
    executor = start_server(
        create_executor_app(ExecutorSettings(demo_mode=True)), name="executor-db"
    )
    control = start_server(
        create_control_app(ControlSettings(
            db_url=control_db,
            executor_url=executor.url,
            auto_migrate=False,
            demo_mode=True,
        )),
        name="control-db",
    )
    yield {
        "control": control,
        "executor": executor,
        "target": {
            "host": target_url.host or "127.0.0.1",
            "port": target_url.port or 5432,
            "database": target_url.database,
            "role": role,
        },
        "control_db": control_db,
    }
    control.stop()
    executor.stop()
    engine.dispose()
    cleanup = create_engine(PG_URL, isolation_level="AUTOCOMMIT")
    with cleanup.connect() as conn:
        conn.execute(text(f'DROP ROLE IF EXISTS "{role}"'))
    cleanup.dispose()


@pytest.fixture(scope="module")
def db_demo(db_stack, fake_keyring) -> dict:
    """The full FEAT-04 chain, bootstrapped once over real HTTP."""
    control_url = db_stack["control"].url
    target = db_stack["target"]
    ids: dict[str, Any] = {}
    with httpx.Client(base_url=control_url, timeout=30.0, trust_env=False) as client:
        created = client.post("/api/v1/services",
                              json={"slug": "crm-db", "name": "CRM PostgreSQL"},
                              headers=auth(ALICE_TOKEN))
        assert created.status_code == 201, created.text
        ids["service_id"] = created.json()["data"]["id"]

        # 1) member registers the target -> REVIEWER approves (separate type)
        target_row = client.post(
            f"/api/v1/services/{ids['service_id']}/db-targets",
            json={"engine": "postgres", "host": target["host"], "port": target["port"],
                  "database": target["database"], "allowed_schemas": ["crm"]},
            headers=auth(ALICE_TOKEN),
        ).json()["data"]
        assert target_row["status"] == "pending"
        ids["db_target_id"] = target_row["id"]
        approved = client.post(f"/api/v1/db-targets/{ids['db_target_id']}/approve",
                               headers=auth(CAROL_TOKEN))
        assert approved.status_code == 200, approved.text

        # 2) FEAT-03 credential path: the human stores the read-only account
        #    in the (faked) OS keyring FIRST; the control plane only ever
        #    sees the opaque keyring name
        ids["secret_name"] = f"crm-db-ro-{uuid.uuid4().hex[:6]}"
        fake_keyring.put(ids["secret_name"], {
            "auth_scheme": "postgres",
            "material": {"user": target["role"], "password": RO_PASSWORD},
        })
        connection = client.post(
            "/api/v1/connections",
            json={"service_id": ids["service_id"], "auth_scheme": "postgres",
                  "secret_name": ids["secret_name"]},
            headers=auth(ALICE_TOKEN),
        ).json()["data"]
        assert connection["status"] == "pending"
        ids["connection_id"] = connection["id"]
        # executor opens the approved target with the credential and proves
        # the role is not superuser-class before the connection activates
        activated = client.post(f"/api/v1/connections/{ids['connection_id']}/activate",
                                headers=auth(ALICE_TOKEN))
        assert activated.status_code == 200, activated.text

        # 3) endpoint-less DB profile + personal binding
        profile = client.post(
            "/api/v1/profiles",
            json={"service_id": ids["service_id"], "endpoint_id": None,
                  "connection_id": ids["connection_id"], "name": "CRM RO"},
            headers=auth(ALICE_TOKEN),
        )
        assert profile.status_code == 201, profile.text
        ids["profile_id"] = profile.json()["data"]["id"]
        bound = client.put(
            f"/api/v1/active-bindings/{ids['service_id']}/personal",
            json={"profile_id": ids["profile_id"], "expected_version": 0},
            headers=auth(ALICE_TOKEN),
        )
        assert bound.status_code == 200, bound.text

        # 4) discovery through the trusted executor -> candidates (drafts)
        discovery = client.post(f"/api/v1/db-targets/{ids['db_target_id']}/discover",
                                headers=auth(ALICE_TOKEN))
        assert discovery.status_code == 201, discovery.text
        ids["discovery"] = discovery.json()["data"]

        operations = client.get(f"/api/v1/operations?service_id={ids['service_id']}",
                                headers=auth(ALICE_TOKEN)).json()["data"]
        ids["operation_id"] = next(
            op["id"] for op in operations if op["normalized_path"] == "/db/crm/customers_v"
        )

        # 5) wizard-shaped capability: STRUCTURED spec, no SQL anywhere
        capability = client.post(
            "/api/v1/capabilities",
            json={
                "name": "crmdb.get_customer",
                "display_name": "Get customer (read-only DB)",
                "description": "Fixed parameterized read-only lookup",
                "operation_id": ids["operation_id"],
                "input_schema": {
                    "type": "object", "additionalProperties": False,
                    "required": ["customer_id"],
                    "properties": {"customer_id": {"type": "string", "maxLength": 64}},
                },
                "output_schema": {
                    "type": "object", "additionalProperties": False,
                    "properties": {"id": {"type": "string"},
                                   "display_name": {"type": "string"}},
                },
                "allow_fields": ["id", "display_name"],
                "risk_level": "low",
                "execution": {"db": {
                    "target_id": ids["db_target_id"],
                    "schema": "crm", "view": "customers_v",
                    "columns": ["id", "display_name"],
                    "filters": {"id": "customer_id"},
                    "limit": 2,
                }},
            },
            headers=auth(ALICE_TOKEN),
        )
        assert capability.status_code == 201, capability.text
        ids["capability_id"] = capability.json()["data"]["id"]
        for action in ("review", "verify"):
            stepped = client.post(
                f"/api/v1/capabilities/{ids['capability_id']}/{action}",
                json={"evidence": f"db e2e {action}"}, headers=auth(CAROL_TOKEN),
            )
            assert stepped.status_code == 200, stepped.text
        published = client.post(
            f"/api/v1/capabilities/{ids['capability_id']}/publish",
            json={"version": "1.0.0"}, headers=auth(CAROL_TOKEN),
        )
        assert published.status_code == 200, published.text

        # 6) agent: identity, delegation, grant, agent-context binding
        agent = client.post("/api/v1/agent-principals",
                            json={"display_name": "db-e2e-agent"},
                            headers=auth(ALICE_TOKEN)).json()["data"]
        ids["agent_id"], ids["agent_token"] = agent["id"], agent["token"]
        delegation = client.post("/api/v1/delegations",
                                 json={"agent_principal_id": ids["agent_id"], "scopes": []},
                                 headers=auth(ALICE_TOKEN)).json()["data"]
        ids["delegation_ticket"] = client.post(
            f"/api/v1/delegations/{delegation['id']}/context",
            headers=auth(ALICE_TOKEN)).json()["data"]["context"]
        granted = client.post(
            "/api/v1/agent-grants",
            json={"agent_principal_id": ids["agent_id"],
                  "capability_id": ids["capability_id"], "scopes": []},
            headers=auth(ALICE_TOKEN),
        )
        assert granted.status_code == 201, granted.text
        agent_binding = client.put(
            f"/api/v1/active-bindings/{ids['service_id']}/agent:{ids['agent_id']}",
            json={"profile_id": ids["profile_id"], "expected_version": 0},
            headers=auth(ALICE_TOKEN),
        )
        assert agent_binding.status_code == 200, agent_binding.text
    return ids


def _invoke(db_stack, arguments: dict, token: str = ALICE_TOKEN, **extra) -> httpx.Response:
    with httpx.Client(base_url=db_stack["control"].url, timeout=30.0,
                      trust_env=False) as client:
        return client.post(
            "/api/v1/invocations",
            json={"capability": "crmdb.get_customer", "arguments": arguments, **extra},
            headers=auth(token),
        )


class TestFeat04DbDiscovery:
    def test_bootstrap_completed_the_chain(self, db_stack, db_demo) -> None:
        assert db_demo["discovery"]["candidates"] >= 1
        assert db_demo["capability_id"]
        assert db_demo["agent_id"]

    def test_discovery_scope_is_limited_to_approved_schemas(self, db_stack, db_demo) -> None:
        """Acceptance 1: hr is neither in allowed_schemas nor granted to the
        read-only role - it must not appear anywhere in the results."""
        discovery = db_demo["discovery"]
        with httpx.Client(base_url=db_stack["control"].url, timeout=30.0,
                          trust_env=False) as client:
            observations = client.get(
                f"/api/v1/observations?service_id={db_demo['service_id']}",
                headers=auth(ALICE_TOKEN)).json()["data"]
        blob = json.dumps(discovery) + json.dumps(observations)
        assert "/db/crm/customers_v" in blob
        assert "hr" not in blob
        assert "salaries" not in blob
        assert RO_PASSWORD not in blob

    def test_invoke_returns_only_approved_columns(self, db_stack, db_demo) -> None:
        """Acceptance 2 (fields): secret_note stays in the database; the
        tool returns exactly the reviewed columns."""
        response = _invoke(db_stack, {"customer_id": "C-1024"})
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["status"] == "succeeded"
        assert data["result"] == {"id": "C-1024", "display_name": "Acme Industries"}
        assert "secret_note" not in data["result"]
        assert "CANARY" not in json.dumps(data)

    def test_agent_tools_call_over_real_stack(self, db_stack, db_demo) -> None:
        """The FEAT-04 closed loop end: agent -> MCP tool -> signed plan ->
        read-only SELECT -> guarded row, via real HTTP servers."""
        from ppswitch import PPSwitchClient
        from ppswitch.client import HttpTransport
        from ppswitch_mcp.registry import build_tool_function

        agent_client = PPSwitchClient(
            HttpTransport(db_stack["control"].url, db_demo["agent_token"], trust_env=False)
        )
        tool = build_tool_function(
            agent_client,
            "crmdb.get_customer",
            {"type": "object", "required": ["customer_id"],
             "properties": {"customer_id": {"type": "string"}}},
            "Get a customer you are authorized to view.",
        )
        outcome = tool(customer_id="C-2048")
        assert outcome["status"] == "succeeded", outcome
        assert outcome["result"]["display_name"] == "Globex"
        assert "MOCKKEY" not in json.dumps(outcome)
        assert "CANARY" not in json.dumps(outcome)

    def test_tampered_plans_refused_or_gutted_by_executor(self, db_stack, db_demo) -> None:
        """Acceptance 2 (SQL): even a plan SIGNED with the internal token
        cannot smuggle SQL text, unapproved schemas or injection - the
        executor only accepts the structured spec shape, and a column set
        the response guard does not know gets dropped entirely."""
        import time

        from ppswitch_executor.plan_signing import sign_plan

        executor_url = db_stack["executor"].url
        target = db_stack["target"]
        base_query = {
            "host": target["host"], "port": target["port"],
            "database": target["database"], "allowed_schemas": ["crm"],
            "schema_name": "crm", "view": "customers_v",
            "columns": ["id", "display_name"], "param_map": {"id": "customer_id"},
            "fetch_limit": 2, "statement_timeout_ms": 5000,
        }
        cases = [
            # unapproved schema: rejected before any connection
            ({"schema_name": "hr", "view": "salaries", "columns": ["emp"],
              "param_map": {}}, "refuse"),
            # injection identifiers: the structured spec cannot carry them
            ({"schema_name": "crm", "view": 'customers_v"; DROP TABLE customers',
              "columns": ["id"], "param_map": {}}, "refuse"),
            # a column set outside the reviewed tool contract: executes the
            # fixed SELECT shape, but the guard (empty allow list/schema)
            # drops every field - the canary never leaves the executor
            ({"columns": ["secret_note"]}, "gut"),
        ]
        for override, expectation in cases:
            query = {**base_query, **override}
            plan = {
                "invocation_id": str(uuid.uuid4()),
                "capability_name": "crmdb.get_customer",
                "capability_version": "1.0.0",
                "capability_version_id": str(uuid.uuid4()),
                "operation": {"method": "GET", "path_template": "/db/crm/customers_v",
                              "path_map": {}, "query_template": {}, "body_template": {}},
                "endpoint_base_url": "", "approved_hosts": [],
                "auth_scheme": "postgres",
                "secret_ref": f"keyring://{db_demo['secret_name']}",
                "allow_fields": [], "input_schema": {}, "output_schema": {},
                "arguments": {"customer_id": "C-1024"},
                "workspace_id": str(uuid.uuid4()), "principal_id": str(uuid.uuid4()),
                "risk_level": "low", "policy_version": "tamper-test",
                "arguments_digest": "0" * 64,
                "deadline_epoch_s": time.time() + 30,
                "nonce": uuid.uuid4().hex + uuid.uuid4().hex[:8],
                "db_query": query,
            }
            response = httpx.post(
                f"{executor_url}/internal/v1/execute",
                json={"plan": plan,
                      "signature": sign_plan(plan, "dev-internal-token-synthetic")},
                headers={"X-Internal-Token": "dev-internal-token-synthetic"},
                timeout=15.0, trust_env=False,
            )
            assert "CANARY" not in response.text, override
            assert RO_PASSWORD not in response.text, override
            if expectation == "refuse":
                assert response.status_code != 200, (override, response.text)
            else:
                assert response.status_code == 200, (override, response.text)
                assert response.json()["result"] == {}  # guard dropped everything

    def test_no_password_or_hr_leakage(self, db_stack, db_demo) -> None:
        """Acceptance 5: the DB password never lands in the control DB
        (audit trail, invocation snapshots/results) nor in agent-visible
        capability payloads."""
        from ppswitch_db.session import create_engine_for_url
        from sqlalchemy import text

        engine = create_engine_for_url(db_stack["control_db"])
        try:
            with engine.connect() as conn:
                audit_blob = " ".join(
                    str(row[0]) for row in conn.execute(text(
                        "SELECT redacted_metadata::text FROM audit_events"
                    ))
                )
                invocation_blob = " ".join(
                    str(row[0]) for row in conn.execute(text(
                        "SELECT profile_snapshot::text FROM invocations"
                    ))
                )
        finally:
            engine.dispose()
        for blob in (audit_blob, invocation_blob):
            assert RO_PASSWORD not in blob
            assert "CANARY" not in blob
        with httpx.Client(base_url=db_stack["control"].url, timeout=30.0,
                          trust_env=False) as client:
            agent_view = client.get(
                "/api/v1/capabilities", headers=auth(db_demo["agent_token"])).json()["data"]
        agent_blob = json.dumps(agent_view)
        assert RO_PASSWORD not in agent_blob
        assert "execution" not in agent_blob  # SEC-045: internal mapping hidden
        assert "secret_ref" not in agent_blob

    def test_revocation_rejects_immediately(self, db_stack, db_demo) -> None:
        """Acceptance 4: disabling the target, then revoking the connection,
        must fail the very next invocations at plan-build/policy time."""
        control_url = db_stack["control"].url
        with httpx.Client(base_url=control_url, timeout=30.0, trust_env=False) as client:
            disabled = client.post(
                f"/api/v1/db-targets/{db_demo['db_target_id']}/disable",
                headers=auth(CAROL_TOKEN),
            )
            assert disabled.status_code == 200, disabled.text
        denied = _invoke(db_stack, {"customer_id": "C-1024"})
        assert denied.status_code == 403, denied.text
        assert "not approved" in denied.json()["error"]["message"]

        with httpx.Client(base_url=control_url, timeout=30.0, trust_env=False) as client:
            revoked = client.post(
                f"/api/v1/connections/{db_demo['connection_id']}/revoke",
                headers=auth(ALICE_TOKEN),
            )
            assert revoked.status_code == 200, revoked.text
        denied2 = _invoke(db_stack, {"customer_id": "C-1024"})
        assert denied2.status_code == 403, denied2.text

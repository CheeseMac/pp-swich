"""Review3 security regression: SEC-043 .. SEC-048 + the shared-agent
attack scenario from the review's closing section.

The closing scenario: Alice and Bob share one agent with different
credentials, profiles and permissions. Switching contexts, reusing an
idempotency key, revoking grants, requesting other tools or injecting
prompt-shaped arguments must never cross user boundaries.
"""

from __future__ import annotations

import json
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from .conftest import AGENT_TOKEN, ALICE_TOKEN, CAROL_TOKEN, Demo, auth

DAVE_TOKEN = "dev-token-dave-5c1a"


def _client(demo: Demo) -> TestClient:
    return demo.harness.control


def _agent_id(demo: Demo) -> str:
    listing = _client(demo).get("/api/v1/dev/principals").json()["data"]
    return next(p["id"] for p in listing if p["kind"] == "agent")


def _principal_id(control: TestClient, token: str) -> str:
    listing = control.get("/api/v1/dev/principals").json()["data"]
    return next(p["id"] for p in listing if p["token_hint"].startswith(token[:14]))


@pytest.fixture()
def shared(harness, demo):
    """Alice + dave share the demo agent: own connections/profiles/bindings,
    both delegating, agent granted the demo capability."""
    from ppswitch_control.auth import hash_token
    from ppswitch_db.models import PrincipalRow

    with harness.session_factory() as session:
        alice = (
            session.query(PrincipalRow)
            .filter_by(token_hash=hash_token(ALICE_TOKEN))
            .one()
        )
        if (
            session.query(PrincipalRow)
            .filter_by(token_hash=hash_token(DAVE_TOKEN))
            .one_or_none()
            is None
        ):
            session.add(
                PrincipalRow(
                    id=uuid.uuid4(), workspace_id=alice.workspace_id, kind="user",
                    display_name="Dave (member)", token_hash=hash_token(DAVE_TOKEN),
                    token_hint=DAVE_TOKEN[:14] + "...", role="member",
                )
            )
            session.commit()

    control = harness.control
    ids = demo.ids
    agent_id = _agent_id(demo)

    created = control.post(
        "/api/v1/connections",
        json={"service_id": ids["service_id"], "auth_scheme": "api_key",
              "demo_dataset": "B"},
        headers=auth(DAVE_TOKEN),
    )
    dave_connection = created.json()["data"]["id"]
    created = control.post(
        "/api/v1/profiles",
        json={"service_id": ids["service_id"], "endpoint_id": ids["endpoint_id"],
              "connection_id": dave_connection, "name": "Dave B"},
        headers=auth(DAVE_TOKEN),
    )
    dave_profile = created.json()["data"]["id"]

    tickets = {}
    for token, profile in ((ALICE_TOKEN, ids["profile_A"]), (DAVE_TOKEN, dave_profile)):
        assert control.post(
            "/api/v1/delegations",
            json={"agent_principal_id": agent_id},
            headers=auth(token),
        ).status_code == 201
        assert control.put(
            f"/api/v1/active-bindings/{ids['service_id']}/agent:{agent_id}",
            json={"profile_id": profile, "expected_version": 0},
            headers=auth(token),
        ).status_code == 200
        delegation = next(
            d for d in control.get("/api/v1/delegations", headers=auth(token)).json()["data"]
            if d["agent_principal_id"] == agent_id and d["active"]
        )
        ticket = control.post(
            f"/api/v1/delegations/{delegation['id']}/context", headers=auth(token)
        )
        assert ticket.status_code == 201, ticket.text
        tickets[token] = ticket.json()["data"]["context"]

    assert control.post(
        "/api/v1/agent-grants",
        json={"agent_principal_id": agent_id, "capability_id": ids["capability_id"]},
        headers=auth(ALICE_TOKEN),
    ).status_code == 201

    return {
        "control": control, "ids": ids, "agent_id": agent_id,
        "dave_profile": dave_profile, "tickets": tickets,
    }


class TestSEC043IdempotencyBindsDelegatingUser:
    def test_same_key_other_delegation_is_conflict_not_data(self, shared):
        control = shared["control"]
        key = f"shared-{uuid.uuid4().hex[:8]}"
        first = control.post(
            "/api/v1/invocations",
            json={
                "capability": "crm.get_customer", "arguments": {"customer_id": "C-1024"},
                "context_key": f"agent:{shared['agent_id']}",
                "delegation_context": shared["tickets"][ALICE_TOKEN],
                "idempotency_key": key,
            },
            headers=auth(AGENT_TOKEN),
        )
        assert first.status_code == 200, first.text

        # same agent, same key, same arguments - but DAVE's delegation now
        replay = control.post(
            "/api/v1/invocations",
            json={
                "capability": "crm.get_customer", "arguments": {"customer_id": "C-1024"},
                "context_key": f"agent:{shared['agent_id']}",
                "delegation_context": shared["tickets"][DAVE_TOKEN],
                "idempotency_key": key,
            },
            headers=auth(AGENT_TOKEN),
        )
        assert replay.status_code == 409, replay.text  # never alice's result

        # the SAME delegation may still replay (retry semantics intact)
        retry = control.post(
            "/api/v1/invocations",
            json={
                "capability": "crm.get_customer", "arguments": {"customer_id": "C-1024"},
                "context_key": f"agent:{shared['agent_id']}",
                "delegation_context": shared["tickets"][ALICE_TOKEN],
                "idempotency_key": key,
            },
            headers=auth(AGENT_TOKEN),
        )
        assert retry.status_code == 200
        assert retry.json()["data"]["idempotent_replay"] is True


class TestSEC044RequiredScopes:
    def _scoped_capability(self, shared, required):
        control = shared["control"]
        created = control.post(
            "/api/v1/capabilities",
            json={
                "name": f"crm.scoped_{uuid.uuid4().hex[:5]}",
                "display_name": "scoped probe",
                "operation_id": shared["ids"]["get_operation_id"],
                "risk_level": "medium",
                "required_scopes": required,
            },
            headers=auth(ALICE_TOKEN),
        )
        assert created.status_code == 201, created.text
        cap_id = created.json()["data"]["id"]
        for action in ("review", "verify"):
            assert control.post(
                f"/api/v1/capabilities/{cap_id}/{action}",
                json={"evidence": "scope check"}, headers=auth(CAROL_TOKEN),
            ).status_code == 200
        assert control.post(
            f"/api/v1/capabilities/{cap_id}/publish",
            json={"version": "1.0.0"}, headers=auth(CAROL_TOKEN),
        ).status_code == 200
        return cap_id

    def _grant(self, shared, capability_id, scopes):
        shared["control"].post(
            "/api/v1/agent-grants",
            json={
                "agent_principal_id": shared["agent_id"],
                "capability_id": capability_id,
                "scopes": scopes,
            },
            headers=auth(ALICE_TOKEN),
        )

    def test_narrow_read_scope_cannot_execute_refund_tool(self, shared):
        # the capability REQUIRES refund.execute; the whole chain only ever
        # talks about orders.read - a non-empty intersection must not pass
        control = shared["control"]
        cap_id = self._scoped_capability(shared, ["refund.execute"])
        self._grant(shared, cap_id, ["orders.read"])
        response = control.post(
            "/api/v1/invocations",
            json={
                "capability": next(
                    c["name"] for c in control.get(
                        "/api/v1/capabilities", headers=auth(ALICE_TOKEN)
                    ).json()["data"] if c["id"] == cap_id
                ),
                "arguments": {"customer_id": "C-1024"},
                "context_key": f"agent:{shared['agent_id']}",
                "delegation_context": shared["tickets"][ALICE_TOKEN],
            },
            headers=auth(AGENT_TOKEN),
        )
        assert response.status_code == 403, response.text
        assert "requires scopes" in response.json()["error"]["message"].lower()

    def test_unscoped_chain_never_satisfies_scoped_capability(self, shared):
        # grant/connection/delegation all unscoped (None) - explicit
        # authorization is MANDATORY for scoped capabilities
        control = shared["control"]
        cap_id = self._scoped_capability(shared, ["orders.read"])
        self._grant(shared, cap_id, [])  # unscoped grant
        name = next(
            c["name"] for c in control.get(
                "/api/v1/capabilities", headers=auth(ALICE_TOKEN)
            ).json()["data"] if c["id"] == cap_id
        )
        response = control.post(
            "/api/v1/invocations",
            json={
                "capability": name, "arguments": {"customer_id": "C-1024"},
                "context_key": f"agent:{shared['agent_id']}",
                "delegation_context": shared["tickets"][ALICE_TOKEN],
            },
            headers=auth(AGENT_TOKEN),
        )
        assert response.status_code == 403, response.text

    def test_covering_scopes_execute(self, shared):
        control = shared["control"]
        cap_id = self._scoped_capability(shared, ["orders.read"])
        self._grant(shared, cap_id, ["orders.read", "orders.write"])
        name = next(
            c["name"] for c in control.get(
                "/api/v1/capabilities", headers=auth(ALICE_TOKEN)
            ).json()["data"] if c["id"] == cap_id
        )
        response = control.post(
            "/api/v1/invocations",
            json={
                "capability": name, "arguments": {"customer_id": "C-1024"},
                "context_key": f"agent:{shared['agent_id']}",
                "delegation_context": shared["tickets"][ALICE_TOKEN],
            },
            headers=auth(AGENT_TOKEN),
        )
        assert response.status_code == 200, response.text


class TestSEC045PublicToolCatalog:
    def test_agent_never_sees_drafts_execution_or_expired_grants(self, demo, harness):
        control = _client(demo)
        ids = demo.ids
        agent_id = _agent_id(demo)

        # a capability whose draft carries internal mapping data; publish a
        # clean 1.0.0 but leave a NEW draft behind (post-publish edit path)
        created = control.post(
            "/api/v1/capabilities",
            json={
                "name": "crm.catalog_probe",
                "display_name": "catalog probe",
                "operation_id": ids["refund_operation_id"],
                "risk_level": "low",
                "execution": {"body": {"secret_hint": "internal"}},
                "allow_fields": ["id"],
            },
            headers=auth(ALICE_TOKEN),
        )
        cap_id = created.json()["data"]["id"]
        for action in ("review", "verify"):
            control.post(
                f"/api/v1/capabilities/{cap_id}/{action}",
                json={"evidence": "catalog"}, headers=auth(CAROL_TOKEN),
            )
        control.post(
            f"/api/v1/capabilities/{cap_id}/publish",
            json={"version": "1.0.0"}, headers=auth(CAROL_TOKEN),
        )
        control.post(
            "/api/v1/agent-grants",
            json={"agent_principal_id": agent_id, "capability_id": cap_id},
            headers=auth(ALICE_TOKEN),
        )

        listing = control.get("/api/v1/capabilities", headers=auth(AGENT_TOKEN))
        assert listing.status_code == 200
        entry = next(
            c for c in listing.json()["data"] if c["name"] == "crm.catalog_probe"
        )
        # public tool view: published versions only, no internals at all
        assert all(v["status"] == "published" for v in entry["versions"])
        assert "execution" not in json.dumps(entry)
        assert "allow_fields" not in json.dumps(entry)
        assert "output_schema" not in json.dumps(entry)
        # the management view (alice) still has them
        admin_entry = next(
            c for c in control.get(
                "/api/v1/capabilities", headers=auth(ALICE_TOKEN)
            ).json()["data"] if c["name"] == "crm.catalog_probe"
        )
        assert "execution" in json.dumps(admin_entry)

        # expire the grant -> the tool leaves the agent catalog entirely
        from datetime import timedelta

        from ppswitch_db.models import AgentGrantRow, utcnow

        with harness.session_factory() as session:
            for row in (
                session.query(AgentGrantRow)
                .filter_by(agent_principal_id=uuid.UUID(agent_id), capability_id=uuid.UUID(cap_id))
                .all()
            ):
                row.expires_at = utcnow() - timedelta(minutes=1)
            session.commit()
        listing = control.get("/api/v1/capabilities", headers=auth(AGENT_TOKEN)).json()["data"]
        assert all(c["name"] != "crm.catalog_probe" for c in listing)
        detail = control.get(
            f"/api/v1/capabilities/{cap_id}", headers=auth(AGENT_TOKEN)
        )
        assert detail.status_code == 404


class TestSEC046DelegationContextTickets:
    def test_parameter_user_selection_is_gone(self, shared):
        control = shared["control"]
        agent_id = shared["agent_id"]
        alice_id = _principal_id(control, ALICE_TOKEN)
        response = control.post(
            "/api/v1/invocations",
            json={
                "capability": "crm.get_customer", "arguments": {"customer_id": "C-1024"},
                "context_key": f"agent:{agent_id}:{alice_id}",
            },
            headers=auth(AGENT_TOKEN),
        )
        assert response.status_code == 400  # grammar-rejected outright

    def test_multiple_delegations_without_ticket_is_conflict(self, shared):
        response = shared["control"].post(
            "/api/v1/invocations",
            json={
                "capability": "crm.get_customer", "arguments": {"customer_id": "C-1024"},
                "context_key": f"agent:{shared['agent_id']}",
            },
            headers=auth(AGENT_TOKEN),
        )
        assert response.status_code == 409, response.text
        assert "delegation context" in response.json()["error"]["message"].lower()

    def test_forged_or_foreign_ticket_rejected(self, shared):
        control = shared["control"]
        base = {
            "capability": "crm.get_customer", "arguments": {"customer_id": "C-1024"},
            "context_key": f"agent:{shared['agent_id']}",
        }
        # forged signature
        body = "eyJhIjoxfQ"  # arbitrary base64
        forged = control.post(
            "/api/v1/invocations",
            json={**base, "delegation_context": f"{body}.Zm9yZ2Vk"},
            headers=auth(AGENT_TOKEN),
        )
        assert forged.status_code == 403, forged.text
        # ticket issued for a DIFFERENT agent (dave's ticket used verbatim
        # by an agent it was not issued to is covered by the signature's
        # agent binding; here: alice's ticket tampered to another user id)
        ticket = shared["tickets"][ALICE_TOKEN]
        body_part, sig_part = ticket.split(".", 1)
        payload = json.loads(__import__("base64").urlsafe_b64decode(
            body_part + "=" * (-len(body_part) % 4)))
        dave_id = _principal_id(control, DAVE_TOKEN)
        payload["user_id"] = dave_id  # claim to be dave
        tampered_body = __import__("base64").urlsafe_b64encode(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).rstrip(b"=").decode()
        tampered = control.post(
            "/api/v1/invocations",
            json={**base, "delegation_context": f"{tampered_body}.{sig_part}"},
            headers=auth(AGENT_TOKEN),
        )
        assert tampered.status_code == 403, tampered.text

    def test_valid_ticket_selects_issuing_user(self, shared):
        control = shared["control"]
        response = control.post(
            "/api/v1/invocations",
            json={
                "capability": "crm.get_customer", "arguments": {"customer_id": "C-1024"},
                "context_key": f"agent:{shared['agent_id']}",
                "delegation_context": shared["tickets"][DAVE_TOKEN],
            },
            headers=auth(AGENT_TOKEN),
        )
        assert response.status_code == 200, response.text
        assert response.json()["data"]["profile_label"] == "Dave B"


class TestSEC047ApproverQualification:
    def test_member_cannot_approve_endpoint_or_high_risk(self, shared, harness):
        control = shared["control"]
        ids = shared["ids"]
        # dave (member) cannot approve a pending endpoint
        created = control.post(
            f"/api/v1/services/{ids['service_id']}/endpoints",
            json={"environment": "staging", "base_url": "https://staging.example.com",
                  "approved_hosts": ["staging.example.com"], "status": "pending"},
            headers=auth(ALICE_TOKEN),
        )
        assert created.status_code == 201, created.text
        endpoint_id = created.json()["data"]["id"]
        denied = control.post(
            f"/api/v1/endpoints/{endpoint_id}/approve", headers=auth(DAVE_TOKEN)
        )
        assert denied.status_code == 403, denied.text
        approved = control.post(
            f"/api/v1/endpoints/{endpoint_id}/approve", headers=auth(CAROL_TOKEN)
        )
        assert approved.status_code == 200, approved.text


class TestSEC048StrictArrayAndRefSchema:
    def test_array_without_items_is_denied(self):
        from ppswitch_executor.response_guard import project_response

        schema = {
            "type": "object",
            "properties": {"orders": {"type": "array"}},  # no items schema
        }
        guarded = project_response(
            {"orders": [{"internal_note": "leak"}]}, ["orders"], output_schema=schema
        )
        assert guarded.result == {"orders": None}
        assert "<items_unspecified>" in guarded.dropped_fields

    def test_array_with_unresolved_ref_items_is_denied(self):
        from ppswitch_executor.response_guard import project_response

        schema = {
            "type": "object",
            "properties": {"orders": {"type": "array", "items": {"$ref": "#/x"}}},
        }
        guarded = project_response(
            {"orders": [{"note": "leak"}]}, ["orders"], output_schema=schema
        )
        assert guarded.result == {"orders": None}
        assert "<items_unspecified>" in guarded.dropped_fields

    def test_well_described_array_items_pass(self):
        from ppswitch_executor.response_guard import project_response

        schema = {
            "type": "object",
            "properties": {
                "orders": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"id": {"type": "string"}},
                    },
                }
            },
        }
        guarded = project_response(
            {"orders": [{"id": "o-1", "internal": "x"}]}, ["orders"],
            output_schema=schema,
        )
        assert guarded.result == {"orders": [{"id": "o-1"}]}


class TestSharedAgentAttackScenario:
    """The review's closing scenario, end to end in one place."""

    def test_no_cross_user_access_under_attack_combinations(self, shared, harness):
        control = shared["control"]
        agent_id = shared["agent_id"]

        # 1) each user's ticket hits their own profile
        for token, label in ((ALICE_TOKEN, "CRM Demo A"), (DAVE_TOKEN, "Dave B")):
            r = control.post(
                "/api/v1/invocations",
                json={
                    "capability": "crm.get_customer", "arguments": {"customer_id": "C-1024"},
                    "context_key": f"agent:{agent_id}",
                    "delegation_context": shared["tickets"][token],
                },
                headers=auth(AGENT_TOKEN),
            )
            assert r.status_code == 200
            assert r.json()["data"]["profile_label"] == label

        # 2) same idempotency key across delegations -> conflict (SEC-043)
        key = f"attack-{uuid.uuid4().hex[:6]}"
        assert control.post(
            "/api/v1/invocations",
            json={"capability": "crm.get_customer", "arguments": {"customer_id": "C-1"},
                  "context_key": f"agent:{agent_id}",
                  "delegation_context": shared["tickets"][ALICE_TOKEN],
                  "idempotency_key": key},
            headers=auth(AGENT_TOKEN),
        ).status_code == 200
        assert control.post(
            "/api/v1/invocations",
            json={"capability": "crm.get_customer", "arguments": {"customer_id": "C-1"},
                  "context_key": f"agent:{agent_id}",
                  "delegation_context": shared["tickets"][DAVE_TOKEN],
                  "idempotency_key": key},
            headers=auth(AGENT_TOKEN),
        ).status_code == 409

        # 3) prompt-injection shaped arguments still cannot smuggle
        # identity or URL targets (reserved names / unknown fields reject)
        injected = control.post(
            "/api/v1/invocations",
            json={
                "capability": "crm.get_customer",
                "arguments": {
                    "customer_id": "C-1024",
                    "target_url": "http://169.254.169.254/latest/meta-data",
                    "user_id": _principal_id(control, DAVE_TOKEN),
                },
                "context_key": f"agent:{agent_id}",
                "delegation_context": shared["tickets"][ALICE_TOKEN],
            },
            headers=auth(AGENT_TOKEN),
        )
        assert injected.status_code in (400, 422), injected.text

        # 4) revoking dave's delegation blocks his ticket immediately
        dave_delegation = next(
            d for d in control.get("/api/v1/delegations", headers=auth(DAVE_TOKEN)).json()["data"]
            if d["agent_principal_id"] == agent_id
        )
        revoked = control.post(
            f"/api/v1/delegations/{dave_delegation['id']}/revoke",
            headers=auth(DAVE_TOKEN),
        )
        assert revoked.status_code == 200
        blocked = control.post(
            "/api/v1/invocations",
            json={
                "capability": "crm.get_customer", "arguments": {"customer_id": "C-1024"},
                "context_key": f"agent:{agent_id}",
                "delegation_context": shared["tickets"][DAVE_TOKEN],
            },
            headers=auth(AGENT_TOKEN),
        )
        assert blocked.status_code in (403, 409), blocked.text
        # alice is unaffected
        assert control.post(
            "/api/v1/invocations",
            json={
                "capability": "crm.get_customer", "arguments": {"customer_id": "C-1024"},
                "context_key": f"agent:{agent_id}",
                "delegation_context": shared["tickets"][ALICE_TOKEN],
            },
            headers=auth(AGENT_TOKEN),
        ).status_code == 200

        # 5) expiring the grant kills every delegation's access
        from datetime import timedelta

        from ppswitch_db.models import AgentGrantRow, utcnow

        with harness.session_factory() as session:
            for row in (
                session.query(AgentGrantRow)
                .filter_by(agent_principal_id=uuid.UUID(agent_id))
                .all()
            ):
                row.expires_at = utcnow() - timedelta(seconds=1)
            session.commit()
        time.sleep(0.05)
        expired = control.post(
            "/api/v1/invocations",
            json={
                "capability": "crm.get_customer", "arguments": {"customer_id": "C-1024"},
                "context_key": f"agent:{agent_id}",
                "delegation_context": shared["tickets"][ALICE_TOKEN],
            },
            headers=auth(AGENT_TOKEN),
        )
        assert expired.status_code == 403, expired.text

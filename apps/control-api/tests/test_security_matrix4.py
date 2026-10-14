"""Review4 security regression: SEC-049 .. SEC-051 + the revocation
invariant the review converged on:

    "For an authorization that has been revoked or narrowed, the data can
    never be re-obtained via a NEW invocation, the IDEMPOTENCY CACHE, the
    HISTORY endpoints, or an output-schema FALLBACK path."
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from .conftest import AGENT_TOKEN, ALICE_TOKEN, Demo, auth

DAVE_TOKEN = "dev-token-dave-5c1a"


def _client(demo: Demo) -> TestClient:
    return demo.harness.control


def _agent_id(demo: Demo) -> str:
    listing = _client(demo).get("/api/v1/dev/principals").json()["data"]
    return next(p["id"] for p in listing if p["kind"] == "agent")


def _principal_id(control: TestClient, token: str) -> str:
    listing = control.get("/api/v1/dev/principals").json()["data"]
    return next(p["id"] for p in listing if p["token_hint"].startswith(token[:14]))


def _issue_ticket(control, token, agent_id):
    delegation = next(
        d for d in control.get("/api/v1/delegations", headers=auth(token)).json()["data"]
        if d["agent_principal_id"] == agent_id and d["active"]
    )
    ticket = control.post(
        f"/api/v1/delegations/{delegation['id']}/context", headers=auth(token)
    )
    assert ticket.status_code == 201, ticket.text
    return delegation["id"], ticket.json()["data"]["context"]




class TestSEC049ReplayRespectsScopeNarrowing:
    def test_narrowed_delegation_blocks_both_new_calls_and_replay(self, scoped_shared):
        control = scoped_shared["control"]
        key = f"scope-{uuid.uuid4().hex[:8]}"
        base = {
            "capability": scoped_shared["capability"],
            "arguments": {"customer_id": "C-1024"},
            "context_key": f"agent:{scoped_shared['agent_id']}",
            "delegation_context": scoped_shared["alice_ticket"],
        }
        first = control.post(
            "/api/v1/invocations", json={**base, "idempotency_key": key},
            headers=auth(AGENT_TOKEN),
        )
        assert first.status_code == 200, first.text

        # alice narrows the SAME delegation to orders.note (still active)
        assert control.post(
            "/api/v1/delegations",
            json={
                "agent_principal_id": scoped_shared["agent_id"],
                "scopes": ["orders.note"],
            },
            headers=auth(ALICE_TOKEN),
        ).status_code == 201
        _, fresh_ticket = _issue_ticket(
            control, ALICE_TOKEN, scoped_shared["agent_id"]
        )

        # a NEW call is denied (SEC-044) ...
        fresh = control.post(
            "/api/v1/invocations", json={**base, "delegation_context": fresh_ticket},
            headers=auth(AGENT_TOKEN),
        )
        assert fresh.status_code == 403, fresh.text
        # ... and the cached result is no longer reachable either (SEC-049)
        replay = control.post(
            "/api/v1/invocations",
            json={**base, "delegation_context": fresh_ticket, "idempotency_key": key},
            headers=auth(AGENT_TOKEN),
        )
        assert replay.status_code == 403, replay.text
        assert "no longer authorized" in replay.json()["error"]["message"].lower()

    def test_revoked_connection_blocks_replay(self, scoped_shared, harness):
        control = scoped_shared["control"]
        key = f"conn-{uuid.uuid4().hex[:8]}"
        base = {
            "capability": scoped_shared["capability"],
            "arguments": {"customer_id": "C-1024"},
            "context_key": f"agent:{scoped_shared['agent_id']}",
            "delegation_context": scoped_shared["alice_ticket"],
        }
        assert control.post(
            "/api/v1/invocations", json={**base, "idempotency_key": key},
            headers=auth(AGENT_TOKEN),
        ).status_code == 200

        from ppswitch_db.models import ConnectionRow

        with harness.session_factory() as session:
            connection = session.get(
                ConnectionRow, uuid.UUID(_connection_id(control, scoped_shared))
            )
            connection.status = "revoked"
            session.commit()

        replay = control.post(
            "/api/v1/invocations", json={**base, "idempotency_key": key},
            headers=auth(AGENT_TOKEN),
        )
        assert replay.status_code == 403, replay.text


def _connection_id(control, scoped_shared) -> str:
    # the binding's profile -> connection (demo A connection of alice)
    bindings = control.get(
        "/api/v1/active-bindings", headers=auth(ALICE_TOKEN)
    ).json()["data"]
    profile_id = next(
        b["active_profile_id"] for b in bindings
        if b["context_key"] == f"agent:{scoped_shared['agent_id']}"
    )
    profiles = control.get(
        f"/api/v1/profiles?service_id={scoped_shared['ids']['service_id']}",
        headers=auth(ALICE_TOKEN),
    ).json()["data"]
    return next(p["connection_id"] for p in profiles if p["id"] == profile_id)


class TestSEC050HistoryScopedToDelegatingUser:
    def test_agent_history_requires_ticket_and_filters_by_user(
        self, scoped_shared
    ):
        control = scoped_shared["control"]
        agent_id = scoped_shared["agent_id"]
        # dave: own connection/profile/binding + delegation (unscoped)
        created = control.post(
            "/api/v1/connections",
            json={"service_id": scoped_shared["ids"]["service_id"],
                  "auth_scheme": "api_key", "demo_dataset": "B"},
            headers=auth(DAVE_TOKEN),
        )
        dave_connection = created.json()["data"]["id"]
        created = control.post(
            "/api/v1/profiles",
            json={
                "service_id": scoped_shared["ids"]["service_id"],
                "endpoint_id": scoped_shared["ids"]["endpoint_id"],
                "connection_id": dave_connection, "name": "Dave B",
            },
            headers=auth(DAVE_TOKEN),
        )
        assert control.post(
            "/api/v1/delegations",
            json={"agent_principal_id": agent_id, "scopes": ["orders.read"]},
            headers=auth(DAVE_TOKEN),
        ).status_code == 201
        assert control.put(
            f"/api/v1/active-bindings/{scoped_shared['ids']['service_id']}/agent:{agent_id}",
            json={"profile_id": created.json()["data"]["id"], "expected_version": 0},
            headers=auth(DAVE_TOKEN),
        ).status_code == 200
        _, dave_ticket = _issue_ticket(control, DAVE_TOKEN, agent_id)

        # one invocation under each delegating user
        for ticket in (scoped_shared["alice_ticket"], dave_ticket):
            response = control.post(
                "/api/v1/invocations",
                json={
                    "capability": scoped_shared["capability"],
                    "arguments": {"customer_id": "C-1024"},
                    "context_key": f"agent:{agent_id}",
                    "delegation_context": ticket,
                },
                headers=auth(AGENT_TOKEN),
            )
            assert response.status_code == 200, response.text

        # no ticket -> 403 (the agent credential alone reads nothing)
        assert control.get(
            "/api/v1/invocations", headers=auth(AGENT_TOKEN)
        ).status_code == 403

        # alice's ticket sees ONLY alice's records; dave's record is 404
        alice_history = control.get(
            "/api/v1/invocations",
            headers={**auth(AGENT_TOKEN), "X-Delegation-Context": scoped_shared["alice_ticket"]},
        ).json()["data"]
        assert len(alice_history) == 1
        dave_history = control.get(
            "/api/v1/invocations",
            headers={**auth(AGENT_TOKEN), "X-Delegation-Context": dave_ticket},
        ).json()["data"]
        assert len(dave_history) == 1
        assert dave_history[0]["id"] != alice_history[0]["id"]

        cross = control.get(
            f"/api/v1/invocations/{dave_history[0]['id']}",
            headers={**auth(AGENT_TOKEN), "X-Delegation-Context": scoped_shared["alice_ticket"]},
        )
        assert cross.status_code == 404  # other user's record: invisible
        own = control.get(
            f"/api/v1/invocations/{alice_history[0]['id']}",
            headers={**auth(AGENT_TOKEN), "X-Delegation-Context": scoped_shared["alice_ticket"]},
        )
        assert own.status_code == 200
        assert own.json()["data"]["result"] is not None

    def test_revoked_delegation_ticket_cannot_read_history(
        self, scoped_shared
    ):
        control = scoped_shared["control"]
        agent_id = scoped_shared["agent_id"]
        assert control.get(
            "/api/v1/invocations",
            headers={**auth(AGENT_TOKEN), "X-Delegation-Context": scoped_shared["alice_ticket"]},
        ).status_code == 200  # still active

        delegation = next(
            d for d in control.get("/api/v1/delegations", headers=auth(ALICE_TOKEN)).json()["data"]
            if d["agent_principal_id"] == agent_id and d["active"]
        )
        assert control.post(
            f"/api/v1/delegations/{delegation['id']}/revoke", headers=auth(ALICE_TOKEN)
        ).status_code == 200

        # ticket signature still valid, delegation revoked -> history denied
        denied = control.get(
            "/api/v1/invocations",
            headers={**auth(AGENT_TOKEN), "X-Delegation-Context": scoped_shared["alice_ticket"]},
        )
        assert denied.status_code == 403, denied.text


class TestSEC051RootRefSchemaRejected:
    def test_root_ref_schema_raises_instead_of_falling_back(self):
        from ppswitch_core.errors import ApiError
        from ppswitch_executor.response_guard import project_response

        with pytest.raises(ApiError) as excinfo:
            project_response(
                {"customer_id": "C-1", "internal_note": "secret-ish"},
                ["customer_id"],
                output_schema={"$ref": "#/components/schemas/Customer"},
            )
        assert "unresolved reference" in str(excinfo.value.message).lower()

    def test_untyped_object_schema_keeps_allowlist_compatibility_path(self):
        # {"type": "object"} without properties stays on the explicit
        # compatibility path: top-level allow_fields whitelist + scrub
        from ppswitch_executor.response_guard import project_response

        guarded = project_response(
            {"id": "1", "session_id": "x", "notes": "fine"},
            ["id", "notes"],
            output_schema={"type": "object"},
        )
        assert guarded.result == {"id": "1", "notes": "fine"}


class TestRevocationInvariant:
    """The review's closing invariant, one flow, four paths."""

    def test_narrowed_authorization_never_re_exposes_data(
        self, scoped_shared
    ):
        control = scoped_shared["control"]
        state = scoped_shared
        key = f"inv-{uuid.uuid4().hex[:8]}"
        base = {
            "capability": state["capability"],
            "arguments": {"customer_id": "C-1024"},
            "context_key": f"agent:{state['agent_id']}",
            "delegation_context": state["alice_ticket"],
        }
        assert control.post(
            "/api/v1/invocations", json={**base, "idempotency_key": key},
            headers=auth(AGENT_TOKEN),
        ).status_code == 200

        # narrow alice's delegation to an unrelated scope
        control.post(
            "/api/v1/delegations",
            json={"agent_principal_id": state["agent_id"], "scopes": ["orders.note"]},
            headers=auth(ALICE_TOKEN),
        )
        _, fresh = _issue_ticket(control, ALICE_TOKEN, state["agent_id"])

        # path 1: a fresh invocation is denied
        assert control.post(
            "/api/v1/invocations", json={**base, "delegation_context": fresh},
            headers=auth(AGENT_TOKEN),
        ).status_code == 403
        # path 2: the idempotency cache no longer serves the old result
        assert control.post(
            "/api/v1/invocations",
            json={**base, "delegation_context": fresh, "idempotency_key": key},
            headers=auth(AGENT_TOKEN),
        ).status_code == 403

        # path 3: history reads require an active, in-scope delegation and
        # are filtered to the acting user (dave cannot read alice's record
        # even with his own perfectly valid ticket)
        # (covered end-to-end by TestSEC050; here: revoked flow)
        delegation = next(
            d for d in control.get(
                "/api/v1/delegations", headers=auth(ALICE_TOKEN)
            ).json()["data"] if d["agent_principal_id"] == state["agent_id"]
        )
        control.post(
            f"/api/v1/delegations/{delegation['id']}/revoke", headers=auth(ALICE_TOKEN)
        )
        assert control.get(
            "/api/v1/invocations",
            headers={**auth(AGENT_TOKEN), "X-Delegation-Context": fresh},
        ).status_code == 403

        # path 4: the schema fallback route is closed at the guard itself
        from ppswitch_core.errors import ApiError
        from ppswitch_executor.response_guard import project_response

        with pytest.raises(ApiError):
            project_response(
                {"secret_leak": "x"}, ["secret_leak"],
                output_schema={"$ref": "#/x"},
            )

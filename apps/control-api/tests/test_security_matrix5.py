"""Review5 security regression: SEC-052 (history re-authorization),
SEC-053 (delegation generations) and PR-DATA-12 result lifecycle
(classification, retention, encryption-at-rest, purge).
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from .conftest import (
    AGENT_TOKEN,
    ALICE_TOKEN,
    CAROL_TOKEN,
    Demo,
    _issue_ticket,
    auth,
)

DAVE_TOKEN = "dev-token-dave-5c1a"


def _client(demo: Demo) -> TestClient:
    return demo.harness.control


class TestSEC052HistoryRespectsScopeNarrowing:
    """A NARROWED (still active) delegation must not re-read history."""

    def test_narrowed_scope_filters_history_without_revocation(self, scoped_shared):
        control = scoped_shared["control"]
        state = scoped_shared
        # produce one record under the full orders.read delegation
        first = control.post(
            "/api/v1/invocations",
            json={
                "capability": state["capability"],
                "arguments": {"customer_id": "C-1024"},
                "context_key": f"agent:{state['agent_id']}",
                "delegation_context": state["alice_ticket"],
            },
            headers=auth(AGENT_TOKEN),
        )
        assert first.status_code == 200, first.text
        record_id = first.json()["data"]["invocation_id"]

        # NARROW the delegation - it stays ACTIVE (no revocation here)
        assert control.post(
            "/api/v1/delegations",
            json={"agent_principal_id": state["agent_id"], "scopes": ["orders.note"]},
            headers=auth(ALICE_TOKEN),
        ).status_code == 201
        _, narrowed_ticket = _issue_ticket(control, ALICE_TOKEN, state["agent_id"])

        # the invariant test from round 4 stopped here and revoked; THIS
        # test keeps the delegation active: history must still disappear
        history = control.get(
            "/api/v1/invocations",
            headers={**auth(AGENT_TOKEN), "X-Delegation-Context": narrowed_ticket},
        )
        assert history.status_code == 200
        assert history.json()["data"] == []  # record no longer authorized

        detail = control.get(
            f"/api/v1/invocations/{record_id}",
            headers={**auth(AGENT_TOKEN), "X-Delegation-Context": narrowed_ticket},
        )
        assert detail.status_code == 404  # indistinguishable from missing

        # ownership stays with the EXECUTING principal (the agent,
        # SEC-029): the delegating user never reads agent records through
        # the user-scoped path - only via a delegation context
        user_detail = control.get(
            f"/api/v1/invocations/{record_id}", headers=auth(ALICE_TOKEN)
        )
        assert user_detail.status_code == 404


class TestSEC053DelegationGenerations:
    """Re-delegation after revocation must not revive old tickets."""

    def test_redelegate_kills_old_generation_tickets(self, scoped_shared):
        control = scoped_shared["control"]
        state = scoped_shared
        old_ticket = state["alice_ticket"]

        # invoke fine with the current generation
        assert control.post(
            "/api/v1/invocations",
            json={
                "capability": state["capability"],
                "arguments": {"customer_id": "C-1024"},
                "context_key": f"agent:{state['agent_id']}",
                "delegation_context": old_ticket,
            },
            headers=auth(AGENT_TOKEN),
        ).status_code == 200

        # revoke, then re-delegate with the same scopes
        delegation = next(
            d for d in control.get(
                "/api/v1/delegations", headers=auth(ALICE_TOKEN)
            ).json()["data"] if d["agent_principal_id"] == state["agent_id"]
        )
        assert control.post(
            f"/api/v1/delegations/{delegation['id']}/revoke",
            headers=auth(ALICE_TOKEN),
        ).status_code == 200
        assert control.post(
            "/api/v1/delegations",
            json={"agent_principal_id": state["agent_id"], "scopes": ["orders.read"]},
            headers=auth(ALICE_TOKEN),
        ).status_code == 201
        _, new_ticket = _issue_ticket(control, ALICE_TOKEN, state["agent_id"])

        # the OLD ticket (same delegation id, still inside its 10-minute
        # validity window, cryptographically intact) must now fail
        stale = control.post(
            "/api/v1/invocations",
            json={
                "capability": state["capability"],
                "arguments": {"customer_id": "C-1024"},
                "context_key": f"agent:{state['agent_id']}",
                "delegation_context": old_ticket,
            },
            headers=auth(AGENT_TOKEN),
        )
        assert stale.status_code == 403, stale.text
        assert "generation" in stale.json()["error"]["message"]

        # the new-generation ticket works normally
        fresh = control.post(
            "/api/v1/invocations",
            json={
                "capability": state["capability"],
                "arguments": {"customer_id": "C-1024"},
                "context_key": f"agent:{state['agent_id']}",
                "delegation_context": new_ticket,
            },
            headers=auth(AGENT_TOKEN),
        )
        assert fresh.status_code == 200, fresh.text

        # and old tickets cannot read history either
        assert control.get(
            "/api/v1/invocations",
            headers={**auth(AGENT_TOKEN), "X-Delegation-Context": old_ticket},
        ).status_code == 403


class TestPRDATA12ResultLifecycle:
    """Classification, metadata-only storage, retention and purge."""

    def _critical_capability(self, control, demo):
        ids = demo.ids
        created = control.post(
            "/api/v1/capabilities",
            json={
                "name": "crm.wipe_account",
                "display_name": "Wipe account (critical demo)",
                "operation_id": ids["refund_operation_id"],
                "risk_level": "critical",
            },
            headers=auth(ALICE_TOKEN),
        )
        assert created.status_code == 201, created.text
        cap_id = created.json()["data"]["id"]
        for action in ("review", "verify"):
            assert control.post(
                f"/api/v1/capabilities/{cap_id}/{action}",
                json={"evidence": "data class"}, headers=auth(CAROL_TOKEN),
            ).status_code == 200
        assert control.post(
            f"/api/v1/capabilities/{cap_id}/publish",
            json={"version": "1.0.0"}, headers=auth(CAROL_TOKEN),
        ).status_code == 200
        return cap_id

    def test_critical_results_are_metadata_only_by_default(self, demo):
        control = _client(demo)
        ids = demo.ids
        self._critical_capability(control, demo)

        # users invoke critical capabilities through the approval flow
        # (the demo bootstrap already activated alice's personal binding)
        bindings = control.get(
            "/api/v1/active-bindings", headers=auth(ALICE_TOKEN)
        ).json()["data"]
        if not any(
            b["service_id"] == ids["service_id"] and b["context_key"] == "personal"
            for b in bindings
        ):
            assert control.put(
                f"/api/v1/active-bindings/{ids['service_id']}/personal",
                json={"profile_id": ids["profile_A"], "expected_version": 0},
                headers=auth(ALICE_TOKEN),
            ).status_code == 200
        blocked = control.post(
            "/api/v1/invocations",
            json={"capability": "crm.wipe_account", "arguments": {"customer_id": "C-1"}},
            headers=auth(ALICE_TOKEN),
        )
        assert blocked.status_code == 409  # APPROVAL_REQUIRED
        approval_id = blocked.json()["error"]["details"]["approval_request_id"]
        assert control.post(
            f"/api/v1/approvals/{approval_id}/approve", headers=auth(CAROL_TOKEN)
        ).status_code == 200
        executed = control.post(
            "/api/v1/invocations",
            json={
                "capability": "crm.wipe_account", "arguments": {"customer_id": "C-1"},
                "approval_request_id": approval_id,
            },
            headers=auth(ALICE_TOKEN),
        )
        assert executed.status_code == 200, executed.text
        assert executed.json()["data"]["result"] is not None  # caller gets it once

        # ... but nothing restricted persists
        invocation_id = executed.json()["data"]["invocation_id"]
        detail = control.get(
            f"/api/v1/invocations/{invocation_id}", headers=auth(ALICE_TOKEN)
        ).json()["data"]
        assert detail["data_class"] == "restricted"
        assert detail["result"] is None  # metadata-only

    def test_expired_results_read_as_none_and_purge_wipes(self, demo, harness):
        control = _client(demo)
        executed = control.post(
            "/api/v1/invocations",
            json={"capability": "crm.get_customer", "arguments": {"customer_id": "C-1024"}},
            headers=auth(ALICE_TOKEN),
        )
        assert executed.status_code == 200, executed.text
        invocation_id = executed.json()["data"]["invocation_id"]

        from datetime import timedelta

        from ppswitch_db.models import InvocationRow, utcnow

        with harness.session_factory() as session:
            row = session.get(InvocationRow, uuid.UUID(invocation_id))
            assert row.result is not None  # stored (normal class)
            row.result_expires_at = utcnow() - timedelta(minutes=1)
            session.commit()

        detail = control.get(
            f"/api/v1/invocations/{invocation_id}", headers=auth(ALICE_TOKEN)
        ).json()["data"]
        assert detail["result"] is None  # past retention: unreadable

        assert control.post(
            "/api/v1/admin/purge-results", headers=auth(ALICE_TOKEN)
        ).status_code == 200
        with harness.session_factory() as session:
            row = session.get(InvocationRow, uuid.UUID(invocation_id))
            assert row.result is None and row.result_encrypted is None  # wiped

    def test_encrypted_at_rest_roundtrip(self, demo, harness):
        from cryptography.fernet import Fernet
        from ppswitch_control.result_vault import ResultVault
        from ppswitch_control.services.result_read import stored_result
        from ppswitch_control.settings import ControlSettings
        from ppswitch_db.models import InvocationRow

        key = Fernet.generate_key().decode()
        vault = ResultVault(key)
        settings = ControlSettings(
            db_url=demo.harness.db_url, demo_mode=True, result_encryption_key=key,
        )

        row = InvocationRow(
            id=uuid.uuid4(), workspace_id=uuid.uuid4(),
            capability_version_id=uuid.uuid4(), capability_name="x.y",
            capability_version="1.0.0", principal_id=uuid.uuid4(),
            profile_id=uuid.uuid4(), profile_snapshot={}, arguments_digest="d",
            status="succeeded",
            result_encrypted=vault.encrypt({"id": "C-9", "note": "plain"}),
        )
        assert vault.encrypts
        assert "C-9" not in row.result_encrypted  # nothing plaintext at rest
        assert stored_result(row, settings) == {"id": "C-9", "note": "plain"}
        # a wrong key yields nothing rather than leaking
        wrong = ResultVault(Fernet.generate_key().decode())
        assert wrong.decrypt(row.result_encrypted) is None


class TestPRNET13CompositionKeywords:
    """allOf/oneOf/anyOf/not are uninterpretable until the bounded
    resolver lands (PR-NET-13): they fail closed everywhere."""

    def test_composition_in_items_denied(self):
        from ppswitch_executor.response_guard import project_response

        schema = {
            "type": "object",
            "properties": {
                "orders": {"type": "array", "items": {"allOf": [{"type": "object"}]}},
            },
        }
        guarded = project_response(
            {"orders": [{"note": "leak"}]}, ["orders"], output_schema=schema
        )
        assert guarded.result == {"orders": None}
        assert "<items_unspecified>" in guarded.dropped_fields

    def test_root_composition_denied(self):
        from ppswitch_core.errors import ApiError
        from ppswitch_executor.response_guard import project_response

        with pytest.raises(ApiError):
            project_response(
                {"a": "x"}, ["a"],
                output_schema={"oneOf": [{"type": "object"}]},
            )

    def test_nested_composition_node_denied(self):
        from ppswitch_executor.response_guard import project_response

        schema = {
            "type": "object",
            "properties": {
                "inner": {"anyOf": [{"type": "object"}], "type": "object"},
            },
        }
        guarded = project_response(
            {"inner": {"secret": "leak"}}, ["inner"], output_schema=schema
        )
        assert guarded.result == {"inner": None}

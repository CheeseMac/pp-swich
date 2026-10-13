"""Acceptance scenarios 1-10 from docs/00-START-HERE.md section 3."""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import text

AGENT_TOKEN = "dev-token-agent-helper-c2d5"
ALICE_TOKEN = "dev-token-alice-4f81"
BOB_TOKEN = "dev-token-bob-77e0"
CAROL_TOKEN = "dev-token-carol-91b3"


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


class TestScenario1UnsanitizedHar:
    def test_har_with_raw_credentials_is_rejected_and_not_stored(self, demo) -> None:
        ids = demo.ids
        malicious = {
            "log": {"entries": [{
                "request": {
                    "method": "GET",
                    "url": "https://crm.example.test/api/leads/7",
                    "headers": [{"name": "Authorization",
                                 "value": "Bearer CANARY_NOT_REAL_ABC123"}],
                    "queryString": [],
                    "cookies": [],
                },
                "response": {"status": 200, "headers": [],
                             "content": {"mimeType": "application/json", "text": "{}"}},
            }]}
        }
        response = demo.harness.control.post(
            "/api/v1/discoveries/imports",
            files={"file": ("leads.har.json", json.dumps(malicious).encode(), "application/json")},
            data={"source_type": "har", "service_id": ids["service_id"]},
            headers=auth(ALICE_TOKEN),
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "UNSANITIZED_INPUT"
        # the canary value itself never appears anywhere in the response
        assert "CANARY_NOT_REAL_ABC123" not in response.text

        with demo.harness.session_factory() as session:
            blob = session.execute(
                text("SELECT group_concat(normalized_shape) FROM observations")
            ).scalar() or ""
            assert "CANARY" not in blob


class TestScenario2DedupMerge:
    def test_three_customer_ids_merged_into_one_operation(self, demo) -> None:
        ids = demo.ids
        response = demo.harness.control.get(
            f"/api/v1/operations?service_id={ids['service_id']}", headers=auth(ALICE_TOKEN)
        )
        operations = response.json()["data"]
        get_ops = [o for o in operations if o["method"] == "GET"]
        assert len(get_ops) == 1
        assert get_ops[0]["normalized_path"] == "/api/customers/{id}"


class TestScenario3PublishGate:
    def test_unreviewed_capability_cannot_be_published(self, demo) -> None:
        ids = demo.ids
        # a fresh draft capability from the refund operation
        response = demo.harness.control.post(
            "/api/v1/capabilities",
            json={
                "name": "crm.refund_customer",
                "display_name": "Refund customer",
                "operation_id": ids["refund_operation_id"],
                "input_schema": {
                    "type": "object", "additionalProperties": False,
                    "required": ["customer_id"],
                    "properties": {"customer_id": {"type": "string"}},
                },
                "output_schema": {"type": "object"},
                "allow_fields": ["refund_id", "status", "amount"],
                "risk_level": "high",
                "execution": {"body": {"amount": "amount"}},
            },
            headers=auth(ALICE_TOKEN),
        )
        assert response.status_code == 201
        draft_id = response.json()["data"]["id"]

        # PR-SEC-01: the creator cannot publish their own capability at all
        response = demo.harness.control.post(
            f"/api/v1/capabilities/{draft_id}/publish", json={"version": "1.0.0"},
            headers=auth(ALICE_TOKEN),
        )
        assert response.status_code == 403
        # a different reviewer on an unverified capability still gets 409
        response = demo.harness.control.post(
            f"/api/v1/capabilities/{draft_id}/publish", json={"version": "1.0.0"},
            headers=auth(CAROL_TOKEN),
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "CAPABILITY_NOT_REVIEWED"


class TestScenario4UnpublishedNotCallable:
    def test_invoking_unpublished_capability_returns_404(self, demo) -> None:
        ids = demo.ids
        # draft capability (never published) must be invisible at invoke time
        response = demo.harness.control.post(
            "/api/v1/capabilities",
            json={
                "name": "crm.internal_draft",
                "display_name": "Draft",
                "operation_id": ids["get_operation_id"],
                "input_schema": {"type": "object"},
                "output_schema": {"type": "object"},
                "allow_fields": ["id"],
            },
            headers=auth(ALICE_TOKEN),
        )
        assert response.status_code == 201
        draft_id = response.json()["data"]["id"]
        del draft_id  # not used further: invoking by NAME must 404

        response = demo.harness.control.post(
            "/api/v1/invocations",
            json={"capability": "crm.internal_draft",
                  "arguments": {"customer_id": "C-1024"}},
            headers=auth(ALICE_TOKEN),
        )
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "CAPABILITY_NOT_FOUND"


class TestScenario5ProfileOwnership:
    def test_other_user_cannot_activate_alices_profile(self, demo) -> None:
        ids = demo.ids
        # carol shares alice's workspace but owns nothing
        response = demo.harness.control.put(
            f"/api/v1/active-bindings/{ids['service_id']}/personal",
            json={"profile_id": ids["profile_A"], "expected_version": 99},
            headers=auth(CAROL_TOKEN),
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "PROFILE_NOT_ALLOWED"

    def test_cross_workspace_profile_returns_404(self, demo) -> None:
        ids = demo.ids
        response = demo.harness.control.put(
            f"/api/v1/active-bindings/{ids['service_id']}/personal",
            json={"profile_id": ids["profile_A"], "expected_version": 0},
            headers=auth(BOB_TOKEN),
        )
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "PROFILE_NOT_FOUND"


class TestScenario6ProfileSwitch:
    def test_switch_changes_profile_for_subsequent_calls(self, demo) -> None:
        ids = demo.ids

        def invoke() -> dict:
            response = demo.harness.control.post(
                "/api/v1/invocations",
                json={"capability": "crm.get_customer",
                      "arguments": {"customer_id": "C-1024"}},
                headers=auth(ALICE_TOKEN),
            )
            assert response.status_code == 200, response.text
            return response.json()["data"]

        first = invoke()
        assert first["profile_label"] == "CRM Demo A"

        response = demo.harness.control.put(
            f"/api/v1/active-bindings/{ids['service_id']}/personal",
            json={"profile_id": ids["profile_B"], "expected_version": first and 1},
            headers=auth(ALICE_TOKEN),
        )
        assert response.status_code == 200

        second = invoke()
        assert second["profile_label"] == "CRM Demo B"

    def test_stale_expected_version_conflicts(self, demo) -> None:
        ids = demo.ids
        # bootstrap created the binding at version 1
        response = demo.harness.control.put(
            f"/api/v1/active-bindings/{ids['service_id']}/personal",
            json={"profile_id": ids["profile_B"], "expected_version": 1},
            headers=auth(ALICE_TOKEN),
        )
        assert response.status_code == 200
        # replay the now-stale expected_version: someone switched first
        response = demo.harness.control.put(
            f"/api/v1/active-bindings/{ids['service_id']}/personal",
            json={"profile_id": ids["profile_A"], "expected_version": 1},
            headers=auth(ALICE_TOKEN),
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "PROFILE_VERSION_CONFLICT"


class TestScenario7ReservedArguments:
    @pytest.mark.parametrize("reserved", [
        {"Authorization": "Bearer x"},
        {"target_url": "http://169.254.169.254/latest/meta-data"},
        {"url": "http://internal"},
        {"token": "abc"},
        {"user_id": "someone-else"},
        {"credential_ref": "secret://x"},
    ])
    def test_reserved_argument_names_rejected(self, demo, reserved) -> None:
        arguments = {"customer_id": "C-1024", **reserved}
        response = demo.harness.control.post(
            "/api/v1/invocations",
            json={"capability": "crm.get_customer", "arguments": arguments},
            headers=auth(ALICE_TOKEN),
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "RESERVED_ARGUMENT"


class TestScenario8OutputGuard:
    def test_only_allowlisted_fields_returned(self, demo) -> None:
        response = demo.harness.control.post(
            "/api/v1/invocations",
            json={"capability": "crm.get_customer", "arguments": {"customer_id": "C-1024"}},
            headers=auth(ALICE_TOKEN),
        )
        assert response.status_code == 200
        data = response.json()["data"]
        # the mock adapter's leaky fields must not survive
        assert set(data["result"].keys()) == {"id", "display_name"}
        assert "CANARY" not in response.text
        assert "access_token" not in response.text


class TestScenario9CrossTenant:
    def test_other_org_cannot_see_or_call_capability(self, demo) -> None:
        response = demo.harness.control.get("/api/v1/capabilities", headers=auth(BOB_TOKEN))
        assert response.status_code == 200
        assert response.json()["data"] == []

        response = demo.harness.control.post(
            "/api/v1/invocations",
            json={"capability": "crm.get_customer", "arguments": {"customer_id": "C-1024"}},
            headers=auth(BOB_TOKEN),
        )
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "CAPABILITY_NOT_FOUND"

    def test_import_size_limit_enforced(self, demo) -> None:
        ids = demo.ids
        big = {"log": {"entries": []}}
        payload = json.dumps(big).encode() + b" " * (6 * 1024 * 1024)
        response = demo.harness.control.post(
            "/api/v1/discoveries/imports",
            files={"file": ("big.har.json", payload, "application/json")},
            data={"source_type": "har", "service_id": ids["service_id"]},
            headers=auth(ALICE_TOKEN),
        )
        assert response.status_code == 413


class TestAgentGrants:
    def _grant_agent(self, harness, ids) -> None:
        response = harness.control.get(
            "/api/v1/dev/principals", headers=auth(ALICE_TOKEN)
        )
        assert response.status_code == 200
        principals = response.json()["data"]
        agent = next(p for p in principals if p["kind"] == "agent")
        response = harness.control.post(
            "/api/v1/agent-grants",
            json={"agent_principal_id": agent["id"],
                  "capability_id": ids["capability_id"]},
            headers=auth(ALICE_TOKEN),
        )
        assert response.status_code == 201

    def test_agent_without_grant_is_forbidden(self, demo) -> None:
        response = demo.harness.control.post(
            "/api/v1/invocations",
            json={"capability": "crm.get_customer", "arguments": {"customer_id": "C-1024"}},
            headers=auth(AGENT_TOKEN),
        )
        assert response.status_code == 403

    def test_agent_with_grant_succeeds(self, demo) -> None:
        ids = demo.ids
        control = demo.harness.control
        listing = control.get("/api/v1/dev/principals", headers=auth(ALICE_TOKEN))
        agent = next(p for p in listing.json()["data"] if p["kind"] == "agent")
        self._grant_agent(demo.harness, ids)
        # alice binds her profile into the agent's scope (agent:<verified-id>)
        binding = control.put(
            f"/api/v1/active-bindings/{ids['service_id']}/agent:{agent['id']}",
            json={"profile_id": ids["profile_A"], "expected_version": 0},
            headers=auth(ALICE_TOKEN),
        )
        assert binding.status_code == 200, binding.text
        response = control.post(
            "/api/v1/invocations",
            json={"capability": "crm.get_customer", "arguments": {"customer_id": "C-1024"}},
            headers=auth(AGENT_TOKEN),
        )
        assert response.status_code == 200
        assert response.json()["data"]["result"]["id"] == "C-1024"


class TestIdempotency:
    def test_same_key_replays_recorded_outcome_without_reexecution(self, demo) -> None:
        key = f"idem-{uuid.uuid4().hex[:8]}"
        first = demo.harness.control.post(
            "/api/v1/invocations",
            json={"capability": "crm.get_customer", "arguments": {"customer_id": "C-1024"},
                  "idempotency_key": key},
            headers=auth(ALICE_TOKEN),
        )
        assert first.status_code == 200
        second = demo.harness.control.post(
            "/api/v1/invocations",
            json={"capability": "crm.get_customer", "arguments": {"customer_id": "C-1024"},
                  "idempotency_key": key},
            headers=auth(ALICE_TOKEN),
        )
        assert second.status_code == 200
        assert second.json()["data"]["idempotent_replay"] is True
        assert second.json()["data"]["invocation_id"] == first.json()["data"]["invocation_id"]


class TestHighRiskApproval:
    def test_high_risk_requires_approval_then_executes(self, demo) -> None:
        ids = demo.ids
        # publish a high-risk capability
        response = demo.harness.control.post(
            "/api/v1/capabilities",
            json={
                "name": "crm.refund_customer",
                "display_name": "Refund customer",
                "operation_id": ids["refund_operation_id"],
                "input_schema": {
                    "type": "object", "additionalProperties": False,
                    "required": ["customer_id", "amount"],
                    "properties": {"customer_id": {"type": "string"},
                                    "amount": {"type": "number"}},
                },
                "output_schema": {"type": "object"},
                "allow_fields": ["refund_id", "status"],
                "risk_level": "high",
                "execution": {"body": {"amount": "amount"}},
            },
            headers=auth(ALICE_TOKEN),
        )
        capability_id = response.json()["data"]["id"]
        for action in ("review", "verify"):
            demo.harness.control.post(
                f"/api/v1/capabilities/{capability_id}/{action}",
                json={"evidence": "demo"}, headers=auth(CAROL_TOKEN),
            )
        demo.harness.control.post(
            f"/api/v1/capabilities/{capability_id}/publish",
            json={"version": "1.0.0"}, headers=auth(CAROL_TOKEN),
        )

        response = demo.harness.control.post(
            "/api/v1/invocations",
            json={"capability": "crm.refund_customer",
                  "arguments": {"customer_id": "C-1024", "amount": 42.5}},
            headers=auth(ALICE_TOKEN),
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "APPROVAL_REQUIRED"
        approval_id = response.json()["error"]["details"]["approval_request_id"]

        # alice cannot approve her own request
        denied = demo.harness.control.post(
            f"/api/v1/approvals/{approval_id}/approve", headers=auth(ALICE_TOKEN)
        )
        assert denied.status_code == 403

        # carol (different principal) approves
        approved = demo.harness.control.post(
            f"/api/v1/approvals/{approval_id}/approve", headers=auth(CAROL_TOKEN)
        )
        assert approved.status_code == 200

        executed = demo.harness.control.post(
            "/api/v1/invocations",
            json={"capability": "crm.refund_customer",
                  "arguments": {"customer_id": "C-1024", "amount": 42.5},
                  "approval_request_id": approval_id},
            headers=auth(ALICE_TOKEN),
        )
        assert executed.status_code == 200, executed.text
        assert executed.json()["data"]["status"] == "succeeded"

        # changed arguments invalidate the consumed approval
        tampered = demo.harness.control.post(
            "/api/v1/invocations",
            json={"capability": "crm.refund_customer",
                  "arguments": {"customer_id": "C-1024", "amount": 999.0},
                  "approval_request_id": approval_id},
            headers=auth(ALICE_TOKEN),
        )
        assert tampered.status_code == 409
        assert tampered.json()["error"]["code"] == "APPROVAL_REQUIRED"


class TestAuth:
    def test_missing_token_401(self, demo) -> None:
        response = demo.harness.control.get("/api/v1/services")
        assert response.status_code == 401

    def test_unknown_token_401(self, demo) -> None:
        response = demo.harness.control.get(
            "/api/v1/services", headers=auth("dev-token-nobody")
        )
        assert response.status_code == 401

    def test_healthz_open(self, demo) -> None:
        assert demo.harness.control.get("/healthz").status_code == 200


class TestAuditTrail:
    def test_invocation_leaves_audit_event_without_secrets(self, demo) -> None:
        demo.harness.control.post(
            "/api/v1/invocations",
            json={"capability": "crm.get_customer", "arguments": {"customer_id": "C-1024"}},
            headers=auth(ALICE_TOKEN),
        )
        response = demo.harness.control.get("/api/v1/audit-events", headers=auth(ALICE_TOKEN))
        events = response.json()["data"]
        actions = [e["action"] for e in events]
        assert "invocation.execute" in actions
        assert "profile.activate" in actions
        assert "CANARY" not in response.text

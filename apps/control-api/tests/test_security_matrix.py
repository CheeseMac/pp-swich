"""Attack-combination security matrix (review section 5, SEC-021..030).

These tests verify that an attacker CANNOT reach an illegal effect by
combining several individually-legal APIs - not merely that each single
guard works.
"""

from __future__ import annotations

import uuid

import pytest

AGENT_TOKEN = "dev-token-agent-helper-c2d5"
ALICE_TOKEN = "dev-token-alice-4f81"
BOB_TOKEN = "dev-token-bob-77e0"
CAROL_TOKEN = "dev-token-carol-91b3"


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def carol_profile(demo):
    """Give carol her own connection + profile + binding for the crm service."""
    control = demo.harness.control
    connection = control.post(
        "/api/v1/connections",
        json={"service_id": demo.ids["service_id"], "auth_scheme": "api_key",
              "demo_dataset": "A"},
        headers=auth(CAROL_TOKEN),
    )
    assert connection.status_code == 201, connection.text
    profile = control.post(
        "/api/v1/profiles",
        json={
            "service_id": demo.ids["service_id"],
            "endpoint_id": demo.ids["endpoint_id"],
            "connection_id": connection.json()["data"]["id"],
            "name": "Carol CRM",
        },
        headers=auth(CAROL_TOKEN),
    )
    assert profile.status_code == 201, profile.text
    binding = control.put(
        f"/api/v1/active-bindings/{demo.ids['service_id']}/personal",
        json={"profile_id": profile.json()["data"]["id"], "expected_version": 0},
        headers=auth(CAROL_TOKEN),
    )
    assert binding.status_code == 200, binding.text
    return profile.json()["data"]


class TestSEC021CrossUserConnectionAssembly:
    def test_carol_cannot_create_profile_on_alices_connection(self, demo) -> None:
        response = demo.harness.control.post(
            "/api/v1/profiles",
            json={
                "service_id": demo.ids["service_id"],
                "endpoint_id": demo.ids["endpoint_id"],
                "connection_id": demo.ids["connection_A"],
                "name": "Stolen connection profile",
            },
            headers=auth(CAROL_TOKEN),
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "PROFILE_NOT_ALLOWED"


class TestSEC022AgentSelfAuthorization:
    def test_agent_cannot_create_grants(self, demo) -> None:
        response = demo.harness.control.post(
            "/api/v1/agent-grants",
            json={"agent_principal_id": demo.ids.get("agent_id", str(uuid.uuid4())),
                  "capability_id": demo.ids["capability_id"]},
            headers=auth(AGENT_TOKEN),
        )
        assert response.status_code == 403

    def test_agent_cannot_review_or_publish(self, demo) -> None:
        control = demo.harness.control
        # a fresh draft capability created by alice
        created = control.post(
            "/api/v1/capabilities",
            json={
                "name": "crm.agent_test_draft",
                "display_name": "Draft",
                "operation_id": demo.ids["get_operation_id"],
                "input_schema": {"type": "object"},
                "output_schema": {"type": "object"},
                "allow_fields": ["id"],
            },
            headers=auth(ALICE_TOKEN),
        )
        draft_id = created.json()["data"]["id"]
        for action in ("review", "verify", "publish"):
            if action == "publish":
                response = control.post(
                    f"/api/v1/capabilities/{draft_id}/publish",
                    json={"version": "1.0.0"}, headers=auth(AGENT_TOKEN),
                )
            else:
                response = control.post(
                    f"/api/v1/capabilities/{draft_id}/{action}",
                    json={"evidence": "agent self-review"}, headers=auth(AGENT_TOKEN),
                )
            assert response.status_code == 403, f"{action} must be human-only"


class TestSEC023AgentCannotApprove:
    def test_agent_cannot_approve_high_risk(self, demo) -> None:
        control = demo.harness.control
        # publish a high-risk capability (alice creates, carol reviews)
        created = control.post(
            "/api/v1/capabilities",
            json={
                "name": "crm.refund_sec023",
                "display_name": "Refund",
                "operation_id": demo.ids["refund_operation_id"],
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
        capability_id = created.json()["data"]["id"]
        for action in ("review", "verify"):
            control.post(
                f"/api/v1/capabilities/{capability_id}/{action}",
                json={"evidence": "sec023"}, headers=auth(CAROL_TOKEN),
            )
        control.post(
            f"/api/v1/capabilities/{capability_id}/publish",
            json={"version": "1.0.0"}, headers=auth(CAROL_TOKEN),
        )
        requested = control.post(
            "/api/v1/invocations",
            json={"capability": "crm.refund_sec023",
                  "arguments": {"customer_id": "C-1024", "amount": 1.0}},
            headers=auth(ALICE_TOKEN),
        )
        approval_id = requested.json()["error"]["details"]["approval_request_id"]
        # the AGENT tries to approve it
        response = control.post(
            f"/api/v1/approvals/{approval_id}/approve", headers=auth(AGENT_TOKEN)
        )
        assert response.status_code == 403


class TestSEC024ApprovalCrossUse:
    def test_approval_cannot_authorize_another_requester(
        self, demo, carol_profile
    ) -> None:
        control = demo.harness.control
        created = control.post(
            "/api/v1/capabilities",
            json={
                "name": "crm.refund_sec024",
                "display_name": "Refund",
                "operation_id": demo.ids["refund_operation_id"],
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
        capability_id = created.json()["data"]["id"]
        for action in ("review", "verify"):
            control.post(
                f"/api/v1/capabilities/{capability_id}/{action}",
                json={"evidence": "sec024"}, headers=auth(CAROL_TOKEN),
            )
        control.post(
            f"/api/v1/capabilities/{capability_id}/publish",
            json={"version": "1.0.0"}, headers=auth(CAROL_TOKEN),
        )
        requested = control.post(
            "/api/v1/invocations",
            json={"capability": "crm.refund_sec024",
                  "arguments": {"customer_id": "C-1024", "amount": 2.0}},
            headers=auth(ALICE_TOKEN),
        )
        approval_id = requested.json()["error"]["details"]["approval_request_id"]
        control.post(f"/api/v1/approvals/{approval_id}/approve",
                     headers=auth(CAROL_TOKEN))

        # carol reuses alice's approval for the SAME arguments - must fail:
        # the approval is bound to alice as the requester
        response = control.post(
            "/api/v1/invocations",
            json={"capability": "crm.refund_sec024",
                  "arguments": {"customer_id": "C-1024", "amount": 2.0},
                  "approval_request_id": approval_id},
            headers=auth(CAROL_TOKEN),
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "APPROVAL_REQUIRED"


class TestSEC025IdempotencyCrossPrincipal:
    def test_same_key_other_principal_gets_conflict_not_data(
        self, demo, carol_profile
    ) -> None:
        control = demo.harness.control
        key = f"sec025-{uuid.uuid4().hex[:8]}"
        first = control.post(
            "/api/v1/invocations",
            json={"capability": "crm.get_customer",
                  "arguments": {"customer_id": "C-1024"},
                  "idempotency_key": key},
            headers=auth(ALICE_TOKEN),
        )
        assert first.status_code == 200, first.text

        stolen = control.post(
            "/api/v1/invocations",
            json={"capability": "crm.get_customer",
                  "arguments": {"customer_id": "C-1024"},
                  "idempotency_key": key},
            headers=auth(CAROL_TOKEN),
        )
        assert stolen.status_code == 409
        assert stolen.json()["error"]["code"] == "IDEMPOTENCY_KEY_CONFLICT"
        assert "result" not in stolen.text or '"result"' not in stolen.text.split("error")[0]


class TestSEC029InvocationResultOwnership:
    def test_carol_cannot_read_alices_invocation(self, demo) -> None:
        control = demo.harness.control
        invoked = control.post(
            "/api/v1/invocations",
            json={"capability": "crm.get_customer",
                  "arguments": {"customer_id": "C-1024"}},
            headers=auth(ALICE_TOKEN),
        )
        invocation_id = invoked.json()["data"]["invocation_id"]

        direct = control.get(f"/api/v1/invocations/{invocation_id}",
                             headers=auth(CAROL_TOKEN))
        assert direct.status_code == 404

        listing = control.get("/api/v1/invocations", headers=auth(CAROL_TOKEN))
        assert listing.status_code == 200
        ids = [row["id"] for row in listing.json()["data"]]
        assert invocation_id not in ids


class TestSEC030DemoProductionIsolation:
    def test_dev_directory_disabled_and_startup_guard(self, demo, tmp_path) -> None:
        from fastapi.testclient import TestClient
        from ppswitch_control.app import create_app
        from ppswitch_control.settings import ControlSettings
        from ppswitch_executor.main import ExecutorSettings
        from ppswitch_executor.main import create_app as create_executor_app

        # startup refuses default synthetic token outside demo mode
        with pytest.raises(RuntimeError):
            create_executor_app(ExecutorSettings(demo_mode=False))
        with pytest.raises(RuntimeError):
            create_app(ControlSettings(
                db_url=demo.harness.db_url, demo_mode=False, auto_migrate=False,
            ))

        # review4: outside demo mode the delegation ticket key must be
        # dedicated - missing, or equal to the internal token, both refuse
        with pytest.raises(RuntimeError):
            create_app(ControlSettings(
                db_url=demo.harness.db_url, demo_mode=False,
                internal_token="ci-strong-token-9f21ab", auto_migrate=False,
            ))
        with pytest.raises(RuntimeError):
            create_app(ControlSettings(
                db_url=demo.harness.db_url, demo_mode=False,
                internal_token="ci-strong-token-9f21ab",
                delegation_signing_key="ci-strong-token-9f21ab", auto_migrate=False,
            ))

        # with a strong token AND a dedicated ticket key the apps start,
        # but dev surfaces stay off
        control_app = create_app(ControlSettings(
            db_url=demo.harness.db_url, demo_mode=False,
            internal_token="ci-strong-token-9f21ab",
            delegation_signing_key="ci-ticket-key-2c81dd", auto_migrate=False,
        ))
        client = TestClient(control_app)
        assert client.get("/api/v1/dev/principals").status_code == 404

        # endpoint self-approval is rejected outside demo mode
        response = client.post(
            f"/api/v1/services/{demo.ids['service_id']}/endpoints",
            json={"environment": "prod", "base_url": "https://crm.example.test",
                  "approved_hosts": ["crm.example.test"], "status": "approved"},
            headers=auth(ALICE_TOKEN),
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "ENDPOINT_NOT_APPROVABLE"

    def test_mock_credentials_rejected_outside_demo(self) -> None:
        from fastapi.testclient import TestClient
        from ppswitch_executor.main import ExecutorSettings
        from ppswitch_executor.main import create_app as create_executor_app
        from ppswitch_executor.plan_signing import sign_plan
        from ppswitch_secrets import MockSecretProvider

        provider = MockSecretProvider()
        provider.seed(
            "mock://connections/x",
            {"auth_scheme": "api_key", "material": {"api_key": "MOCKKEY-A"}},
        )
        settings = ExecutorSettings(demo_mode=False, internal_token="ci-strong-token-9f21ab")
        app = create_executor_app(settings, provider=provider)
        client = TestClient(app)

        plan = {
            "invocation_id": str(uuid.uuid4()),
            "capability_name": "crm.get_customer",
            "capability_version": "1.0.0",
            "capability_version_id": str(uuid.uuid4()),
            "operation": {"method": "GET", "path_template": "/api/customers/{customer_id}",
                          "path_map": {}, "query_template": {}, "body_template": {}},
            "endpoint_base_url": "http://127.0.0.1:59998",
            "approved_hosts": ["127.0.0.1:59998"],
            "auth_scheme": "api_key",
            "secret_ref": "mock://connections/x?dataset=A",
            "allow_fields": ["id"],
            "input_schema": {"type": "object", "additionalProperties": False,
                             "required": ["customer_id"],
                             "properties": {"customer_id": {"type": "string"}}},
            "output_schema": {"type": "object"},
            "arguments": {"customer_id": "C-1024"},
            "workspace_id": str(uuid.uuid4()),
            "principal_id": str(uuid.uuid4()),
            "agent_id": None,
            "risk_level": "low",
            "policy_version": "policy-v1",
            "arguments_digest": "0" * 64,
            "idempotency_key": None,
            "deadline_epoch_s": __import__("time").time() + 60,
            "nonce": uuid.uuid4().hex + uuid.uuid4().hex[:8],
            "profile_label": None,
        }
        response = client.post(
            "/internal/v1/execute",
            json={"plan": plan,
                  "signature": sign_plan(plan, settings.internal_token)},
            headers={"X-Internal-Token": settings.internal_token},
        )
        assert response.status_code in (502, 503)
        assert "demo" in response.json()["error"]["message"]

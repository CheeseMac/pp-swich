"""Executor internal API tests (mock adapter path + security gates)."""

from __future__ import annotations

import time
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient
from ppswitch_executor.main import DEFAULT_INTERNAL_TOKEN, ExecutorSettings, create_app
from ppswitch_executor.plan_signing import fresh_deadline, sign_plan
from ppswitch_secrets import MockSecretProvider, UnavailableSecretProvider


def _base_plan() -> dict:
    return {
        "invocation_id": str(uuid.uuid4()),
        "capability_name": "crm.get_customer",
        "capability_version": "1.0.0",
        "capability_version_id": str(uuid.uuid4()),
        "operation": {
            "method": "GET",
            "path_template": "/api/customers/{customer_id}",
            "query_template": {},
            "body_template": {},
        },
        "endpoint_base_url": "mock://demo",
        "approved_hosts": [],
        "auth_scheme": "api_key",
        "secret_ref": "mock://connections/demo-a",
        "allow_fields": ["id", "display_name"],
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["customer_id"],
            "properties": {"customer_id": {"type": "string"}},
        },
        "output_schema": {"type": "object"},
        "arguments": {"customer_id": "C-1024"},
        "workspace_id": str(uuid.uuid4()),
        "principal_id": str(uuid.uuid4()),
        "agent_id": None,
        "risk_level": "low",
        "policy_version": "policy-v1",
        "arguments_digest": "0" * 64,
        "idempotency_key": None,
        "deadline_epoch_s": fresh_deadline(60),
        "nonce": uuid.uuid4().hex + uuid.uuid4().hex[:8],  # 40 chars
    }


def _client(provider=None, settings=None) -> TestClient:
    app = create_app(settings or ExecutorSettings(demo_mode=True), provider=provider)
    return TestClient(app)


def _post_execute(client: httpx.Client, plan: dict, signature: str | None = None) -> httpx.Response:
    return client.post(
        "/internal/v1/execute",
        json={"plan": plan, "signature": signature or sign_plan(plan, DEFAULT_INTERNAL_TOKEN)},
        headers={"X-Internal-Token": DEFAULT_INTERNAL_TOKEN},
    )


@pytest.fixture()
def seeded_provider() -> MockSecretProvider:
    provider = MockSecretProvider()
    provider.seed(
        "mock://connections/demo-a",
        {"auth_scheme": "api_key", "material": {"api_key": "MOCKKEY-DEMO-A-9f21"}},
    )
    return provider


class TestExecute:
    def test_mock_execution_returns_only_allowlisted_fields(self, seeded_provider) -> None:
        with _client(provider=seeded_provider) as client:
            response = _post_execute(client, _base_plan())
        assert response.status_code == 200
        body = response.json()
        assert body["result"] == {"id": "C-1024", "display_name": "Mock Adapter Customer"}
        assert "access_token" in body["dropped_fields"]
        assert "CANARY" not in response.text

    def test_bad_signature_rejected(self, seeded_provider) -> None:
        with _client(provider=seeded_provider) as client:
            response = _post_execute(client, _base_plan(), signature="0" * 64)
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "INVALID_SIGNATURE"

    def test_missing_internal_token_rejected(self, seeded_provider) -> None:
        plan = _base_plan()
        with _client(provider=seeded_provider) as client:
            response = client.post(
                "/internal/v1/execute",
                json={"plan": plan, "signature": sign_plan(plan, DEFAULT_INTERNAL_TOKEN)},
            )
        assert response.status_code == 401  # empty header -> deny, not a validation error
        with _client(provider=seeded_provider) as client:
            response = client.post(
                "/internal/v1/execute",
                json={"plan": plan, "signature": sign_plan(plan, DEFAULT_INTERNAL_TOKEN)},
                headers={"X-Internal-Token": "wrong-token"},
            )
        assert response.status_code == 401

    def test_replayed_nonce_rejected(self, seeded_provider) -> None:
        plan = _base_plan()
        with _client(provider=seeded_provider) as client:
            first = _post_execute(client, plan)
            second = _post_execute(client, plan)
        assert first.status_code == 200
        assert second.status_code == 409
        assert second.json()["error"]["code"] == "REPLAY_DETECTED"

    def test_expired_deadline_rejected(self, seeded_provider) -> None:
        plan = _base_plan()
        plan["deadline_epoch_s"] = time.time() - 5
        with _client(provider=seeded_provider) as client:
            response = _post_execute(client, plan)
        assert response.status_code == 410
        assert response.json()["error"]["code"] == "DEADLINE_EXCEEDED"

    def test_reserved_arguments_rejected_independent_of_control_plane(self,
        seeded_provider) -> None:
        plan = _base_plan()
        plan["arguments"] = {"customer_id": "C-1", "Authorization": "Bearer x"}
        with _client(provider=seeded_provider) as client:
            response = _post_execute(client, plan)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "RESERVED_ARGUMENT"

    def test_secret_provider_unavailable_fails_closed(self) -> None:
        plan = _base_plan()
        with _client(provider=UnavailableSecretProvider()) as client:
            response = _post_execute(client, plan)
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "SECRET_PROVIDER_UNAVAILABLE"

    def test_unknown_secret_ref_fails_closed(self) -> None:
        with _client(provider=MockSecretProvider()) as client:
            response = _post_execute(client, _base_plan())
        assert response.status_code == 503


class TestNetworkPolicyGate:
    def _http_plan(self) -> dict:
        plan = _base_plan()
        plan["endpoint_base_url"] = "http://169.254.169.254:80"
        plan["approved_hosts"] = []
        return plan

    def test_metadata_ip_blocked(self, seeded_provider) -> None:
        plan = self._http_plan()
        with _client(provider=seeded_provider) as client:
            response = _post_execute(client, plan)
        assert response.status_code == 502
        assert response.json()["error"]["code"] == "UPSTREAM_FAILURE"

    def test_private_ip_requires_explicit_approval(self, seeded_provider, monkeypatch) -> None:
        from ppswitch_executor import main as executor_main

        def fake_checker(url, approved_hosts, **kwargs):
            return executor_main.check_outbound_target(url, approved_hosts)

        # 10.0.0.5 not approved -> blocked by real checker via fake resolver
        plan = self._http_plan()
        plan["endpoint_base_url"] = "http://10.0.0.5:8913"
        monkeypatch.setattr(executor_main, "check_outbound_target", fake_checker)
        with _client(provider=seeded_provider) as client:
            response = _post_execute(client, plan)
        assert response.status_code == 502

    def test_approved_loopback_passes_policy_but_needs_real_server(
        self, seeded_provider
    ) -> None:
        # approved host passes the policy layer; connection then fails
        # because nothing listens -> UPSTREAM_FAILURE (not a policy denial)
        plan = self._http_plan()
        plan["endpoint_base_url"] = "http://127.0.0.1:59999"
        plan["approved_hosts"] = ["127.0.0.1:59999"]
        with _client(provider=seeded_provider) as client:
            response = _post_execute(client, plan)
        assert response.status_code == 502
        assert response.json()["error"]["code"] == "UPSTREAM_FAILURE"


class TestSeedEndpoint:
    def test_seed_in_demo_mode(self) -> None:
        with _client() as client:
            response = client.post(
                "/internal/v1/admin/seeds",
                json={"secret_ref": "mock://x", "material": {"api_key": "MOCKKEY-DEMO-A-1"}},
                headers={"X-Internal-Token": DEFAULT_INTERNAL_TOKEN},
            )
        assert response.status_code == 200
        assert response.json()["status"] == "seeded"

    def test_seed_refused_outside_demo_mode(self) -> None:
        settings = ExecutorSettings(demo_mode=False, internal_token="ci-strong-token-9f21ab")
        with _client(settings=settings) as client:
            response = client.post(
                "/internal/v1/admin/seeds",
                json={"secret_ref": "mock://x", "material": {"api_key": "k"}},
                headers={"X-Internal-Token": settings.internal_token},
            )
        assert response.status_code == 404


class TestResponseGuard:
    def test_canary_values_redacted_even_in_allowed_fields(self) -> None:
        from ppswitch_executor.response_guard import project_response

        guarded = project_response(
            {"id": "C-1", "display_name": "Bearer CANARY_SECRET_01"},
            ["id", "display_name"],
        )
        assert "CANARY_SECRET_01" not in str(guarded.result)
        assert guarded.result["display_name"] == "<redacted>"

    def test_oversized_response_rejected(self) -> None:
        from ppswitch_executor.response_guard import project_response

        with pytest.raises(Exception) as excinfo:
            project_response({"id": "x" * 100_000}, ["id"], max_response_bytes=1024)
        assert "size" in str(excinfo.value).lower()

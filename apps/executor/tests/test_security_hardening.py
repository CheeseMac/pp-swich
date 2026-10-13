"""Executor-side attack tests: path manipulation (SEC-026), host whitelist
and non-overridable metadata deny (SEC-027), nested secret leakage
(SEC-028), and request-time DNS re-resolution (SEC-008 hardening)."""

from __future__ import annotations

import time
import uuid

import pytest
from fastapi.testclient import TestClient
from ppswitch_executor.main import DEFAULT_INTERNAL_TOKEN, ExecutorSettings, create_app
from ppswitch_executor.plan_signing import sign_plan


def _client(provider=None) -> TestClient:
    app = create_app(ExecutorSettings(demo_mode=True), provider=provider)
    return TestClient(app)


def _mock_plan(customer_id: str = "C-1024", **overrides) -> dict:
    plan = {
        "invocation_id": str(uuid.uuid4()),
        "capability_name": "crm.get_customer",
        "capability_version": "1.0.0",
        "capability_version_id": str(uuid.uuid4()),
        "operation": {
            "method": "GET",
            "path_template": "/api/customers/{customer_id}",
            "path_map": {"customer_id": "customer_id"},
            "query_template": {},
            "body_template": {},
        },
        "endpoint_base_url": "mock://demo",
        "approved_hosts": [],
        "auth_scheme": "api_key",
        "secret_ref": "mock://connections/demo-a",
        "allow_fields": ["id", "display_name", "customer"],
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["customer_id"],
            "properties": {"customer_id": {"type": "string"}},
        },
        "output_schema": {"type": "object"},
        "arguments": {"customer_id": customer_id},
        "workspace_id": str(uuid.uuid4()),
        "principal_id": str(uuid.uuid4()),
        "agent_id": None,
        "risk_level": "low",
        "policy_version": "policy-v1",
        "arguments_digest": "0" * 64,
        "idempotency_key": None,
        "deadline_epoch_s": time.time() + 60,
        "nonce": uuid.uuid4().hex + uuid.uuid4().hex[:8],
        "profile_label": None,
    }
    plan.update(overrides)
    return plan


def _execute(client: TestClient, plan: dict) -> TestClient:
    del client
    raise NotImplementedError


def _post(client: TestClient, plan: dict):
    return client.post(
        "/internal/v1/execute",
        json={"plan": plan, "signature": sign_plan(plan, DEFAULT_INTERNAL_TOKEN)},
        headers={"X-Internal-Token": DEFAULT_INTERNAL_TOKEN},
    )


class TestSEC026PathManipulation:
    @pytest.mark.parametrize("hostile", [
        "../../admin",
        "../..%2fadmin",
        "a/b/c",
        "x?override=1",
        "x#fragment",
        "..",
        "%2e%2e%2f",
        "a%2Fb",
    ])
    def test_hostile_path_arguments_cannot_change_the_route(self, hostile) -> None:
        from ppswitch_core.entities import InvocationPlan, InvocationPlanOperation
        from ppswitch_core.errors import ApiError
        from ppswitch_executor.adapters.http_adapter import HttpAdapter

        plan = InvocationPlan(
            invocation_id=uuid.uuid4(),
            capability_name="crm.get_customer",
            capability_version="1.0.0",
            capability_version_id=uuid.uuid4(),
            operation=InvocationPlanOperation(
                method="GET",
                path_template="/api/customers/{customer_id}",
                path_map={"customer_id": "customer_id"},
            ),
            endpoint_base_url="http://crm.example.test",
            approved_hosts=["crm.example.test"],
            auth_scheme="api_key",
            secret_ref="mock://x",
            allow_fields=["id"],
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            arguments={"customer_id": hostile},
            workspace_id=uuid.uuid4(),
            principal_id=uuid.uuid4(),
            arguments_digest="0" * 64,
            deadline_epoch_s=time.time() + 60,
            nonce=uuid.uuid4().hex * 2,
        )
        with pytest.raises(ApiError):
            HttpAdapter()._build_url(plan)


class TestSEC027HostWhitelistAndMetadata:
    def _plan_http(self, url: str, approved: list[str]) -> dict:
        plan = _mock_plan()
        plan["endpoint_base_url"] = url
        plan["approved_hosts"] = approved
        return plan

    def test_public_host_not_in_whitelist_rejected(self) -> None:
        """Public targets are no longer implicitly allowed (review PR-SEC-03)."""
        from ppswitch_core.errors import ApiError
        from ppswitch_executor.network_policy import check_outbound_target

        with pytest.raises(ApiError) as excinfo:
            check_outbound_target(
                "https://evil.example.com/api",
                ["approved.example.com"],
                resolver=lambda host: ["93.184.216.34"],
            )
        assert excinfo.value.details["reason"] == "host_not_approved"

    def test_metadata_ip_denied_even_when_approved(self) -> None:
        """Link-local / metadata ranges can never be approved away."""
        from ppswitch_core.errors import ApiError
        from ppswitch_executor.network_policy import check_outbound_target

        with pytest.raises(ApiError) as excinfo:
            check_outbound_target(
                "http://169.254.169.254/latest/meta-data",
                ["169.254.169.254"],
                resolver=lambda host: ["169.254.169.254"],
            )
        assert excinfo.value.details["reason"] == "link_local_or_metadata"

    def test_dns_rebinding_to_denied_range_blocked_at_request_time(self) -> None:
        """The validating transport re-resolves at request time: a host that
        was safe at check time but answers with a metadata IP at request
        time is refused before any bytes are sent (SEC-008)."""
        import httpx
        from ppswitch_core.entities import (
            InvocationPlan,
            InvocationPlanOperation,
        )
        from ppswitch_executor.adapters.http_adapter import (
            HttpAdapter,
            NetworkPolicyViolation,
        )
        from ppswitch_executor.network_policy import check_outbound_target

        def rebind_resolver(host: str) -> list[str]:
            return ["169.254.169.254"]  # metadata answer at request time

        def checker(url: str, hosts: list[str]):
            return check_outbound_target(url, hosts, resolver=rebind_resolver)

        plan = InvocationPlan(
            invocation_id=uuid.uuid4(),
            capability_name="crm.get_customer",
            capability_version="1.0.0",
            capability_version_id=uuid.uuid4(),
            operation=InvocationPlanOperation(
                method="GET", path_template="/api/customers/{customer_id}"
            ),
            endpoint_base_url="http://rebind.example.com",
            approved_hosts=["rebind.example.com"],
            auth_scheme="api_key",
            secret_ref="mock://x",
            allow_fields=[],
            input_schema={},
            output_schema={},
            arguments={"customer_id": "C-1"},
            workspace_id=uuid.uuid4(),
            principal_id=uuid.uuid4(),
            arguments_digest="0" * 64,
            deadline_epoch_s=time.time() + 60,
            nonce=uuid.uuid4().hex * 2,
        )
        adapter = HttpAdapter(network_checker=checker)
        client = adapter._client_for(plan)
        try:
            transport = client._transport
            request = httpx.Request("GET", "http://rebind.example.com/api/customers/C-1")
            with pytest.raises(NetworkPolicyViolation):
                transport.handle_request(request)
        finally:
            client.close()
            adapter._client = None


class TestSEC028NestedSecretLeakage:
    def test_nested_api_key_dropped_even_without_regex_match(self) -> None:
        from ppswitch_executor.response_guard import project_response

        body = {
            "id": "C-1",
            "customer": {
                "name": "Acme",
                "api_key": "plain-looking-value-not-matching-any-regex",
                "contacts": [{"email": "x@y", "token": "also-plain"}],
            },
        }
        guarded = project_response(body, ["id", "customer"])
        assert "api_key" not in guarded.result["customer"]
        assert "token" not in guarded.result["customer"]["contacts"][0]
        assert guarded.result["customer"]["name"] == "Acme"
        assert "api_key" in guarded.dropped_fields

    def test_schema_guided_recursive_projection(self) -> None:
        from ppswitch_executor.response_guard import project_response

        schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "id": {"type": "string"},
                "customer": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "name": {"type": "string"},
                        "tier": {"type": "string"},
                    },
                },
            },
        }
        body = {
            "id": "C-1",
            "internal_note": "gone",
            "customer": {"name": "Acme", "tier": "gold", "secret_field": "gone",
                          "count": 5},
        }
        guarded = project_response(body, [], output_schema=schema)
        assert guarded.result == {"id": "C-1", "customer": {"name": "Acme", "tier": "gold"}}
        assert set(guarded.dropped_fields) >= {"internal_note", "secret_field"}

    def test_type_mismatch_nulls_value(self) -> None:
        from ppswitch_executor.response_guard import project_response

        schema = {
            "type": "object",
            "properties": {"id": {"type": "string"}},
        }
        guarded = project_response({"id": 12345}, [], output_schema=schema)
        assert guarded.result["id"] is None

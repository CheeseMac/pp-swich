"""SDK tests: mock invoke, error mapping, and the no-secrets surface."""

from __future__ import annotations

import pytest
from ppswich import HttpTransport, LocalBrokerTransport, PPSwichClient, PPSwichError


def test_mock_invoke_roundtrip() -> None:
    pp = PPSwichClient.from_local_broker()
    result = pp.invoke("crm.get_customer", {"customer_id": "C-1024"})
    assert result.status == "succeeded"
    assert result.result["id"] == "C-1024"
    assert result.profile_label == "mock-profile"


def test_sdk_has_no_secret_api() -> None:
    client = PPSwichClient(LocalBrokerTransport())
    public = [name for name in dir(client) if not name.startswith("_")]
    assert sorted(public) == [
        "from_http", "from_local_broker", "invoke", "list_capabilities", "profile_status",
    ]
    forbidden = ("get_secret", "export_secret", "read_env", "proxy_url", "run_shell")
    for name in forbidden:
        assert not hasattr(client, name)
        assert not hasattr(PPSwichClient, name)


def test_reserved_arguments_rejected_client_side() -> None:
    pp = PPSwichClient.from_local_broker()
    with pytest.raises(ValueError, match="reserved"):
        pp.invoke("crm.get_customer", {"customer_id": "C-1", "Authorization": "Bearer x"})
    with pytest.raises(ValueError, match="reserved"):
        pp.invoke("crm.get_customer", {"customer_id": "C-1", "target_url": "http://x"})


def test_mock_transport_records_calls_without_secret_fields() -> None:
    transport = LocalBrokerTransport()
    pp = PPSwichClient(transport)
    pp.invoke("crm.get_customer", {"customer_id": "C-1024"})
    assert transport.calls[0]["path"] == "/api/v1/invocations"
    assert "Authorization" not in transport.calls[0]["json"]["arguments"]


def test_list_capabilities_and_profile_status_mock() -> None:
    pp = PPSwichClient.from_local_broker()
    capabilities = pp.list_capabilities()
    assert capabilities[0]["name"] == "crm.get_customer"
    bindings = pp.profile_status()
    assert bindings[0]["context_key"] == "personal"


class TestHttpTransport:
    def test_error_envelope_maps_to_typed_error(self) -> None:
        import httpx

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                404,
                json={"error": {"code": "CAPABILITY_NOT_FOUND",
                                "message": "Capability not found"},
                      "request_id": "req_x"},
            )

        transport = HttpTransport("http://control.test", token="dev-token-x")
        transport._client._transport = httpx.MockTransport(handler)  # test seam
        client = PPSwichClient(transport)
        with pytest.raises(PPSwichError) as excinfo:
            client.invoke("crm.missing", {"customer_id": "C-1"})
        assert excinfo.value.code == "CAPABILITY_NOT_FOUND"

    def test_success_envelope_unwrapped(self) -> None:
        import httpx

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"data": {"invocation_id": "inv-1", "status": "succeeded",
                               "result": {"id": "C-9"}},
                      "request_id": "req_y"},
            )

        transport = HttpTransport("http://control.test", token="dev-token-x")
        transport._client._transport = httpx.MockTransport(handler)  # test seam
        client = PPSwichClient(transport)
        result = client.invoke("crm.get_customer", {"customer_id": "C-9"})
        assert result.invocation_id == "inv-1"
        assert result.result == {"id": "C-9"}

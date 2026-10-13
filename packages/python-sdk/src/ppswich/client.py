"""PPSwichClient implementation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import httpx


class PPSwichError(Exception):
    """Typed SDK error carrying the control plane's error code."""

    def __init__(self, code: str, message: str, details: dict | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


@dataclass
class InvokeResult:
    invocation_id: str
    status: str
    result: dict[str, Any] | None = None
    profile_label: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class Transport(Protocol):
    def request(self, method: str, path: str, json: dict | None = None) -> Any:
        """Perform one API call and return the ``data`` envelope payload."""
        ...  # pragma: no cover


class HttpTransport:
    """Real transport against the pp swich control plane."""

    def __init__(self, base_url: str, token: str, timeout: float = 30.0) -> None:
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
            follow_redirects=False,
        )

    def request(self, method: str, path: str, json: dict | None = None) -> Any:
        response = self._client.request(method, path, json=json)
        try:
            payload = response.json()
        except ValueError as exc:
            raise PPSwichError("BAD_RESPONSE", "Control plane returned invalid JSON") from exc
        if response.status_code >= 400:
            error = payload.get("error", {})
            raise PPSwichError(
                error.get("code", "UNKNOWN"),
                error.get("message", "Request failed"),
                error.get("details"),
            )
        return payload.get("data", {})

    def close(self) -> None:
        self._client.close()


class LocalBrokerTransport:
    """In-memory mock transport.

    Returns deterministic synthetic results and records every call so tests
    can assert that no credential material ever crosses the SDK boundary.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self._capabilities = [
            {
                "id": "cap-mock-1",
                "name": "crm.get_customer",
                "display_name": "Get customer",
                "status": "published",
                "versions": [
                    {
                        "version": "1.0.0",
                        "status": "published",
                        "input_schema": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["customer_id"],
                            "properties": {"customer_id": {"type": "string"}},
                        },
                    }
                ],
            }
        ]

    def request(self, method: str, path: str, json: dict | None = None) -> Any:
        from ppswich.reserved import assert_no_reserved_arguments

        record = {"method": method, "path": path, "json": json}
        self.calls.append(record)
        if method == "POST" and path == "/api/v1/invocations":
            body = json or {}
            assert_no_reserved_arguments(body.get("arguments") or {})
            return {
                "invocation_id": "inv-mock-0001",
                "status": "succeeded",
                "result": {
                    "id": body.get("arguments", {}).get("customer_id"),
                    "display_name": "Local Broker Mock Customer",
                },
                "profile_label": "mock-profile",
            }
        if method == "GET" and path == "/api/v1/capabilities":
            return self._capabilities
        if method == "GET" and path == "/api/v1/active-bindings":
            return [
                {
                    "service_id": "svc-mock",
                    "context_key": "personal",
                    "active_profile_id": "prof-mock",
                    "display_label": "crm / mock-profile",
                    "version": 1,
                }
            ]
        raise PPSwichError("NOT_FOUND", f"No mock handler for {method} {path}")


class PPSwichClient:
    """Secretless client for published pp swich capabilities."""

    def __init__(self, transport: Transport) -> None:
        self._transport = transport

    @classmethod
    def from_local_broker(cls) -> PPSwichClient:
        """Mock transport: offline development and unit tests."""
        return cls(LocalBrokerTransport())

    @classmethod
    def from_http(cls, base_url: str, token: str, timeout: float = 30.0) -> PPSwichClient:
        """Real transport against a control plane (dev token for now)."""
        return cls(HttpTransport(base_url, token, timeout))

    def invoke(
        self,
        capability: str,
        arguments: dict[str, Any],
        *,
        version: str | None = None,
        context_key: str = "personal",
        idempotency_key: str | None = None,
    ) -> InvokeResult:
        """Invoke one published capability with business arguments only.

        There is intentionally no parameter for tokens, URLs or headers.
        """
        from ppswich.reserved import assert_no_reserved_arguments

        assert_no_reserved_arguments(arguments)
        payload: dict[str, Any] = {
            "capability": capability,
            "arguments": arguments,
            "context_key": context_key,
        }
        if version:
            payload["version"] = version
        if idempotency_key:
            payload["idempotency_key"] = idempotency_key
        data = self._transport.request("POST", "/api/v1/invocations", payload)
        return InvokeResult(
            invocation_id=data.get("invocation_id", ""),
            status=data.get("status", ""),
            result=data.get("result"),
            profile_label=data.get("profile_label"),
            raw=data,
        )

    def list_capabilities(self) -> list[dict[str, Any]]:
        return self._transport.request("GET", "/api/v1/capabilities")

    def profile_status(self) -> list[dict[str, Any]]:
        return self._transport.request("GET", "/api/v1/active-bindings")

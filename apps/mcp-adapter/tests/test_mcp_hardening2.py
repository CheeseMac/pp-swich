"""Review2 MCP adapter tests: SEC-039 (parameter ordering) and
SEC-040 (tool-name collisions never silently shadow)."""

from __future__ import annotations

from inspect import Parameter
from typing import Any

import pytest
from ppswitch import LocalBrokerTransport, PPSwitchClient
from ppswitch_mcp.registry import (
    ToolNameConflict,
    build_tool_function,
    register_capability_tools,
)

_EMPTY = Parameter.empty


class _CatalogTransport(LocalBrokerTransport):
    """Serves a fixed capability catalog without a live control plane."""

    def __init__(self, capabilities: list[dict[str, Any]]) -> None:
        super().__init__()
        self._catalog = capabilities

    def request(self, method: str, path: str, json: dict | None = None) -> Any:
        if method == "GET" and path == "/api/v1/capabilities":
            return self._catalog
        return super().request(method, path, json)


def _capability(name: str, input_schema: dict | None = None) -> dict[str, Any]:
    return {
        "name": name,
        "display_name": name,
        "description": name,
        "status": "published",
        "versions": [
            {"version": "1.0.0", "status": "published",
             "input_schema": input_schema or {"type": "object", "properties": {}}},
        ],
    }


class _FakeServer:
    def __init__(self) -> None:
        self.tools: list[tuple[str, Any]] = []

    def add_tool(self, fn, name: str = "") -> None:
        self.tools.append((name, fn))


class TestSEC039OptionalBeforeRequired:
    def test_signature_builds_with_optional_first(self):
        """A reviewed schema whose optional parameter precedes a required
        one must still produce a valid Python signature (R-09)."""
        fn = build_tool_function(
            PPSwitchClient(LocalBrokerTransport()),
            "crm.get_customer",
            {
                "type": "object",
                "required": ["customer_id"],
                "properties": {
                    "verbose": {"type": "boolean"},      # optional, FIRST
                    "customer_id": {"type": "string"},   # required, SECOND
                },
            },
            "desc",
        )
        params = list(fn.__signature__.parameters.values())
        # every parameter carrying a default must come after all required
        seen_default = False
        for p in params:
            if p.default is not _EMPTY:
                seen_default = True
            elif seen_default:
                pytest.fail(f"required parameter {p.name} follows an optional one")
        assert [p.name for p in params] == ["customer_id", "verbose"]

    def test_mixed_order_schemas_register(self):
        client = PPSwitchClient(_CatalogTransport([
            _capability(
                "svc.query",
                {
                    "type": "object",
                    "required": ["q"],
                    "properties": {"limit": {"type": "integer"}, "q": {"type": "string"}},
                },
            )
        ]))
        server = _FakeServer()
        assert register_capability_tools(server, client) == 1


class TestSEC040ToolNameCollision:
    def test_registry_refuses_duplicate_tool_names(self):
        client = PPSwitchClient(_CatalogTransport([
            _capability("svc.foo_bar"),
            _capability("svc_foo.bar"),  # same public name: svc_foo_bar
        ]))
        server = _FakeServer()
        with pytest.raises(ToolNameConflict):
            register_capability_tools(server, client)
        # the FIRST tool was registered; the second never shadows it
        assert [name for name, _ in server.tools] == ["svc_foo_bar"]

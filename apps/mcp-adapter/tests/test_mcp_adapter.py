"""MCP adapter tests: registry mapping, tool listing, and a full MCP
client-server round trip over in-memory streams."""

from __future__ import annotations

from ppswich import LocalBrokerTransport, PPSwichClient
from ppswich_mcp.registry import (
    build_tool_function,
    capability_name_for,
    mcp_tool_name,
)


def test_tool_name_mapping_roundtrip() -> None:
    assert mcp_tool_name("crm.get_customer") == "crm_get_customer"
    assert capability_name_for("crm_get_customer") == "crm.get_customer"


def test_built_function_exposes_reviewed_parameters() -> None:
    fn = build_tool_function(
        PPSwichClient(LocalBrokerTransport()),
        "crm.get_customer",
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["customer_id"],
            "properties": {
                "customer_id": {"type": "string", "maxLength": 64},
                "verbose": {"type": "boolean"},
            },
        },
        "Get a customer you are authorized to view.",
    )
    params = list(fn.__signature__.parameters.values())
    by_name = {p.name: p for p in params}
    assert by_name["customer_id"].default is inspect_empty()
    assert by_name["verbose"].default is None
    assert "target_url" not in by_name
    assert "Authorization" not in by_name


def inspect_empty():
    import inspect

    return inspect.Parameter.empty


def test_built_function_invokes_client() -> None:
    transport = LocalBrokerTransport()
    fn = build_tool_function(
        PPSwichClient(transport),
        "crm.get_customer",
        {
            "type": "object",
            "required": ["customer_id"],
            "properties": {"customer_id": {"type": "string"}},
        },
        "desc",
    )
    outcome = fn(customer_id="C-1024")
    assert outcome["status"] == "succeeded"
    assert outcome["result"]["id"] == "C-1024"
    # the invocation carried business arguments only
    sent = transport.calls[0]["json"]
    assert sent["capability"] == "crm.get_customer"
    assert sent["arguments"] == {"customer_id": "C-1024"}


class TestProtocolRoundTrip:
    def test_mcp_client_lists_and_calls_tools(self) -> None:
        import json

        import anyio
        from mcp import ClientSession
        from mcp.shared.memory import create_client_server_memory_streams
        from ppswich_mcp.server import build_mcp_server

        class FakeTransport:
            """Pretend to be the control plane for list+invoke."""

            def request(self, method: str, path: str, json: dict | None = None) -> dict:
                if method == "GET" and path == "/api/v1/capabilities":
                    return [
                        {
                            "id": "cap-1",
                            "name": "crm.get_customer",
                            "display_name": "Get customer",
                            "description": "Get a customer you are authorized to view.",
                            "status": "published",
                            "versions": [
                                {
                                    "version": "1.0.0",
                                    "status": "published",
                                    "input_schema": {
                                        "type": "object",
                                        "additionalProperties": False,
                                        "required": ["customer_id"],
                                        "properties": {
                                            "customer_id": {"type": "string"}
                                        },
                                    },
                                }
                            ],
                        }
                    ]
                if method == "POST" and path == "/api/v1/invocations":
                    assert json is not None
                    assert json["capability"] == "crm.get_customer"
                    assert json["arguments"] == {"customer_id": "C-1024"}
                    return {
                        "invocation_id": "inv-1",
                        "status": "succeeded",
                        "result": {"id": "C-1024", "display_name": "Acme"},
                        "profile_label": "CRM Demo A",
                    }
                raise AssertionError(f"unexpected {method} {path}")

        fake_client = PPSwichClient(FakeTransport())
        server = build_mcp_server("http://control.test", "dev-token", client=fake_client)

        async def scenario() -> None:
            async with create_client_server_memory_streams() as (
                client_streams, server_streams
            ):
                lowlevel = server._lowlevel_server

                async def serve(read_stream, write_stream) -> None:
                    await lowlevel.run(
                        read_stream,
                        write_stream,
                        lowlevel.create_initialization_options(),
                    )

                async with anyio.create_task_group() as tg:
                    tg.start_soon(serve, server_streams[0], server_streams[1])
                    async with ClientSession(
                        client_streams[0], client_streams[1]
                    ) as session:
                        await session.initialize()
                        tools = await session.list_tools()
                        assert tools.tools is not None
                        names = [t.name for t in tools.tools]
                        assert "crm_get_customer" in names
                        tool = next(
                            t for t in tools.tools if t.name == "crm_get_customer"
                        )
                        assert "target_url" not in json.dumps(tool.input_schema)
                        assert tool.input_schema.get("required") == ["customer_id"]

                        result = await session.call_tool(
                            "crm_get_customer", {"customer_id": "C-1024"}
                        )
                        text = result.content[0].text if result.content else ""
                        assert "Acme" in text
                        assert "C-1024" in text
                        tg.cancel_scope.cancel()

        anyio.run(scenario)

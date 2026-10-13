"""Tool registry: map published capabilities to MCP tools.

Security model (docs/07-API-SPEC.md section 8):
- ``tools/list`` exposes only capabilities published AND visible to this
  principal - the adapter holds no registry authority of its own;
- tool input schemas come from the reviewed capability contract, not from
  model negotiation;
- descriptions are the reviewed human text; the adapter never appends
  endpoints, hosts or credential hints;
- ``tools/call`` delegates to the same invocation service as HTTP callers,
  so every policy, guard and audit applies unchanged.

The adapter NEVER imports the SecretProvider or the executor internals
(docs/08 dependency rules).
"""

from __future__ import annotations

import inspect
import keyword
from typing import Any

from ppswitch import PPSwitchClient
from ppswitch_core.naming import mcp_tool_name

_TYPE_MAP = {
    "string": str,
    "number": float,
    "integer": int,
    "boolean": bool,
    "array": list,
    "object": dict,
}


class ToolNameConflict(Exception):
    """Two capabilities map to the same public MCP tool name (SEC-040)."""


def capability_name_for(mcp_name: str) -> str | None:
    parts = mcp_name.split("_", 1)
    if len(parts) != 2:
        return None
    namespace, operation = parts
    return f"{namespace}.{operation}"


def published_capabilities(client: PPSwitchClient) -> list[dict[str, Any]]:
    data = client.list_capabilities()
    return [cap for cap in data if cap.get("status") == "published"]


def latest_published_version(capability: dict[str, Any]) -> dict[str, Any] | None:
    versions = capability.get("versions") or []
    published = [v for v in versions if v.get("status") == "published"]
    if not published:
        return None
    return published[-1]


def build_tool_function(
    client: PPSwitchClient,
    capability_name: str,
    input_schema: dict[str, Any],
    description: str,
):
    """Create a typed handler function for one published capability.

    The generated signature mirrors the reviewed JSON Schema so the MCP
    client presents real parameters to the model. Everything else
    (authorization, allowlist projection) stays server-side.
    """
    properties = (input_schema or {}).get("properties") or {}
    required = set((input_schema or {}).get("required") or [])

    # SEC-039: required parameters MUST come first - a reviewed schema with
    # an optional parameter ahead of a required one would otherwise raise
    # "non-default argument follows default argument" from inspect
    ordered = [name for name in properties if name in required] + [
        name for name in properties if name not in required
    ]

    parameters = []
    for name in ordered:
        if not name.isidentifier() or keyword.iskeyword(name):
            continue  # reviewed schemas should be clean; skip pathological names
        annotation = _TYPE_MAP.get(str((properties[name] or {}).get("type")), str)
        if name in required:
            parameters.append(
                inspect.Parameter(name, inspect.Parameter.POSITIONAL_OR_KEYWORD,
                                  annotation=annotation)
            )
        else:
            parameters.append(
                inspect.Parameter(name, inspect.Parameter.POSITIONAL_OR_KEYWORD,
                                  annotation=annotation, default=None)
            )
    signature = inspect.Signature(parameters)

    def _tool(**kwargs: Any) -> dict[str, Any]:
        arguments = {
            k: v for k, v in kwargs.items() if v is not None or k in required
        }
        result = client.invoke(capability_name, arguments)
        return {
            "status": result.status,
            "result": result.result,
            "profile": result.profile_label,
        }

    _tool.__signature__ = signature  # type: ignore[attr-defined]
    _tool.__doc__ = description or f"Invoke {capability_name}"
    return _tool


def register_capability_tools(server, client: PPSwitchClient) -> int:
    """Register every published capability as an MCP tool. Returns count.

    SEC-040: a name collision is NEVER silently overwritten - the second
    mapping raises instead of shadowing the first tool.
    """
    registered = 0
    seen: dict[str, str] = {}
    for capability in published_capabilities(client):
        version = latest_published_version(capability)
        if version is None:
            continue
        tool_name = mcp_tool_name(capability["name"])
        if tool_name in seen:
            raise ToolNameConflict(
                f"Capabilities {seen[tool_name]!r} and {capability['name']!r} both map "
                f"to MCP tool name {tool_name!r}"
            )
        seen[tool_name] = capability["name"]
        fn = build_tool_function(
            client,
            capability["name"],
            version.get("input_schema") or {},
            capability.get("description") or capability.get("display_name", ""),
        )
        server.add_tool(fn, name=tool_name)
        registered += 1
    return registered

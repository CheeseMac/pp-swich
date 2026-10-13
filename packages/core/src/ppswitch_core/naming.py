"""Shared naming rules for the public tool surface (docs/04 section 3).

Lives in core so the control plane (publish-time collision check) and the
MCP adapter (registry-time collision check) enforce the exact same rule.
"""

from __future__ import annotations


def mcp_tool_name(capability_name: str) -> str:
    """Public tool naming: dots become underscores."""
    return capability_name.replace(".", "_")

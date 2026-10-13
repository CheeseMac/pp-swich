"""pp switch MCP adapter server.

Runs on stdio for local agent clients:

    uv run ppswitch-mcp --control-url http://127.0.0.1:8911 --token <dev-token>

The token is a synthetic dev principal token (demo deployment). Production
uses OAuth resource-server audiences per the MCP authorization spec; the
adapter never holds downstream API credentials in any mode.
"""

from __future__ import annotations

import argparse
import os


def build_mcp_server(control_url: str, token: str, *, client=None):
    from mcp.server.mcpserver import MCPServer
    from ppswitch import PPSwitchClient
    from ppswitch_mcp.registry import register_capability_tools

    if client is None:
        client = PPSwitchClient.from_http(control_url, token)
    server = MCPServer(
        "pp-switch",
        instructions=(
            "pp switch exposes reviewed, published business capabilities. "
            "Tools accept business parameters only; credentials and target URLs "
            "are managed server-side and cannot be supplied here."
        ),
    )
    register_capability_tools(server, client)
    return server


def main(argv: list[str] | None = None) -> None:  # pragma: no cover - console entry
    parser = argparse.ArgumentParser(prog="ppswitch-mcp")
    parser.add_argument(
        "--control-url",
        default=os.environ.get("PP_SWITCH_CONTROL_URL", "http://127.0.0.1:8911"),
    )
    parser.add_argument("--token", default=os.environ.get("PP_SWITCH_DEV_TOKEN", ""))
    args = parser.parse_args(argv)
    if not args.token:
        raise SystemExit(
            "A dev principal token is required (--token or PP_SWITCH_DEV_TOKEN). "
            "Demo tokens are printed by scripts/seed-demo.py."
        )
    server = build_mcp_server(args.control_url, args.token)
    server.run("stdio")

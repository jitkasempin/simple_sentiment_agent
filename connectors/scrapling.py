"""Scrapling MCP tools loaded by Managed Deep Agents at runtime startup."""

from __future__ import annotations

import os

from managed_deepagents import connectors

SCRAPLING_MCP_URL = os.environ.get(
    "SCRAPLING_MCP_URL",
    "http://127.0.0.1:8000/mcp",
)
SCRAPLING_MCP_AUTH_TOKEN = os.environ.get("SCRAPLING_MCP_AUTH_TOKEN")

# Keep this allowlist explicit so an upstream server upgrade cannot silently add
# new capabilities to the agent. Names are the raw MCP names before MDA applies
# the default ``scrapling__`` prefix.
SCRAPLING_TOOLS = [
    "bulk_fetch",
    "bulk_get",
    "bulk_stealthy_fetch",
    "close_session",
    "fetch",
    "list_sessions",
    "make_request",
    "open_request_session",
    "open_session",
    "screenshot",
    "session_fetch",
    "session_make_request",
    "stealthy_fetch",
]

_server_config: dict[str, object] = {
    "transport": "http",
    "url": SCRAPLING_MCP_URL,
    "include_tools": SCRAPLING_TOOLS,
    "default_tool_timeout": 120.0,
}
if SCRAPLING_MCP_AUTH_TOKEN:
    _server_config["headers"] = {
        "Authorization": f"Bearer {SCRAPLING_MCP_AUTH_TOKEN}",
    }

connector = connectors.mcp(
    mcp_servers={"scrapling": _server_config},
    prefix_tool_name_with_server_name=True,
    throw_on_load_error=True,
    use_standard_content_blocks=True,
)

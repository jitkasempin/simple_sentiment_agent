"""Contract tests for the Managed Deep Agents Scrapling connector."""

from __future__ import annotations

import importlib
import sys

EXPECTED_SCRAPLING_TOOLS = {
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
}


def _load_connector_module():
    sys.modules.pop("connectors.scrapling", None)
    return importlib.import_module("connectors.scrapling")


def test_connector_uses_remote_http_server_and_auth_from_environment(monkeypatch):
    """MDA receives all Scrapling tools from the configured authenticated server."""
    monkeypatch.setenv("SCRAPLING_MCP_URL", "https://scrapling.example.test/mcp")
    monkeypatch.setenv("SCRAPLING_MCP_AUTH_TOKEN", "test-token")

    module = _load_connector_module()

    config = module.connector.config
    server = config["mcp_servers"]["scrapling"]

    assert server["transport"] == "http"
    assert server["url"] == "https://scrapling.example.test/mcp"
    assert server["headers"] == {"Authorization": "Bearer test-token"}
    assert set(server["include_tools"]) == EXPECTED_SCRAPLING_TOOLS
    assert server["default_tool_timeout"] == 120.0
    assert config["prefix_tool_name_with_server_name"] is True
    assert config["throw_on_load_error"] is True
    assert config["use_standard_content_blocks"] is True


def test_connector_defaults_to_local_server_without_inventing_credentials(monkeypatch):
    """Local development works by URL default while credentials remain opt-in."""
    monkeypatch.delenv("SCRAPLING_MCP_URL", raising=False)
    monkeypatch.delenv("SCRAPLING_MCP_AUTH_TOKEN", raising=False)

    module = _load_connector_module()
    server = module.connector.config["mcp_servers"]["scrapling"]

    assert server["url"] == "http://127.0.0.1:8000/mcp"
    assert "headers" not in server

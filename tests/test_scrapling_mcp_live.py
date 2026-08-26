"""Opt-in live test for the authenticated Scrapling MCP integration."""

from __future__ import annotations

import asyncio
import importlib
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_PREFIXED_TOOLS = {
    "scrapling__bulk_fetch",
    "scrapling__bulk_get",
    "scrapling__bulk_stealthy_fetch",
    "scrapling__close_session",
    "scrapling__fetch",
    "scrapling__list_sessions",
    "scrapling__make_request",
    "scrapling__open_request_session",
    "scrapling__open_session",
    "scrapling__screenshot",
    "scrapling__session_fetch",
    "scrapling__session_make_request",
    "scrapling__stealthy_fetch",
}

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_SCRAPLING_MCP_LIVE") != "1",
    reason="set RUN_SCRAPLING_MCP_LIVE=1 to start the local server and scrape example.com",
)


def _available_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_listener(process: subprocess.Popen[str], port: int) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            output = process.communicate()[0]
            pytest.fail(f"Scrapling MCP server exited during startup:\n{output}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.1)
    pytest.fail("Scrapling MCP server did not listen within 30 seconds")


def test_mda_discovers_all_tools_and_scrapes_a_real_page(monkeypatch):
    """The connector discovers 13 tools and both HTTP and browser fetches work."""
    port = _available_port()
    token = "scrapling-live-test-token"
    environment = os.environ.copy()
    environment.update(
        {
            "PATH": f"{Path(sys.executable).parent}{os.pathsep}{environment['PATH']}",
            "SCRAPLING_MCP_AUTH_TOKEN": token,
        }
    )
    process = subprocess.Popen(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "start_scrapling_mcp.py"),
            "--port",
            str(port),
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    connector = None
    try:
        _wait_for_listener(process, port)
        mcp_url = f"http://127.0.0.1:{port}/mcp"
        unauthorized = httpx.post(
            mcp_url,
            headers={"Accept": "application/json, text/event-stream"},
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {},
            },
        )
        assert unauthorized.status_code == 401

        monkeypatch.setenv("SCRAPLING_MCP_URL", mcp_url)
        monkeypatch.setenv("SCRAPLING_MCP_AUTH_TOKEN", token)
        sys.modules.pop("connectors.scrapling", None)
        module = importlib.import_module("connectors.scrapling")
        connector = module.connector

        tools = connector.tools()
        tools_by_name = {tool.name: tool for tool in tools}

        assert set(tools_by_name) == EXPECTED_PREFIXED_TOOLS
        result = asyncio.run(
            tools_by_name["scrapling__make_request"].ainvoke(
                {
                    "url": "https://example.com",
                    "extraction_type": "text",
                    "main_content_only": True,
                }
            )
        )
        assert "Example Domain" in str(result)

        browser_result = asyncio.run(
            tools_by_name["scrapling__fetch"].ainvoke(
                {
                    "url": "https://example.com",
                    "extraction_type": "text",
                    "main_content_only": True,
                }
            )
        )
        assert "Example Domain" in str(browser_result)
    finally:
        if connector is not None:
            from managed_deepagents._connectors.mcp.load import (
                clear_managed_mcp_tools_cache_for_tests,
            )

            clear_managed_mcp_tools_cache_for_tests(connector)
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)

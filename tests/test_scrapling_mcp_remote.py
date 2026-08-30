"""Opt-in test against an already-deployed Scrapling MCP server.

    RUN_SCRAPLING_MCP_REMOTE=1 \
    SCRAPLING_MCP_URL=https://<service-host>/mcp \
    SCRAPLING_MCP_AUTH_TOKEN=<token> \
    python -m pytest tests/test_scrapling_mcp_remote.py

Unlike ``test_scrapling_mcp_live.py`` this starts no server; it exercises the
running Cloud Run revision through the same connector the agent uses.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import sys
from urllib.parse import urlparse

import httpx
import pytest

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

# Loopback is the only place a bearer token may cross plain HTTP.
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_SCRAPLING_MCP_REMOTE") != "1",
    reason=(
        "set RUN_SCRAPLING_MCP_REMOTE=1 with SCRAPLING_MCP_URL and "
        "SCRAPLING_MCP_AUTH_TOKEN to test a deployed server"
    ),
)


@pytest.fixture(scope="module")
def endpoint() -> tuple[str, str]:
    """The deployed URL and its token, refusing to leak the token over plain HTTP."""
    url = os.environ.get("SCRAPLING_MCP_URL")
    token = os.environ.get("SCRAPLING_MCP_AUTH_TOKEN")
    if not url or not token:
        pytest.fail("SCRAPLING_MCP_URL and SCRAPLING_MCP_AUTH_TOKEN must both be set")

    parsed = urlparse(url)
    if parsed.scheme != "https" and parsed.hostname not in LOCAL_HOSTS:
        pytest.fail(f"refusing to send a bearer token over plain HTTP to {parsed.hostname}")
    return url, token


@pytest.fixture(scope="module")
def redact(endpoint):
    """Scrub the token out of anything that reaches an assertion message."""
    _, token = endpoint
    return lambda value: str(value).replace(token, "<redacted>")


@pytest.fixture(scope="module")
def tools(endpoint):
    """The connector's tools, resolved against the deployed server."""
    # connectors.scrapling reads its configuration at import time.
    sys.modules.pop("connectors.scrapling", None)
    module = importlib.import_module("connectors.scrapling")
    connector = module.connector
    try:
        yield {tool.name: tool for tool in connector.tools()}
    finally:
        from managed_deepagents._connectors.mcp.load import (
            clear_managed_mcp_tools_cache_for_tests,
        )

        clear_managed_mcp_tools_cache_for_tests(connector)
        sys.modules.pop("connectors.scrapling", None)


def test_unauthenticated_request_is_rejected(endpoint, redact):
    """The public URL is useless without the bearer token."""
    url, _ = endpoint
    response = httpx.post(
        url,
        headers={"Accept": "application/json, text/event-stream"},
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        timeout=30,
    )

    assert response.status_code == 401, redact(response.text)


def test_deployed_server_exposes_exactly_the_allowlisted_tools(tools, redact):
    """A server upgrade cannot silently widen or narrow the agent's capabilities."""
    assert set(tools) == EXPECTED_PREFIXED_TOOLS, redact(sorted(tools))


def test_http_fetcher_scrapes_a_real_page(tools, redact):
    """The requests-backed path works end to end through Cloud Run."""
    result = asyncio.run(
        tools["scrapling__make_request"].ainvoke(
            {
                "url": "https://example.com",
                "extraction_type": "text",
                "main_content_only": True,
            }
        )
    )

    assert "Example Domain" in str(result), redact(result)


def test_browser_fetcher_scrapes_a_real_page(tools, redact):
    """Chromium launches inside the container, which plain HTTP fetching would not prove."""
    result = asyncio.run(
        tools["scrapling__fetch"].ainvoke(
            {
                "url": "https://example.com",
                "extraction_type": "text",
                "main_content_only": True,
            }
        )
    )

    assert "Example Domain" in str(result), redact(result)


def session_id_of(opened) -> str:
    """Tools answer with content blocks whose text is the JSON session record."""
    return json.loads(opened[0]["text"])["session_id"]


def test_request_session_lifecycle(tools, redact):
    """Sessions open, serve a request, appear in the listing, and close."""

    async def exercise() -> tuple[str, str, list]:
        opened = await tools["scrapling__open_request_session"].ainvoke({})
        session_id = session_id_of(opened)
        try:
            page = await tools["scrapling__session_make_request"].ainvoke(
                {
                    "url": "https://example.com",
                    "session_id": session_id,
                    "extraction_type": "text",
                    "main_content_only": True,
                }
            )
            listing = await tools["scrapling__list_sessions"].ainvoke({})
            return session_id, str(page), str(listing)
        finally:
            await tools["scrapling__close_session"].ainvoke({"session_id": session_id})

    session_id, page, listing = asyncio.run(exercise())

    assert "Example Domain" in page, redact(page)
    assert session_id in listing, redact(listing)

    remaining = str(asyncio.run(tools["scrapling__list_sessions"].ainvoke({})))
    assert session_id not in remaining, redact(remaining)

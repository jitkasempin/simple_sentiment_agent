"""Contract tests for the Scrapling MCP launcher."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = PROJECT_ROOT / "scripts" / "start_scrapling_mcp.py"


def _load_launcher():
    """Import the launcher by path; scripts/ is not a package."""
    spec = importlib.util.spec_from_file_location("start_scrapling_mcp", LAUNCHER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


launcher = _load_launcher()


def test_launcher_refuses_to_start_without_authentication_token():
    """Local HTTP mode is authenticated by default and cannot silently downgrade."""
    environment = os.environ.copy()
    environment.pop("SCRAPLING_MCP_AUTH_TOKEN", None)

    result = subprocess.run(
        [sys.executable, str(LAUNCHER), "--port", "18000"],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 2
    assert "SCRAPLING_MCP_AUTH_TOKEN is required" in result.stderr


@pytest.mark.parametrize(
    ("environment", "expected"),
    [
        ({}, 8000),
        ({"PORT": "8080"}, 8080),
        ({"SCRAPLING_MCP_PORT": "9001"}, 9001),
        # An explicit local override beats Cloud Run's injected PORT.
        ({"SCRAPLING_MCP_PORT": "9001", "PORT": "8080"}, 9001),
    ],
)
def test_port_precedence(monkeypatch, environment, expected):
    """Cloud Run injects PORT; SCRAPLING_MCP_PORT still wins when both are set."""
    monkeypatch.delenv("SCRAPLING_MCP_PORT", raising=False)
    monkeypatch.delenv("PORT", raising=False)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)

    assert launcher._parser().parse_args([]).port == expected


def test_allowed_hosts_merges_environment_and_flags():
    """CSV environment entries and repeated flags combine, deduped and stripped."""
    merged = launcher.allowed_hosts(
        ["cli.example.com", "shared.example.com"],
        " env.example.com , shared.example.com ,, ",
    )

    assert merged == ["env.example.com", "shared.example.com", "cli.example.com"]


def test_allowed_hosts_empty_without_configuration():
    """No allowlist configured means no --allowed-host arguments at all."""
    assert launcher.allowed_hosts([], None) == []
    assert launcher.allowed_hosts([], "") == []


def test_build_command_targets_the_isolated_server_project():
    """The launcher always execs the pinned scrapling-server environment over HTTP."""
    command = launcher.build_command(
        "/usr/local/bin/uv", "0.0.0.0", 8080, ["svc.run.app"]
    )

    assert command[:6] == [
        "/usr/local/bin/uv",
        "run",
        "--project",
        str(PROJECT_ROOT / "scrapling-server"),
        "--frozen",
        "scrapling-mcp",
    ]
    assert "--http" in command
    assert command[command.index("--host") + 1] == "0.0.0.0"
    assert command[command.index("--port") + 1] == "8080"
    assert command[command.index("--allowed-host") + 1] == "svc.run.app"
    # The token is an environment concern; it must never reach argv.
    assert not any("token" in argument.lower() for argument in command)

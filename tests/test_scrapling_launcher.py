"""Security contract tests for the local Scrapling MCP launcher."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = PROJECT_ROOT / "scripts" / "start_scrapling_mcp.py"


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

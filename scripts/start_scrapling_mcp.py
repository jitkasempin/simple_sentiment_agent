"""Start an authenticated Scrapling Streamable HTTP MCP server."""

from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Start the Scrapling MCP server for local Managed Deep Agents development.",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("SCRAPLING_MCP_HOST", "127.0.0.1"),
        help="listen address (default: SCRAPLING_MCP_HOST or 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=os.environ.get("SCRAPLING_MCP_PORT", "8000"),
        help="listen port (default: SCRAPLING_MCP_PORT or 8000)",
    )
    parser.add_argument(
        "--allowed-host",
        action="append",
        default=[],
        help="accepted Host header; repeat for multiple public hostnames",
    )
    return parser


def _uv_binary() -> str | None:
    return shutil.which("uv")


def main() -> None:
    parser = _parser()
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    if not os.environ.get("SCRAPLING_MCP_AUTH_TOKEN"):
        parser.error(
            "SCRAPLING_MCP_AUTH_TOKEN is required; keep the token in the environment "
            "instead of passing it on the command line"
        )

    executable = _uv_binary()
    if executable is None:
        parser.error("uv is required to run the isolated Scrapling server environment")

    command = [
        executable,
        "run",
        "--project",
        str(PROJECT_ROOT / "scrapling-server"),
        "--frozen",
        "scrapling-mcp",
        "--http",
        "--host",
        args.host,
        "--port",
        str(args.port),
    ]
    for allowed_host in args.allowed_host:
        command.extend(["--allowed-host", allowed_host])

    # Replace this process so Ctrl-C and termination signals reach the MCP server.
    os.execvpe(executable, command, os.environ.copy())


if __name__ == "__main__":
    main()

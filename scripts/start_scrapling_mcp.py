"""Start an authenticated Scrapling Streamable HTTP MCP server."""

from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _default_port() -> str:
    # Cloud Run injects PORT. SCRAPLING_MCP_PORT still wins so a local override
    # works even inside a container that already has PORT set.
    return os.environ.get("SCRAPLING_MCP_PORT") or os.environ.get("PORT") or "8000"


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
        default=_default_port(),
        help="listen port (default: SCRAPLING_MCP_PORT, then PORT, then 8000)",
    )
    parser.add_argument(
        "--allowed-host",
        action="append",
        default=[],
        help="accepted Host header; repeat for multiple public hostnames",
    )
    return parser


def allowed_hosts(cli_hosts: list[str], env_value: str | None) -> list[str]:
    """Merge SCRAPLING_MCP_ALLOWED_HOSTS (CSV) with repeated --allowed-host flags."""
    # dict keys keep first-seen order and drop duplicates in one pass.
    ordered: dict[str, None] = {}
    for host in [*(env_value or "").split(","), *cli_hosts]:
        stripped = host.strip()
        if stripped:
            ordered[stripped] = None
    return list(ordered)


def build_command(executable: str, host: str, port: int, hosts: list[str]) -> list[str]:
    """The argv that replaces this process. Pure, so tests can assert on it."""
    command = [
        executable,
        "run",
        "--project",
        str(PROJECT_ROOT / "scrapling-server"),
        "--frozen",
        "scrapling-mcp",
        "--http",
        "--host",
        host,
        "--port",
        str(port),
    ]
    for allowed_host in hosts:
        command.extend(["--allowed-host", allowed_host])
    return command


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

    command = build_command(
        executable,
        args.host,
        args.port,
        allowed_hosts(args.allowed_host, os.environ.get("SCRAPLING_MCP_ALLOWED_HOSTS")),
    )

    # Replace this process so Ctrl-C and termination signals reach the MCP server.
    os.execvpe(executable, command, os.environ.copy())


if __name__ == "__main__":
    main()

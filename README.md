# forex-sentiment-agent

A Managed Deep Agent built with [`managed-deepagents`](https://github.com/langchain-ai/managed-deepagents-sdk).

## Project structure

```text
forex-sentiment-agent/
  agent.py             # define_deep_agent(...) — required `name` is the deploy id
  instructions.md      # always-loaded system prompt
  pyproject.toml       # project dependencies
  .env                 # API keys (LangSmith + model providers); never commit
  identity.py          # managed authentication
  sandbox/__init__.py  # managed LangSmith sandbox (delete `sandbox/` to opt out)
  tools/               # optional custom tools
  middleware/          # optional middleware
  skills/scrapling-official/  # official Scrapling skill synced to Context Hub
  connectors/scrapling.py     # remote HTTP MCP connector loaded by MDA
  scrapling-server/            # isolated Scrapling MCP server environment
  scripts/start_scrapling_mcp.py
  tests/
```

## Install

```bash
uv sync
uv sync --project scrapling-server
uv run --project scrapling-server --frozen scrapling install --force
```

The Scrapling server intentionally has its own environment. Scrapling 0.4.15
uses MCP 2.x, while the MDA 0.5.3 connector runtime uses
`langchain-mcp-adapters` with MCP 1.x. Separating the HTTP server from the agent
client avoids an unsatisfiable single-environment dependency graph.

## Scrapling web scraping

`skills/scrapling-official/` vendors the upstream Scrapling 0.4.15 agent skill,
including its references, examples, and BSD-3-Clause license.

The MDA runtime discovers `connectors/scrapling.py`, connects to the configured
Streamable HTTP endpoint when it initializes the agent, and caches all 13
allowlisted tools. Tool names are exposed with the `scrapling__` prefix. Startup
fails instead of silently dropping scraping capability when the endpoint cannot
be reached.

### Local development

Generate a token and export it in the terminal that runs the server. Put the
same two variables in the project `.env` so `mda dev` can authenticate to it:

```bash
export SCRAPLING_MCP_AUTH_TOKEN="$(openssl rand -hex 32)"
export SCRAPLING_MCP_URL="http://127.0.0.1:8000/mcp"

uv run python scripts/start_scrapling_mcp.py
```

The launcher reads the token from the environment rather than putting it in the
process list. It binds to `127.0.0.1:8000` by default. Run `mda dev` in a second
terminal after the MCP server is listening.

### Managed deployment

LangSmith Cloud cannot reach a server bound to the developer machine. Run the
isolated `scrapling-server` project on a network-accessible host, terminate TLS
in front of it, and configure:

```text
SCRAPLING_MCP_URL=https://scrapling.example.com/mcp
SCRAPLING_MCP_AUTH_TOKEN=<same secret configured on the server>
```

For a directly exposed server, bind with `--host 0.0.0.0` and repeat
`--allowed-host host.example.com:PORT` for accepted hostnames. Keep
authentication enabled; never expose `--no-auth` on a public interface. MDA
forwards non-reserved `.env` values as deployment secrets.

### Tests

The default suite validates the connector declaration and skips the networked
smoke test. The opt-in live test starts the authenticated local server, verifies
discovery of all 13 prefixed tools, and fetches `https://example.com` through
both `scrapling__make_request` and the Chromium-backed `scrapling__fetch`:

```bash
uv run python -m pytest -q
RUN_SCRAPLING_MCP_LIVE=1 uv run python -m pytest tests/test_scrapling_mcp_live.py -q
mda build
```

## Evaluate

Managed Deep Agent evals are Harbor evals. Author full Harbor tasks directly under
`evals/tasks/<task>/`. To start from a minimal task, run:

```bash
mda evals init my-task
```

This creates the optional scaffold `evals/scaffold/my-task/` with an `instruction.md` and a language
verifier. Run the same command with another name to add more scaffolds. At compile
time MDA copies selected scaffolds to `evals/tasks/` and preserves
every other task. Compile the managed agent, then run Harbor yourself:

```bash
mda evals compile ./forex-sentiment-agent                  # all tasks
mda evals compile ./forex-sentiment-agent --task my-task   # only my-task
# follow the printed `harbor run` command
```

## Develop

Edit `agent.py` to configure your model, tools, and middleware, and edit
`instructions.md` to shape the system prompt.

Run the compiled app on the local LangGraph dev server:

```bash
mda dev
```

For Python projects, `mda dev` requires `uv` on `PATH`, but it resolves the local LangGraph dev server automatically; you do not need to install a global `langgraph` command.

## Identity

`identity.py` enables managed authentication: threads are owned
per caller. Set `auth` to one or more `auth.*` entries if browsers call
the deployment directly. Durable memory is declared separately.

## Memory

This project declares no memory, so nothing is kept between runs. Add
`memory.py` exporting `defineMemory({ scope: "agent" })` (or
`define_memory(scope="agent")`) to mount one deployment-shared tree at
`/memories/agent/`.

## Sandbox

`sandbox/__init__.py` declares a managed LangSmith sandbox. MDA only enables the
sandbox when this declaration is present — remove the `sandbox/` directory to
opt out (for example for chat-only agents). If `sandbox/setup.sh` exists, MDA
embeds it and runs it once when the sandbox is first provisioned.

## Optional Runtime Pieces

`connectors/scrapling.py` exports the required module-level `connector`
declaration. MDA supports remote HTTP/SSE MCP transports only; it does not run
the Scrapling stdio server inside the managed agent.

## Deploy

Compile and deploy the project to LangSmith:

```bash
mda deploy
```

This copies your files verbatim, generates a managed entry module, and writes a
deployable build (including `langgraph.json`) to `.mda/build`. The CLI uploads
that build to LangSmith to run your agent on the managed runtime.

Common options:

```bash
mda deploy --name forex-sentiment-agent-dev --deployment-type dev
mda deploy --workspace-id "$LANGSMITH_WORKSPACE_ID"
mda deploy --no-wait
```

Deploy prints both the Agent Server URL to call and the LangSmith dashboard URL
to inspect.

## Logs

Read the deployed agent's server logs:

```bash
mda logs
mda logs --lines 200 --level error
```

In a terminal this streams new output until you press Ctrl-C. When the output is
piped or redirected it prints the most recent lines (1000 by default) and exits.

## Delete

Remove the deployment and the LangSmith resources it created:

```bash
mda delete
```

This deletes the deployment, the tracing project created alongside it, the
Context Hub repo holding this agent's context and memory, and the managed
sandboxes this agent created. It asks first; pass `--yes` to skip the prompt.
Agent memory and thread history are not recoverable afterwards.

## Environment

`mda deploy` loads `.env`, uses `LANGSMITH_API_KEY` for LangSmith, and forwards
model provider keys such as `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` as deployment
secrets. Provider keys must be in `.env` or configured as LangSmith workspace
secrets — a value exported in your shell is not read. Set
`LANGSMITH_WORKSPACE_ID` or pass `--workspace-id` if your LangSmith API key
requires a workspace selection.

Scrapling additionally uses `SCRAPLING_MCP_URL` and
`SCRAPLING_MCP_AUTH_TOKEN`. Never commit the token.

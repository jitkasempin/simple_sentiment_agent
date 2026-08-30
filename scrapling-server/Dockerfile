# syntax=docker/dockerfile:1

# Build context is the repository root, so the launcher and the pinned server
# project ship from one source of truth:
#   docker build -f scrapling-server/Dockerfile -t IMAGE .

# Digest-pinned: both `python:3.13-slim-bookworm` and the uv tag are rolling refs.
FROM python:3.13-slim-bookworm@sha256:c45a22ea000adfd9cda29364bbe7edd23001ce5cc2ad15857cfbf7766943b9ca

COPY --from=ghcr.io/astral-sh/uv:0.11.7@sha256:240fb85ab0f263ef12f492d8476aa3a2e4e1e333f7d67fbdd923d00a506a516a /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1 \
    UV_NO_SYNC=1 \
    UV_PYTHON=/usr/local/bin/python3 \
    UV_PYTHON_DOWNLOADS=never \
    SCRAPLING_MCP_HOST=0.0.0.0 \
    HOME=/home/app

WORKDIR /app

# Dependency layer: rebuilt only when the lockfile changes.
COPY scrapling-server/pyproject.toml scrapling-server/uv.lock /app/scrapling-server/
RUN uv sync --project /app/scrapling-server --frozen --no-dev

# Chromium and its shared libraries. `scrapling install` shells out to
# `playwright install-deps`, which needs root and apt lists. Browsers land in
# PLAYWRIGHT_BROWSERS_PATH as root, so make them world-readable for the runtime user.
RUN apt-get update \
 && uv run --project /app/scrapling-server --frozen scrapling install --force \
 && rm -rf /var/lib/apt/lists/* \
 && chmod -R a+rX /opt/ms-playwright

# Application layer: rebuilt on every launcher edit.
COPY scripts/start_scrapling_mcp.py /app/scripts/start_scrapling_mcp.py

# uv touches marker files in the environment it runs, so the runtime user owns it.
RUN useradd --create-home --uid 1000 app \
 && chown -R app:app /app/scrapling-server/.venv

USER app
EXPOSE 8080

# Cloud Run injects PORT and, from Secret Manager, SCRAPLING_MCP_AUTH_TOKEN.
# Neither is baked into the image, and neither may appear on the command line.
CMD ["python", "/app/scripts/start_scrapling_mcp.py"]

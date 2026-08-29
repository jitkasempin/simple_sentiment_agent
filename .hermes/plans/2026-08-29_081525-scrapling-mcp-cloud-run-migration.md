# Scrapling MCP Cloud Run Migration Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Move the repository's authenticated Scrapling MCP server from a developer-local process to Google Cloud Run over HTTPS Streamable HTTP, then deploy the forex sentiment agent to LangSmith Managed Deep Agents with a verified remote MCP connector.

**Architecture:** Keep the Scrapling server in its existing isolated dependency environment, package that environment and its Chromium dependencies in a dedicated container, and expose only `https://<cloud-run-host>/mcp`. Managed Deep Agents continues to load `connectors/scrapling.py` with `transport="http"` and a static bearer header. Because Scrapling 0.4.15 keeps both MCP transport sessions and its 13 browser/request sessions in process memory, the first production configuration deliberately uses one Cloud Run instance; scale-out is deferred until the server is stateless or session state is externalized.

**Tech Stack:** Python 3.13, uv, Scrapling 0.4.15 (`scrapling[ai]`), MCP Streamable HTTP, OCI/Docker, Cloud Build, Artifact Registry, Cloud Run gen2, Secret Manager, Managed Deep Agents 0.5.3, LangSmith Cloud, pytest, `langchain-mcp-adapters`.

---

## 1. Current repository context

- `scripts/start_scrapling_mcp.py:13-73` starts `scrapling-mcp --http`, defaults to `127.0.0.1:8000`, requires `SCRAPLING_MCP_AUTH_TOKEN`, and can pass repeated `--allowed-host` arguments. It does **not** yet consume Cloud Run's injected `PORT` variable or an environment-based production host allowlist.
- `scrapling-server/pyproject.toml:1-11` isolates `scrapling[ai]==0.4.15` from the MDA client environment. Preserve this split: the server uses MCP 2.x while the MDA 0.5.3 client currently uses MCP 1.x.
- `connectors/scrapling.py:9-50` already declares the correct MDA remote transport (`"http"` means Streamable HTTP), loads the URL and bearer token from environment variables, fails closed on connector load errors, and allowlists all 13 raw Scrapling tool names.
- `tests/test_scrapling_mcp_live.py:60-144` already proves MDA can discover the 13 tools and call HTTP- and Chromium-backed fetches against a local Streamable HTTP server.
- Upstream Scrapling stores reusable browser/request sessions in an in-memory dictionary (`ScraplingMCPServer._sessions`). A Cloud Run instance restart loses them, and two live instances would not share them.
- Managed Deep Agents supports only remote Streamable HTTP and legacy SSE connectors, not stdio. Its connector `headers` are static deployment secrets.
- Managed Deep Agents is currently public beta and available in US LangSmith Cloud only.

## 2. Target architecture

```text
LangSmith Cloud / Managed Deep Agent
  agent.py
  connectors/scrapling.py
    transport = "http"
    URL = https://<service-host>/mcp
    Authorization = Bearer <shared-secret>
               |
               | HTTPS + Streamable HTTP
               v
Google Cloud Run (gen2, max instances = 1 initially)
  Cloud Run TLS edge
  exact Host/Origin validation
  Scrapling static bearer-token verifier
  /mcp
  Scrapling MCP server + Chromium
               |
               | outbound HTTPS
               v
        Public websites
```

### Required boundary decisions

1. **Transport:** Use only Streamable HTTP at `/mcp`; do not expose stdio or add legacy SSE fallback.
2. **TLS:** Use the Cloud Run `run.app` HTTPS endpoint initially. A custom domain/load balancer is optional hardening, not a migration prerequisite.
3. **Authentication:** Keep Scrapling's application bearer token. Store it in Google Secret Manager for Cloud Run and as a LangSmith hosted deployment secret for MDA.
4. **Cloud Run IAM compatibility:** The MDA connector documents static headers but no Google ID-token refresh hook. A normal Cloud Run ID token expires and would also compete with Scrapling for the `Authorization` header. Therefore the baseline service must be network-public at the Cloud Run IAM layer while remaining application-authenticated by Scrapling. If organization policy forbids disabling the Cloud Run invoker check, stop: use an OAuth-aware gateway/custom connector, LangSmith BYOC, or host the agent in GCP instead of weakening authentication.
5. **State:** Set `max-instances=1` while exposing `open_session`, `open_request_session`, `session_fetch`, `session_make_request`, `screenshot`, `list_sessions`, and `close_session`. Cloud Run session affinity is best effort and MDA may create fresh HTTP clients for tool calls, so affinity is not a substitute for the single-instance cap.
6. **Restart semantics:** Treat Scrapling sessions as disposable. Agent instructions and tests must require recreation after "session not found" and always call `close_session` when possible. A Cloud Run revision rollout intentionally terminates existing sessions.
7. **Least privilege:** Run as a dedicated service account with no project-wide role. Grant it `roles/secretmanager.secretAccessor` on the single MCP token secret only. Do not attach a VPC or credentials the scraper does not need; the tools can fetch caller-selected URLs and must not inherit valuable cloud permissions.
8. **Capacity starting point:** Use gen2, 2 vCPU, 4 GiB memory, concurrency 4, request timeout 300 seconds, max instances 1, and min instances 0 for development. Measure browser memory/cold starts before choosing min instances 1 for production.
9. **Timeout ordering:** Keep the MDA tool timeout at 120 seconds initially and Cloud Run at 300 seconds, so the client gives up before the platform. Tool-specific Scrapling timeouts must remain below the MDA limit.

## 3. Acceptance criteria

- The container listens on `0.0.0.0:$PORT` and serves the MCP endpoint at `/mcp`.
- A request without the Scrapling token receives `401`; a wrong token receives `401`.
- Invalid Host/Origin input is rejected after the exact Cloud Run hostname is configured.
- An authenticated MDA-compatible client discovers exactly the 13 currently allowlisted tools with the `scrapling__` prefix.
- `scrapling__make_request` and `scrapling__fetch` both retrieve `https://example.com` from the deployed service.
- A complete reusable-session flow (`open_request_session` → `session_make_request` → `close_session`) succeeds through the Cloud Run endpoint.
- Cloud Run is configured with one maximum instance and a least-privilege service identity.
- No token appears in source, the image, command-line arguments, test output, Cloud Build substitutions, or logs.
- `uv run python -m pytest -q`, the opt-in remote MCP smoke test, and `mda build .` pass.
- A deployed Managed Deep Agent run invokes a `scrapling__*` tool, succeeds, and has a corresponding LangSmith trace.
- Rollback of both the Cloud Run revision and MDA deployment is documented and rehearsed in the development environment.

---

## 4. Implementation tasks

### Task 1: Add the Cloud Run launcher contract with tests

**Objective:** Preserve local defaults while making the existing launcher consume Cloud Run environment variables safely.

**Files:**
- Modify: `tests/test_scrapling_launcher.py:1-30`
- Modify: `scripts/start_scrapling_mcp.py:13-73`

**Step 1: Write failing unit tests**

Add tests that monkeypatch command execution and prove:

- `PORT=8080` is used when `SCRAPLING_MCP_PORT` is absent.
- `SCRAPLING_MCP_PORT` overrides `PORT` for explicit local/operator configuration.
- Local defaults remain `127.0.0.1:8000` when neither variable is set.
- `SCRAPLING_MCP_ALLOWED_HOSTS="service.example.run.app,custom.example.com"` becomes two repeated `--allowed-host` arguments after whitespace is stripped and empty items are discarded.
- Repeated CLI `--allowed-host` values merge with environment values without duplicates.
- The token remains in the child environment and never appears in the command argument list.
- Missing/invalid ports and a missing auth token still fail before execution.

Refactor command construction into a small pure function so tests do not need to launch the server.

**Step 2: Run tests to verify failure**

```bash
uv run python -m pytest tests/test_scrapling_launcher.py -q
```

Expected: new Cloud Run environment-contract tests fail against the current launcher.

**Step 3: Implement the minimal launcher changes**

Use this precedence:

```text
host: SCRAPLING_MCP_HOST -> 127.0.0.1
port: SCRAPLING_MCP_PORT -> PORT -> 8000
allowed hosts: SCRAPLING_MCP_ALLOWED_HOSTS CSV + repeated --allowed-host
```

Keep `os.execvpe`, `uv run --project scrapling-server --frozen`, mandatory `SCRAPLING_MCP_AUTH_TOKEN`, and the existing local behavior.

**Step 4: Run the focused tests**

```bash
uv run python -m pytest tests/test_scrapling_launcher.py -q
```

Expected: all launcher tests pass.

**Step 5: Commit**

```bash
git add scripts/start_scrapling_mcp.py tests/test_scrapling_launcher.py
git commit -m "feat: support Cloud Run environment in Scrapling launcher"
```

### Task 2: Build a reproducible Scrapling container

**Objective:** Produce an immutable image containing the isolated Scrapling environment and installed Chromium/runtime dependencies.

**Files:**
- Create: `scrapling-server/Dockerfile`
- Create: `.dockerignore`
- Create: `deploy/cloud-run/cloudbuild.scrapling.yaml`

**Step 1: Define the container build contract**

The Dockerfile should:

1. Use a pinned Python 3.13 slim base image (pin the tested digest before production).
2. Copy a pinned `uv` binary from an immutable image/version, not `latest`.
3. Copy `scrapling-server/pyproject.toml` and `scrapling-server/uv.lock` first.
4. Run `uv sync --project /app/scrapling-server --frozen --no-dev`.
5. Run `uv run --project /app/scrapling-server --frozen scrapling install --force` during the image build so no browser download occurs at runtime.
6. Copy only `scripts/start_scrapling_mcp.py` after the dependency layer.
7. Set `PYTHONUNBUFFERED=1`, `SCRAPLING_MCP_HOST=0.0.0.0`, and a browser cache path whose permissions work for the runtime user.
8. Run as a dedicated non-root user after installation if the installed browser smoke test passes under that user.
9. Start `python /app/scripts/start_scrapling_mcp.py`; do not put the token in `CMD` or `ENTRYPOINT`.

The root `.dockerignore` must exclude at least `.git`, `.env*`, `.venv`, `scrapling-server/.venv`, `.mda`, `.hermes`, caches, test artifacts, `openwiki`, and local credentials.

The Cloud Build file should run Docker with the repository root as context and `-f scrapling-server/Dockerfile`, accept only an `_IMAGE` substitution, and publish that image. Do not pass the MCP token to Cloud Build.

**Step 2: Build locally**

```bash
docker build -f scrapling-server/Dockerfile -t scrapling-mcp:local .
```

Expected: image build completes with the pinned Scrapling lock and browser installed.

**Step 3: Inspect the image for startup and secret hygiene**

```bash
docker image inspect scrapling-mcp:local
```

Expected: launcher is the startup command; no auth token exists in image configuration/history.

**Step 4: Run a local container smoke test**

```bash
export SCRAPLING_MCP_AUTH_TOKEN="$(openssl rand -hex 32)"
docker run --rm -p 18000:8080 \
  -e PORT=8080 \
  -e SCRAPLING_MCP_AUTH_TOKEN \
  -e SCRAPLING_MCP_ALLOWED_HOSTS=localhost:18000 \
  scrapling-mcp:local
```

From a second terminal, run the remote smoke test added in Task 3 against `http://localhost:18000/mcp`. Expected: authentication, tool discovery, HTTP fetch, browser fetch, and session lifecycle pass.

**Step 5: Commit**

```bash
git add .dockerignore scrapling-server/Dockerfile deploy/cloud-run/cloudbuild.scrapling.yaml
git commit -m "build: containerize Scrapling MCP server"
```

### Task 3: Add an opt-in remote MCP integration test

**Objective:** Verify the exact protocol and tool behavior that Cloud Run and Managed Deep Agents must support without creating cloud resources in the default test suite.

**Files:**
- Create: `tests/test_scrapling_mcp_remote.py`
- Reuse: `connectors/scrapling.py`
- Reference: `tests/test_scrapling_mcp_live.py:18-144`

**Step 1: Write the skipped-by-default test**

Gate it with `RUN_SCRAPLING_MCP_REMOTE=1` and require:

```text
SCRAPLING_MCP_URL
SCRAPLING_MCP_AUTH_TOKEN
```

The test must:

1. Send an unauthenticated MCP initialize POST and assert `401`.
2. Load `connectors.scrapling` with the remote values.
3. Assert exactly the existing 13 prefixed tool names.
4. Invoke `scrapling__make_request` against `https://example.com`.
5. Invoke `scrapling__fetch` against `https://example.com`.
6. Open a request session, call `session_make_request`, and close it in `finally`.
7. Clear the MDA connector tool cache in `finally`.
8. Redact the token from all assertion messages and captured errors.

For production URLs, reject plain HTTP. Permit HTTP only for localhost/container testing.

**Step 2: Verify default behavior**

```bash
uv run python -m pytest tests/test_scrapling_mcp_remote.py -q
```

Expected: one skip because the opt-in flag is absent.

**Step 3: Verify against the local container**

```bash
RUN_SCRAPLING_MCP_REMOTE=1 \
SCRAPLING_MCP_URL=http://127.0.0.1:18000/mcp \
uv run python -m pytest tests/test_scrapling_mcp_remote.py -q
```

Expected: pass, with the token inherited from the shell but never printed.

**Step 4: Commit**

```bash
git add tests/test_scrapling_mcp_remote.py
git commit -m "test: add remote Scrapling MCP smoke coverage"
```

### Task 4: Document the Cloud Run deployment runbook

**Objective:** Make project setup, image publication, service configuration, verification, and rollback repeatable without committing environment-specific values.

**Files:**
- Create: `deploy/cloud-run/README.md`
- Modify: `README.md:64-90`

**Step 1: Add parameterized prerequisites**

Use shell variables, not literal project identifiers:

```bash
export PROJECT_ID="<gcp-project-id>"
export REGION="us-central1"
export SERVICE="forex-scrapling-mcp"
export REPOSITORY="agent-containers"
export IMAGE="$REGION-docker.pkg.dev/$PROJECT_ID/$REPOSITORY/scrapling-mcp:<git-sha>"
export SERVICE_ACCOUNT="scrapling-mcp@$PROJECT_ID.iam.gserviceaccount.com"
gcloud config set project "$PROJECT_ID"
```

Document required APIs: Cloud Run, Cloud Build, Artifact Registry, Secret Manager, and Service Usage. Document deployer roles separately from the runtime service account.

**Step 2: Add least-privilege identity and secret setup**

The runbook must create a dedicated service account, create a high-entropy token without placing it in command arguments, add it as a Secret Manager version, and grant `roles/secretmanager.secretAccessor` on that one secret only. Pin the secret version in the Cloud Run service rather than using `latest`.

Do not grant Editor, Owner, broad Secret Accessor, storage access, or a VPC role to the runtime identity.

**Step 3: Add immutable image publication**

```bash
gcloud builds submit . \
  --region="$REGION" \
  --config=deploy/cloud-run/cloudbuild.scrapling.yaml \
  --substitutions="_IMAGE=$IMAGE"
```

Record the resulting image digest and deploy by digest for production/rollback.

**Step 4: Add the initial private deployment**

Use a private first revision so its assigned `run.app` hostname can be discovered before public traffic is allowed:

```bash
gcloud run deploy "$SERVICE" \
  --image="$IMAGE" \
  --region="$REGION" \
  --service-account="$SERVICE_ACCOUNT" \
  --execution-environment=gen2 \
  --cpu=2 \
  --memory=4Gi \
  --concurrency=4 \
  --min-instances=0 \
  --max-instances=1 \
  --timeout=300s \
  --port=8080 \
  --cpu-boost \
  --ingress=all \
  --invoker-iam-check \
  --set-env-vars="SCRAPLING_MCP_HOST=0.0.0.0" \
  --update-secrets="SCRAPLING_MCP_AUTH_TOKEN=scrapling-mcp-auth:<version>"
```

Expected: revision becomes Ready but still requires Cloud Run IAM authentication.

**Step 5: Discover and configure the exact allowed hostname**

```bash
SERVICE_URL="$(gcloud run services describe "$SERVICE" \
  --region="$REGION" --format='value(status.url)')"
SERVICE_HOST="${SERVICE_URL#https://}"
gcloud run services update "$SERVICE" \
  --region="$REGION" \
  --update-env-vars="SCRAPLING_MCP_ALLOWED_HOSTS=$SERVICE_HOST"
```

Keep the service private while validating it through `gcloud run services proxy`. Verify wrong/missing app tokens fail and an authenticated MCP client succeeds.

**Step 6: Make the edge reachable by MDA without removing app authentication**

After private validation, disable only the Cloud Run invoker IAM check using the current recommended Cloud Run mechanism:

```bash
gcloud run services update "$SERVICE" \
  --region="$REGION" \
  --no-invoker-iam-check
```

Immediately verify:

- `POST $SERVICE_URL/mcp` without the app token returns `401` from Scrapling.
- The authenticated remote smoke test passes over HTTPS.
- The service remains `max-instances=1`.
- No `--no-auth` option is present.

If this update is blocked by organization policy, do not grant broader public IAM as a workaround; return to the authentication architecture decision above.

**Step 7: Add rollback commands**

Capture revision names before cutover and document:

```bash
gcloud run revisions list --service="$SERVICE" --region="$REGION"
gcloud run services update-traffic "$SERVICE" \
  --region="$REGION" \
  --to-revisions="<previous-revision>=100"
```

Retain the previous image digest and secret version for the agreed rollback window.

**Step 8: Commit**

```bash
git add deploy/cloud-run/README.md README.md
git commit -m "docs: add Scrapling Cloud Run deployment runbook"
```

### Task 5: Validate the deployed Cloud Run MCP server

**Objective:** Prove Cloud Run behavior before pointing LangSmith at it.

**Files:**
- No source changes expected.
- Execute: `tests/test_scrapling_mcp_remote.py`

**Step 1: Inspect live configuration**

```bash
gcloud run services describe "$SERVICE" --region="$REGION"
```

Verify the image digest, service account, max instances 1, concurrency 4, 300-second timeout, gen2, secret reference, exact allowed host, and public invoker check setting.

**Step 2: Run protocol/tool smoke tests**

```bash
RUN_SCRAPLING_MCP_REMOTE=1 \
SCRAPLING_MCP_URL="$SERVICE_URL/mcp" \
uv run python -m pytest tests/test_scrapling_mcp_remote.py -q
```

Expected: pass for 401 enforcement, 13-tool discovery, request fetch, browser fetch, and request-session lifecycle.

**Step 3: Exercise restart semantics**

Open a disposable session, deploy/restart a revision, then confirm the old session fails clearly and a newly opened session succeeds. Record this as expected behavior, not a Cloud Run bug.

**Step 4: Check logs and resource use**

Inspect Cloud Run logs, startup latency, CPU, peak memory, request latency, 4xx/5xx, and instance count. Confirm no authorization token or sensitive URL query parameter is logged.

### Task 6: Configure Managed Deep Agents for the remote endpoint

**Objective:** Use the Cloud Run endpoint through the repository's existing managed connector without adding server dependencies to the agent runtime.

**Files:**
- Modify only if validation reveals a gap: `connectors/scrapling.py:9-50`
- Modify: local `.env` (never commit)
- Test: `tests/test_scrapling_connector.py:30-59`

**Step 1: Preserve the connector contract**

Keep:

```python
"transport": "http"
"url": SCRAPLING_MCP_URL
"headers": {"Authorization": f"Bearer {SCRAPLING_MCP_AUTH_TOKEN}"}
"include_tools": SCRAPLING_TOOLS
"throw_on_load_error": True
```

Do not switch to `sse`, stdio, or `automatic_sse_fallback`. Keep the explicit 13-tool allowlist so upstream additions are not silently exposed.

**Step 2: Add deployment secrets locally**

Put these values in the untracked project `.env` using the exact HTTPS endpoint and the same secret value held by Google Secret Manager:

```text
SCRAPLING_MCP_URL=https://<cloud-run-host>/mcp
SCRAPLING_MCP_AUTH_TOKEN=<shared-secret>
```

The MDA CLI will forward these non-reserved values as hosted deployment secrets; `.env` itself must remain excluded from source and build archives.

**Step 3: Run connector and build validation**

```bash
uv run python -m pytest tests/test_scrapling_connector.py -q
mda build .
```

Expected: connector tests pass and the MDA build succeeds without packaging `scrapling-server/.venv`, the Scrapling server package, Docker files, or `.env` into the agent runtime.

**Step 4: Test with local MDA runtime against Cloud Run**

```bash
mda dev .
```

In LangSmith Studio, issue a deterministic prompt that requires `scrapling__make_request` on `https://example.com`. Verify the tool name, successful result, and trace. Then test `scrapling__fetch`.

### Task 7: Deploy the forex sentiment agent to LangSmith

**Objective:** Deploy only after remote MCP validation, then prove the hosted runtime can reach Cloud Run.

**Files:**
- No source changes expected unless MDA build validation identifies one.

**Step 1: Deploy a development MDA revision**

```bash
mda deploy . \
  --name forex-sentiment-agent-dev \
  --deployment-type dev
```

Expected: hosted build reaches `DEPLOYED`; `SCRAPLING_MCP_URL` and `SCRAPLING_MCP_AUTH_TOKEN` are present as hosted secrets, not source files.

**Step 2: Run a hosted end-to-end smoke test**

From the LangSmith deployment/Studio, ask the agent to scrape a fixed public test page. Verify:

- Startup does not fail while loading the MCP connector.
- The trace exposes exactly the 13 `scrapling__*` tools.
- A `scrapling__make_request` call succeeds.
- A browser-backed `scrapling__fetch` call succeeds.
- Cloud Run logs show the corresponding authenticated requests.

**Step 3: Test failure behavior**

In the dev deployment only, temporarily point to an invalid endpoint or wrong token, redeploy, and confirm `throw_on_load_error=True` prevents a falsely healthy agent with missing scraping capability. Restore the correct secrets and redeploy.

**Step 4: Promote to production**

```bash
mda deploy . \
  --name forex-sentiment-agent \
  --deployment-type prod
```

Expected: production revision is deployed and the same hosted smoke test passes.

### Task 8: Add production operations and security controls

**Objective:** Make the remote scraper operable without overstating Cloud Run's durability.

**Files:**
- Modify: `deploy/cloud-run/README.md`
- Modify: `README.md`
- Consider later: monitoring-as-code under the organization's existing infrastructure repository, not this repo unless requested.

**Step 1: Document monitoring**

Create dashboards/alerts for Cloud Run request count, authenticated 401s, 4xx/5xx, p95 latency, container startup failures, instance count, CPU, memory, and billable time. Add a low-frequency synthetic MCP initialize/list-tools check using the token from a secure monitor secret.

**Step 2: Document capacity tuning**

- Keep max instances 1 until session state is externalized or session tools are removed.
- Tune concurrency downward if browser memory is unstable; tune upward only with parallel browser smoke/load tests.
- Consider min instances 1 only after measuring cold-start impact and accepting cost.
- Keep `MDA timeout < Cloud Run timeout`; a Cloud Run timeout does not stop container work automatically.

**Step 3: Document token rotation**

Scrapling 0.4.15 accepts a single static token, so same-service rotation can create a mismatch between Cloud Run and MDA. Use a blue/green procedure for no-downtime rotation:

1. Deploy a second Cloud Run service/revision with the new token and exact new hostname.
2. Pass the remote smoke test.
3. deploy MDA with the new URL/token.
4. verify hosted traces.
5. disable/delete the old endpoint after the rollback window.

Never overwrite the only working secret version before the MDA cutover.

**Step 4: Document SSRF/egress posture**

Treat the MCP token as access to an arbitrary outbound fetch/browser service. Keep the Cloud Run identity nearly permissionless, do not attach private networks, do not store unrelated credentials in the service, and avoid sensitive data in target URL query strings. If future requirements add VPC access or valuable Google permissions, add an egress proxy/network policy and destination validation that blocks loopback, link-local/metadata, private/reserved ranges, unsafe redirects, and DNS rebinding before proceeding.

**Step 5: Document release behavior**

Do not split traffic gradually while process-local sessions are active. Schedule cutovers, expect open sessions to be lost, send all traffic to one revision, and retain the previous image/revision for rollback.

**Step 6: Commit**

```bash
git add deploy/cloud-run/README.md README.md
git commit -m "docs: add Scrapling MCP production operations"
```

### Task 9: Run the complete release gate

**Objective:** Verify source, container, cloud server, and hosted agent together before declaring migration complete.

**Files:**
- No source changes expected.

**Step 1: Run the local suite**

```bash
uv run python -m pytest -q
```

Expected: all default tests pass; opt-in live/remote tests skip unless enabled.

**Step 2: Run local server integration**

```bash
RUN_SCRAPLING_MCP_LIVE=1 \
uv run python -m pytest tests/test_scrapling_mcp_live.py -q
```

Expected: local authenticated Streamable HTTP compatibility remains intact.

**Step 3: Run container and remote integration**

Build the image again from a clean checkout, start it locally, and pass `tests/test_scrapling_mcp_remote.py`. Then run the same test against the Cloud Run HTTPS URL.

**Step 4: Build and deploy-check MDA**

```bash
mda build .
mda logs . --lines 200 --level error
```

Expected: build succeeds; no connector, auth, protocol, browser, or timeout errors appear after the hosted smoke run.

**Step 5: Record evidence**

Record, without secrets:

- source commit and image digest;
- Cloud Run service/revision and configuration snapshot;
- remote test result;
- MDA deployment revision/dashboard URL;
- LangSmith trace ID for the successful `scrapling__make_request` and `scrapling__fetch` calls;
- rollback target revision.

---

## 5. Risks and tradeoffs

| Risk | Impact | Mitigation / decision |
| --- | --- | --- |
| MDA cannot refresh Google Cloud Run ID tokens | A private IAM-protected Cloud Run endpoint is not directly usable through documented static headers | Public Cloud Run edge + mandatory high-entropy Scrapling bearer token; stop and redesign if org policy forbids it |
| `Authorization` is needed by both Cloud Run IAM and Scrapling | Two bearer schemes cannot share the same header; static `X-Serverless-Authorization` ID tokens still expire | Do not stack Cloud Run IAM and Scrapling auth for MDA baseline |
| Process-local Scrapling sessions | Session calls can hit missing state after scale-out/restart | `max-instances=1`, disposable sessions, retry/recreate, no gradual traffic splitting |
| Cloud Run may restart the only instance | Open browser/request sessions are lost | Explicitly document non-durability; agent closes/recreates sessions |
| Browser workload exceeds memory or latency budget | OOM, 5xx, slow tools | Start at 2 vCPU/4 GiB/concurrency 4; monitor and load-test before tuning |
| Long tool calls exceed client/platform timeout | Client sees failure while container might continue work | Keep MDA at 120s and Cloud Run at 300s; bound tool timeouts/retries |
| Token leakage exposes a general web-fetch capability | Abuse, cost, SSRF | Secret Manager, MDA hosted secret, no logs/CLI args, least-privilege identity, rate/capacity limits, rotation runbook |
| Public scraper can reach cloud metadata/private destinations | Credential/SSRF exposure if service identity has useful access | Dedicated nearly permissionless identity, secret-level access only, no VPC; add egress enforcement before granting more access |
| Dependency protocol mismatch | Agent build conflict or runtime failures | Preserve isolated server/client lockfiles and test over HTTP, not a shared Python environment |
| MDA public beta / US-only availability | API/operational changes and region limits | Pin MDA version, validate against live docs before implementation, deploy dev before prod |
| Upstream Scrapling adds/changes tools | Unexpected agent capability drift | Preserve `include_tools` allowlist and exact 13-tool contract test |

## 6. Open questions to resolve before production cutover

1. Does the GCP organization allow `--no-invoker-iam-check` for a service protected at the application layer? If not, select the OAuth/gateway/BYOC alternative before implementation.
2. Is one concurrent Cloud Run instance acceptable for the expected forex-agent traffic? If not, either expose only the six one-shot tools or design durable/external session routing before scale-out.
3. Is `us-central1` acceptable for data residency and latency relative to the US LangSmith MDA runtime, or is another US Cloud Run region required?
4. Does production need min instances 1 for cold-start latency, and is the resulting idle cost approved?
5. Is a custom domain + external load balancer + Cloud Armor required at launch, or is the authenticated `run.app` endpoint acceptable for phase one?
6. What retention and redaction policy applies to scraped URLs/content in Cloud Run and LangSmith traces?

## 7. Authoritative references checked

- [Managed Deep Agents MCP connectors](https://docs.langchain.com/langsmith/python/managed-deep-agents-mcp-connectors) — remote `http` Streamable HTTP, static headers, tool allowlists, timeout, and error behavior.
- [Deploy a Managed Deep Agent](https://docs.langchain.com/langsmith/python/managed-deep-agents-deploy) — `.env` secret forwarding, deployment flow, and US public-beta scope.
- [Host MCP servers on Cloud Run](https://docs.cloud.google.com/run/docs/host-mcp-servers) — Cloud Run support for remote Streamable HTTP and client authentication choices.
- [Build and deploy a remote MCP server on Cloud Run](https://docs.cloud.google.com/run/docs/tutorials/deploy-remote-mcp-server) — `0.0.0.0`, `PORT`, container build, and Cloud Run deployment pattern.
- [Cloud Run container runtime contract](https://docs.cloud.google.com/run/docs/container-contract) — listener, port, timeout, and instance behavior.
- [Cloud Run request timeout](https://docs.cloud.google.com/run/docs/configuring/request-timeout) — default 5 minutes, maximum 60 minutes, and continued work caveat after 504.
- [Cloud Run concurrency](https://docs.cloud.google.com/run/docs/configuring/concurrency) and [maximum instances](https://docs.cloud.google.com/run/docs/configuring/max-instances) — capacity controls.
- [Cloud Run session affinity](https://docs.cloud.google.com/run/docs/configuring/session-affinity) — best-effort only; in-memory state cannot rely on it.
- [Cloud Run secrets](https://docs.cloud.google.com/run/docs/configuring/services/secrets) — Secret Manager references and version pinning guidance.
- [Cloud Run public access](https://docs.cloud.google.com/run/docs/authenticating/public) — current `--no-invoker-iam-check` mechanism.
- [MCP Streamable HTTP transport specification (2025-11-25)](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports) — single endpoint, POST/GET behavior, Host/Origin security, auth, and session semantics used by the current MDA client line.
- [Scrapling MCP Server Guide](https://scrapling.readthedocs.io/en/latest/ai/mcp-server.html) — 13 tools, Streamable HTTP, bearer auth, exact allowed hosts, browser installation, and persistent-session behavior.

## 8. Definition of done

The migration is done only when the Cloud Run HTTPS endpoint passes the remote protocol/tool/session test, the service configuration and least-privilege identity are verified, the production Managed Deep Agent invokes both HTTP- and browser-backed Scrapling tools in LangSmith traces, secrets are absent from artifacts/logs, and rollback has a known-good Cloud Run revision plus an MDA recovery path.

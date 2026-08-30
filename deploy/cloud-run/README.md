# Scrapling MCP on Cloud Run

Runbook for publishing and operating the Scrapling MCP server as a private-by-token
Cloud Run service. Every command is parameterized; no project identifier, hostname,
or token belongs in this file or in git.

The service is authenticated by a static bearer token that Scrapling itself checks.
Cloud Run's own invoker IAM check is disabled at the end (step 6) because Managed
Deep Agents can only send static headers — the app token is the security boundary,
so it must never be weakened.

## 0. Parameters

```bash
export PROJECT_ID="<gcp-project-id>"
export REGION="us-central1"
export SERVICE="forex-scrapling-mcp"
export REPOSITORY="agent-containers"
export SERVICE_ACCOUNT="scrapling-mcp@$PROJECT_ID.iam.gserviceaccount.com"
export IMAGE="$REGION-docker.pkg.dev/$PROJECT_ID/$REPOSITORY/forex-scrapling-mcp:sha256:0ed4b0f7b229e4bcd9f4529e8b1c7b1bdd838306d1451274d75df1e8222b218a"
gcloud config set project "$PROJECT_ID"
```

**APIs:** `run`, `cloudbuild`, `artifactregistry`, `secretmanager`, `serviceusage`.

```bash
gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
  artifactregistry.googleapis.com secretmanager.googleapis.com \
  serviceusage.googleapis.com
```

**Roles.** The *deployer* (you) needs `roles/run.admin`, `roles/cloudbuild.builds.editor`,
`roles/artifactregistry.writer`, `roles/secretmanager.admin`, and
`roles/iam.serviceAccountUser` on the runtime account. The *runtime* service account
gets exactly one grant, in step 2. These are deliberately different identities.

## 1. Artifact Registry

```bash
gcloud artifacts repositories create "$REPOSITORY" \
  --repository-format=docker --location="$REGION" \
  --description="Container images for agent sidecar services"
```

## 2. Runtime identity and token

The runtime account holds no project-wide role. It reads one secret and nothing else.

```bash
gcloud iam service-accounts create scrapling-mcp \
  --display-name="Scrapling MCP Cloud Run runtime"
```

Generate the token and pipe it straight into Secret Manager. It is never echoed, never
an argument, and never written to a file that git can see:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32), end='')" \
  | gcloud secrets create scrapling-mcp-auth --data-file=- --replication-policy=automatic
```

Grant access to that one secret:

```bash
gcloud secrets add-iam-policy-binding scrapling-mcp-auth \
  --member="serviceAccount:$SERVICE_ACCOUNT" \
  --role="roles/secretmanager.secretAccessor"
```

Do **not** grant Editor, Owner, project-level Secret Accessor, storage, or VPC roles.

To read the token later (for the smoke test or the MDA connector), pull it into a
shell variable rather than printing it:

```bash
TOKEN="$(gcloud secrets versions access 1 --secret=scrapling-mcp-auth)"
```

## 3. Build and publish

The build context is the repository root. `.dockerignore` denies everything (`*`) and
re-includes only `scrapling-server/pyproject.toml`, `scrapling-server/uv.lock`, and
`scripts/start_scrapling_mcp.py`, so nothing else can be baked into an image layer
even if it is added to the repo later. Docker always reads the `-f` Dockerfile
regardless of the ignore rules, which is why the deny-all pattern still builds.

Two different gates, and it is worth not confusing them: `.dockerignore` governs the
**image**, while what `gcloud builds submit` uploads to its GCS source bucket is
governed by `.gcloudignore`. There is no `.gcloudignore` here, so gcloud falls back to
`.gitignore` (which covers `.env` and `.env.*`) and always excludes `.git`. A
credential that is neither gitignored nor dockerignored therefore stays out of the
image but still lands in the Cloud Build source bucket. Add a `.gcloudignore` if you
want that upload constrained independently of git.

```bash
gcloud builds submit . \
  --config=deploy/cloud-run/cloudbuild.scrapling.yaml \
  --substitutions="_IMAGE=$IMAGE"
```

`_IMAGE` is the only substitution. The MCP token must never be passed to Cloud Build.

Record the digest and deploy by digest for production and rollback:

```bash
gcloud artifacts docker images describe "$IMAGE" --format='value(image_summary.digest)'
```

The first build measured 2m33s end to end and pushed 0.75 GB compressed across 11
layers, the largest being 421 MB (Chromium and its shared libraries). The Cloud Build
config still sets `timeout: 1800s` as headroom — an upstream Chromium revision or a
cold apt mirror can cost far more than the observed time, and a build that dies at the
10 minute default leaves a half-pushed tag behind.

## 4. First deployment — private

Deploy privately first so the assigned `run.app` hostname can be discovered before
any public traffic is possible.

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
  --update-secrets="SCRAPLING_MCP_AUTH_TOKEN=scrapling-mcp-auth:1"
```

Pin the secret **version** (`:1`), not `latest`, so a rotation cannot change a running
revision underneath you.

`max-instances=1` is not a cost control — Scrapling sessions live in the server
process's memory, so a second instance would serve `list_sessions` and
`session_fetch` from a different session table.

## 5. Host allowlist

Scrapling's Streamable HTTP transport rejects unexpected `Host` headers
(DNS-rebinding protection). The hostname only exists after the first deploy:

```bash
SERVICE_URL="$(gcloud run services describe "$SERVICE" \
  --region="$REGION" --format='value(status.url)')"
SERVICE_HOST="${SERVICE_URL#https://}"
gcloud run services update "$SERVICE" \
  --region="$REGION" \
  --update-env-vars="SCRAPLING_MCP_ALLOWED_HOSTS=$SERVICE_HOST"
```

Validate while still private:

```bash
gcloud run services proxy "$SERVICE" --region="$REGION" --port=8080 &
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:8080/mcp \
  -H 'Accept: application/json, text/event-stream' -H 'Content-Type: application/json' \
  --data '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}'   # expect 401
```

## 6. Open the edge, keep the app token

```bash
gcloud run services update "$SERVICE" --region="$REGION" --no-invoker-iam-check
```

Then verify, in order:

- `POST $SERVICE_URL/mcp` with no token returns `401` from Scrapling.
- The authenticated remote test passes over HTTPS (section 7).
- `max-instances` is still `1`.
- `--no-auth` appears nowhere in the service configuration.

If org policy blocks `--no-invoker-iam-check`, do **not** grant broader public IAM as
a workaround. Revisit the authentication architecture instead.

## 7. Verify the deployment

```bash
RUN_SCRAPLING_MCP_REMOTE=1 \
PYTHONPATH="$PWD" \
SCRAPLING_MCP_URL="$SERVICE_URL/mcp" \
SCRAPLING_MCP_AUTH_TOKEN="$TOKEN" \
uv run --frozen pytest tests/test_scrapling_mcp_remote.py -q
```

The test refuses to send the token over plain HTTP to anything but loopback, and
redacts it from every assertion message.

Confirm the token never reached the logs:

```bash
gcloud run services logs read "$SERVICE" --region="$REGION" --limit=200 \
  | grep -c "$TOKEN"   # expect 0
```

## 8. Rollback

Capture revisions before any cutover:

```bash
gcloud run revisions list --service="$SERVICE" --region="$REGION"
gcloud run services update-traffic "$SERVICE" \
  --region="$REGION" --to-revisions="<previous-revision>=100"
```

Retain the previous image digest and secret version for the agreed rollback window.
Never delete or overwrite the only working secret version before the MDA cutover is
complete.

## Operational notes

- **Engines.** All three Scrapling fetchers work in this image. `stealthy_fetch` uses
  `patchright` (a stealth-patched Playwright) against the same Chromium, not Camoufox,
  so `scrapling install` covers every engine and nothing is downloaded at runtime.
- **Cold start.** With `min-instances=0`, the first request after idle pays container
  start plus Chromium launch. `--cpu-boost` covers this; browser fetches measured
  ~1.8 s warm.
- **Sessions are disposable.** They live in the server process. Any revision change,
  scale-to-zero, or instance restart drops them. Agents must tolerate a missing
  `session_id` and reopen.
- **Token rotation.** Add a new secret version, deploy a revision pinned to it, verify,
  then disable the old version. Update the MDA connector in the same window.

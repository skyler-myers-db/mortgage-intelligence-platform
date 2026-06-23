# Deployment guide

## Local

```bash
cp .env.example .env.local
pip install -r requirements.txt
npm --prefix frontend install
npm --prefix frontend run dev
uvicorn backend.main:app --reload
```

## Build frontend

```bash
npm --prefix frontend run build
```

FastAPI serves `frontend/dist` automatically if present.

## Databricks App

1. Fill `.env.local` with workspace, warehouse, and lender values. Customer
   forks should set the public brand before deploy:

```bash
MIP_LENDER_NAME="Acme Mortgage"
# Optional; defaults from MIP_LENDER_NAME when unset.
MIP_TENANT_ID="acme_mortgage"
MIP_DEFAULT_CATALOG="acme_mip"
# Required for customer/non-dev targets; dev warns when omitted.
MIP_COTALITY_ID_MASK_SECRET="<deployment-scoped-random-secret>"
```

   See [`docs/se-onboarding.md`](se-onboarding.md) for the full
   one-workspace-one-lender tenancy checklist.
2. For a customer fork, rebind the bundle's single workspace-host anchor
   before any deploy:

```bash
./scripts/configure-workspace.sh https://<customer-workspace>.cloud.databricks.com
```

3. Run the deployment script. It builds the frontend, provisions Genie if
   needed, validates/plans/deploys the direct bundle through the env-aware wrapper,
   promotes the uploaded source to the running Databricks App, and runs
   the refresh/smoke steps.

```bash
./scripts/deploy.sh -t dev
```

The Entrada dev target also supports the plain Databricks bundle resource path:

```bash
databricks bundle deploy -t dev --profile DEFAULT
```

That command updates bundle-managed resources and now binds the governed
Entrada Genie space directly, so it no longer sends the placeholder
`00000000PLACEHOLDER` to Databricks Apps. It does not run the rest of the
shipping workflow: use `./scripts/deploy.sh` when you also need frontend build,
app snapshot promotion, refresh jobs, Genie rebinding, and smoke checks.

For a narrow resource-only recovery, `make bundle-validate`,
`make bundle-plan`, and `make bundle-deploy` are safe because they use
`tools/databricks/bundle_env.py`.
After a resource-only deploy, promote the uploaded source with
`databricks apps deploy mip-app --mode SNAPSHOT`.

For disaster-recovery rollback paths, including Lakebase point-in-time
restore, prior app snapshots, source-regression rollback via
`git checkout <prior-good-sha>`, Genie space re-provisioning, audit archival,
and governed-action HMAC key rotation, use
[`docs/disaster-recovery.md`](disaster-recovery.md).

## Resources

Databricks App resources expected by `app.yaml`:

- `sql_warehouse`: SQL warehouse resource, `CAN_USE`.
- `genie_space`: Genie space resource, `CAN_RUN`.
- `database`: Lakebase database resource, `CAN_CONNECT_AND_CREATE`.

## Optional OTLP log export

The shipped default is stdout-safe JSON only. To prove durable
off-platform logs, configure the collector endpoint before promoting the
snapshot, plus headers when the collector requires auth:

```bash
MIP_OTEL_ENDPOINT=https://<otlp-collector>/v1/logs
# MIP_OTEL_HEADERS is stored in Databricks Secrets and referenced by
# the Databricks App resource named otel_headers. Do not paste it into
# app.yaml, bundle JSON, screenshots, or shell history.
```

The endpoint must be a customer-owned collector reachable from the
Databricks App container. Without an endpoint, `/api/v1/admin/health` should honestly
report `"log_export": "stdout-only"`. With them configured and the
OpenTelemetry exporter wheels present in `requirements.txt`, it should
report `"log_export": "otlp"`; then verify the collector has a fresh
deployed app `correlation_id`.

Current Entrada dev status (2026-05-15): external OTLP is not configured
on `mip-app`. CLI/API evidence shows active deployment
`01f14ff2c4cd10b99ebad8f8785c307f` is `SUCCEEDED`, the app is `RUNNING`,
and only the warehouse, Genie, Lakebase, and lifecycle job resources are
attached; no app secret resource is attached for collector headers.
`/api/v1/admin/health` returns
`log_export: stdout-only`. The Databricks CLI `apps deploy` command in
use here (`0.299.1`) has no top-level `--env` flag, but the underlying
API accepts deployment `env_vars` via `--json`. That list replaces the
app.yaml env list, so one-off proof deploys must include the normal
resource-derived env vars as well as `MIP_OTEL_ENDPOINT`. For production
collector headers, use the `prod_otlp` app secret resource and a
deployment payload with `value_from: otel_headers`; do not paste
collector tokens into `app.yaml`, a bundle var, or shell history.

App-side proof was completed on 2026-05-14 with temporary deployment
`01f14fe0164a1b6388f9d240679492db`: `/api/v1/admin/health` reported
`log_export: otlp`, a sanitized RUM request returned correlation id
`b07b2f0825a043188fda96e041a14d19`, and the external collector received
the matching OTLP HTTP protobuf payload. The sandbox was then restored to
stdout-only so it is not left exporting logs to the temporary collector.

Secret-backed header proof was completed on 2026-05-14/15 with temporary
deployment `01f14ff1cb5513a9824b1e040701141d`: `MIP_OTEL_HEADERS` was
resolved through Databricks App resource `value_from: otel_headers`,
backed by a temporary non-sensitive `mip/otel-headers` secret. The
external collector received request `d64260ce-232a-498e-bb9a-4bf65306c090`
with proof header `x-mip-otel-proof=proof-20260515T000246Z` and a
matching `/api/v1/telemetry/rum` log body for correlation id
`c864cd1bbbf44779874fdb235ae7c6bf`. The sandbox was restored to
deployment `01f14ff2c4cd10b99ebad8f8785c307f` with no OTLP env vars, the
temporary app secret resource removed, and the temporary Databricks
Secret deleted.

For customer production, use the non-default `prod_otlp` target after the
customer provides a collector endpoint and a Databricks Secret containing
`MIP_OTEL_HEADERS`. The target attaches an app secret resource named
`otel_headers`; `tools/databricks/otlp_deploy_payload.py` then emits the
full deployment `env_vars` list with `MIP_OTEL_HEADERS` set by
`value_from: otel_headers`. Do not add `MIP_OTEL_HEADERS` directly to
`app.yaml`; that would make ordinary dev deploys depend on a secret that
does not exist in every workspace.

All MIP refresh schedules deploy paused by default, including `prod` and
`prod_otlp`. The app's Admin **Data operations** panel is the normal
customer-facing refresh surface. Only unpause FRED or lifecycle schedules
after the customer approves a recurring cadence and confirms the target writes
to an isolated catalog; otherwise multiple bundle targets can contend on the
same Unity Catalog tables and burn avoidable compute.

If OTLP is being added to an already-deployed app in the same workspace,
avoid deploying a second bundle target that tries to recreate existing
Lakebase or warehouse resources unless that target has imported/owns
those resources. In that upgrade path, preserve the existing app
resources, add the `otel_headers` app secret resource, then deploy using
the helper-generated full `env_vars` payload.

## Release checklist

The app runs on live Unity Catalog + Lakebase in every environment — there is no mock-mode runtime toggle (see [CLAUDE.md](../CLAUDE.md) "Negative prompting"). Flakiness is handled by the resilience layer (retry, warehouse warm-start, SWR cache, circuit breaker, degraded-state banner), never by silent mock fallback.

- Warehouse ID + Genie space ID are set (`BUNDLE_VAR_sql_warehouse_id`, `BUNDLE_VAR_genie_space_id`).
- Lakebase schema + `mip_app.action_audit` table exist.
- Genie space is curated against `mip.semantics.*` metric views only.
- `/api/v1/health` returns `status: ok`; `/api/v1/admin/health` reports
  `warehouse: up`, `genie: up`, `lakebase: up`, all circuits `closed`,
  and the current `log_export` posture.
- Resilience is observable: degraded banner renders when a dependency drops; Approve writes a real row to `mip_app.action_audit`.
- `tools/databricks/bundle_env.py validate -t dev` passes.
- `tools/databricks/bundle_env.py plan -t dev` shows the expected direct
  deployment changes.
- Optional production observability gate: if external log durability is
  required, `MIP_OTEL_ENDPOINT` and any required `MIP_OTEL_HEADERS` are
  configured, `/api/v1/admin/health` reports `log_export: otlp`, and the
  collector has a fresh deployed-app log line. Validate the customer
  evidence packet with
  `python tools/databricks/otlp_customer_retention_gate.py <evidence.json>`
  before claiming durable customer retention. If any customer-owned
  collector, real secret reference, retention/ACL proof, or fresh
  collector query proof input is missing, record the gate as bounded
  stdout-only or transport-only instead of treating local mocked OTLP
  tests as external collector proof.
- Frontend build passes (`npm --prefix frontend run build`).
- Python tests pass (`pytest -q`).
- Talk track rehearsed.

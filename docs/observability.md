# Observability — operator guide

Scope: how logs leave the Mortgage Intelligence Platform Databricks App
and reach a queryable sink, and what to do when the rolling-hour
counters on `/api/health` look odd after a restart.

## 1. Log format

Every log line the backend emits is a single-line JSON object produced
by `backend/services/observability.py::StructuredFormatter`. Minimum
keys on every line:

| Key              | Type    | Notes                                                |
|------------------|---------|------------------------------------------------------|
| `ts`             | string  | ISO-8601 UTC, microsecond precision                  |
| `level`          | string  | `DEBUG` / `INFO` / `WARNING` / `ERROR`               |
| `logger`         | string  | Python logger name (`mip.databricks_sql`, etc.)      |
| `event`          | string  | Structured event id (e.g. `warehouse_query_end`)     |
| `correlation_id` | string  | UUID4 hex; one per inbound HTTP request              |

Dependency calls (`timed_dependency(...)`) additionally produce:

| Key            | Example                    |
|----------------|----------------------------|
| `dependency`   | `warehouse` / `lakebase` / `genie` |
| `operation`    | `execute` / `fetchone` / `ask`     |
| `duration_ms`  | `142.73`                   |
| `outcome`      | `ok` / `error`             |

Caller-supplied kwargs (`rows_returned`, `statement_hash`, …) appear as
top-level keys. PII-denylisted kwargs are replaced with `<redacted>`
before serialisation — never the raw value.

Sample line:

```json
{"ts":"2026-04-22T19:18:41.332Z","level":"INFO","logger":"mip.databricks_sql","event":"warehouse_query_end","correlation_id":"a1b2c3d4e5f6789012345678abcdef01","dependency":"warehouse","duration_ms":142.73,"outcome":"ok","statement_hash":"4f8b2e1c9a7d3e55","rows_returned":127}
```

## 2. Where the logs go by default

On Databricks Apps the runtime captures the container's stdout and
stderr and exposes them in the App's **Logs** tab in the workspace UI
(Workspace → Apps → *mip-app* → Logs). Each line in that tab is one of
our JSON objects — ready to paste into a log parser.

Reference: the [Databricks Apps logging
documentation](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/overview#logs)
covers the UI location, retention, and the CLI download path. Do not
memorise CLI flag names from this file; check that page for the current
shape — it's been changing as Apps GA'd.

For local development (`uvicorn backend.main:app`) the same JSON lands
on your terminal's stdout. Pipe it through `jq` to pretty-print:

```bash
uvicorn backend.main:app | tee /tmp/mip.log | jq 'select(.level != "DEBUG")'
```

## 3. Shipping logs to an external sink (optional)

Set two env vars at App deploy time to turn on OTLP log export:

```
MIP_OTEL_ENDPOINT=https://<otlp-collector>/v1/logs
# MIP_OTEL_HEADERS is stored in Databricks Secrets and referenced by
# the Databricks App resource named otel_headers.
```

When `MIP_OTEL_ENDPOINT` is set at process start, the backend wires
`opentelemetry-sdk` + `opentelemetry-exporter-otlp` so every structured
log line is shipped to the endpoint in addition to stdout. When unset,
behaviour is unchanged (stdout JSON only).

The production App image installs the exporter wheels from
`requirements.txt`. For minimal local installs, the same dependencies are
also exposed as the `otel` extra:

```bash
pip install .[otel]
# or, equivalently:
pip install 'opentelemetry-sdk>=1.27,<2' 'opentelemetry-exporter-otlp>=1.27,<2'
```

If `MIP_OTEL_ENDPOINT` is set but the wheels are absent, the backend
logs one `WARNING` line at boot and keeps running on stdout-only —
the app does NOT crash. The `/api/admin/health` body's `log_export` key is the
at-a-glance status:

| `log_export` value | Meaning                                                |
|--------------------|--------------------------------------------------------|
| `"stdout-only"`    | Default. No durable export configured.                  |
| `"otlp"`           | OTLP handler attached; verify collector receipt for durability. |

The endpoint is used verbatim for the exporter but boot diagnostics log
only a sanitized endpoint label: scheme, host, port, and a generic path
marker. Header values are never logged; only sorted header keys appear
in the successful wiring line. Put credentials in `MIP_OTEL_HEADERS`,
not in the URL.

### Proof status

The repo carries two local proof lanes:

- Missing optional wheels with `MIP_OTEL_ENDPOINT` set: boot logs a
  warning and continues on stdout-only. This preserves the default
  Databricks Apps posture.
- Mocked OTLP exporter: a structured log emitted through the normal
  root logger path reaches the OTLP handler as the same redacted JSON
  body that stdout receives; endpoint credentials, query strings, and
  header values are not present in the handler body.

Run the focused proof:

```bash
.venv/bin/python -m pytest -q tests/unit/test_observability.py
MIP_BYPASS_STARTUP_CHECKS=1 MIP_OTEL_ENDPOINT=http://127.0.0.1:9999 \
  .venv/bin/python -c "from backend.main import app; print('boot OK with OTEL env')"
```

This does **not** prove a deployed external collector. Close that gate
only after a real Databricks App has `MIP_OTEL_ENDPOINT` configured
(`MIP_OTEL_HEADERS` too when the collector requires auth),
`/api/admin/health` reports `"log_export": "otlp"`, and the target
collector shows a fresh `correlation_id` from the deployed app.

### Deployed sandbox status — 2026-05-15

The Entrada dev Databricks App is bounded at stdout-only until a
collector endpoint is supplied for a deployment:

- `databricks apps get mip-app --profile DEFAULT -o json` reports active
  deployment `01f14ff2c4cd10b99ebad8f8785c307f`, `SUCCEEDED`, with the
  app `RUNNING` and only
  `sql_warehouse`, `genie_space`, `database`, and `lifecycle_sync_job`
  resources. No secret or collector resource is attached.
- `curl -H "Authorization: Bearer $(databricks auth token --profile DEFAULT -o json | jq -r .access_token)" "$MIP_APP_URL/api/admin/health"`
  returned `status=ok`, `warehouse/lakebase/genie=up`,
  `counters_persistence=process-local`, and `log_export=stdout-only`.
- `databricks apps deploy --help` on CLI `0.299.1` exposes no top-level
  `--env` flag, but the deploy API accepts runtime `env_vars` through
  `--json`. Use that only for non-sensitive endpoint values. Collector
  header secrets should be wired through Databricks Secrets and an app
  secret resource.
- `databricks secrets list-secrets` checks of the likely local scopes
  (`mip`, `entrada-dashboard`, `gateway-keys`, `dbx_scope`,
  `dbx_default_scope`) found no OTLP, collector, Splunk, Datadog, Loki,
  Grafana, or HEC secret key names.
At that point, the external collector proof was still bounded by the
missing collector endpoint. The app-side transport proof requires runtime
env wiring, `/api/admin/health` showing `log_export=otlp`, and
collector-side receipt from the deployed app. Durable production retention
also requires a customer-owned collector plus Databricks-managed secrets
for any collector headers.

### Temporary external-collector proof — 2026-05-14

The transport path has been proven with a short-lived, headerless
collector deployment and then restored to the normal stdout-only sandbox
posture:

- Proof deployment `01f14fe0164a1b6388f9d240679492db` included the base
  app env/resource bindings plus `MIP_OTEL_ENDPOINT` pointing at a
  temporary collector. `/api/admin/health` returned `log_export=otlp`,
  `warehouse/lakebase/genie=up`, and all breakers closed.
- A sanitized `POST /api/telemetry/rum` probe returned `202` with
  correlation id `b07b2f0825a043188fda96e041a14d19`.
- The collector received an OTLP HTTP protobuf POST containing the same
  `http_request` log body and correlation id for
  `/api/telemetry/rum`; collector request id
  `f080e74e-2771-4db5-b895-4e9e186dca14`.
- The sandbox was then redeployed without OTLP env vars as deployment
  `01f14fee2c4415e2b8e4eed4d192e950`; `/api/admin/health` again reports
  `log_export=stdout-only`.

This closes the app-side OTLP transport proof. A customer production
deployment still needs a customer-owned collector endpoint and
secret-backed headers before durable off-platform retention can be
claimed for that environment.

### Temporary secret-backed header proof — 2026-05-14/15

The `MIP_OTEL_HEADERS` secret-resolution path has also been proven
without committing or printing a header value:

- A temporary non-sensitive Databricks Secret `mip/otel-headers` was set
  to `x-mip-otel-proof=proof-20260515T000246Z`.
- The existing sandbox app was updated with an app secret resource named
  `otel_headers`, preserving the four existing app resources
  (`sql_warehouse`, `genie_space`, `database`, `lifecycle_sync_job`).
- Temporary deployment `01f14ff1cb5513a9824b1e040701141d` used a full
  `env_vars` payload with `MIP_OTEL_HEADERS` set through
  `value_from=otel_headers`, not a plaintext value.
- `/api/admin/health` returned `log_export=otlp`,
  `warehouse/lakebase/genie=up`, and all breakers closed.
- A sanitized `POST /api/telemetry/rum` probe returned `202` with
  correlation id `c864cd1bbbf44779874fdb235ae7c6bf`.
- The collector received OTLP request
  `d64260ce-232a-498e-bb9a-4bf65306c090` with a matching
  `/api/telemetry/rum` log body and inbound request header
  `x-mip-otel-proof=proof-20260515T000246Z`.
- The sandbox was then redeployed without OTLP env vars as deployment
  `01f14ff2c4cd10b99ebad8f8785c307f`; `/api/admin/health` again reports
  `log_export=stdout-only`; the temporary app secret resource was removed;
  and the temporary Databricks Secret was deleted.

This proves app-side secret-backed OTLP transport. It still does not
prove durable customer retention: that requires a customer-owned
collector endpoint, a real customer `MIP_OTEL_HEADERS` secret,
collector-side retention/ACL proof, and a collector query that finds a
fresh deployed-app correlation id in the customer's logging system.

Safe closure path when a real customer-owned collector exists:

```bash
# 1. Put sensitive headers in Databricks Secrets. Do not put the token in
# source files, app.yaml, screenshots, or shell history.
read -r -s MIP_OTEL_HEADERS
printf '%s' "$MIP_OTEL_HEADERS" \
  | databricks secrets put-secret mip otel-headers --profile DEFAULT

# 2. Deploy the OTLP-capable bundle target. The target adds the
# otel_headers app secret resource but does not commit the secret value.
databricks bundle deploy -t prod_otlp --profile DEFAULT \
  --var genie_space_id=<customer-genie-space-id> \
  --var otel_headers_secret_scope=mip \
  --var otel_headers_secret_key=otel-headers

# If adding OTLP to an app that is already deployed under another bundle
# target in the same workspace, do not run a second production target that
# recreates Lakebase/warehouse resources unless that target has imported
# or already owns those resources. Instead, update the existing app's
# resources to add the `otel_headers` app secret resource while preserving
# the current resources, then use the deploy payload below.

# 3. Build a full deployment payload. Apps deployment env_vars replace
# app.yaml, so use the helper instead of hand-writing a partial list.
python tools/databricks/otlp_deploy_payload.py \
  --source-code-path /Workspace/Users/<user>/.bundle/mortgage-intelligence-platform/prod_otlp/files \
  --endpoint https://<customer-owned-collector>/v1/logs \
  > /tmp/mip-otlp-deploy.json

databricks apps deploy mip-app \
  --json @/tmp/mip-otlp-deploy.json \
  --auto-approve \
  --profile DEFAULT \
  --timeout 20m

# 4. Prove the deployed app handler is active.
MIP_APP_URL="$(databricks apps get mip-app --profile DEFAULT -o json | jq -r .url)"
TOK="$(databricks auth token --profile DEFAULT -o json | jq -r .access_token)"
curl -sS -H "Authorization: Bearer $TOK" \
  "$MIP_APP_URL/api/admin/health" \
  | jq '{status, dependencies, counters_persistence, log_export}'

# 5. Prove receipt in the collector using the fresh deployed-app
# correlation_id or event name from the same time window.
```

Record the customer proof as a structured evidence file and validate it
before claiming durable customer retention:

```bash
python tools/databricks/otlp_customer_retention_gate.py \
  /path/to/customer-otlp-retention-evidence.json \
  --min-retention-days 365
```

The evidence file must contain:

- Active deployed app id, `log_export=otlp`, app secret resource
  `otel_headers`, and a Databricks Secrets reference such as
  `databricks://secrets/mip/otel-headers`.
- Customer-owned collector owner and HTTPS endpoint, with no credentials
  in the URL.
- Collector retention policy reference, ACL proof reference, and query
  proof reference.
- A fresh deployed-app correlation id from a sanitized probe and the
  same correlation id found by the collector query.

The gate returns `blocked` if any of those fields are missing, stale,
mismatched, or appear to contain plaintext collector headers or tokens.
It is a guardrail against overclaiming: `passed` means the evidence packet
is complete for the environment under review, not that the application
can independently certify the customer's logging platform.

For a non-secret proof collector that requires no headers, a one-off
deployment can also use the API-level override:

```bash
databricks apps deploy mip-app \
  --json '{
    "source_code_path": "/Workspace/Users/<user>/.bundle/mortgage-intelligence-platform/dev/files",
    "mode": "SNAPSHOT",
    "env_vars": [
      {"name":"APP_ENV","value":"sandbox"},
      {"name":"DATABRICKS_WAREHOUSE_ID","value_from":"sql_warehouse"},
      {"name":"GENIE_SPACE_ID","value_from":"genie_space"},
      {"name":"PGHOST","value_from":"database"},
      {"name":"LAKEBASE_HOST","value_from":"database"},
      {"name":"MIP_LIFECYCLE_SYNC_JOB_ID","value_from":"lifecycle_sync_job"},
      {"name":"MIP_DEFAULT_CATALOG","value":"mip"},
      {"name":"MIP_DEFAULT_SCHEMA","value":"gold"},
      {"name":"MIP_OTEL_ENDPOINT","value":"https://<collector>/v1/logs"}
    ]
  }' \
  --auto-approve \
  --profile DEFAULT \
  --timeout 20m
```

Deployment-level `env_vars` replace the app.yaml env list, so include
the base resource-derived variables shown above. Supplying only
`MIP_OTEL_ENDPOINT` will make the app fail closed at startup because the
warehouse and Genie bindings are absent.

### Splunk (HEC)

Splunk's HEC endpoint speaks OTLP HTTP when the HEC token is passed as
an OTEL-standard header value in the Databricks Secret referenced by
`otel_headers`:

```
MIP_OTEL_ENDPOINT=https://<splunk-host>:8088/services/collector/otlp/v1/logs
```

### Datadog

Datadog accepts OTLP directly when the API key is included as a header
value in the Databricks Secret referenced by `otel_headers`:

```
MIP_OTEL_ENDPOINT=https://http-intake.logs.datadoghq.com/api/v2/logs
```

### Grafana Loki / OTEL Collector

Point `MIP_OTEL_ENDPOINT` at your OTEL collector's `/v1/logs` receiver.
No headers required for in-cluster collectors. The collector handles
the final translation to Loki's native protocol.

## 4. Rolling-hour counters — intentional ephemerality

`/api/admin/health` exposes two process-local rolling counters:

```json
{
  "breaker_state_changes_last_hour": 0,
  "recent_errors_count": 0,
  "counters_persistence": "process-local",
  "log_export": "stdout-only"
}
```

These two counters live in memory inside
`backend/services/observability.py` (see `_BREAKER_CHANGES` and
`_ERRORS` deques). **They reset on every process restart.** The
`counters_persistence` key exists precisely so operators don't read a
freshly-zeroed counter 30 seconds after a deploy and conclude "the
system is healthy" when what really happened was "we lost the history".

### Why not persist them?

We deliberately did not back these counters with a Lakebase table. The
reasoning:

1. **Durability is solved elsewhere.** Every breaker state change and
   every dependency error already emits a structured JSON log line. If
   `MIP_OTEL_ENDPOINT` is configured and the exporter is healthy, that
   line should reach Splunk / Datadog / Loki through the collector. The
   counter is a glance; the log is the truth.
2. **The hot path stays hot.** A Lakebase round-trip inside
   `record_error` would add a millisecond-scale write to every caught
   exception — magnified across a request fan-out that already includes
   real Unity Catalog and Genie calls.
3. **The signal a non-zero counter carries is "right now".** "3 breaker
   flips in the last hour" is an acute-phase signal: you want to see it
   while it's happening, not reconstruct it post-mortem. The log sink
   is where post-mortems live.

### What operators should do instead

If the counter on `/api/admin/health` looks suspiciously clean after a
restart:

- Grep the durable log sink for `event=circuit_breaker_state_change`
  or `level=ERROR` scoped to the previous hour.
- Use `correlation_id` to reconstruct the request chain.

If `log_export` says `"stdout-only"` and the Databricks Apps log tab
only retains short-term stdout, that is the signal to turn on
`MIP_OTEL_ENDPOINT` for production. The default posture is safe for
development and demo; it is not a production durability story.

## 5. API contract (frozen)

The unauthenticated `/api/health` load-balancer path returns only
coarse runtime status. Admin diagnostics live at `/api/admin/health`.
The admin body MUST continue to return:

- `status` — `"ok" | "degraded"`
- `mode` — `"live"`
- `app_env`, `warehouse_id`
- `dependencies` — `{warehouse, lakebase, genie}` each `up|down`
- `circuit_breakers` — `{warehouse, lakebase, genie}` each `closed|open|half_open`
- `breaker_state_changes_last_hour` — integer, ephemeral
- `recent_errors_count` — integer, ephemeral

The Slice-13 follow-up adds (additive, non-breaking):

- `counters_persistence` — always the literal string `"process-local"`
- `log_export` — `"stdout-only"` or `"otlp"`

Any client reading the first seven keys keeps working unchanged.

# Observability — operator guide

Scope: how logs leave the Mortgage Intelligence Platform Databricks App
and reach a queryable sink, and what to do when the rolling-hour
counters on `/api/v1/health` look odd after a restart.

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
the app does NOT crash. The `/api/v1/admin/health` body's `log_export` key is the
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
`/api/v1/admin/health` reports `"log_export": "otlp"`, and the target
collector shows a fresh `correlation_id` from the deployed app.

### Deployed sandbox status — 2026-05-15

The Entrada dev Databricks App is bounded at stdout-only until a
collector endpoint is supplied for a deployment:

- `databricks apps get mip-app --profile DEFAULT -o json` reports active
  deployment `01f14ff2c4cd10b99ebad8f8785c307f`, `SUCCEEDED`, with the
  app `RUNNING` and only
  `sql_warehouse`, `genie_space`, `database`, and `lifecycle_sync_job`
  resources. No secret or collector resource is attached.
- `curl -H "Authorization: Bearer $(databricks auth token --profile DEFAULT -o json | jq -r .access_token)" "$MIP_APP_URL/api/v1/admin/health"`
  returned `status=ok`, `warehouse/lakebase/genie=up`,
  `counters_persistence=process-local`, and `log_export=stdout-only`.
- Apps deployment help on Databricks CLI `0.299.1` exposes no top-level
  `--env` flag, but the deploy API accepts runtime `env_vars` through
  `--json`. This is why direct App deployment is unsupported: the governed
  script must emit the complete payload and bind collector headers through a
  Databricks Secret resource.
- `databricks secrets list-secrets` checks of the likely local scopes
  (`mip`, `entrada-dashboard`, `gateway-keys`, `dbx_scope`,
  `dbx_default_scope`) found no OTLP, collector, Splunk, Datadog, Loki,
  Grafana, or HEC secret key names.
At that point, the external collector proof was still bounded by the
missing collector endpoint. The app-side transport proof requires runtime
env wiring, `/api/v1/admin/health` showing `log_export=otlp`, and
collector-side receipt from the deployed app. Durable production retention
also requires a customer-owned collector plus Databricks-managed secrets
for any collector headers.

### Temporary external-collector proof — 2026-05-14

The transport path has been proven with a short-lived, headerless
collector deployment and then restored to the normal stdout-only sandbox
posture:

- Proof deployment `01f14fe0164a1b6388f9d240679492db` included the base
  app env/resource bindings plus `MIP_OTEL_ENDPOINT` pointing at a
  temporary collector. `/api/v1/admin/health` returned `log_export=otlp`,
  `warehouse/lakebase/genie=up`, and all breakers closed.
- A sanitized `POST /api/v1/telemetry/rum` probe returned `202` with
  correlation id `b07b2f0825a043188fda96e041a14d19`.
- The collector received an OTLP HTTP protobuf POST containing the same
  `http_request` log body and correlation id for
  `/api/v1/telemetry/rum`; collector request id
  `f080e74e-2771-4db5-b895-4e9e186dca14`.
- The sandbox was then redeployed without OTLP env vars as deployment
  `01f14fee2c4415e2b8e4eed4d192e950`; `/api/v1/admin/health` again reports
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
- `/api/v1/admin/health` returned `log_export=otlp`,
  `warehouse/lakebase/genie=up`, and all breakers closed.
- A sanitized `POST /api/v1/telemetry/rum` probe returned `202` with
  correlation id `c864cd1bbbf44779874fdb235ae7c6bf`.
- The collector received OTLP request
  `d64260ce-232a-498e-bb9a-4bf65306c090` with a matching
  `/api/v1/telemetry/rum` log body and inbound request header
  `x-mip-otel-proof=proof-20260515T000246Z`.
- The sandbox was then redeployed without OTLP env vars as deployment
  `01f14ff2c4cd10b99ebad8f8785c307f`; `/api/v1/admin/health` again reports
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
# Scope creation is idempotent: create it only when the exact scope is absent.
databricks secrets list-scopes --profile DEFAULT -o json \
  | jq -e '.scopes[]? | select(.name == "customer-observability")' >/dev/null \
  || databricks secrets create-scope customer-observability --profile DEFAULT
read -r -s MIP_OTEL_HEADERS
printf '%s' "$MIP_OTEL_HEADERS" \
  | databricks secrets put-secret customer-observability otlp-headers --profile DEFAULT
unset MIP_OTEL_HEADERS

# 2. Configure only the secret reference. The command of record attaches it
# to the existing target and retains lease, rollback, treatment, and smoke gates.
export MIP_OTEL_ENDPOINT=https://<customer-owned-collector>/v1/logs
export MIP_OTEL_HEADERS_SECRET_SCOPE=customer-observability
export MIP_OTEL_HEADERS_SECRET_KEY=otlp-headers
CI=1 ./scripts/deploy.sh -t prod --no-confirm

# 3. Prove the deployed app handler is active.
MIP_APP_URL="$(databricks apps get mip-app --profile DEFAULT -o json | jq -r .url)"
TOK="$(databricks auth token --profile DEFAULT -o json | jq -r .access_token)"
curl -sS -H "Authorization: Bearer $TOK" \
  "$MIP_APP_URL/api/v1/admin/health" \
  | jq '{status, dependencies, counters_persistence, log_export}'

# 4. Prove receipt in the collector using the fresh deployed-app
# correlation_id or event name from the same time window.
```

The deployment script rejects partial OTLP configuration, credential-bearing
endpoint URLs, and a plaintext `MIP_OTEL_HEADERS` process variable before
workspace mutation. Do not run a second bundle target or promote an Apps
snapshot directly; those paths do not carry the signed deployment contract.

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

Headerless proof collectors are not a deployment exception. Put a
non-sensitive proof header in the dedicated Databricks Secret and use the same
canonical path so the resource and full runtime contract remain identical to
an authenticated collector deployment.

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

`/api/v1/admin/health` exposes two process-local rolling counters:

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

If the counter on `/api/v1/admin/health` looks suspiciously clean after a
restart:

- Grep the durable log sink for `event=circuit_breaker_state_change`
  or `level=ERROR` scoped to the previous hour.
- Use `correlation_id` to reconstruct the request chain.

If `log_export` says `"stdout-only"` and the Databricks Apps log tab
only retains short-term stdout, that is the signal to turn on
`MIP_OTEL_ENDPOINT` for production. The default posture is safe for
development and demo; it is not a production durability story.

## 5. API contract (frozen)

The unauthenticated `/api/v1/health` load-balancer path returns only
coarse runtime status. Admin diagnostics live at `/api/v1/admin/health`.
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

The 2026-09-21 audit (`delivery-01`) adds one dependency value, additively:
`dependencies.warehouse` may be `resuming` while the serverless warehouse is
`STARTING` from auto-stop. The probe reads the warehouse lifecycle state
(`GET /api/2.0/sql/warehouses/{id}`) instead of running `SELECT 1`;
`STOPPED`/`STOPPING` read as `up` (available on demand), `DELETING`/`DELETED`
as `down`, and an unreadable state falls back to `SELECT 1`, which never
reports `resuming`. `status` is `ok` when every dependency is `up` or
`resuming`, and an open or half-open breaker still forces `down`. Consumers
treat any value other than `down` as "not an outage"; readiness checks that
need the warehouse answering (`tools/wait_app_ready.py`,
`scripts/smoke_live.sh`) keep waiting for `up`. The wire type stays
`dict[str, str]`.

The state read has a 2 s HTTP timeout, but building its SDK client resolves
host metadata with the SDK's default timeouts, so the client is built once in
the startup warm path (`warehouse_state_client_prime_failed` on failure, type
only). A probe never waits on a build in flight, a failed build is retried at
most once a minute, and until a client exists the probe uses `SELECT 1`. If
the App service principal cannot read the warehouse, every probe logs
`warehouse_state_read_failed` (WARNING once per 5 minutes, DEBUG in between;
a good read re-arms it) and falls back to `SELECT 1`; that restores the
pre-2026-09 behaviour (including the accidental keep-warm), so check for that
event after the first deploy.

## 6. Server-Timing (per-request attribution)

Every `/api/*` HTTP response carries one `Server-Timing` header
(2026-09-21 audit `delivery-v3`, server half), emitted by
`backend/services/server_timing.py`'s pure-ASGI middleware, which sits just
inside `CorrelationIdMiddleware` so a backpressure 429 still carries it:

```
Server-Timing: cache;desc=hit|miss|stale, warehouse;dur=<ms>, lakebase;dur=<ms>, total;dur=<ms>
```

- `cache` — the worst outcome any aggregate cache saw for the request
  (`miss` > `stale` > `hit`): `TTLCache.get_or_set` (hit / double-check hit,
  a `stale_if_error` serve, a leader or a follower after waiting) and the gold
  stale-while-revalidate cache.
- `warehouse` / `lakebase` — the summed statement durations the request itself
  ran (`DatabricksSqlClient.execute`, Lakebase statement end / error hooks).
- `total` — middleware entry to response start. Always present.
- An entry that was not observed is omitted; `dur` has one decimal.
- Only these four names, and only enum or numeric values: never an id, a
  path, a statement hash or an error message. Non-`/api` paths (the SPA shell,
  `/assets`) carry no header.

Work that runs outside the request (gold-cache background refreshes, the health
probe executor, keep-warm pings) records nothing: the collector lives in a
ContextVar set once by the middleware, and executor threads start with an empty
context. The browser reads the header through
`PerformanceResourceTiming.serverTiming` (same origin, so no
`Timing-Allow-Origin` is needed). The client half is w2-error-telemetry's:
once it lands, `frontend/src/lib/rum.ts` forwards these entries with a
route-templated path only; until then the header is read in the browser's
network panel.

## 7. Warehouse keep-warm: cost vs cold start

The SQL warehouse is serverless (`2X-Small`, `auto_stop_mins: 10` in
`databricks.yml`). Until the 2026-09-21 audit (`delivery-v1`) the health
poll's `SELECT 1` kept it awake by accident: any visible tab queried it every
8 s, so it never auto-stopped and only the very first visitor ever paid a cold
start. Health now reads the warehouse lifecycle state instead (§5), so keeping
the warehouse warm is an explicit, configured trade-off with one resolver,
`backend/services/keep_warm.py`:

| `MIP_WAREHOUSE_KEEP_WARM` | What runs | Cost | Cold starts |
| --- | --- | --- | --- |
| `off` (default in `app.yaml` and the deploy payload) | Nothing | Warehouse stops 10 min after the last query | The next visitor waits for a serverless resume, typically 2–6 s, shown as a calm amber "Waking Ns" pill (accessible name "Waking warehouse"), not an outage |
| `activity` | An authenticated `GET /api/v1/health` from a tab with user input in the last `MIP_WAREHOUSE_KEEP_WARM_ACTIVITY_WINDOW_MIN` minutes (default 15) submits at most one `SELECT 1 AS keep_warm` per 240 s, process-wide, fire-and-forget | Runs while someone is actively working, stops 10 min after they stop | Only after a quiet spell |
| `scheduled` | The lead-page refresh-ahead loop every `MIP_LEADS_WARM_INTERVAL_S` seconds (must be > 0; `scheduled` with 0 resolves to `off` and logs an ERROR) | Never stops while the App runs | None |

Precedence: the policy value alone decides what runs. A positive
`MIP_LEADS_WARM_INTERVAL_S` under `off` or `activity` is ignored with one
startup WARNING (`warehouse_keep_warm_interval_ignored`) and never starts a
second keep-warm; `tools/databricks/app_deploy_payload.py` refuses such a
payload outright. Startup logs `warehouse_keep_warm_policy` once. The browser
sends only an integer `idle_s` hint (seconds since the last pointer, key,
wheel or touch input) on its health poll, which runs only while the tab is
visible; the anonymous load-balancer branch and `/api/v1/admin/health` never
ping. A failed ping logs `warehouse_keep_warm_ping_failed` with the exception
type only.

Assumption, not live-verified: any statement resets the warehouse idle timer
(standard Databricks SQL behaviour). The 240 s ping interval is pinned well
inside the 10-minute auto-stop by `tests/unit/test_keep_warm.py`.

## 8. Gold aggregate cache (stale-while-revalidate)

Hot gold aggregates (the portfolio preview and day-zero probe, the geography
rollups, the analytics tabs, the config options and footprint, the headline
KPIs) sit behind `backend/services/gold_cache.py` (2026-09-21 audit
`delivery-06`). Past each site's soft TTL the last value is served at once and
one background refresh runs on the two-worker `mip-gold-swr` pool; only a
value older than `MIP_GOLD_CACHE_MAX_STALE_S` (default 86400) is recomputed
inline. A served-stale payload keeps its own `data_refreshed_at` /
`snapshot_date`. DEBUG events `gold_cache_hit` / `gold_cache_stale` /
`gold_cache_miss` and the WARNING `gold_cache_refresh_failed` carry the cache
key and exception type only; the per-request outcome is the `cache` entry of
`Server-Timing` (§6).

Values that read the Lakebase lifecycle mirror (`gold.borrower_lifecycle_state`:
the preview's approved / in-outreach counts, the executive funnel's Approved /
Actioned stages, the segment approval and outreach rates) never ride the
long-lived value: their keys carry a workflow generation that moves on every
approve, reject, assignment and outcome write (`clear_sales_state_cache`) and
when a warehouse-mode lifecycle sync completes. A sync that runs as the
Databricks job (`MIP_LIFECYCLE_SYNC_MODE=job`, or the retry job submitted after
a warehouse-mode failure) finishes outside the App and moves no generation, so
there those values trail the mirror by at most one soft TTL (default 120 s preview,
300 s analytics) plus one stale serve. Each move also sweeps the older
generations of those keys out of every live gold cache (`workflow_key`), so
a burst of approval writes leaves no dead entries to push live previews out of
the bounded LRU.

During a sustained warehouse outage, sites built with `stale_if_error` keep
serving their last good value (up to the `MIP_GOLD_CACHE_MAX_STALE_S` hard
cap), and every later stale read schedules one more background refresh per key
(at most one in flight per key on the two-worker pool; the open circuit breaker
makes each attempt fail fast). A repeated `gold_cache_refresh_failed` WARNING
for the same key during an outage is that retry, not a new problem; it stops
once the warehouse is back.

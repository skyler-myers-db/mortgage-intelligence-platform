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

### X-Data-Last-Good-At (stale-after-failure marker)

A separate header, emitted by the same middleware (decision record e1,
2026-09-30):

```
X-Data-Last-Good-At: 2026-09-29T06:00:00Z
```

- It is set ONLY when a value the response served was retained after a failed
  refresh (`stale_if_error` in `gold_cache.py` or `TTLCache`), or was built
  from such a read (a list whose readiness gates were retained, say). The
  value is the UTC second of that value's last successful Unity Catalog read.
- When several reads in one request are marked, the OLDEST time wins.
- It is absent on a plain soft-window stale serve (a background refresh is
  merely in flight, nothing failed) and on every fresh or miss response.
- The staleness travels through `backend/services/cache_staleness.py`: each
  cache factory runs in its own ContextVar scope, a cache that serves a
  retained entry calls `report_stale(wall)`, and an entry built inside a
  marked scope is stored `degraded`, so later hits keep marking. Executor
  threads never set the request collector, as for Server-Timing.
- Server-Timing names and values are unchanged; the RUM cache vocabulary is
  still `hit|miss|stale`.

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
rollups, the analytics tabs, the Executive rate window, the segment
source-readiness gates, the config options and footprint, the headline KPIs)
sit behind `backend/services/gold_cache.py` (2026-09-21 audit
`delivery-06`). Past each site's soft TTL the last value is served at once and
one background refresh runs on the two-worker `mip-gold-swr` pool; only a
value older than `MIP_GOLD_CACHE_MAX_STALE_S` (default 86400) is recomputed
inline. A served-stale payload keeps its own `data_refreshed_at` /
`snapshot_date`. DEBUG events `gold_cache_hit` / `gold_cache_stale` /
`gold_cache_miss` and the WARNING `gold_cache_refresh_failed` carry the cache
key and exception type only; the per-request outcome is the `cache` entry of
`Server-Timing` (§6).

The segment list (`DatabricksSegmentRepository.list`, `/api/v1/segments`) is on
this cache too since 2026-09-30 (decision record e1): 300 s soft TTL, the
24 h `MIP_GOLD_CACHE_MAX_STALE_S` hard cap, `stale_if_error`. An expired list
never MASKS a refresh failure: a cold failure propagates as the 503, and a
list retained after a failed refresh (or built from retained readiness gates)
is served with `X-Data-Last-Good-At` (§6), which clears after one successful
refresh. A list built from retained readiness gates stays marked degraded
(`X-Data-Last-Good-At`) until its OWN next refresh, up to the 300 s soft TTL,
even after the gate cache recovers: the list entry recorded its retained input
when it was built, and only the list's own successful refresh replaces it.
The source-readiness gates fail closed: a cold failure of the
readiness read logs WARNING `segment_source_readiness_unavailable` and
RE-RAISES (the list answers 503; nothing is cached as good), and an EMPTY
readiness snapshot gates every mapped segment `not_connected` instead of
skipping the gates.

Admin data-source readiness (`/api/v1/admin/sources`, `/api/v1/data-estate`) no
longer probes: it reads `mip.gold.source_readiness` only (App SQL is
gold-only). A source the summary has no row for reads `unavailable`, every
non-roadmap source does when the summary is absent, and the WARNING
`admin_source_readiness_unavailable` (outcome `absent` / `partial`,
`missing_count`) says so; any other read failure is the 503. The App never
runs `DESCRIBE DETAIL` or `COUNT(*)` against `mip.silver.*` /
`mip.first_party.*`.

The state coverage footprint the Genie footprint guards and the schema
validators read (`backend/services/state_footprint.py`) is the one site with
tighter caps: it is served stale after 240 s and recomputed inline at 300 s,
so no reader sees coverage older than the 300 s hard TTL it had before. It
has no stale-if-error: a degraded load (metadata only, or the generic
fallback) is a value that REPLACES the live snapshot, so `using_fallback()`
flips on the next read, and a refresh whose load fails drops the snapshot so
the next read reloads inline. Rows and the fallback flag are always read from
one snapshot.

Values that read the Lakebase lifecycle mirror (`gold.borrower_lifecycle_state`:
the preview's approved / in-outreach counts, the executive funnel's Approved /
Actioned stages, the segment approval and outreach rates) never ride the
long-lived value: their keys carry a workflow generation that moves on every
approve, reject, assignment and outcome write (`clear_sales_state_cache`), on
the post-approval warehouse-mode lifecycle MERGE, on Admin Data operations'
"Sync workflow state", and when a job-mode lifecycle run the App submitted is
observed terminal. Each move also sweeps the older generations of those keys
out of every live gold cache (`workflow_key`), so a burst of approval writes
leaves no dead entries to push live previews out of the bounded LRU.

A sync that runs as the Databricks job (`MIP_LIFECYCLE_SYNC_MODE=job`, or the
recovery job submitted after a warehouse-mode failure) finishes outside the
App. `backend/services/lifecycle_run_watch.py` remembers each run the App
submitted (the newest 8) and checks it with `jobs.get_run` at most once per
15 s per run, on a single-worker `mip-lifecycle-watch` thread, and only while
requests read workflow counts: there is no timer, so with nobody reading
there are no Jobs API calls. A terminal life-cycle state (`TERMINATED`,
`SKIPPED`, `INTERNAL_ERROR`, whatever the result state) moves the generation
and logs INFO `lifecycle_sync_completed` with `mode` job, `job_id`, `run_id`,
`result_state` and `generation`. A run still queued or running after 1 h
expires without a bump (WARNING `lifecycle_job_watch_expired`); a failed
`get_run` keeps the run pending (WARNING `lifecycle_job_watch_error`,
exception type only); a submit that returned no run id logs WARNING
`lifecycle_job_unobservable`. Only the App process that submitted a run
observes it: other App processes trail the mirror by at most one soft TTL
(default 120 s preview, 300 s analytics) plus one stale serve, as they do for
the approval-write bump, which is process-local too. Runs the App did not
submit are NOT observed and trail the same way: the job's 04:00 schedule ships
PAUSED, so a scheduled run happens only if an operator unpauses it, and a run
started from the Jobs UI is not the App's either.

### Client half: the retained-value marker on screen (W5b)

The browser shows `X-Data-Last-Good-At` instead of only logging it
(D-platform-process-e1 items 7-9, client half, 2026-10-01):

- Validation. `frontend/src/lib/apiClients/headers.ts` `lastGoodAtHeader`
  accepts exactly `YYYY-MM-DDTHH:MM:SSZ` with a finite `Date.parse`; any other
  value reads as "not stale". The header is read on 2xx responses only:
  `apiTransport` rejects a non-2xx before any caller sees its headers, and the
  middleware appends the header to 2xx responses only (§6), so a failure
  never carries a "last good" age.
- The Fresh shape. The geo client's `segmentsWithFreshness`,
  `stateRollupsWithFreshness`, `countyRollupsWithFreshness` and
  `zipRollupsWithFreshness` (same URLs as the plain reads) return
  `{ data, lastGoodAt }`. The map's state rollups (Home's prefetch stores the
  same `{ byCode, lastGoodAt }` shape under the same key) and ZIP rollups ride
  them, and each map read exposes `lastGoodAt` (null for the overlay and the
  Rate Lever).
- Where it renders. One `StaleDataNote` ("Showing counts last read …") per
  surface that shows retained data. Segment Intelligence shows one: the
  OLDEST of the catalog and the map read on screen beside the ranked table's
  FetchedAt, as the one-line compact form ("Refresh failed; counts from …",
  the next-refresh sentence read to assistive technology) so the header
  keeps its height (above the EmptyState when a measured zero replaces the
  table, in the full form). Home can show two at once: the map's in the
  legend, and the hero's at the top of the page when a hero refresh failed
  over the briefing on screen (below). It clears on the first response
  without the header, and never offers a Refresh of its own.
- Persisted aggregates. A restored snapshot shows its true age (FetchedAt on
  Home) while it bridges the load, and a restored value whose first refresh
  fails is reset (lib/queryPersist), so the surface shows its warming or
  error state instead. A Home hero read whose manual refresh fails over data
  on screen shows the note with that data's age. A read that already failed
  BEFORE the lazy snapshot restore lands (its final error, or a failed
  attempt whose retry is in flight) is screened out of the snapshot right
  before the newer-wins hydrate, so the restored value never replaces its
  error with a success; a failure that lands while the restore is in
  progress is settled like any first-refresh failure once it has finished
  (`frontend/src/lib/queryPersist.restoreRace.test.ts`, and at the rendered
  layer home-geo-lever.fixture.spec.ts "a refresh that fails before the
  snapshot restore lands never brings the restored figures back").
- A list built from retained readiness stays marked until its own next
  refresh, up to the 300 s soft TTL: one successful readiness read does not
  clear a list already built from a retained one.

During a sustained warehouse outage, sites built with `stale_if_error` keep
serving their last good value (up to the `MIP_GOLD_CACHE_MAX_STALE_S` hard
cap), and every later stale read schedules one more background refresh per key
(at most one in flight per key on the two-worker pool; the open circuit breaker
makes each attempt fail fast). A repeated `gold_cache_refresh_failed` WARNING
for the same key during an outage is that retry, not a new problem; it stops
once the warehouse is back.

A missing table or view is not an outage. The warehouse's
`TABLE_OR_VIEW_NOT_FOUND` / SQLSTATE `42P01` (a roll-forward promotes the App
before `mip_refresh_scores` builds a new gold table) is classified as
`DatabricksSqlObjectMissingError`: it costs ONE attempt (no retries, no
backoff sleeps), counts as a warehouse breaker success (repeats never open the
breaker, and one landing as the half-open probe closes it), and answers 503
`{retryable: true, reason: retries_exhausted}` with the constant detail after
that single attempt. The `dependency_down_handled` WARNING shows
`last_error_type=DatabricksSqlObjectMissingError`. The Rate Lever's own two
tables answer 200 `built: false` instead. Since 2026-09-30 a missing schema
(`SCHEMA_NOT_FOUND`) or routine (`UNRESOLVED_ROUTINE`) fails fast the same way,
under the same `retries_exhausted` reason; only the error classes match
(SQLSTATE 42704 / 42883 are shared with other classes), and a missing
`mip.gold` schema stays a 503 on the Rate Lever too.

### Gold snapshot generation (delivery-06 remainder, W5c)

The soft and hard TTLs alone let a process serve the previous gold snapshot's
hot aggregates until each key's own soft TTL (and a stale serve) after a gold
refresh. `backend/services/gold_snapshot.py` learns the current snapshot and
moves a process-wide GENERATION when it advances.

- Source. `SELECT CAST(MAX(checked_at) AS STRING) FROM gold.source_readiness`.
  `checked_at` is the refresh run's anchor, stamped by `ctas_source_readiness`,
  which depends on lead_scores, lead_population, segment_population and
  borrower_dossier: the id moves only after the hot lead tables are rebuilt.
  `ref.refresh_run_state.refresh_at` is written at the TOP of the DAG and would
  advance mid-refresh, so it is not used. source_readiness is a ~20-row gold
  table, so App SQL stays gold-only.
- Trigger. The probe runs only on the `mip-gold-swr` executor and only when a
  `GoldAggregateCache` read already goes to the warehouse: an inline miss
  schedules it, a background refresh runs it before its factory. At most one
  probe per `MIP_CACHE_TTL_S`. There is no timer, so an idle warehouse still
  auto-stops. The first learn sets the id without a bump; a different id bumps
  the generation and logs INFO `gold_snapshot_advanced` (`generation`,
  `previous`, `current`: snapshot timestamps only). A failed probe keeps the
  generation, logs WARNING `gold_snapshot_probe_failed` with the exception
  type only, and retries after the next soft TTL. The watch is off when
  `MIP_CACHE_TTL_S <= 0`.
- The generation, not a key suffix. Every `GoldAggregateCache` entry carries
  the generation its value was read under and a lookup compares it: an
  older-generation entry is a MISS, computed inline with single-flight (DEBUG
  `gold_cache_miss`, `reason=snapshot_advanced`), never a hit or a plain stale
  serve. The entry stays in place, so a recompute that fails under
  `stale_if_error` still serves it WITH `X-Data-Last-Good-At`. A per-key
  suffix was rejected: it lengthens every key, breaks `workflow_key`'s
  `family:generation` parsing in `drop_workflow_generations`, and orphans the
  last-good value `stale_if_error` needs across a refresh. The hard-expiry
  `TTLCache` applies the same rule to the closed prefixes
  `GOLD_VERSIONED_TTL_PREFIXES` (`borrower_dossier:`, `lead_list:`,
  `lead_count:`, `lead_facets:`); `get_stale` still serves an older entry and
  every other key is unaffected.
- Cost. One advance is a coordinated miss: each hot key a reader touches pays
  one inline warehouse read once. The cold-cache Locust profile's scenario B
  (docs/load-baseline.md, "Cold-cache profile") measures it. The inline miss
  that carries the advancing probe pays one more: the probe runs
  asynchronously, so the generation can move while that key's own read is in
  flight, and the read stores under the generation it BEGAN in (it may have
  read the previous snapshot), so its next read misses once more. One extra
  warehouse round trip per advance per process, never an old read served as
  the new generation. A single-flight follower of that read still takes its
  value with no `X-Data-Last-Good-At`: only a FAILED leader yields a marked
  serve (`tests/unit/test_gold_cache_snapshot_keys.py`, the `_Deferred`
  executor cases).
- The get/set window. The versioned `TTLCache` callers (the dossier, lead
  list, lead count and lead facets repositories) read with `get`, query the
  warehouse, then `set`; `set` stamps the generation current when it stores.
  A query that began before an advance and finished after it is therefore
  stored under the newer generation and can be served for up to one soft TTL,
  the same bound as before the generation existed. `get_or_set` callers
  capture the generation before the read and have no such window.
- Per process. Each App process learns on its own next warehouse-bound read,
  so two processes can disagree for up to one soft TTL.
- The funnel lag. `gold.funnel_snapshot_daily` is recorded at deploy step 9
  (and by the lifecycle job), AFTER the step-8 refresh that moves the
  generation, so a Delta Explainer attribution read cached between the two can
  trail the new snapshot by one soft TTL.

### Optional gold columns (fail-soft projection, W5c)

A roll-forward can promote the App ahead of the gold refresh that adds a
column (a first install deploys the App before any gold exists; the local
piecewise recipe can ship it before `mip_refresh_scores`). `UNRESOLVED_COLUMN`
/ SQLSTATE `42703` is classified as `DatabricksSqlColumnMissingError`, a
SIBLING of `DatabricksSqlObjectMissingError` (a route that maps a missing lane
table to "not built" never swallows a missing column): it fails fast like a
missing table (one statement, a breaker success, the unchanged 503
`retries_exhausted`). Before W5c it was retried three times and counted as a
breaker failure.

`backend/services/optional_gold_columns.py` is a CLOSED registry of column
families: `score_points` (the five `*_points` in the lead_population and
borrower_360 projections the Lead Queue and its geo drill-down read) and
`spread_history` (the dossier's `first_pos_date`, `first_pos_rate_type`,
`first_itm_week`). Each family maps the exact projection fragments to NULL
twins that alias every column. On a missing registered column whose fragment
is in the statement, the resilient client latches the family for one
`MIP_CACHE_TTL_S`, re-runs the statement ONCE on the twins, rewrites every
statement while the latch holds, and logs one WARNING
`optional_gold_columns_unavailable` per latch (family and error class only; no
SQL, no ids). Anything else re-raises unchanged. The rows stay real; only the
optional fields come back null, never a fabricated value, and every client
treats a null `score_points` or crossing field as absent. The funnel snapshot's
`competitor_lien_borrowers` is not in the registry (its read filters on it):
the attribution read answers the no-snapshot shape on that column's
`DatabricksSqlColumnMissingError` instead.

## 9. Genie completion jobs

A live Genie turn's governed completion (verification, the output policy,
the RUN_GENIE audit row, session recording and, for deep asks, the planned
sweep) runs as a server-side job (2026-09-21 audit `genie-01`,
`backend/services/genie_completion_runner.py`). The browser opts in with
`respond_async` on `POST /api/v1/genie/message/complete`, gets `202` with the
job's status, and polls `POST /api/v1/genie/message/status` about every 1.5 s.
One `mip_app.genie_completion_jobs` row exists per (actor, conversation,
message), so a reloaded or retried complete joins the job instead of running
the tail, and the audit row, a second time.

Events (logger `mip-genie-jobs` unless noted). None carries question text, an
answer, SQL, or exception text; ids are the job UUID only:

| Event | Level | Fields | Meaning |
| --- | --- | --- | --- |
| `genie_job_enqueued` | INFO | `outcome` created / joined, `joined`, `status`, `job_id` | A complete created the turn's job or joined the existing one. A `joined` burst for one job is a reload or a retry, not extra Genie work. |
| `genie_job_stage_write_failed` | WARNING, at most once a minute per process | `stage`, `error_type` | A progress-stage write failed. Best effort: the answer is unaffected; the rail just shows an older stage. Stage writes run on one background thread per process (the latest stage wins per job), so a slow Lakebase never delays the governed completion. |
| `genie_job_expired` | INFO | `reason` stale_lease / past_expiry, `via` read / sweep, `job_id` | A job was expired: its runner stopped renewing the lease (the process died or restarted), or a served answer passed `expires_at` and its stored result was NULLed. |
| `genie_job_finished` (logger `mip-genie`) | INFO | `status` succeeded / failed / expired, `duration_ms`, `failure_kind`, `job_id` | The runner finished. `failure_kind` is dependency_down, upstream_error or internal; the browser gets the canned hint for it. `expired` here means the runner lost its lease before it could store the answer. |
| `genie_job_internal_error` (logger `mip-genie`) | ERROR | `error_type`, `job_id` | An unexpected exception inside a job, logged with the enqueuing request's correlation id. |
| `genie_jobs_table_absent` | WARNING, once per absence | — | The App runs ahead of the Lakebase migration: submit advertises no jobs and an older tab's completion stays inline (no job) until `mip_lakebase_migrate` has run; the probe re-checks every 60 s. |
| `genie_complete_async_refused` (logger `mip-genie`) | WARNING | `outcome` refused | An async complete arrived while the turn could not get a job (the probe failed or found no table). It got a non-retryable 503 before any Genie work or audit row: the browser re-sends an async complete, and a job-less run could not be joined, so the re-send would complete the turn twice. |
| `genie_job_enqueued`, outcome `adopted` | INFO | `outcome` adopted, `job_id` | Audit `genie-01` risk 4: a retried complete joined a queued job this process created but never ran (its creating request failed before enqueueing it) and ran it. A second adopter only loses the compare-and-set claim (`genie_job_claim_lost`), so the turn still completes once. |
| `genie_job_cancel_requested` | INFO; WARNING when `unavailable` | `outcome` accepted / duplicate / recorded / ended / unavailable, `status` (the job's status before the request), `job_id`, `error_type` (unavailable only) | Audit `genie-03`: the owner pressed Stop on a job turn (`POST /api/v1/genie/message/cancel`). `accepted` set `cancel_requested_at` and wrote the one `GENIE_TURN_CANCELLED` audit row in the same Lakebase transaction; `duplicate`, `recorded` (the answer was already recorded) and `ended` (failed or expired) wrote nothing. `unavailable` rolled the flag back with its audit row (a 503; a retry is safe). |
| `genie_job_finished` (logger `mip-genie`), status `cancelled` | INFO | `status` cancelled, `duration_ms`, `job_id`, no `failure_kind` | The runner stopped a job whose cancel came before its governed record (at a stage boundary, or refused by the `recorded_at` commit point): no `genie.run_query` RUN_GENIE row, no action tokens, no session row. The submit's own `genie.message_submitted` RUN_GENIE row stays, so a cancelled turn has exactly one RUN_GENIE row: count `metadata->>'action' = 'genie.run_query'`, not `event_type`. Not a failure. |
| `genie_job_cancel_end_failed` (logger `mip-genie`) | WARNING | `error_type`, `job_id` | The runner could not mark a stopped job `cancelled`. Nothing was recorded; the lease lapses and the next read expires the job. |
| `genie_jobs_table_absent`, 2026_09_25 columns | WARNING, once per absence | — | Also logged when the table exists without `cancel_requested_at`, `recorded_at` and `deep` (the App promoted ahead of the 2026_09_25 migration): the App completes inline exactly as without the table. |
| `genie_job_durations_failed` | WARNING, at most once a minute per process | `error_type` | The typical-duration aggregate (audit `genie-01`) could not be read. The status poll simply carries no `typical_seconds`; the miss is cached for 60 s. |
| `genie_job_section_write_failed` | WARNING, at most once a minute per process | `error_type` | Audit `genie-01` phase 1b: a verified-sections write (its `GENIE_SECTION_REVEALED` audit rows and the `sections_json` UPDATE share one transaction) failed. Fail closed: the sections that write would have revealed stay withheld until the final answer; the answer itself is unaffected. |
| `genie_job_sections_skipped` | WARNING, once per job | `reason` too_large, `sections`, `job_id` | The verified-sections payload of a running deep job exceeded 4 MiB, so nothing was written or audited for it; the recorded answer is unaffected. |
| `genie_jobs_table_absent`, 2026_10_01_genie_job_sections column | WARNING, once per absence | — | Also logged when the table lacks `sections_json` (the App promoted ahead of the 2026_10_01_genie_job_sections migration): the App completes inline exactly as without the table, so no job statement can fail on the column. |

Leases and expiry. Postgres `now()` is the only clock. A job is leased to its
process for 45 s; one daemon thread per process renews its own queued and
running jobs every 10 s. Nothing expires jobs at startup: the next status
read (on any worker) expires a job whose lease went stale, and every new job
first runs one bounded sweep (at most 50 rows, any actor) that expires stale
leases and served answers past `expires_at`, NULLing their `result_json`. So
no stored answer outlives its 15-minute window by more than one later turn,
even when its tab never polls again. Rows are never deleted.

What a row holds. `question_hash` is the progress token's binding digest; no
column holds question text. `result_json` is the governed answer de-authorized
before it is stored: `question` blanked and every action's
`confirmation_token` dropped (its `request_id` kept). The status poll puts the
question back from its own hash-checked request and re-signs the actions for
the polling actor, so a leaked row authorizes nothing.

The status poll is budgeted as `genie-job` (the default read rate and a
Lakebase slot, never the 30/min Genie budget or a Genie slot). The complete
call's Genie slot is adopted by the job and released when the job ends. The
poll is also excluded from RUM `api_call` events, like the progress poll.

Verified sections (audit `genie-01` phase 1b). While a deep sweep runs, each
sub-analysis that passes its own output-policy scan is written to the job's
`sections_json` by one background writer per process (latest snapshot wins
per job, rows capped at 50 per section). Every newly revealed section first
gets a `GENIE_SECTION_REVEALED` audit row in the same transaction (ruling R1);
the status poll serves the sections from the three-section floor, only for a
revision the poller does not hold, and never on a terminal or
cancel-requested job. Every terminal statement NULLs the column.

### Admin SSE ingress probe (delivery-04)

`GET /api/v1/admin/sse-probe` (admin only, schema-hidden, audit exempt)
streams a bounded tick sequence so `tools/databricks/sse_ingress_spike.py`
can measure whether the Apps ingress passes Server-Sent Events unbuffered and
reports a disconnect (docs/load-baseline.md). It holds no dependency slot
(budget `admin-diagnostic`) and reads no product data. Logger
`mip-sse-probe`; no event carries the actor:

| Event | Level | Fields | Meaning |
| --- | --- | --- | --- |
| `sse_probe_started` | INFO | `outcome` started, `probe_id`, `events` | A probe stream opened. |
| `sse_probe_finished` | INFO | `outcome` completed / disconnected, `events_sent`, `duration_ms`, `probe_id` | The stream ended at its bound, or the server saw `http.disconnect` (or a failed send) first. `GET /api/v1/admin/sse-probe/{probe_id}` returns the same record for 10 minutes (32 runs per process). |

## 10. Approval review ledger

<!-- w5-approval-core, 2026-09-30. Appended as section 10; the integrator renumbers. -->

Every APPROVE row in `mip_app.action_audit` says how the copy it certifies
was reviewed (audit `flow-03` / `states-06` / `wow-power-1`,
D-approval-flow-a1), and a bulk rejection is one run under one id
(`tables-07`, D-approval-flow-d). Both are metadata keys on the existing
table, under the existing audit metadata allowlist and value policy; there
is no migration.

`review_mode` (APPROVE rows, every row from this release on). The client
declares it on `POST /api/v1/outreach/approve`; the server checks it against
the request's shape (a bulk mode needs a `bulk_id`, an individual mode must
not carry one, any mode needs the generated draft proof) and refuses a
mismatch with 422 before anything is written. The value policy admits
exactly these tokens:

| Value | Meaning |
| --- | --- |
| `individual` | One row's review (the Lead Queue review, the Offer Orchestrator): the approver was shown this copy. |
| `triage` | The same, from the Triage deck. |
| `bulk_sample` | A bulk run's row whose copy the approver previewed in the gate's samples: what was shown is what the row certifies. |
| `bulk_cohort` | A bulk run's row drafted during the run and approved under the shared rationale: its copy was not individually shown, and its offer was one the samples showed (the in-run check). |
| `undeclared` | Written by the server when a request carried no `review_mode` (an older client). |

`draft_age_seconds` (APPROVE rows). Whole seconds from the generated
draft's `created_at` to the approval, on the Postgres clock (`now()`),
floored at 0 and capped by the value policy at 315,360,000. It is omitted
when the draft lookup returned no age and never enters the decision intent
or the request id. For `individual` and `triage` it is roughly how long the
review was open; for `bulk_sample`, how long the samples sat before the run;
for `bulk_cohort`, seconds.

Dwell by review mode (run as the audit reader; the 30-day window is an
example):

```sql
SELECT metadata->>'review_mode' AS review_mode,
       count(*) AS approvals,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY (metadata->>'draft_age_seconds')::bigint) AS median_draft_age_s,
       percentile_cont(0.9) WITHIN GROUP (ORDER BY (metadata->>'draft_age_seconds')::bigint) AS p90_draft_age_s
FROM mip_app.action_audit
WHERE event_type = 'APPROVE'
  AND metadata ? 'draft_age_seconds'
  AND event_at >= now() - interval '30 days'
GROUP BY 1
ORDER BY 1;
```

A `bulk_cohort` share that grows while `bulk_sample` dwell shrinks toward
zero is the signal to look at: runs approved with little time on the
samples. `undeclared` rows should fall to zero within a release.

OUTREACH_REJECT rows now carry `decision_inputs` (the same governed
decision inputs an APPROVE row carries) on every rejection, and `bulk_id`
on each row of a bulk rejection run: one id, one reason code and one
required shared note (the `rationale`) for every row of the run. A bulk
rejection without a note, or under a consent reason (`do_not_call`,
`opt_out`), is refused with 422; consent is recorded per borrower. Group a
run with `metadata->>'bulk_id'`; the decision receipt projects
`review_mode` and `bulk_id` (a foreign value reads null).

Free text on these writes (the approval rationale, the shared bulk
rationale, the rejection note) is checked against the governed text policy
before the replay lookup and again at commit: a refusal is a 422
`"<field> failed the governed text policy"` and writes no approval or
audit row (it was a 503 before).

Follow-up, dated 2026-09-30 (tracked in the audit report's §12.3): one
release after this SPA ships, make `review_mode` required on
`POST /api/v1/outreach/approve`. A request without it then gets a 422 whose
detail tells the reader to reload the app to approve, and the server stops
writing `undeclared`.

## 11. Approval requests, revoke and the queue version

<!-- w5-approval-ledger-api, 2026-10-01. Appended as section 11; the integrator renumbers. -->

Audit flow-02 / shell-06 (report 12.4 #10), flow-v2 and states-09. A
signed-in user without the approver role asks an approver to review named
borrowers (a maker-checker request); an approver may revoke an approval
while its outreach is still none or queued. Lakebase migration
`2026_10_01_approval_requests` adds `mip_app.approval_request_batches`
(finalize-only) and `mip_app.approval_request_items` (open, then withdrawn
or expired; one open item per borrower), widens `approvals_action_check` to
admit `revoke`, and indexes `approvals.decided_at`. Nothing is ever deleted
from either table, and the approve row a revoke supersedes is never changed.

Event vocabulary (all server-owned; free text only on `rationale`, through
the governed text policy unchanged):

| Event | Action / entity | Metadata | Written |
| --- | --- | --- | --- |
| `APPROVAL_REQUESTED` | `outreach.approval_request` / `approval_request_batch` (the batch id) | `approval_request_batch_id`, `borrower_ids` (requested), `requested_count`, `skipped_count`, `skipped_by_reason` (when any was skipped), `rationale` (the screened note) | in the same transaction as the batch and its items |
| `APPROVAL_REQUEST_REFUSED` | `outreach.approval_request_refused` / `approval_request` (the client request key) | `borrower_ids` (every id asked for), `requested_count` 0, `skipped_count`, `skipped_by_reason`, `rationale` | its own transaction, after the attempt rolled back; the 409 answers counts per reason only |
| `APPROVAL_REQUEST_WITHDRAWN` | `outreach.approval_request_withdraw` / `approval_request_batch` | `approval_request_batch_id`, `withdrawn_count`, `borrower_ids` (withdrawn) | only when the withdraw closed at least one borrower |
| `OUTREACH_REVOKE` | `outreach.revoke` / `approval` (the revoke row) | `approval_id`, `revoked_approval_id`, `borrower_id`, `offer_code`, `channel`, `rationale`, `request_id`, `released_assignment_id` (when a not-yet-worked assignment was released) | in the same transaction as the revoke's approvals row |

Value rules: `approval_request_batch_id`, `revoked_approval_id` and
`released_assignment_id` are opaque ids; the three counts are bounded row
counts; `skipped_by_reason` groups masked borrower ids under the four closed
reasons `not_found`, `not_contactable`, `already_decided`,
`already_requested`. APPROVE and OUTREACH_REJECT carry
`approval_request_batch_id` when the decision answers a request; the decision
intent carries it only then, so every unlinked intent (and its derived
fallback request id) is unchanged. The decision receipt reads a revoke as
`revoked`.

Request state is Lakebase workflow app state, not audit-explorer visibility
(12.4 #3 is unchanged): the list (`GET /api/v1/outreach/approval-requests`)
writes no audit row. Each borrower's state is derived, never stored, in this
order: the latest finalized approve or reject LINKED to the request
(`approved` / `rejected`), the requester's withdraw (`withdrawn`), an
expired item or a request older than 30 days (`expired`), a later finalized
approve or reject without the link (`decided_outside`), else `open`. Hold
and revoke rows never decide a request. A request older than 30 days frees
its borrowers lazily: the next request for one of them expires the stale
item first. A decided item keeps `status = 'open'` in the table (the
decision is derived), so the approvers' list (`scope=open`, oldest first, at
most 100) excludes requests with no derived-open borrower in its SQL read,
and fully decided requests never crowd newer open ones out. Request and
approval ids are compared in the ledger's lower-case spelling: the API
canonicalizes an upper-case id on approve, reject, revoke and
`GET /api/v1/leads?approval_request_batch=`.

Revoke rules (`POST /api/v1/outreach/revoke`, approver-only): the named
approval must still be the borrower's current, finalized, unbound decision;
no call disposition or lead outcome may exist since it was decided, and no
delivered activation for it; an active assignment a loan officer has worked
(`actioned`, `outcome_recorded`) refuses, a not-yet-worked one is released.
The borrower's open request items expire so it can be requested again. A new
revoke clears the sales-state cache and enqueues the lifecycle sync with
reason `revocation`; the sync, `lifecycle_for` and the activation delivery
guard read the borrower as pending / none / superseded. The funnel's
"approved" stage counts borrowers ever approved, so a revoked approval still
counts there, exactly like approve-then-reject.

Queue version: the change signal now reads six ledgers (approvals,
assignments, call dispositions, loan-officer outcomes, activation delivery
status and CRM-imported `lead_outcomes`) under the prefix
`mip.queue-version.v2|`. The prefix change moves every version once, so each
open Lead Queue shows one "Queue updated" pill after the deploy that ships
it. The request tables are not folded in (a request sends no notification).

Open requests by age (run as the audit reader):

```sql
SELECT batch.batch_id,
       batch.created_at,
       now() - batch.created_at AS age,
       count(*) FILTER (WHERE item.status = 'open') AS open_items
FROM mip_app.approval_request_batches AS batch
JOIN mip_app.approval_request_items AS item USING (batch_id)
WHERE batch.audit_event_id IS NOT NULL
  AND batch.created_at >= now() - interval '30 days'
GROUP BY batch.batch_id, batch.created_at
HAVING count(*) FILTER (WHERE item.status = 'open') > 0
ORDER BY batch.created_at;
```

Revokes in the last 30 days, with what each superseded:

```sql
SELECT event_at,
       actor_email,
       metadata->>'revoked_approval_id' AS revoked_approval_id,
       metadata->>'borrower_id' AS borrower_id,
       metadata->>'released_assignment_id' AS released_assignment_id
FROM mip_app.action_audit
WHERE event_type = 'OUTREACH_REVOKE'
  AND event_at >= now() - interval '30 days'
ORDER BY event_at DESC;
```

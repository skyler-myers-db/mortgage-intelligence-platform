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
MIP_OTEL_HEADERS=authorization=Bearer <token>,x-team=mip
```

When `MIP_OTEL_ENDPOINT` is set at process start, the backend wires
`opentelemetry-sdk` + `opentelemetry-exporter-otlp` so every structured
log line is shipped to the endpoint in addition to stdout. When unset,
behaviour is unchanged (stdout JSON only).

Install the optional wheels on the App image:

```bash
pip install .[otel]
# or, equivalently:
pip install 'opentelemetry-sdk>=1.27,<2' 'opentelemetry-exporter-otlp>=1.27,<2'
```

If `MIP_OTEL_ENDPOINT` is set but the wheels are absent, the backend
logs one `WARNING` line at boot and keeps running on stdout-only —
the app does NOT crash. The `/api/health` body's `log_export` key is the
at-a-glance status:

| `log_export` value | Meaning                                                |
|--------------------|--------------------------------------------------------|
| `"stdout-only"`    | Default. No durable export configured.                  |
| `"otlp"`           | OTLP handler attached and accepting records.           |

### Splunk (HEC)

Splunk's HEC endpoint speaks OTLP HTTP when the HEC token is passed as
an OTEL-standard header:

```
MIP_OTEL_ENDPOINT=https://<splunk-host>:8088/services/collector/otlp/v1/logs
MIP_OTEL_HEADERS=authorization=Splunk <hec-token>
```

### Datadog

Datadog accepts OTLP directly when the API key is included as a header:

```
MIP_OTEL_ENDPOINT=https://http-intake.logs.datadoghq.com/api/v2/logs
MIP_OTEL_HEADERS=dd-api-key=<api-key>,dd-protocol=otlp
```

### Grafana Loki / OTEL Collector

Point `MIP_OTEL_ENDPOINT` at your OTEL collector's `/v1/logs` receiver.
No headers required for in-cluster collectors. The collector handles
the final translation to Loki's native protocol.

## 4. Rolling-hour counters — intentional ephemerality

`/api/health` exposes two process-local rolling counters:

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
   `MIP_OTEL_ENDPOINT` is configured, that line is in Splunk / Datadog /
   Loki by the time the counter ticks. The counter is a glance; the log
   is the truth.
2. **The hot path stays hot.** A Lakebase round-trip inside
   `record_error` would add a millisecond-scale write to every caught
   exception — magnified across a request fan-out that already includes
   real Unity Catalog and Genie calls.
3. **The signal a non-zero counter carries is "right now".** "3 breaker
   flips in the last hour" is an acute-phase signal: you want to see it
   while it's happening, not reconstruct it post-mortem. The log sink
   is where post-mortems live.

### What operators should do instead

If the counter on `/api/health` looks suspiciously clean after a
restart:

- Grep the durable log sink for `event=circuit_breaker_state_change`
  or `level=ERROR` scoped to the previous hour.
- Use `correlation_id` to reconstruct the request chain.

If `log_export` says `"stdout-only"` and the Databricks Apps log tab
only retains short-term stdout, that is the signal to turn on
`MIP_OTEL_ENDPOINT` for production. The default posture is safe for
development and demo; it is not a production durability story.

## 5. API contract (frozen)

`/api/health` MUST continue to return:

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

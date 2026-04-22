# Observability validation — Slice 13

## Scope

Structured, stdlib-only logging + per-request correlation across every
Module 0 backend call path. When the live app flakes, an operator can
grep one correlation ID and see every downstream SQL, Lakebase, and
Genie call it fanned out into, with duration and outcome.

## What was added

- `backend/services/observability.py` (new) — `StructuredFormatter`
  (JSON-line formatter with PII denylist), `correlation_id_var`
  (`ContextVar`), `emit(logger, event, **kwargs)`, `timed_dependency`
  context manager, rolling-hour counters
  (`record_breaker_state_change`, `record_error`,
  `recent_breaker_state_changes`, `recent_error_count`), and an
  idempotent `configure_logging()` that installs the formatter on root
  + the three `uvicorn.*` loggers.
- `backend/main.py` — `CorrelationIdMiddleware` reads
  `X-Correlation-ID` (or mints a UUID4 hex), binds it to the
  ContextVar, echoes it on the response, and emits one `http_request`
  event per request with `method`, `path`, `status`, `duration_ms`.
  `configure_logging()` is called at import time.
- `backend/services/resilience.py` — `CircuitBreaker` transitions
  (CLOSED→OPEN, OPEN→HALF_OPEN, HALF_OPEN→CLOSED, HALF_OPEN→OPEN) emit
  `circuit_breaker_state_change` events with `from_state`, `to_state`,
  `name`, `failure_count`, `cooldown_s`; the legacy `log.warning/info`
  lines are preserved. `Resilient.call` wraps the downstream invocation
  in `timed_dependency(name, "call")`.
- `backend/services/databricks_sql.py` — `execute()` emits
  `warehouse_query_start` / `warehouse_query_end` / `warehouse_query_error`
  with `duration_ms`, `rows_returned`, and `statement_hash` (SHA1-16 of
  the SQL text — never the text itself or parameter values).
- `backend/services/lakebase.py` — `execute` / `executemany` /
  `fetchone` / `fetchall` emit matching `lakebase_query_*` events with
  `operation` discriminator.
- `backend/services/genie_client.py` — `ask()` emits `genie_query_*`
  events. The question text is hashed, not logged.
- `backend/api/health.py` — response now carries
  `breaker_state_changes_last_hour` and `recent_errors_count`.
- `tests/unit/test_observability.py` (new, 10 tests).

## PII denylist

`backend/services/observability._DENYLIST_PREFIXES` is a superset of
`backend/services/pii_redaction._FORBIDDEN_OUTPUT_KEYS`:

| Source                              | Prefix                  |
|-------------------------------------|-------------------------|
| pii_redaction                       | `owner_name`, `owner_1_full_name`, `owner_full_name`, `buyer_1_full_name`, `buyer_full_name`, `mailing_`, `situs_street`, `trigger_timeline_json` |
| Added for auth/secret posture       | `token`, `password`, `authorization`, `set-cookie`, `api_key`, `apikey`, `secret`, `cookie` |

Match is case-insensitive prefix. Shallow-walked nested dicts are also
scrubbed so `context={"owner_name_hash": ...}` is caught.

## Sample JSON log line

```json
{"ts":"2026-04-21T23:20:53.752476+00:00","level":"INFO","logger":"mip.databricks_sql","event":"warehouse_query_end","correlation_id":"a1b2c3d4e5f6789012345678abcdef01","dependency":"warehouse","duration_ms":142.73,"outcome":"ok","statement_hash":"4f8b2e1c9a7d3e55","rows_returned":127}
```

## Validation

| Command                                                  | Result                 |
|----------------------------------------------------------|------------------------|
| `pytest -q tests/unit/test_observability.py`             | 10 passed              |
| `pytest -q` (full unit + skipped integrations)           | 342 passed, 75 skipped |
| `ruff check backend tests`                               | All checks passed      |

## Design trade-offs

- **No `opentelemetry`.** Stdlib-only keeps the Databricks Apps
  serverless runtime tight; we can layer OTEL later by pointing a
  `logging.Handler` at an OTEL exporter without touching call sites.
- **Formatter bound to root + uvicorn loggers.** Every existing
  `logging.getLogger(__name__)` call site starts producing JSON for
  free; only the new `emit(...)` / `timed_dependency(...)` call sites
  get the richer structured fields. Zero rewrites of existing
  observability-blind code were required.
- **Statement hash, not SQL text.** The SQL may contain inlined CLIP
  or owner_link values; logging it would be a PII leak. The SHA1-16
  prefix lets ops group latency by query shape without content.
- **`configure_logging()` at import time.** Startup warm-up lines now
  flow through the structured formatter without any `lifespan` coupling.
- **Rolling-hour counters in-memory only.** No external store — the
  counters reset on process restart, which is fine for the intended
  use (a spike shows up on /api/health until the spike ages out of the
  window). A future slice can emit them to Prometheus or a UC delta
  table without reshaping the call sites.

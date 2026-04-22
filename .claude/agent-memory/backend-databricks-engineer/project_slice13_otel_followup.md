---
name: Slice-13 OTEL log export
description: Optional OTLP log exporter wired in backend/services/observability.py; env-gated, deps are optional
type: project
---

`MIP_OTEL_ENDPOINT` + `MIP_OTEL_HEADERS` env vars turn on OTLP log
export at `configure_logging()` time. `opentelemetry-sdk` +
`opentelemetry-exporter-otlp` are declared as an OPTIONAL `[otel]`
extra in pyproject and commented out in requirements.txt. Missing
wheels + env-var-set is a WARNING, not a crash — the app must keep
serving traffic on stdout-only.

`/api/health` exposes `counters_persistence: "process-local"` and
`log_export: "otlp" | "stdout-only"` so operators can see the durable
sink posture at a glance.

**Why:** The rolling-hour counters in observability.py are
process-local by design. OTEL is the durable path. Persisting counters
to Lakebase would slow the hot path for little gain; the same events
already land in the log sink.

**How to apply:** When touching observability code, don't reintroduce
a synchronous write to Lakebase inside `record_error` /
`record_breaker_state_change`. Don't make the OTEL deps mandatory. Keep
`get_otel_handler()` as the single introspection entry point; the
health body reads it.

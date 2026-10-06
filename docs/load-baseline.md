# Module 0 load-test baseline

Latency baseline for the Module 0 API under sustained warm load. The
operator harness lives in `tools/load_test/`; the machine-readable
baseline lives in `tools/load_test/baseline.json`.

## Environment Snapshot

| Field | Value |
|---|---|
| Date | 2026-05-18 |
| Target | `https://mip-app-2543889327043640.aws.databricksapps.com` |
| Active deployment | `01f152e659dd1f42aab69164a47db116` |
| API prefix | `/api/v1` |
| Auth | Databricks workspace Bearer token |
| Host | macOS Darwin 25.5.0, Apple silicon |
| Python | 3.13.13, repo `.venv` |
| Locust | repo/operator install |
| Frontend | not exercised directly; API-only load |

`run.sh` warms read caches before its measured window by default:
health, segments, portfolio preview, the default lead queue, six segment
lead queues, and up to 50 borrower dossiers per lead key. This baseline
therefore measures sustained warm load, not warehouse or Genie cold
start.

## Read Profile

Command:

```bash
MIP_API_URL=https://mip-app-2543889327043640.aws.databricksapps.com \
MIP_BEARER_TOKEN=<workspace token> \
MIP_USERS=20 MIP_SPAWN_RATE=5 MIP_RUN_TIME=2m \
MIP_LOAD_TEST_FAIL_ON_BASELINE_REGRESSION=1 \
bash tools/load_test/run.sh
```

Evidence: `tools/load_test/results/20260518T183252Z_stats.csv`

| Endpoint | Budget | p50 | p95 | p99 | Requests | Fail% | Status |
|---|---:|---:|---:|---:|---:|---:|---|
| `GET /api/v1/health` | 500 ms | 100 | 140 | 1500 | 72 | 0.0 | pass |
| `POST /api/v1/portfolio/preview` | 1000 ms | 100 | 150 | 290 | 202 | 0.0 | pass |
| `GET /api/v1/segments` | 1000 ms | 100 | 170 | 490 | 131 | 0.0 | pass |
| `GET /api/v1/leads` | 1500 ms | 330 | 780 | 3100 | 398 | 0.0 | pass |
| `GET /api/v1/borrowers/{id}` | 2000 ms | 100 | 150 | 270 | 269 | 0.0 | pass |

Comparator result:

```text
baseline comparison: no p95/failure-rate regressions against committed baseline
```

## Write Profile

Write-path load is opt-in because it creates real Lakebase rows and
immutable audit events. It should be used only in dev/staging or during
an approved production drill window.

Command:

```bash
MIP_API_URL=https://mip-app-2543889327043640.aws.databricksapps.com \
MIP_BEARER_TOKEN=<workspace token> \
MIP_LOAD_TEST_WRITE=1 \
MIP_USERS=5 MIP_SPAWN_RATE=2 MIP_RUN_TIME=1m \
MIP_LOAD_TEST_FAIL_ON_BASELINE_REGRESSION=1 \
bash tools/load_test/run.sh
```

Evidence: `tools/load_test/results/20260518T190152Z_stats.csv`

| Endpoint | Budget | p50 | p95 | p99 | Requests | Fail% | Status |
|---|---:|---:|---:|---:|---:|---:|---|
| `POST /api/v1/outreach/draft` | 2000 ms | 600 | 1100 | 1100 | 6 | 0.0 | pass |
| `POST /api/v1/outreach/approve` | 2000 ms | 1500 | 1700 | 1700 | 6 | 0.0 | pass |
| `POST /api/v1/portfolio/create` | 5000 ms | 2700 | 2700 | 2700 | 2 | 0.0 | pass |
| `POST /api/v1/genie/message` | 30000 ms | 12000 | 15000 | 15000 | 5 | 0.0 | pass |
| `POST /api/v1/genie/actions` | 5000 ms | 980 | 1000 | 1000 | 5 | 0.0 | pass |

Comparator result:

```text
baseline comparison: no p95/failure-rate regressions against committed baseline
```

The write run also includes a small read sample so it can keep a real
borrower pool. The comparator intentionally does not overwrite or fail
the read-only p95 baseline from that small mixed-profile sample.

## What Degrades First

1. **Genie message latency.** A governed Genie turn is expected to take
   seconds, not milliseconds. The write budget is 30s and the validated
   p95 is 15s.
2. **Campaign creation.** `portfolio/create` writes campaign and variant
   rows to Lakebase. The validated p95 is 2.7s, below the 5s budget.
3. **Warehouse cold start.** This baseline is warm. A cold 2X-Small
   serverless SQL warehouse can still add tens of seconds before caches
   are primed.
4. **Horizontal scale cache behavior.** The app currently runs as a
   single Databricks App instance. TTL caches are process-local; if the
   app is scaled to multiple replicas, each replica warms independently.

## Re-run

Read-only baseline:

```bash
MIP_API_URL="$MIP_APP_URL" \
MIP_BEARER_TOKEN="$TOKEN" \
MIP_LOAD_TEST_FAIL_ON_BASELINE_REGRESSION=1 \
bash tools/load_test/run.sh
```

Write-enabled baseline:

```bash
MIP_API_URL="$MIP_APP_URL" \
MIP_BEARER_TOKEN="$TOKEN" \
MIP_LOAD_TEST_WRITE=1 \
MIP_USERS=5 MIP_RUN_TIME=1m \
MIP_LOAD_TEST_FAIL_ON_BASELINE_REGRESSION=1 \
bash tools/load_test/run.sh
```

Intentional baseline refresh:

```bash
MIP_LOAD_TEST_WRITE_BASELINE=1 bash tools/load_test/run.sh
```

For write-path baseline capture, include `MIP_LOAD_TEST_WRITE=1`.
Commit `tools/load_test/baseline.json` only when the new values are
expected and the CSV/HTML evidence is attached to the release PR.

## SSE ingress spike (delivery-04)

The Genie job-events stream (named post-guard stages pushed instead of the
1.5 s status poll) is built only if the Databricks Apps ingress carries SSE
unbuffered and reports a client disconnect. The instrument is the admin
probe `GET /api/v1/admin/sse-probe` (backend/api/admin_sse_probe.py) and the
spike tool `tools/databricks/sse_ingress_spike.py`, run by the integrator as
an admin after the W5b deploy:

    python tools/databricks/sse_ingress_spike.py --profile <admin profile> \
        --base-url https://<mip-app>.databricksapps.com --out spike.json

Method. `flush`: 20 events x 500 ms. `pad`: the same with a 2,048-byte
comment per event. `idle`: 3 events x 45 s gaps, once with no keep-alive and
once with a `: keepalive` comment every 15 s. `disconnect`: read 3 of 60
events, close, wait 3 s, then read the server's record of the run.

PASS rule. flush and pad pass when the first event arrives within 3 s, the
median per-event lag stays within 250 ms of the server cadence, and no burst
follows a silence of 2 s or more (a buffering ingress releases events in
bursts). disconnect passes when the server stopped by sequence 6. Decision:
build the job-events stream only if flush, pad and disconnect all PASS and
the keep-alive idle run survived; otherwise keep polling (the stages already
reach the rail through the poll). Exit codes: 0 PASS, 1 FAIL, 2 INCONCLUSIVE
(auth or network).

In-process observation (W5b, tests/unit/test_admin_sse_probe.py, driving
`backend.main.app` with a recording ASGI `send` through the real middleware
stack): each event leaves as its own body message with `more_body=True`, GZip
leaves `text/event-stream` alone, and an `http.disconnect` delivered after 3
events DOES reach the probe through the four BaseHTTPMiddleware layers
(events_sent 3, disconnected_at_seq 3, completed false). Recorded as a fact,
not asserted in CI.

| Scenario | Result | Notes |
| --- | --- | --- |
| flush | pending integrator run | |
| pad | pending integrator run | |
| idle, no keep-alive | pending integrator run | |
| idle, keep-alive 15 s | pending integrator run | |
| disconnect | pending integrator run | |
| Decision | pending integrator run | build / keep_polling |

## Cold-cache profile (delivery-09)

The baselines above are WARM. delivery-09 asks the cold question: on a cold
cache, do simultaneous users fill the dependency semaphores with blocked
single-flight followers until the worker threads run out (a 429
`dependency_saturated`) or the shell stalls (`/health`, `/session`)? The
profile is `tools/load_test/locust_cold_cache.py`; its pure analysis is
`tools/load_test/cold_cache_analysis.py` (no Locust import, unit-tested).

Profile. 30 users at spawn rate 30. Each user fires the first-load sequence
once, back to back (health, session, config/options, home/summary,
POST portfolio/preview, geo/state-rollups, geo/rate-sensitivity, segments,
analytics/rate-window, POST genie/start), then weighted tasks at
between(1, 3) s. The default profile is audit-free reads only: the two POSTs
are read-only, `/leads` is opt-in (`MIP_COLD_CACHE_INCLUDE_AUDITED=1`, because
every page writes a VIEW_LEADS row) and `/borrowers/{id}` is never called.
Requests are named by route template; the bearer is never printed.

    MIP_BEARER_TOKEN=<dedicated load identity token> \
      locust -f tools/load_test/locust_cold_cache.py --headless \
      -u 30 -r 30 --run-time 5m --host https://<mip-app>.databricksapps.com

Scenarios.

- A, cold process: start within 60 s of green activation, or right after an
  `apps stop` / `apps start` in the piecewise recipe.
- B, warm process right after a gold refresh: the coordinated generation
  miss. Every hot key misses once when `gold_snapshot_advanced` is logged
  (the learned gold snapshot, docs/observability.md section 8); watch for that
  line in the App log during the run.

Metrics. Per templated route: p50 / p95 / p99 / max for the first 60 s (the
cold window) and for the whole run; 429s by class (`dependency_saturated:<dependency>`,
`rate_limited:<scope>`, other); 503s by reason; `/health` and `/session` p95 in
the cold window. Results land in `tools/load_test/results/<UTC>-cold-cache.json`
(git-ignored) and the summary and verdict print on quit.

Verdict rule. `reproduced` iff the cold window holds any
`dependency_saturated` 429, or the `/health` or `/session` p95 in the cold
window exceeds 3000 ms; otherwise `not_reproduced`.

Caveats. One bearer is one actor: the per-actor buckets (360/min expensive,
600/min default) belong to the harness, so `rate_limited` 429s are reported
but never counted. `/home/summary` updates the identity's last visit, so run
as a dedicated load identity, never a person's.

Decision. W5d `w5-genie-provenance-tiles` builds the single-flight slot
release only if A or B is `reproduced`; otherwise it records notBuilt with the
measured numbers.

| Scenario | Verdict | Cold p95 /health | Cold p95 /session | Cold 429 dependency_saturated | Notes |
| --- | --- | --- | --- | --- | --- |
| A, cold process | pending integrator run | | | | |
| B, post-refresh generation advance | pending integrator run | | | | |
| Decision | pending integrator run | | | | build / notBuilt |

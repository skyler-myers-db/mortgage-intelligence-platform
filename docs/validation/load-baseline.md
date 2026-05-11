> **Internal implementation artifact. Not approved for public release.**

# Validation: load-baseline

Short companion to [`docs/load-baseline.md`](../load-baseline.md).
Captures what was tested, which thresholds passed, and which would
fail under production-scale load.

## Scope of this validation

- **What ran:** `tools/load_test/locustfile.py` executed headlessly
  via `tools/load_test/run.sh` against `uvicorn backend.main:app` on
  `127.0.0.1:8000`. Three passes: 1 VU / 30s, 5 VU / 45s, 20 VU /
  60s.
- **What the harness hit:** `/api/health`, `/api/portfolio/preview`,
  `/api/leads`, `/api/segments`, and the chained
  `/api/borrowers/{id}` drill-down. Five endpoints, weighted 1:3:5:4:2
  respectively.
- **What the harness did NOT hit:** the real SQL warehouse, real
  Lakebase, or real Genie. The shell the harness ran in did not have
  `DATABRICKS_*` credentials exported; the backend booted with
  `MIP_BYPASS_STARTUP_CHECKS=1` so the harness could measure FastAPI +
  resilience-layer overhead. This is documented in the baseline doc
  and called out on every relevant row.

## Thresholds that passed

| Threshold                                            | Measured   | Status |
| ---------------------------------------------------- | ---------- | ------ |
| Locust harness completes 60s @ 20 VUs without crashing | 616 reqs / 10.3 rps | pass |
| Harness-side errors (connection, JSON) = 0            | 0 of 616   | pass |
| `/api/health` warm p95 < 500ms                       | 450 ms     | pass   |
| `/api/leads` p95 < 1500ms (fast-fail path)           | 13 ms      | pass   |
| `/api/segments` p95 < 1000ms (fast-fail path)        | 13 ms      | pass   |
| `POST /api/portfolio/preview` p95 < 1000ms (fast-fail path) | 12 ms | pass   |
| FastAPI+resilience overhead < 20ms p95               | 13 ms agg  | pass   |

The "fast-fail path" rows mean that when the repository factory fails
to construct a SQL client, the 500 response path takes single-digit
milliseconds — so the resilience layer is not adding latency of its
own. They do NOT mean the endpoints are fast under live UC load.

## What would fail under production-scale load

These are *predictions* grounded in the architecture, not
measurements. Re-running the harness against a real-UC backend is
required to confirm each.

1. **`/api/borrowers/{id}` p95 on a cold cache** would likely exceed
   the 2000ms threshold on first-hit-per-CLIP, because the borrower
   repository joins across multiple gold tables plus evidence.
   Warm-cache hits should meet the threshold; the cold-start case is
   the risk.
2. **`/api/leads` p95 at 50+ concurrent users** may exceed 1500ms
   when the warehouse is auto-paused and a cold-start burns 20-60s on
   the first batch. The warm-start lifespan hook covers a planned
   boot, but not a mid-day auto-pause + surge.
3. **Lakebase audit writes queue up** behind the 10.3 rps leads + 10
   rps borrower traffic. The routers fire audit writes via FastAPI
   `BackgroundTasks`, so user-facing p95 should not regress — but if
   Lakebase starts rejecting connections, the background queue grows
   unbounded. Need a bounded-queue / drop-oldest policy in
   `audit_store.py` before a real load test at scale.
4. **The borrower chain breaks at 100+ VUs** when `/api/leads`
   throttles or warms slowly — the harness handles this by
   refreshing borrower IDs between ticks, but a real operator load
   would see dossier 404s on stale IDs if the queue re-ranks during
   the fetch.
5. **Circuit breaker trips** would cascade: one 503 from warehouse
   would flip the breaker open, and every read endpoint returns 503
   until the half-open probe succeeds. The harness will report this
   as a spike in fail-rate; the frontend's degraded banner is the
   real user mitigation.

## Required next steps before "load-ready"

- Re-run the harness against a backend booted with `.env.local`
  loaded, capture live-UC percentiles, update `docs/load-baseline.md`
  with warm + cold numbers side by side.
- Smoke-test against a deployed dev Databricks App with
  service-principal auth (see README), not just localhost.
- Confirm the `MIP_CACHE_TTL_S` default of 30s is compatible with
  the observed p95 variance; may want to bump to 60s for KPI reads.
- Add a `MIP_MAX_CONCURRENT_WAREHOUSE_QUERIES` semaphore before
  testing above 50 VUs so we don't saturate the warehouse pool.

## Files covered by this validation

- `tools/load_test/locustfile.py`
- `tools/load_test/k6_smoke.js`
- `tools/load_test/run.sh`
- `tools/load_test/README.md`
- `docs/load-baseline.md`

None of these are imported by `backend/` or `frontend/` — the harness
is an operator tool and does not affect the running app.

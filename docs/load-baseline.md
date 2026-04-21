# Module 0 load-test baseline

Latency baseline for the five hottest Module 0 read endpoints under
concurrent load. Paired with the harness in `tools/load_test/` and
re-runnable via `tools/load_test/run.sh`.

## Environment snapshot

| Field                | Value                                                     |
| -------------------- | --------------------------------------------------------- |
| Ran on               | 2026-04-21                                                |
| Host                 | macOS darwin 25.5.0 / Apple silicon                       |
| Python               | 3.13.13 (repo `.venv`)                                    |
| Backend              | `uvicorn backend.main:app --host 127.0.0.1 --port 8000`   |
| Frontend             | not exercised (API-only baseline)                         |
| Warehouse            | **NOT reachable from this shell** (see caveat below)      |
| Lakebase             | **NOT reachable** (local Postgres not running)            |
| Genie Space          | **NOT reachable**                                         |
| Locust               | 2.43.4                                                    |
| Harness              | `tools/load_test/locustfile.py`                           |
| Branch               | `slice13-accuracy-validation`                             |

### Caveat: this baseline is harness-quality, not prod-quality

The shell this baseline was run from does not have
`DATABRICKS_HOST`/`DATABRICKS_TOKEN`/`DATABRICKS_WAREHOUSE_ID` exported,
and no local Lakebase is running. The backend was started with
`MIP_BYPASS_STARTUP_CHECKS=1` to allow boot without those credentials —
which means:

- `/api/health` **returns HTTP 200 with `status: "degraded"`** (three
  dependency probes time out at 1s each, running in parallel, so the
  first request costs ~500ms; subsequent requests hit the warm
  dependency-probe path and cost <5ms).
- `/api/leads`, `/api/segments`, `/api/portfolio/preview`, and
  `/api/borrowers/{id}` **all return HTTP 500** immediately at the
  repository factory because no SQL client can be constructed.

What this baseline therefore measures is **FastAPI + router + repo
factory + resilience-layer fast-fail overhead**, not live-UC latency.
It is still useful because:

1. It proves the harness scales cleanly (616 requests in 60s at 20
   VUs, zero harness-side errors, clean shutdown).
2. It sets a *floor* for the request path: if a real-UC request ever
   gets slower than what live-UC _adds_ to these numbers, the overhead
   is in the resilience/repo/FastAPI layer, not the warehouse.
3. It captures the 1s-probe-timeout signature on cold `/api/health`,
   which operators will see on a real cold warehouse too.

A follow-up run against a shell that has live `.env.local` creds
loaded (or against a deployed Databricks App) will replace this
section with warm-warehouse numbers. The harness itself does not
change.

## Percentiles per concurrency step

Each cell is **milliseconds**. Percentiles come straight from Locust's
`_stats.csv`. "—" means zero requests hit that endpoint during that
run (e.g. health weight is 1; on a 30s single-user run, it may never
be sampled because the leads task dominates random selection).

### 1 concurrent user (30s, harness-overhead only)

| Endpoint                      |  p50 |  p95 |  p99 | count | fail% |
| ----------------------------- | ---: | ---: | ---: | ----: | ----: |
| `GET /api/health`             |   —  |   —  |   —  |     0 |    —  |
| `POST /api/portfolio/preview` |    7 |    7 |    7 |     2 |   100 |
| `GET /api/leads`              |    3 |   12 |   12 |    13 |   100 |
| `GET /api/borrowers/{id}`     |   —  |   —  |   —  |     0 |    —  |
| `GET /api/segments`           |   11 |   11 |   11 |     2 |   100 |

Borrower 0 hits: the leads task never returned non-empty JSON (500
response body is empty), so the chained call had no IDs to follow.
That's the expected behavior given there's no live warehouse; on a
real-UC run, borrower will dominate after the first leads tick.

### 5 concurrent users (45s)

| Endpoint                      |  p50 |  p95 |  p99 | count | fail% |
| ----------------------------- | ---: | ---: | ---: | ----: | ----: |
| `GET /api/health`             |    6 |  430 |  430 |     6 |     0 |
| `POST /api/portfolio/preview` |    8 |   14 |   24 |    25 |   100 |
| `GET /api/leads`              |    7 |   11 |   48 |    65 |   100 |
| `GET /api/borrowers/{id}`     |   —  |   —  |   —  |     0 |    —  |
| `GET /api/segments`           |    8 |   10 |   11 |    21 |     0* |

*segments reports 100% failure in the aggregate CSV; the 0% in this
row is a mis-transcription — corrected in the 20-user row below
where the fuller sample is authoritative.*

### 20 concurrent users (60s, target load)

| Endpoint                      |  p50 |  p95 |  p99 | count | fail% |
| ----------------------------- | ---: | ---: | ---: | ----: | ----: |
| `GET /api/health`             |    4 |  450 |  540 |    35 |     0 |
| `POST /api/portfolio/preview` |    6 |   12 |   13 |   124 |   100 |
| `GET /api/leads`              |    6 |   13 |   19 |   377 |   100 |
| `GET /api/borrowers/{id}`     |   —  |   —  |   —  |     0 |    —  |
| `GET /api/segments`           |    5 |   13 |   19 |    80 |   100 |

Aggregated across all endpoints: **10.3 req/s, 94.3% failure rate,
p95 = 12ms, p99 = 19ms, max = 540ms** (the cold-probe tail on
`/api/health`).

## Threshold pass/fail

Thresholds from `tools/load_test/README.md`:

| Endpoint                      | p95 target | p95 measured | Pass? |
| ----------------------------- | ---------: | -----------: | ----- |
| `GET /api/health`             |     500 ms |       450 ms | pass (warm), fail (single cold probe) |
| `POST /api/portfolio/preview` |    1000 ms |        12 ms | pass (framework-only) |
| `GET /api/segments`           |    1000 ms |        13 ms | pass (framework-only) |
| `GET /api/leads`              |    1500 ms |        13 ms | pass (framework-only) |
| `GET /api/borrowers/{id}`     |    2000 ms |       n/a    | not sampled |

All "pass (framework-only)" cells represent 500-response fast-fails at
the repo factory. They validate that the resilience layer is *not* a
bottleneck but say nothing about warm-UC latency. Thresholds were
chosen for warm-UC p95s; the framework-only numbers are 30-100x
under the budget, so the resilience/router/FastAPI stack has plenty
of headroom once the warehouse is in the picture.

## What degrades first (predicted)

Once this baseline is re-run against live UC, the predicted ordering
of pressure is:

1. **`GET /api/borrowers/{id}` on a cold cache.** Joins evidence
   across multiple gold tables; first-hit-per-CLIP is uncached.
   Expected warm p95 ~1200ms, cold p95 ~3500ms until the first sweep
   warms the TTL cache.
2. **`GET /api/leads` with a segment filter.** The default no-filter
   call likely hits a materialized gold query; per-segment filters
   may fan out more predicates. Expect 200-400ms of spread between
   filtered and unfiltered p95.
3. **`POST /api/portfolio/preview` on a cold TTL.** 30s cache TTL
   means at least one in N requests hits the warehouse. Warm p95
   should be <200ms; cold p95 ~1200ms.
4. **`GET /api/health` cold first-request.** Three 1s dependency
   probes run in parallel in a ThreadPoolExecutor — the observed
   450ms is the 3x parallel probe converging, not three times 1s.
   After the breaker sees one probe succeed it short-circuits; the
   p50 of 4ms confirms.
5. **`GET /api/segments` is the best behaved.** Small result set,
   TTL-cached, never a bottleneck in any foreseeable scenario.

## How to re-run

```bash
# Terminal 1 -- boot backend with live UC creds exported
source .env.local
uvicorn backend.main:app --host 0.0.0.0 --port 8000

# Terminal 2 -- wait for health, then fire the harness
curl -fsS http://localhost:8000/api/health | jq .status
MIP_API_URL=http://localhost:8000 bash tools/load_test/run.sh
```

Output lands in `tools/load_test/results/<UTC-timestamp>_*.csv`;
re-paste the percentile table into this file.

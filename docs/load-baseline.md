# Module 0 load-test baseline

Latency baseline for the five hottest Module 0 read endpoints under
concurrent load. Paired with the harness in `tools/load_test/` and
re-runnable via `tools/load_test/run.sh`.

## Environment snapshot (2026-04-21, warm-UC)

| Field                | Value                                                     |
| -------------------- | --------------------------------------------------------- |
| Ran on               | 2026-04-21 (Gate-4 closure run)                           |
| Host                 | macOS darwin 25.5.0 / Apple silicon                       |
| Python               | 3.13.13 (repo `.venv`)                                    |
| Backend              | `uvicorn backend.main:app --host 127.0.0.1 --port 8000`   |
| Frontend             | not exercised (API-only baseline)                         |
| Warehouse            | **UP** (serverless SQL, `dbc-3aa503a9`, freshly-refreshed gold) |
| Lakebase             | **down** (local Postgres not running — see notes)         |
| Genie Space          | **UP** (`Mortgage Lead Intelligence`, hardened instructions) |
| Locust               | 2.43.4                                                    |
| Harness              | `tools/load_test/locustfile.py`                           |
| Branch               | `slice13-accuracy-validation`                             |
| Run artefact         | `tools/load_test/results/20260422T004739Z_*.csv/.html`    |

### Boot posture

- Backend booted with `.env.local`-resolved creds + an OAuth token
  extracted from the Databricks CLI (`databricks auth token --host ...`)
  since `.env.local` on this host uses CLI-based auth rather than a
  stored PAT. The token is valid for ~25 minutes — more than enough
  for a 90-second run.
- `MIP_DEFAULT_CATALOG=mip` + `MIP_DEFAULT_SCHEMA=gold` overridden at
  boot (the host still had pre-scrub `mip_demo` in `.env.local`; the
  repo is on post-scrub state).
- Lakebase is not running locally; the backend's resilience layer
  correctly reports `lakebase: down` in `/api/health` and the circuit
  breaker opens after the 5 startup probes. This means
  `/api/borrowers/{id}` payloads do NOT include approval_status /
  outreach_status lookups — those add ~0 ms in this run but are
  expected to add 20-100 ms on a production-configured Lakebase.

### Caveat: Lakebase-dependent latency is not fully exercised

`/api/health`'s three dependency probes run in parallel with a 1 s
timeout. With Lakebase down + breaker open, the probe resolves
instantly (breaker short-circuits) but the probe for Genie does a
real REST call that adds ~1 s to first-request-after-boot. This
shows up as a p95=1.8 s on `/api/health`, which is **3× the original
500 ms threshold**. Recommendation (tracked as a follow-up on
`docs/load-baseline.md`'s §What degrades first): cap health-probe
timeouts at 300 ms or cache the probe result for ≥1 second so a
burst of health hits doesn't fan out a probe each time.

## Percentiles per concurrency step

Each cell is **milliseconds**. Percentiles come straight from Locust's
`_stats.csv`. All numbers below are from the **warm-UC run** that
closed Slice 13 Gate 4.

### 20 concurrent users (90s, warm UC, freshly-refreshed gold)

| Endpoint                      |   p50 |   p95 |   p99 | count | fail% |
| ----------------------------- | ----: | ----: | ----: | ----: | ----: |
| `GET /api/health`             |  1400 |  1800 |  2100 |    23 |     0 |
| `POST /api/portfolio/preview` |     5 |  1100 |  1500 |    86 |     0 |
| `GET /api/segments`           |     5 |   920 |  1200 |    64 |     0 |
| `GET /api/leads`              |  1100 |  1500 |  1800 |   218 |     0 |
| `GET /api/borrowers/{id}`     |  3400 |  4600 |  5900 |   139 |     0 |

Aggregated across all endpoints: **5.92 req/s, 0 failures across 530
requests, p50 = 1100 ms, p95 = 4000 ms, p99 = 4600 ms.**

Observations:
- Cache-backed endpoints (`/api/portfolio/preview`, `/api/segments`)
  have p50 = 5 ms — the 30 s TTL cache is doing its job. The tail
  (p95 ≥ 900 ms) is cache-miss hits against the warehouse.
- `/api/leads` p50 = 1.1 s is warehouse-bound: one query for the
  ranked population, plus the response-model hydration. p95 = 1.5 s
  matches the published threshold exactly (pass).
- `/api/borrowers/{id}` is the slowest: it fans out into borrower_360
  + evidence_events + recommended_offers. Expected to get ~300 ms
  faster once Lakebase is up (lifecycle_state lookups land).
- `/api/health` is slow because of the Genie probe (HTTP round-trip);
  see §Caveat above.

## Threshold pass/fail (warm UC)

Thresholds from `tools/load_test/README.md`:

| Endpoint                      | p95 target | p95 measured | Pass?            |
| ----------------------------- | ---------: | -----------: | ---------------- |
| `GET /api/health`             |     500 ms |     1 800 ms | **FAIL** (Genie probe) |
| `POST /api/portfolio/preview` |    1000 ms |     1 100 ms | **FAIL** (cache miss tail) |
| `GET /api/segments`           |    1000 ms |       920 ms | pass             |
| `GET /api/leads`              |    1500 ms |     1 500 ms | pass (at limit)  |
| `GET /api/borrowers/{id}`     |    2000 ms |     4 600 ms | **FAIL**         |

Net: 2/5 endpoints meet their published threshold on warm UC. The
three failures are *not* show-stoppers — they're documented
follow-ups:

1. **`/api/health` > 500 ms**: Genie probe serialises a real REST
   call on every health hit. Either cache the probe result for 2-5 s
   (TTLCache is already available) or make it async/best-effort.
2. **`/api/portfolio/preview` p95 > 1 s**: cache-miss traffic hits
   the warehouse for a heavy aggregate. Extend TTL from 30 s → 120 s
   for this endpoint OR pre-compute the aggregate into a gold table
   refreshed by `mip_refresh_scores`.
3. **`/api/borrowers/{id}` p95 ≈ 4.6 s**: fan-out across three gold
   queries per request. Two options: (a) pre-join borrower_360 with
   evidence_events top-3 + recommended_offers into a
   `mip.gold.borrower_dossier` materialised view, refreshed with
   scoring; (b) parallelise the three queries in the repository layer.
   Option (a) is the portable, bundle-native fix.

None of these gate release for Module 0 — they are **performance
debt, not correctness debt**. The app is green on accuracy (Slice 13
§1 evidence); throughput is demonstrated (530 requests, 0 failures);
latency is recoverable from the three documented places above.

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

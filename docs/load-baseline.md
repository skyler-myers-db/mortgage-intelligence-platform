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

**Latest run** — after every Slice-13 perf follow-up landed: health
probe cache → 2 s soft / 10 s hard stale-while-revalidate,
portfolio-preview TTL 30 s → 120 s, parallel borrower queries, and
the new `mip.gold.borrower_dossier` CTAS that collapses the dossier
fan-out into a single row read. Target: **deployed Databricks App**
at `https://mip-app-2543889327043640.aws.databricksapps.com` via
workspace-identity Bearer.

| Endpoint                      |   p50 |   p95 |   p99 | count | fail% |
| ----------------------------- | ----: | ----: | ----: | ----: | ----: |
| `GET /api/health`             |   100 |   130 |   810 |    54 |     0 |
| `POST /api/portfolio/preview` |   100 |   110 |   560 |   148 |     0 |
| `GET /api/segments`           |   100 |   190 |   580 |    98 |     0 |
| `GET /api/leads`              |   930 |  1300 |  1500 |   261 |     0 |
| `GET /api/borrowers/{id}`     |   920 |  1200 |  1300 |   153 |     0 |

Aggregated across all endpoints: **7.93 req/s, 0 failures across 714
requests, p50 = 540 ms, p95 = 1100 ms, p99 = 1400 ms.**

The full journey of the Slice-13 perf arc, same harness +
configuration throughout:

| Endpoint                      | baseline | after commit `d520d67` | after commits `db1bb5a`+`529708f` (this run) |
| ----------------------------- | -------: | ---------------------: | -------------------------------------------: |
| `GET /api/health`             |  1 800 ms | 1 100 ms (-38 %)        | **130 ms (-93 % from baseline)**              |
| `POST /api/portfolio/preview` |  1 100 ms |   120 ms (-89 %)        | **110 ms (-90 %)**                             |
| `GET /api/segments`           |    920 ms |   580 ms (-37 %)        | **190 ms (-79 %)**                             |
| `GET /api/leads`              |  1 500 ms | 1 400 ms ( -7 %)        | **1 300 ms (-13 %)**                           |
| `GET /api/borrowers/{id}`     |  4 600 ms | 3 300 ms (-28 %)        | **1 200 ms (-74 %)**                           |

The `/api/health` drop from 1 100 ms → 130 ms is the SWR cache
doing its job: within the 2 s soft TTL every caller hits a sub-ms
local cache; background refreshes run off the request thread so
callers never block on the ~1 s Genie HTTP round-trip. The
`/api/borrowers/{id}` drop from 3 300 ms → 1 200 ms is the
`mip.gold.borrower_dossier` pre-join replacing two serialised
warehouse queries with one indexed row read.

## Threshold pass/fail (warm UC, deployed)

Thresholds from `tools/load_test/README.md`:

| Endpoint                      | p95 target | p95 measured | Pass?         |
| ----------------------------- | ---------: | -----------: | ------------- |
| `GET /api/health`             |     500 ms |       130 ms | **pass**      |
| `POST /api/portfolio/preview` |    1000 ms |       110 ms | **pass**      |
| `GET /api/segments`           |    1000 ms |       190 ms | **pass**      |
| `GET /api/leads`              |    1500 ms |     1 300 ms | **pass**      |
| `GET /api/borrowers/{id}`     |    2000 ms |     1 200 ms | **pass**      |

Net: **5/5 endpoints meet their published p95 threshold on warm UC**
against the deployed app — up from 2/5 at the original baseline and
3/5 after the first perf wave.

### Cold-start footnote

Two separate runs were captured for this section. The first fired
immediately after the 9-task `mip_refresh_scores` deploy completed;
the warehouse was still warming so `/api/segments` and
`/api/leads` recorded bimodal percentiles (p50 under 200 ms, p95
spiking above 2 s on the first 5-10 requests before the SQL query
cache warmed). The numbers above are the **second run**, executed
~60 s after the first when the warehouse was fully warm. Operators
running this in production should expect a similar first-10-seconds
spike after a cold warehouse restart; subsequent minutes sit at the
warm numbers here.

## What degrades first (observed)

With every endpoint under its threshold on warm UC, the remaining
pressure points are operational, not per-request:

1. **Warehouse cold-start after the 15-min auto-stop idle.** First
   request after the warehouse stops adds 30-60 s. Mitigated today
   by the Lakeflow refresh running every business day and by
   operator warm-up scripts (`docs/runbook.md §1.1`).
2. **Genie first-query cold-start.** ~10-15 s once the space has
   been idle. The SWR cache keeps `/api/health` fast through this
   window; only `/api/genie` calls hit it directly.
3. **Lakebase OAuth credential expiry.** Short-lived creds are
   re-minted via `_resolve_lakebase_connection_params`. No p95
   impact observed.

None of these gate release. The Slice-13 performance arc is
complete.

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

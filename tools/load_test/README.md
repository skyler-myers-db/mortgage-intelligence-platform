# Module 0 load-test harness

Operator-only harness for probing the five hottest Module 0 API
endpoints under realistic concurrent load. Not a runtime dependency,
not wired into CI — use it when you want a latency baseline for the
app in a specific environment (local uvicorn, dev Databricks App,
staging Databricks App).

## What it exercises

Weighted against expected real-world traffic:

| Weight | Endpoint                         | Why it's hot                              |
| -----: | -------------------------------- | ----------------------------------------- |
|      1 | `GET /api/health`                | Load-balancer probe every few seconds     |
|      3 | `POST /api/portfolio/preview`    | Home-page KPI strip                       |
|      5 | `GET /api/leads`                 | Lead queue — the dominant read path       |
|      4 | `GET /api/borrowers/{id}`        | Dossier drill-down, chained off `/leads`  |
|      2 | `GET /api/segments`              | Segment chip strip + filter dropdowns     |

The borrower task chains off the leads response — it picks a random
`borrower_id` from the most recent `/api/leads` body instead of
hardcoding fixture IDs. That keeps the IDs grounded in whatever the
live warehouse actually returns and avoids 404 noise.

## Install (one-time)

Locust is *not* in `requirements.txt`. Keeping it out means production
images don't carry Locust + gevent + flask for a tool only operators
use.

```bash
pip install locust
# or, into the repo venv:
./.venv/bin/pip install locust
```

For the k6 variant: `brew install k6` (optional — use either Locust or
k6, not both).

## Run locally (uvicorn + real UC)

```bash
# Terminal 1 — boot the backend against real UC
uvicorn backend.main:app --host 0.0.0.0 --port 8000

# Wait for startup (warehouse warm-start runs in lifespan)
curl -fsS http://localhost:8000/api/health | jq .status   # -> "ok" or "degraded"

# Terminal 2 — run the harness
MIP_API_URL=http://localhost:8000 bash tools/load_test/run.sh
```

`run.sh` drives Locust headlessly for two minutes at 20 users (5/s
spawn rate), writes a timestamped CSV + HTML into
`tools/load_test/results/`, and prints a per-endpoint latency table on
exit.

Tune the load with env vars:

```bash
MIP_USERS=50 MIP_RUN_TIME=5m MIP_SPAWN_RATE=10 bash tools/load_test/run.sh
```

## Run against a deployed app

```bash
MIP_API_URL=https://<app-host>.databricksapps.com bash tools/load_test/run.sh
```

Two things to know before you do this:

1. **Coordinate with the workspace admin.** Every lead/borrower
   request touches a real SQL warehouse and Lakebase. At 20 VUs you're
   fine; at 200 VUs you're a noisy neighbour. Don't point this at a
   shared prod warehouse without a heads-up.
2. **Auth.** Deployed Databricks Apps sit behind workspace SSO. Locust
   does not ride a browser cookie for you. For headless runs against a
   deployed URL, hit a service-principal-authed route or run from a
   network that has app-level allowlist access. A trivial bootstrap is
   to add `--headers "Authorization: Bearer <token>"` via a custom
   Locust hook; this harness leaves auth open by design because the
   local baseline doesn't need it.

## k6 alternative

If you have k6 installed and don't want to deal with Locust:

```bash
MIP_API_URL=http://localhost:8000 k6 run tools/load_test/k6_smoke.js
```

Shape: 30s ramp → 60s steady at 20 VUs → 30s ramp-down. Same
endpoint coverage; a flatter scripting model.

## Thresholds (what "pass" means)

These are the numbers we expect on a warm local uvicorn pointed at a
warm serverless warehouse. Cold-start runs (first request after the
warehouse auto-stopped) blow past them — that's expected, and the
degraded-state banner in the UI is the user-facing mitigation.

| Endpoint                      | p95 target | Notes                                       |
| ----------------------------- | ---------: | ------------------------------------------- |
| `GET /api/health`             |    500 ms  | No warehouse round-trip unless probe opens  |
| `POST /api/portfolio/preview` |   1000 ms  | Cached KPI shape, short-TTL                 |
| `GET /api/segments`           |   1000 ms  | Small result set, cache-friendly            |
| `GET /api/leads`              |   1500 ms  | Gold table read, 50–500 rows                |
| `GET /api/borrowers/{id}`     |   2000 ms  | Joins + evidence fetch on miss              |

Error-rate threshold: `http_req_failed < 2%`. A cold-start 503 from
the circuit breaker counts; a 404 from a stale borrower_id does not
(the harness marks those as success — see `locustfile.py`).

## Reading the output

`tools/load_test/results/<timestamp>_stats.csv` is the file to keep.
Columns of interest:

- `50%`, `95%`, `99%` — latency percentiles in milliseconds.
- `Requests/s` — throughput per endpoint.
- `Failure Count` / `Request Count` — error rate.

The console summary at the end of `run.sh` pretty-prints the same
numbers. The HTML report (`<timestamp>.html`) has per-endpoint
time-series charts — easy to attach to a PR.

## When to re-run

- After any change to `backend/services/scoring.py`,
  `backend/services/resilience.py`, or `sql/uc_functions/fn_lead_score.sql`.
- After warehouse size changes in `databricks.yml`.
- Before cutting a release candidate.

Baseline numbers (env + percentile table) live in
`docs/load-baseline.md`; the short validation companion lives in
`docs/validation/load-baseline.md`.

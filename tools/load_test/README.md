# Module 0 load-test harness

Operator-only harness for probing the hottest Module 0 API endpoints
under realistic concurrent load. Not a runtime dependency, not wired
into CI — use it when you want a latency baseline for the app in a
specific environment (local uvicorn, dev Databricks App, staging
Databricks App).

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

Write paths are intentionally **off by default**. Set
`MIP_LOAD_TEST_WRITE=1` to add three weight-1 tasks:

| Task | Endpoints | Why it exists |
|---|---|---|
| Outreach approval | `POST /api/v1/outreach/draft` then `/approve` | Exercises the governed approval + audit-ledger transaction. |
| Portfolio create | `POST /api/v1/portfolio/create` | Exercises campaign creation and Lakebase state writes. |
| Genie confirm | `POST /api/v1/genie/message` then `/actions` | Exercises action-token issuance, HMAC confirmation, and cohort/campaign/audit writes. |

These calls create real rows in Lakebase and the immutable audit ledger.
Use them only against dev/staging or an explicitly approved production
drill window.

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

Before the measured Locust window starts, `run.sh` performs a warmup
pass against health, segments, portfolio preview, the default lead
queue, each supported segment filter, and the first 50 borrower dossiers
from the ranked queue. That matches the documented "sustained warm
load" baseline rather than measuring warehouse cold-start and first-key
cache misses. Set `MIP_LOAD_TEST_SKIP_WARMUP=1` only when you explicitly
want a cold-start load run.

Tune the load with env vars:

```bash
MIP_USERS=50 MIP_RUN_TIME=5m MIP_SPAWN_RATE=10 bash tools/load_test/run.sh
```

The borrower drill-down task samples from the first 50 ranked borrowers
by default, which mirrors visible first-page operator behavior while
keeping per-ID stats coalesced. Override with
`MIP_LOAD_TEST_BORROWER_POOL_SIZE=100` when you intentionally want a
wider dossier-cache churn test.

The harness targets canonical `/api/v1` paths by default. Override only
when validating the temporary unversioned compatibility alias:

```bash
MIP_API_PREFIX=/api MIP_API_URL=http://localhost:8000 bash tools/load_test/run.sh
```

## Run against a deployed app

```bash
MIP_API_URL=https://<app-host>.databricksapps.com \
MIP_BEARER_TOKEN="$(databricks auth token --host "$DATABRICKS_HOST" | jq -r .access_token)" \
bash tools/load_test/run.sh
```

Two things to know before you do this:

1. **Coordinate with the workspace admin.** Every lead/borrower
   request touches a real SQL warehouse and Lakebase. At 20 VUs you're
   fine; at 200 VUs you're a noisy neighbour. Don't point this at a
   shared prod warehouse without a heads-up.
2. **Auth.** Deployed Databricks Apps sit behind workspace SSO. Locust
   does not ride a browser cookie for you. `run.sh`/`locustfile.py`
   read `MIP_BEARER_TOKEN` and attach it as an `Authorization: Bearer`
   header. Mint one with `databricks auth token --host "$DATABRICKS_HOST"`.

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

Write-path budgets are intentionally looser because they hit Lakebase
transactions and sometimes Genie:

| Endpoint | p95 target |
|---|---:|
| `POST /api/v1/outreach/draft` | 2000 ms |
| `POST /api/v1/outreach/approve` | 2000 ms |
| `POST /api/v1/portfolio/create` | 5000 ms |
| `POST /api/v1/genie/message` | 30000 ms |
| `POST /api/v1/genie/actions` | 5000 ms |

## Reading the output

`tools/load_test/results/<timestamp>_stats.csv` is the file to keep.
Columns of interest:

- `50%`, `95%`, `99%` — latency percentiles in milliseconds.
- `Requests/s` — throughput per endpoint.
- `Failure Count` / `Request Count` — error rate.

The console summary at the end of `run.sh` pretty-prints the same
numbers. The HTML report (`<timestamp>.html`) has per-endpoint
time-series charts — easy to attach to a PR.

## Baseline comparison

`tools/load_test/baseline.json` is the machine-readable companion to
`docs/load-baseline.md`. `run.sh` compares every endpoint in the latest
`_stats.csv` against:

- the endpoint p95 budget,
- the 2% failure-rate budget, and
- a 25% p95 regression tolerance from the committed baseline when a
  measured baseline exists.

When `MIP_LOAD_TEST_WRITE=1`, the comparator still enforces failure
rate for every endpoint, but p95 budget/tolerance checks apply only to
the write endpoints. The mixed write drill includes a small read sample
only to keep a real borrower pool; it must not overwrite or fail the
sustained 20-user read baseline.

By default, regressions are printed as warnings so an exploratory load
test still finishes. To fail the shell on any regression:

```bash
MIP_LOAD_TEST_FAIL_ON_BASELINE_REGRESSION=1 bash tools/load_test/run.sh
```

To intentionally refresh the JSON baseline after a coordinated warm
staging run:

```bash
MIP_LOAD_TEST_WRITE_BASELINE=1 bash tools/load_test/run.sh
```

For write-path baseline capture, include `MIP_LOAD_TEST_WRITE=1` and use
a short approved window. Commit the refreshed JSON only when the new
numbers are expected and the HTML/CSV evidence is attached to the
release PR.

## When to re-run

- After any change to `backend/services/scoring.py`,
  `backend/services/resilience.py`, or `sql/uc_functions/fn_lead_score.sql`.
- After warehouse size changes in `databricks.yml`.
- Before cutting a release candidate.

Baseline numbers (env + percentile table) live in
`docs/load-baseline.md`; the scriptable baseline lives in
`tools/load_test/baseline.json`; the short validation companion lives
in `docs/validation/load-baseline.md`.

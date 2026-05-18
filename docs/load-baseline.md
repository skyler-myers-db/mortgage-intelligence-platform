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

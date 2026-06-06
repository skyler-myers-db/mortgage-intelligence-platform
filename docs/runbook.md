# Module 0 — Operator Runbook

**Who uses this:** the operator, the backup walkthrough lead, and the
on-call engineer during a live session or after a production deploy.
**Companion docs:**
- [`docs/module0-rehearsal-checklist.md`](module0-rehearsal-checklist.md)
  is the proactive pre-session pass (dry-run / pre-walkthrough checklist;
  filename retained to preserve external links). This runbook is the
  reactive incident-response + deploy guide. Run the checklist **before**
  every session; reach for this file when something goes sideways.
- [`docs/dashboards.md`](dashboards.md) — dashboard cold-start &
  pending-state behaviour. Read this before explaining to a partner
  why a `delta_vs_prior_*` column is blank or an approval-rate cell is
  `0` on a first-day deploy.
- [`docs/disaster-recovery.md`](disaster-recovery.md) — recovery for
  corrupted Lakebase state, bad app snapshots, bundle resource regressions,
  deleted Genie spaces, audit archival, and HMAC action-secret rotation.

The Module 0 app runs on live Unity Catalog + Lakebase — there is no
mock fallback in the deployed app. Everything below assumes the
operator can hit both the app URL and the Databricks workspace CLI.
Use canonical `/api/v1/*` paths in every operator command. Deprecated
`/api/*` aliases still work today for compatibility, but new runbooks
and customer procedures should not depend on them.

---

## 1. Session morning-of

Run [`docs/module0-rehearsal-checklist.md`](module0-rehearsal-checklist.md)
end-to-end. It warms the SQL warehouse, probes `/api/v1/health`, cold-starts
Genie, verifies a Lakebase write, and walks the click path. If the
checklist returns all-green, skip to §4 (deploy-from-scratch is only for
a clean workspace).

If any step on the checklist fails, the three highest-likelihood
cold-start problems — and their one-line recovery commands — are:

### 1.1 Warehouse cold / `warehouse: down` in `/api/v1/health`

The 2X-Small serverless warehouse auto-stops after 15 min idle. First
query after a cold window is 30–60 s.

```bash
databricks warehouses start "$DATABRICKS_WAREHOUSE_ID"
# Expect: RUNNING within ~30 s.
databricks sql-warehouses get "$DATABRICKS_WAREHOUSE_ID" | jq .state
```

Then prime the first query so the audience doesn't see the cold tax:

```bash
databricks api post /api/2.0/sql/statements \
  --json '{"statement":"SELECT 1 FROM mip.gold.borrower_360 LIMIT 1","warehouse_id":"'"$DATABRICKS_WAREHOUSE_ID"'"}' | jq
```

### 1.2 Lakebase cold / auth token expired

`/api/v1/health` → `"lakebase": "down"` or `/api/v1/audit` returns a 500.

```bash
# Re-issue workspace identity; token auto-refresh on the deployed app,
# but a laptop-connected session sometimes needs a manual refresh.
databricks auth login --host "$DATABRICKS_HOST"
# Probe directly (port 5432, psql client):
psql "host=$LAKEBASE_HOST user=$LAKEBASE_USER dbname=$LAKEBASE_DATABASE sslmode=require" \
  -c "SELECT 1"
```

If the Lakebase instance itself is stopped, bounce it:

```bash
databricks bundle run mip_lakebase_migrate -t dev
```

### 1.3 Genie cold / first ask returns `source: "degraded"`

A cold Genie space takes 10–30 s on the first conversation. The app now
returns an honest degraded response with no fabricated metrics until the
real space is available. Prime it before the audience pitch:

```bash
curl -s -X POST "$MIP_APP_URL/api/v1/genie/message" \
  -H 'content-type: application/json' \
  -d '{"question":"How many borrowers across the current Cotality data coverage are currently in-the-money?"}' \
  | jq '{source, metric_value}'
# Once `source == "genie"`, cached and fast for the session.
```

### 1.4 Load posture before a high-traffic walkthrough

The backend process protects live dependencies with app-side token
buckets plus per-dependency semaphores. Current defaults are:

| Dependency | Limit | Operator note |
|---|---:|---|
| Warehouse reads | 24 concurrent | Slightly above the 2X-Small serverless scheduler so the warehouse queues short bursts. |
| Lakebase reads/writes | 16 concurrent | Matches the Lakebase connection-pool max size; pool checkout should not bottleneck before the semaphore. |
| Genie turns | 6 concurrent | Enough for a demo panel; ask the room to avoid everyone firing Genie at once. |

Short-TTL caches (`TTLCache` / stale-while-revalidate health cache) are
**process-local by design**. Databricks Apps runs Module 0 as a single
app instance today, so local caches reduce warehouse pressure without a
Redis-style dependency. If a customer scales the app to more than one
replica, each replica has its own cache and `tools/load_test/run.sh`
must be re-run against that deployment before signoff.

For walkthrough planning, say this plainly: the app supports six
concurrent Genie turns before it starts applying backpressure.

Use the load harness before major customer sessions:

```bash
MIP_API_URL="$MIP_APP_URL" MIP_BEARER_TOKEN="$(databricks auth token --host "$DATABRICKS_HOST" | jq -r .access_token)" \
  bash tools/load_test/run.sh
```

`run.sh` warms the read caches before its measured window by default
(health, segments, portfolio preview, lead keys, and the first 50
borrower dossiers). Set `MIP_LOAD_TEST_SKIP_WARMUP=1` only when you
want a cold-start stress run rather than the sustained warm-load
baseline.

Write-path load is opt-in because it creates real Lakebase rows:

```bash
MIP_LOAD_TEST_WRITE=1 MIP_USERS=5 MIP_RUN_TIME=1m \
  MIP_API_URL="$MIP_APP_URL" MIP_BEARER_TOKEN="$MIP_BEARER_TOKEN" \
  bash tools/load_test/run.sh
```

---

## 2. Mid-session degraded

### 2.1 The DegradedBanner appeared at the top of the page

**What it means:** `/api/v1/health` flipped to `status: "degraded"` — one
of warehouse / Lakebase / Genie is `down` or a breaker is `open`. The
frontend auto-retries; the banner clears itself when the breaker
closes (typically 30 s after recovery).

**What to say:** *"You'll see a banner — the warehouse is warming up.
That's the real-time health probe being honest, not a stage trick.
Back in a moment."*

**What to do:**
1. Don't click frantically; the retry + circuit-breaker logic handles
   it. Each breaker reopens after a 30 s cool-down and then sends a
   single probe; one success closes it.
2. If the banner persists > 60 s, narrate from the second-monitor API
   endpoints (see §2.3) and come back to the UI after.
3. Operator: in a side terminal, run `curl -s $MIP_APP_URL/api/v1/health | jq`
   to see which dependency is down. That tells you whether to re-warm
   the warehouse (§1.1), refresh the Lakebase token (§1.2), or wait out
   Genie (§1.3).

### 2.2 A Genie answer returned `source: "degraded"`

The Genie circuit breaker is open. The app did not display fabricated
analytics. **Do not re-ask the same question on-stage** — the breaker's
cool-down is 30 s and re-asking just re-opens it.

**What to say:** *"Genie is reconnecting, and the app is refusing to show
unsourced analytics. We'll use the already-loaded proof-backed surfaces and
come back to Genie once the live space is warm."*

### 2.3 Whole UI is gone

Swap to the second-monitor terminal and pre-loaded tabs:

```bash
curl -s $MIP_APP_URL/api/v1/leads?limit=5 | jq
BORROWER_ID="$(curl -s "$MIP_APP_URL/api/v1/leads?limit=1" | jq -r '.[0].borrower_id')"
curl -s "$MIP_APP_URL/api/v1/borrowers/$BORROWER_ID" | jq
curl -s $MIP_APP_URL/api/v1/segments | jq
curl -s $MIP_APP_URL/api/v1/audit/events?limit=5 | jq '.[0]'
```

**What to say:** *"The UI is the skin, not the substance — here's the
same answer from the API."* Narrate the JSON; the dossier endpoint
contains everything the Borrower 360 page renders.

---

## 3. Parity-test red (`tests/integration/test_sql_python_parity.py`)

Nightly CI compares `fn_lead_score` / `fn_in_the_money` /
`fn_rate_spread` / `fn_next_best_offer` (UC SQL UDFs) against the
Python mirrors in `backend/services/scoring.py` across 60+ golden
cases. A red here is a **drift regression** — the Python primitive no
longer matches the UC UDF — and blocks any merge that touches
scoring-adjacent code.

### Diagnose

1. Identify the failing case from the pytest output: it prints
   `case_NN` plus the divergent inputs + outputs.
2. Pull the fixture row:

   ```bash
   jq '.[] | select(.case_id == "case_12")' tests/fixtures/lead_score_golden.json
   ```

3. Run the Python primitive in isolation:

   ```bash
   .venv/bin/python -c "from backend.services.scoring import lead_score; print(lead_score(0.35, 0.30, 0.15, 0.10, 0.10))"
   ```

4. Run the SQL UDF against the warehouse:

   ```bash
   databricks api post /api/2.0/sql/statements \
     --json '{"statement":"SELECT mip.gold.fn_lead_score(0.35, 0.30, 0.15, 0.10, 0.10) AS score","warehouse_id":"'"$DATABRICKS_WAREHOUSE_ID"'"}' | jq
   ```

### Common causes + fixes

| Symptom | Likely cause | Fix |
|---|---|---|
| UDF returns NULL | `fn_*` not deployed to `mip.gold.*` | `databricks bundle run mip_refresh_silver -t dev` |
| Python says 82, UDF says 83 | Rate coerced to percent (`5.75`) instead of fraction (`0.0575`) on one side | Check the fixture input schema; rate_spread expects fraction |
| Python says 81, UDF says 82 | Banker's rounding (HALF_EVEN) vs half-up drift | `backend/services/scoring.py::_round_half_even` must match UDF `round(..., 0)` which is HALF_EVEN in Spark |
| Both say different ints | True scoring drift — primitive changed without fixture update | **STOP.** Revert the primitive edit, update the golden JSON in a separate PR with governance review |

Never "fix" a parity failure by editing the fixture unless you've got
governance + qa-test-engineer sign-off. The fixtures are the contract.

---

## 4. Deploy from scratch

Use this when setting up a new dev/prod workspace, or when the
workspace has been rebuilt. **The one command is `./scripts/deploy.sh`
(or `make deploy-dev`).** Everything below is what that script runs,
in order, idempotently. You shouldn't need to invoke the steps
manually unless something failed and you want to resume from a
specific step.

```bash
# 0. Prereqs: .env.local populated with DATABRICKS_HOST + DATABRICKS_WAREHOUSE_ID.
#    If GENIE_SPACE_ID is blank, deploy.sh provisions it before bundle deploy.

# One command:
./scripts/deploy.sh
# or equivalently:
make deploy-dev
```

That single invocation executes:

1. `npm --prefix frontend run build` — the bundle sync uploads
   `frontend/dist/**` so the FastAPI runtime can serve the SPA.
2. `tools/databricks/bundle_env.py validate -t dev`
   (env-aware wrapper around `databricks bundle validate`; it maps
   `.env.local` to `BUNDLE_VAR_sql_warehouse_id` / `BUNDLE_VAR_genie_space_id`).
3. `tools/databricks/bundle_env.py plan -t dev` — read-only direct
   deployment plan. Review this output before a real app/customer deploy.
4. `tools/databricks/bundle_env.py deploy -t dev` — env-aware wrapper
   around direct `databricks bundle deploy`; SQL warehouse, app, jobs,
   pipelines, Lakebase instance, MLflow experiment, dashboards.
5. `databricks apps deploy mip-app --mode SNAPSHOT` — promotes the
   uploaded bundle source to the running app compute.
6. `databricks bundle run mip_fred_rates_ingest -t dev` — FRED
   MORTGAGE30US into `silver.market_rates_weekly`.
7. `databricks bundle run mip_refresh_silver -t dev` — Cotality Delta
   Share → `mip.silver.*`; geography coverage is discovered from source
   rows with non-null states.
8. `databricks bundle run mip_lakebase_migrate -t dev` — Postgres
   `schema.sql` + `seed_campaigns.sql` (both idempotent).
9. `databricks bundle run mip_refresh_scores -t dev` — CTAS chain:
   `property_owner_bridge` → `evidence_events` → `borrower_360` →
   `lead_scores` → `lead_population` → `segment_population` →
   `lockin_cohort` → `borrower_dossier` → **`refresh_semantics_views`**
   (the three `mip.semantics.*` metric views Genie binds to).
10. `databricks bundle run mip_sync_lifecycle_state -t dev` — initial
   seed run so `mip.gold.borrower_lifecycle_state` has a row per
   borrower and `delta_vs_prior_*` columns can start resolving. After
   deploy, this job is **event-triggered** from the backend approval
   path (POST `/api/v1/outreach/approve` fires
   `backend.services.job_trigger.trigger_lifecycle_sync` via FastAPI
   `BackgroundTasks`, debounced 60 s). A fallback schedule is defined
   but ships **PAUSED in every target**. Only
   unpause it for a customer-approved production cadence; otherwise
   use the Admin Data operations button when a refresh is needed.
11. `python tools/databricks/provision_genie_space.py` — reads
   `genie/mortgage_lead_intelligence_space.yml`, creates or updates
   the Genie Space, binds trusted assets, writes `genie/space_id.txt`.
12. `./scripts/smoke_live.sh` — verify the app and all four deps up.

Flags on `scripts/deploy.sh` for partial re-runs:

| Flag | Effect |
|---|---|
| `--dry-run` | print the plan, make no changes |
| `--skip-silver` | skip steps 5–6 (fast path when silver is already fresh) |
| `--skip-smoke` | skip step 11 |
| `--no-confirm` | skip the interactive `y/N` prompt |

Every step is idempotent — re-running `./scripts/deploy.sh` is safe
and picks up where a previous run stopped.

### Day-to-day refresh from the app

Customer operators do not need the Databricks CLI for normal refreshes. The
deployed app binds the FRED, silver, gold, and lifecycle jobs as app resources,
and `/admin-config` exposes them under **Data operations**:

1. **Refresh market rates** (`mip_fred_rates_ingest`) after FRED publishes or
   when the Admin source-readiness panel shows stale market rates.
2. **Refresh source features** (`mip_refresh_silver`) after Cotality or
   first-party data shares update.
3. **Rebuild scoring snapshot** (`mip_refresh_scores`) after either upstream
   refresh so Borrower 360, Lead Queue, segment populations, source readiness,
   and Genie metric views read the new snapshot.
4. **Sync workflow state** (`mip_sync_lifecycle_state`) when approvals or
   outreach state need to mirror into gold immediately.

Each button is admin-only, writes a Lakebase audit row before launching
compute, refuses duplicate active runs, and shows the latest Databricks run
state. If the audit ledger is unavailable, the app does not launch the job.
The bundle-defined FRED and lifecycle fallback schedules deploy **paused by
default** in dev, prod, and prod_otlp so intermittent development and demo
workspaces do not burn recurring warehouse/Lakebase compute. If a customer
later wants scheduled refreshes, unpause the schedule explicitly in that
customer workspace and document the approved cadence.

**No manual UI step is required for deploy/bootstrap.** The previous runbook called for
opening the Databricks UI to rebind the Genie space's trusted assets
after a metric view rename; that is no longer required. Step 7
publishes the views, step 10 binds them, and re-running `deploy.sh`
re-runs both.

On a brand-new deploy, the dashboards render but a handful of widgets
will show `0` / `NULL` / "pending" by design — specifically the
`delta_vs_prior_*` WoW measures (need ≥ 2 daily snapshots separated by
≥ 7 days) and the approval / outreach / actioned counters (populate
as operators use the app). This is documented in
[`docs/dashboards.md`](dashboards.md) — read it before explaining the
blanks to a reviewer.

---

## 5. Rotate a Databricks PAT

Only applies to local dev (`uvicorn backend.main:app ...` on a laptop).
The deployed app uses workspace identity and doesn't need a PAT.

```bash
# 1. Create a new token in the Databricks UI (User Settings -> Developer -> Access Tokens).
# 2. Update .env.local:
echo "DATABRICKS_TOKEN=<new-token>" >> .env.local   # replace the old line
# 3. Revoke the old token in the UI.
# 4. Restart uvicorn so it picks up the new env.
```

**Do not commit `.env.local`.** Do not paste a PAT into Slack, a PR, or
a notebook. If you accidentally do, rotate immediately (above) and
audit recent commits with `git log --all --source -S "$OLD_TOKEN_PREFIX"`.

---

## 6. Stale real data (silver > 24h old)

If the Cotality Delta Share refresh stalled or the silver pipeline
skipped a run, the app still renders (it reads gold, which is built
off silver) but cohort counts drift. Detect + fix:

```bash
# Detect
databricks api post /api/2.0/sql/statements \
  --json '{"statement":"SELECT MAX(_ingested_at) FROM mip.silver.lien_current","warehouse_id":"'"$DATABRICKS_WAREHOUSE_ID"'"}' | jq

# If that timestamp is > 24h old, re-run silver + gold in sequence:
databricks bundle run mip_refresh_silver -t dev
databricks bundle run mip_refresh_scores -t dev
```

For a session window where live refresh is impractical, **do not**
silently fall back to mock data — the no-silent-mock posture is
load-bearing. Instead: acknowledge on-stage that you're showing
"yesterday's live data" and continue.

---

## 7. Live-UC smoke check (`scripts/smoke_live.sh`)

Run this after any deploy to prove the self-contained promise: the
app can be reached, every dependency responds, and the five canonical
API calls return data.

```bash
./scripts/smoke_live.sh
# or to target a different host:
MIP_APP_URL="$(databricks apps get mip-app -o json | jq -r '.url')" ./scripts/smoke_live.sh
```

The script boots a local uvicorn + vite if `MIP_APP_URL` is unset, waits
for `/api/v1/health` to go green, then exercises `/api/v1/portfolio/preview`,
`/api/v1/leads`, a dynamically selected `/api/v1/borrowers/{borrower_id}`,
`/api/v1/borrowers/{borrower_id}/evidence`, and `/api/v1/genie/message`.
It also verifies Cotality property/owner identifiers are masked on the
lead and borrower payloads. Any non-200 response, unmasked identifier, or
non-up dependency exits non-zero and prints the failing call.

This is the operator's "is real UC actually reachable from this laptop"
check — run it after §4 deploy-from-scratch and after any cred rotation.

---

## 8. CI failure triage

See [`.github/workflows/README.md`](../.github/workflows/README.md)
for the full workflow map. Common red jobs:

- `backend-tests` / `frontend-tests`: local repro with `pytest -q` or
  `npm --prefix frontend run lint && npm run test && npm run build`.
- `talk-track-lint`: spoken word count out of `[1000, 1500]`. Trim or
  expand `docs/module0-talk-track.md` spoken copy (lines starting
  `> `). See `tools/talk_track_wc.py`.
- `bundle-validate`: `databricks bundle validate -t dev` locally with
  placeholder BUNDLE_VARs set (see workflow YAML).
- `playwright-e2e-offline` (PR): missing fixture / test-fixture regression.
  Repro: `npm --prefix frontend run e2e:ci`.
- `parity-live` / `playwright-e2e-live` (nightly): **real-UC regression
  or a creds issue**. Check the workflow run logs; if it's a creds
  issue the workflow will print the missing env var name. Parity drift
  goes to §3.

---

*Owner: qa-test-engineer + principal-architect. Review cadence: after
every incident (post-mortem updates this doc), and before every release
dry-run.*

---

## 9. Credential-kill drill

**When to run:** before every release dry-run, and after any change
to `backend/services/resilience.py`, `backend/api/v1/health.py`, the
warehouse / Lakebase / Genie client modules, or
`frontend/src/components/mortgage/DegradedBanner.tsx`.

**What it proves:** the Module 0 app surfaces a visible degraded state
when any of its four upstream dependencies fails, and never silently
returns fake data.

**How to run (summary):**

```bash
# Warehouse: operator stops the SQL warehouse; script observes degraded
./tools/kill_drill/run_drill.sh --target warehouse

# Lakebase: operator stops the database instance (or rotates password)
./tools/kill_drill/run_drill.sh --target lakebase

# Genie: simulated -- script forks a private backend with a bogus space id
./tools/kill_drill/run_drill.sh --target genie

# Token: simulated -- script unsets DATABRICKS_TOKEN in a subshell
./tools/kill_drill/run_drill.sh --target token

# While any drill is in flight, verify the UI in another terminal:
./tools/kill_drill/verify_degraded_ui.py
```

Each drill writes an evidence log to `tools/kill_drill/evidence/`. The
full procedure, expected signals, and sign-off template live in
[`docs/credential-kill-drill.md`](credential-kill-drill.md). Attach the
four evidence logs (one per target) to the governance record for every
release PR.

A drill FAIL means the resilience posture is broken and the app is
serving fake data during an outage — treat it as a release blocker
and route it to governance-security-reviewer + principal-architect.

---

## 10. Accuracy evidence (Slice 13)

**Where to look** when a customer or a partner asks "prove that the
number on the screen is right":

| Claim | Evidence |
|---|---|
| Segment counts match the raw share | [`tests/integration/test_segment_count_parity.py`](../tests/integration/test_segment_count_parity.py), [`docs/validation/segment-count-parity.md`](validation/segment-count-parity.md) |
| Every borrower page arithmetic reproduces from raw | [`tools/e2e_borrower_audit.py`](../tools/e2e_borrower_audit.py), [`docs/validation/borrower-e2e-audit.md`](validation/borrower-e2e-audit.md) |
| SQL ↔ Python scoring parity | [`tests/integration/test_sql_python_parity.py`](../tests/integration/test_sql_python_parity.py) (nightly) |
| No PII on `/api/*` | [`tests/integration/test_api_pii_boundary.py`](../tests/integration/test_api_pii_boundary.py) |
| Genie grounded + guarded | [`tests/integration/test_genie_regression.py`](../tests/integration/test_genie_regression.py) (nightly), [`genie/regression_suite.md`](../genie/regression_suite.md) |
| Dashboards only hit trusted assets | [`tests/unit/test_lakeview_dashboards.py`](../tests/unit/test_lakeview_dashboards.py) |
| Dependency outage ⇒ visible degraded UI | [`tools/kill_drill/run_drill.sh`](../tools/kill_drill/run_drill.sh), drill evidence logs in `tools/kill_drill/evidence/` |
| Supply-chain + secret hygiene | `.github/workflows/ci.yml` §`security-scan` + `.gitleaks.toml` + `.bandit` |

The full report is [`docs/slice13-accuracy-report.md`](slice13-accuracy-report.md).

**After any SQL-plane change** (silver, gold, UDF, metric view) the
evidence above is only as fresh as the last warehouse refresh. Run:

```bash
databricks bundle run mip_refresh_silver        -t dev
databricks bundle run mip_refresh_scores        -t dev
databricks bundle run mip_sync_lifecycle_state  -t dev
```

Then re-run the gated integration tests with workspace creds exported:

```bash
set -a && source .env.local && set +a
pytest -q tests/integration/test_segment_count_parity.py \
          tests/integration/test_borrower_id_uniqueness.py \
          tests/integration/test_silver_coercion.py \
          tests/integration/test_silver_zip_5_digit.py \
          tests/integration/test_sql_python_parity.py
```

If any fail, route to data-modeler + principal-architect before release.

---

## 11. Admin RBAC header for local dev

The `/api/v1/admin/*` endpoints are gated by
[`backend/services/rbac.py`](../backend/services/rbac.py). Admission is
a match against the configured admin group (default `mip-admin`, env
override `MIP_ADMIN_GROUP_NAME`) or the hard-coded fallback `admins`.
Databricks Apps forwards workspace group membership via
`X-Forwarded-Groups`; the deployed app gets this for free from the
edge.

Local `uvicorn` and `curl` do **not** get that header automatically —
we deliberately chose fail-closed over an `app_env == "local"` auto-
admit (flags like that rot into production). Carry the header on
every local admin call:

```bash
curl -s -H "X-Forwarded-Groups: mip-admin" \
     -H "X-Forwarded-Email: you@entrada.ai" \
     http://localhost:8000/api/v1/admin/rules | jq .

curl -s -X PUT -H "X-Forwarded-Groups: mip-admin" \
     -H "X-Forwarded-Email: you@entrada.ai" \
     -H "Content-Type: application/json" \
     -d '{"overrides":{"note":"local test"}}' \
     http://localhost:8000/api/v1/admin/rules | jq .
```

Missing header returns `403 {"detail": "forbidden"}` — that exact body
string is what the frontend's admin 403 banner keys off of.

Signals to watch in `/api/v1/health` response:

- `fallback_identity_fallbacks_process_total` (canonical as of R6-08;
  the legacy `fallback_identity_fallbacks_total` key is still emitted
  for one cycle for dashboard compatibility) — non-zero in a
  production deploy means Databricks Apps is not forwarding
  `X-Forwarded-Email` on some path, and audit rows are landing under
  `settings.default_actor` instead of the real user. Treat as a
  governance regression and route to governance-security-reviewer.
  The `_process_` infix signals per-replica scope: on a multi-replica
  deploy each replica emits its own count, not a global total.

---

## 12. Diagnostic playbook

Symptom-first triage for the three regressions reviewers hit most often
during a walkthrough. Each entry lists the three highest-probability
causes and the exact command to confirm each. For recovery commands the
full procedure lives elsewhere in this runbook — the links below point
you at it instead of duplicating.

### R4-06 Home KPIs all show 0 or em-dash

**Symptom:** the four headline KPIs on the home dashboard (marketable
population, high-intent leads, top-tier opportunities, offers
recommended) render as `0` or `—` instead of the expected evaluation-share totals.

**Likely causes, in order:**

1. **`mip.gold.lead_population` empty or not refreshed** (highest
   probability — the gold CTAS chain skipped or was never run in this
   workspace).

   Confirm:
   ```bash
   databricks api post /api/2.0/sql/statements \
     --json '{"statement":"SELECT COUNT(*) AS n, MAX(_refreshed_at) AS last_refresh FROM mip.gold.lead_population","warehouse_id":"'"$DATABRICKS_WAREHOUSE_ID"'"}' | jq
   ```
   A zero count or a stale `last_refresh` confirms. Fix: re-run the
   scoring chain from §4 step 7:
   ```bash
   databricks bundle run mip_refresh_scores -t dev
   ```

2. **Schema drift** — the Python `_LEAD_POPULATION_COLUMNS` projection
   in `backend/services/repositories/databricks_repo.py` references a column the
   current gold table doesn't materialize, so the SELECT fails silently
   and the route returns an empty list.

   Confirm:
   ```bash
   databricks api post /api/2.0/sql/statements \
     --json '{"statement":"DESCRIBE mip.gold.lead_population","warehouse_id":"'"$DATABRICKS_WAREHOUSE_ID"'"}' | jq '.result.data_array[] | .[0]'
   ```
   Diff against `_LEAD_POPULATION_COLUMNS`. Fix: rerun silver + gold to
   pick up the latest DDL (§6), or land the missing column in
   `sql/transformations/gold_lead_population.sql` and redeploy.

3. **Warehouse circuit breaker tripped open** — a burst of 5xx from the
   Statement Execution API flipped the warehouse breaker, and every KPI
   call is now short-circuiting to `DependencyDownError`.

   Confirm:
   ```bash
   curl -s "$MIP_APP_URL/api/v1/health" | jq '{deps: .dependencies, breakers: .circuit_breakers}'
   ```
   If `circuit_breakers.warehouse` is `open` or `half_open`, or
   `dependencies.warehouse == "down"`, that's your cause. Fix: follow
   §1.1 to re-warm the warehouse; the breaker re-probes after the 30 s
   cool-down and closes on one successful probe.

### R4-07 Approvals aren't appearing in the audit ledger

**Symptom:** the analyst clicks "Approve" on a lead, the UI shows the
success toast, but `/audit` (and the Audit dashboard) don't surface the
row.

**Likely causes, in order:**

1. **Lakebase credentials missing or stopped** — the write raised, the
   route caught and 500'd, but the frontend optimistic-success swallowed
   the toast signal.

   Confirm:
   ```bash
   curl -s "$MIP_APP_URL/api/v1/health" \
     | jq '{lakebase_dep: .dependencies.lakebase, lakebase_breaker: .circuit_breakers.lakebase}'
   ```
   `lakebase == "down"` is the smoking gun. Fix: §1.2 (re-auth /
   bounce the instance).

2. **RBAC denied the approval call** — the analyst isn't in the admin
   group, or the Databricks Apps edge isn't forwarding
   `X-Forwarded-Groups`, so `POST /api/v1/outreach/approve` returns 403.

   Confirm in the browser devtools Network panel: the approve POST
   should be 200. If it's 403 with body `{"detail":"forbidden"}`, RBAC
   is rejecting. Replay from a trusted host:
   ```bash
   BORROWER_ID="$(curl -s "$MIP_APP_URL/api/v1/leads?limit=1" | jq -r '.[0].borrower_id')"
   curl -s -X POST "$MIP_APP_URL/api/v1/outreach/approve" \
     -H "X-Forwarded-Groups: mip-admin" \
     -H "X-Forwarded-Email: you@entrada.ai" \
     -H "Content-Type: application/json" \
     -d "{\"borrower_id\":\"$BORROWER_ID\",\"offer_code\":\"RATE_TERM_REFI\"}" | jq
   ```
   Fix: see §11 for the header contract; for production the edge should
   be forwarding both headers automatically — if it isn't, route to
   governance-security-reviewer.

3. **Write succeeded, read filtered it out** — the audit list query
   scopes by `actor_email`, and the email the write recorded disagrees
   with the email the read request carries (commonly:
   `X-Forwarded-Email` went through on the write but the read fell back
   to `settings.default_actor`).

   Confirm the row is actually in Lakebase (bypass the read filter):
   ```bash
   psql "host=$LAKEBASE_HOST user=$LAKEBASE_USER dbname=$LAKEBASE_DATABASE sslmode=require" \
     -c "SELECT actor_email, action, borrower_id, created_at FROM mip_app.audit_events ORDER BY created_at DESC LIMIT 5"
   ```
   Then check the identity-fallback counter (canonical key as of R6-08
   is `fallback_identity_fallbacks_process_total`; the legacy
   `_total` key is still emitted for one cycle):
   ```bash
   curl -s "$MIP_APP_URL/api/v1/health" | jq .fallback_identity_fallbacks_process_total
   ```
   A non-zero value means Databricks Apps dropped the header on one of
   the two paths. Fix: see §11's identity-fallback note — governance
   regression, route accordingly.

### R4-08 Map won't drill from state to county

**Symptom:** the user clicks a state on the footprint map and the
county layer hangs on "Loading counties…" or shows no data.

**Likely causes, in order:**

1. **`/api/v1/geo/county-rollups` is 503** — warehouse down, warehouse
   breaker open, or `mip.gold.county_rollup` empty.

   Confirm:
   ```bash
   curl -s -o /dev/null -w '%{http_code}\n' \
     "$MIP_APP_URL/api/v1/geo/county-rollups?state=IL"
   curl -s "$MIP_APP_URL/api/v1/health" \
     | jq '{warehouse_dep: .dependencies.warehouse, warehouse_breaker: .circuit_breakers.warehouse}'
   databricks api post /api/2.0/sql/statements \
     --json '{"statement":"SELECT state_code, COUNT(*) AS n FROM mip.gold.county_rollup GROUP BY state_code","warehouse_id":"'"$DATABRICKS_WAREHOUSE_ID"'"}' | jq
   ```
   Empty per-state counts ⇒ gold table was never built. Fix:
   `databricks bundle run mip_refresh_scores -t dev` (the rollup is
   part of the chain in §4 step 7). Warehouse down ⇒ §1.1.

2. **Session footprint context out of sync with gold** — the user's
   footprint list includes a state that isn't actually in
   `mip.gold.county_rollup`. Clicking it returns 200 with an empty
   `counties: []` and the map shows no features.

   Confirm:
   ```bash
   curl -s "$MIP_APP_URL/api/v1/portfolio/preview" | jq '.footprint'
   ```
   Cross-check discovered states/counties against the SQL query above. The app
   should disclose whatever county coverage is present after the latest gold
   refresh; if the session shows a different state set, route the drift to
   data-modeler and do not record the demo until gold/UI coverage agrees.

3. **`/us-counties.json` TopoJSON returning HTML** — the SPA catch-all
   route is serving `index.html` at that asset path instead of the
   static TopoJSON. This was fixed in the Cycle-8 post-merge round and
   shouldn't recur, but check first because it masquerades as a county
   rollup bug.

   Confirm:
   ```bash
   curl -s -I "$MIP_APP_URL/us-counties.json" | grep -i content-type
   curl -s "$MIP_APP_URL/us-counties.json" | head -c 80
   ```
   Expect `application/json` and a leading `{`. If you see `text/html`
   or `<!doctype`, the static-file mount in `backend/runtime.py`
   regressed. Fix: redeploy (`./scripts/deploy.sh`) and verify the
   frontend build's `frontend/dist/us-counties.json` is present in the
   bundle sync.

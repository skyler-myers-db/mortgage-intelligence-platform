# Module 0 — Operator Runbook

**Who uses this:** the booth operator, the backup presenter, and the
on-call engineer during a live demo or after a production deploy.
**Companion doc:** [`docs/module0-rehearsal-checklist.md`](module0-rehearsal-checklist.md)
is the proactive pre-demo pass. This runbook is the reactive
incident-response + deploy guide. Run the checklist **before** every
demo block; reach for this file when something goes sideways.

The Module 0 app runs on live Unity Catalog + Lakebase — there is no
mock fallback in the deployed app. Everything below assumes the
operator can hit both the app URL and the Databricks workspace CLI.

---

## 1. Booth morning-of

Run [`docs/module0-rehearsal-checklist.md`](module0-rehearsal-checklist.md)
end-to-end. It warms the SQL warehouse, probes `/api/health`, cold-starts
Genie, verifies a Lakebase write, and walks the click path. If the
checklist returns all-green, skip to §4 (deploy-from-scratch is only for
a clean workspace).

If any step on the checklist fails, the three highest-likelihood
cold-start problems — and their one-line recovery commands — are:

### 1.1 Warehouse cold / `warehouse: down` in `/api/health`

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

`/api/health` → `"lakebase": "down"` or `/api/audit` returns a 500.

```bash
# Re-issue workspace identity; token auto-refresh on the deployed app,
# but a laptop-connected session sometimes needs a manual refresh.
databricks auth login --host "$DATABRICKS_HOST"
# Probe directly (port 5432, psql client):
psql "host=$LAKEBASE_HOST user=$LAKEBASE_USER dbname=$LAKEBASE_DATABASE_NAME sslmode=require" \
  -c "SELECT 1"
```

If the Lakebase instance itself is stopped, bounce it:

```bash
databricks bundle run mip_lakebase_migrate -t dev
```

### 1.3 Genie cold / first ask returns `source: "fallback"`

A cold Genie space takes 10–30 s on the first conversation. The safe
corpus in `backend/services/genie_answers.py` catches this invisibly,
but you probably want the real space for the audience pitch. Prime it:

```bash
curl -s -X POST "$MIP_APP_URL/api/genie/ask" \
  -H 'content-type: application/json' \
  -d '{"question":"How many borrowers across the 6-state footprint are currently in-the-money?"}' \
  | jq '{source, metric_value}'
# Once `source == "genie"`, cached and fast for the demo.
```

---

## 2. Mid-demo degraded

### 2.1 The DegradedBanner appeared at the top of the page

**What it means:** `/api/health` flipped to `status: "degraded"` — one
of warehouse / Lakebase / Genie is `down` or a breaker is `open`. The
frontend auto-retries; the banner clears itself when the breaker
closes (typically 30 s after recovery).

**What to say:** *"You'll see a banner — the warehouse is warming up.
That's the real-time health probe being honest, not a demo trick.
Back in a moment."*

**What to do:**
1. Don't click frantically; the retry + circuit-breaker logic handles
   it. Each breaker reopens after a 30 s cool-down and then sends a
   single probe; one success closes it.
2. If the banner persists > 60 s, narrate from the second-monitor API
   endpoints (see §2.3) and come back to the UI after.
3. Operator: in a side terminal, run `curl -s $MIP_APP_URL/api/health | jq`
   to see which dependency is down. That tells you whether to re-warm
   the warehouse (§1.1), refresh the Lakebase token (§1.2), or wait out
   Genie (§1.3).

### 2.2 A Genie answer returned `source: "fallback"`

The Genie circuit breaker is open; the safe corpus answered instead.
Audience sees a structured answer with a provenance chip. **Do not
re-ask the same question on-stage** — the breaker's cool-down is 30 s
and re-asking just re-opens it.

**What to say:** *"Our safe corpus just answered that — ten canonical
questions pinned to sample_questions.md, always available even if the
Genie space is cold-starting. The provenance chip is real."*

### 2.3 Whole UI is gone

Swap to the second-monitor terminal and pre-loaded tabs:

```bash
curl -s $MIP_APP_URL/api/leads?limit=5 | jq
curl -s $MIP_APP_URL/api/borrowers/B-48291 | jq
curl -s $MIP_APP_URL/api/segments | jq
curl -s $MIP_APP_URL/api/audit/events?limit=5 | jq '.[0]'
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
workspace has been rebuilt. Run commands in order; each is idempotent
but depends on its predecessor.

```bash
# 0. Prereqs
export DATABRICKS_CONFIG_PROFILE=DEFAULT   # or whichever profile
databricks bundle validate -t dev          # expect no errors

# 1. Deploy the bundle (SQL warehouse, app, jobs, pipelines, Genie space, Lakebase)
databricks bundle deploy -t dev

# 2. Seed silver.market_rates_weekly from FRED MORTGAGE30US
databricks bundle run mip_fred_rates_ingest -t dev

# 3. Silver lift from Cotality Delta Share (6-state filter; ~10M rows)
databricks bundle run mip_refresh_silver -t dev

# 4. Lakebase schema migration + seed campaigns
databricks bundle run mip_lakebase_migrate -t dev

# 5. Gold pipeline — computes borrower_360, lead_scores, evidence_events, lead_population
databricks bundle run mip_gold_pipeline -t dev

# 6. Verify: warehouse running, app live, all deps up
curl -s "$MIP_APP_URL/api/health" | jq
# Expect: status=ok, dependencies.{warehouse,lakebase,genie}=up, circuit_breakers all closed.

# 7. Smoke-run the real-UC golden path
./scripts/smoke_live.sh
```

After step 3 the silver tables are queryable but the app still returns
503 because the gold layer isn't populated. Step 5 is what makes the
UI render real borrowers.

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
databricks bundle run mip_gold_pipeline -t dev
```

For a booth slot where live refresh is impractical, **do not** silently
fall back to mock data — the no-silent-mock posture is load-bearing.
Instead: acknowledge on-stage that you're showing "yesterday's live
data" and continue.

---

## 7. Live-UC smoke check (`scripts/smoke_live.sh`)

Run this after any deploy to prove the self-contained promise: the
app can be reached, every dependency responds, and the five canonical
API calls return data.

```bash
./scripts/smoke_live.sh
# or to target a different host:
MIP_APP_URL="https://mip-dev.databricksapps.com" ./scripts/smoke_live.sh
```

The script boots a local uvicorn + vite if `MIP_APP_URL` is unset, waits
for `/api/health` to go green, then exercises `/api/portfolio/preview`,
`/api/leads`, `/api/borrowers/B-48291`, `/api/borrowers/B-48291/evidence`,
and `/api/genie/ask`. Any non-200 response or a `"down"` dependency
exits non-zero and prints the failing call.

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
every incident (post-mortem updates this doc), and before every DAIS
rehearsal.*

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

### 1.1 Warehouse resuming / `warehouse: down` in `/api/v1/health`

The 2X-Small serverless warehouse auto-stops after 10 min idle, and a
serverless resume typically takes 2–6 s. `/api/v1/health` reads the
warehouse lifecycle state: while it is `STARTING` the body reports
`"warehouse": "resuming"` with `status: "ok"`, and the app shows a calm
amber "Waking" pill with an elapsed timer (announced and titled "Waking
warehouse"), not the red Degraded state.
`STOPPED` reads as `up` (it starts on the next query). Only `"warehouse":
"down"` (a deleted warehouse, a failed state read whose `SELECT 1`
fallback also failed, or an open breaker) is an outage.

This idle stop is intentional. What keeps the warehouse warm is one policy,
`MIP_WAREHOUSE_KEEP_WARM` (`docs/observability.md` §7): `off` (the deployed
default) lets it stop; `activity` pings at most once per 240 s while someone
is actively using the app; `scheduled` runs the lead-page refresh-ahead loop
at `MIP_LEADS_WARM_INTERVAL_S`, which is ignored under the other two
policies. For a staffed walkthrough block where warm-route latency matters
more than idle cost, set `MIP_WAREHOUSE_KEEP_WARM=activity` (or `scheduled`
with a positive interval) in `.env.local` before `./scripts/deploy.sh -t dev`.

```bash
databricks warehouses start "$DATABRICKS_WAREHOUSE_ID"
# Expect: RUNNING within seconds (serverless).
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

If the Lakebase instance or App is stopped, first establish which install it
belongs to:

- **The live dev App** (`mip-app` in the anchor workspace). No governed path
  recovers it today. Its command of record, an operator
  `./scripts/deploy.sh -t dev`, is NO-GO on the live workspace
  (docs/deployment.md, "The live install is outside this bootstrap, and its
  governed deploy is NO-GO"). A read-only listing on 2026-10-27 found none
  of the seven reserved principals in the live workspace, the deploy never
  creates one, and a failed first governed run leaves the App stopped. Do
  not start that deploy during an incident, and do not dispatch the
  workflow below for the live App. Escalate to the owner:
  any live-App recovery, including a manual restart or payload redeploy, is
  an owner-only decision.
- **The CI staging install** (the forked-anchor `pr105-host` target,
  docs/deployment.md, "CI staging install"). Recover it only through the
  governed deployment workflow below. That workflow keeps the App stopped
  while it reconciles the exact Lakebase roles, runs the migration and grant
  postflight, and activates source only after those checks pass:

```bash
BRANCH="$(git branch --show-current)"
# Routine recovery of a signed last-good App leaves this false. Set it to true
# only for an approved legacy or failed pre-capture App expected to have no
# signed record; the workflow re-proves that absence before any rebase mutation.
REBASE_UNVERIFIED_APP=false
# The break-glass acknowledgement is required: without it the run ends in the
# refuse-unacknowledged job and deploys nothing.
RUN_URL="$(gh workflow run deploy-dev.yml --ref "$BRANCH" \
  -f acknowledge_single_maintainer_break_glass=true \
  -f skip_silver=true \
  -f skip_smoke=false \
  -f rebase_unverified_app="$REBASE_UNVERIFIED_APP" \
  -f remediate_foreign_catalog_bindings=false)"
# Fail closed unless this gh version returned exactly the created Actions URL.
if [[ "$RUN_URL" =~ ^https://github\.com/[^/]+/[^/]+/actions/runs/([0-9]+)$ ]]; then
  RUN_ID="${BASH_REMATCH[1]}"
else
  echo "workflow dispatch did not return an exact run URL" >&2
  exit 1
fi
gh run watch "$RUN_ID" --exit-status
```

The GitHub workflow above is dev-only. Since post-W5 it deploys only the CI
staging install on the forked-anchor `pr105-host` target in its own workspace,
so it can never deploy or recover the live dev App.
Do not transpose it to a customer or production workspace: production recovery requires its separately reviewed
environment contract and change-controlled `scripts/deploy.sh -t prod`
invocation. A failed pre-capture rebase remains unsigned and therefore requires
`REBASE_UNVERIFIED_APP=true` again on its next explicitly approved recovery;
once a signed last-good record exists, the exceptional path refuses to run.

Do not run `mip_lakebase_migrate` by itself after an App snapshot has ever been
activated. A standalone migration can race live requests and trip the Lakebase
circuit breaker; `scripts/deploy.sh` is the command of record for the required
stop → migrate/grant → activate ordering.

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
workspace has been rebuilt. A brand-new workspace has one credentials-only
bootstrap followed by the normal `./scripts/deploy.sh` (or `make deploy-dev`).
Bootstrap cannot touch an App or data resource; deploy creates the App and then
reconciles its operator grants. After that first install, deploy remains the one
idempotent command.

```bash
# 0. One-time credentials before the App exists. The GitHub sink must match origin.
# Export MIP_AI_GATEWAY_PROOF_SIGNING_KEY and its derived
# MIP_AI_GATEWAY_PROOF_VERIFY_KEY first. Bootstrap holds the outer signed App
# lease and the fixed global mip-oauth-credential-mutations lease. These
# standalone commands refuse tracked or untracked changes and require any
# configured MIP_DEPLOYMENT_SOURCE_GIT_SHA to equal HEAD.
GH_REPO=skyler-myers-db/mortgage-intelligence-platform
python tools/databricks/provision_m2m_oauth.py --pre-app-bootstrap \
  --identity-role agent_runtime --set-gh-secrets --gh-repo "$GH_REPO"
python tools/databricks/provision_m2m_oauth.py --pre-app-bootstrap \
  --identity-role normal --set-gh-secrets --gh-repo "$GH_REPO"
python tools/databricks/provision_m2m_oauth.py --pre-app-bootstrap \
  --identity-role operator2 --set-gh-secrets --gh-repo "$GH_REPO"
python tools/databricks/provision_m2m_oauth.py --pre-app-bootstrap \
  --identity-role admin --create-group --set-gh-secrets --gh-repo "$GH_REPO"
python tools/databricks/provision_m2m_oauth.py --pre-app-bootstrap \
  --identity-role release_probe --set-gh-secrets --gh-repo "$GH_REPO"
python tools/databricks/provision_m2m_oauth.py --pre-app-bootstrap \
  --identity-role verifier --set-gh-secrets --gh-repo "$GH_REPO"
python tools/databricks/provision_m2m_oauth.py --pre-app-bootstrap \
  --identity-role agent_proxy --set-gh-secrets --gh-repo "$GH_REPO"

# Pre-App bootstrap is creation-only. If a failed bootstrap was fully
# compensated, prove and remove the credentialless principal before repeating
# bootstrap; never add --rotate to a bootstrap command. After the App exists,
# rotate in normal mode with the role's exact --expected-application-id,
# --rotate, and --set-gh-secrets. Never reuse a client ID across roles.
# A signed intent is durable before provider create; observation and sink
# records are durable before the one-shot secret is returned or written.
# A durable oauth-credential-quarantine record blocks every App namespace until
# an administrator runs the exact signed-intent recovery under governance
# review. Never remove that record or wait for lease expiry merely to make
# bootstrap proceed.
python -m tools.databricks.oauth_credential_recovery_cli inspect \
  --intent-path "$INTENT_PATH"
# Confirm the displayed principal, authority identity, and provider API against
# the reviewed deployment inventory. Then export all four values and rerun the
# normal deploy; partial recovery configuration fails preflight.
export MIP_OAUTH_CREDENTIAL_RECOVERY_INTENT_PATH="$INTENT_PATH"
export MIP_OAUTH_CREDENTIAL_RECOVERY_PRINCIPAL_ID="<confirmed-scim-id>"
export MIP_OAUTH_CREDENTIAL_RECOVERY_AUTHORITY_IDENTITY="<confirmed-client-id>"
export MIP_OAUTH_CREDENTIAL_RECOVERY_PROVIDER_API="<confirmed-provider-api>"
./scripts/deploy.sh -t dev
# A zero-delta unobserved intent remains quarantined because a bounded read
# cannot prove that a delayed provider create will never commit. The current
# provider exposes no durable non-commit evidence. Recovery also refuses
# multiple deltas, missing prior credentials, any delete exception, or any
# mismatch. It never reconstructs or re-delivers a one-shot secret.
# If the global mutation lease exists but no intent was committed, inspect and
# explicitly recover only that signed orphan coordinate. The recovery
# subcommand requires a live signed outer deployment lease, so do not invoke it
# directly after the failed process exits. Export both reviewed coordinates and
# rerun deploy; deploy acquires the new outer lease before recovering the
# orphan. Do not combine this with the four intent-recovery variables above.
python -m tools.databricks.oauth_credential_recovery_cli inspect-orphan-lease
export MIP_OAUTH_CREDENTIAL_ORPHAN_LEASE_ID="<reviewed-lease-id>"
export MIP_OAUTH_CREDENTIAL_ORPHAN_RECOVERY_ROOT_LEASE_ID="<reviewed-recovery-root-id>"
./scripts/deploy.sh -t dev

# The outer App-lease recovery variables remain unchanged under lease protocol
# v5. `recovery-root` now authenticates the canonical signed lease head and the
# exact holder checkpoint named by its digest; it does not enumerate thousands
# of heartbeat generations. Treat a missing/corrupt/oversized checkpoint,
# app/chain mismatch, signature failure, or holder-head divergence as a hard
# governance stop. Do not delete historical `.next` or `.recovery-index` files,
# and do not edit `.head` by hand: it is only a read accelerator. Recovery reads
# the selected current checkpoint and its exact immediate predecessor when the
# signed checkpoint names one.
python -m tools.databricks.app_deployment_lease recovery-root \
  --app-name "$MIP_APP_NAME" \
  --out-env /tmp/mip-app-deployment-recovery.env

# Exceptional missing/corrupt `.head` or unsigned `.protocol-v5` locator
# recovery. Run as the exact signed holder. This command performs a full
# authenticated scan (bounded by MAX_CANONICAL_GENERATIONS), re-proves the
# protected-root ACL, re-anchors a missing/corrupt locator to the canonical
# signed head, replaces `.head`, and exact-postflights both locators. It cannot
# repair a corrupt signed generation/checkpoint.
python -m tools.databricks.app_deployment_lease repair-head \
  --app-name "$MIP_APP_NAME"

# 1. Separately create/store DATABRICKS_ACCOUNT_ID plus the distinct
#    DATABRICKS_ACCOUNT_CLIENT_ID/SECRET account-SCIM pair.
#    A first install with an approved group owner needs account-admin authority
#    because the new target App cannot be delegated in advance. Afterward,
#    downscope to Service Principal Manager over every app-facing M2M and the
#    now-existing target App.
#    Also configure distinct MIP_AI_GATEWAY_PROOF_SIGNING_KEY and
#    MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY values and the runtime
#    HMAC/masking secrets. These are not generated by the workspace-SP tool.

# 2. Populate .env.local/GitHub with DATABRICKS_HOST and warehouse/data inputs.
#    If GENIE_SPACE_ID is blank, deploy.sh provisions it before bundle deploy.

# 3. Create/deploy the App; post-bundle reconciliation grants CAN_USE to the
#    exact normal, operator2, and admin client IDs without rotating credentials.
./scripts/deploy.sh
# or equivalently:
make deploy-dev
```

The normal deploy creates and verifies only the empty managed
`<MIP_DEFAULT_CATALOG>.silver` pipeline namespace before bundle apply. This
breaks the first-install pipeline dependency cycle; the bundle's dedicated
`mip_init_catalog_schemas` job still owns the full governed schema/table DDL and
runs before schema, table, and reviewed-function grants. It creates the empty
gold/ref contracts and the three Growth Agent UC functions even when a refresh
is skipped. Lakebase Sync access is granted separately only after the
synced-table API has created and proven the configured schema and exact reviewed
table allowlist. The App receives `USE SCHEMA` plus table-scoped `SELECT`, never
schema-wide table reads. Namespace readback must
match the current workspace metastore, and every owner resolves to one immutable
SCIM principal while the
target App and all app-facing M2M identities are excluded, including through
approved owner groups. Catalog and schema identifiers are lowercase unquoted
UC names of at most 255 characters.

That single invocation executes:

1. `npm --prefix frontend run build` — the bundle sync uploads
   `frontend/dist/**` so the FastAPI runtime can serve the SPA.
2. `tools/databricks/bundle_env.py validate -t dev`
   (env-aware wrapper around `databricks bundle validate`; it maps
   `.env.local` to `BUNDLE_VAR_sql_warehouse_id` / `BUNDLE_VAR_genie_space_id`).
3. `tools/databricks/bundle_env.py plan -t dev` — read-only direct
   deployment plan. Review this output before a real app/customer deploy.
4. `tools/databricks/bundle_env.py deploy -t dev --select ...` — deploys
   every non-App bundle resource (warehouse, jobs, pipelines, Lakebase,
   experiment, and dashboards) while explicitly excluding `apps.mip_app`.
5. `tools.databricks.app_resource_bindings` resolves the now-concrete IDs and
   applies only the App resource bindings. A first install creates the App with
   `databricks apps create --no-compute`; an upgrade uses `apps update`. The
   postflight requires exact bindings and proves no source deployment or
   compute transition occurred. First install then binds that stopped App into
   DAB state. Failed first installs unbind and delete only the newly created,
   unsigned App so a normal retry starts from authoritative absence.
6. `databricks bundle run mip_lakebase_migrate -t dev` — Postgres
   `schema.sql` + `seed_campaigns.sql` and the exact App/verifier role-grant
   postflight. This runs only after the live database binding has provisioned
   the App role and before any App source activation.
7. The script submits its generated, governed App deployment payload to promote
   the uploaded bundle source only after Lakebase migration and treatment
   constraints have converged. Operators must not replace this phase with a bare
   App deployment command.
8. `databricks bundle run mip_fred_rates_ingest -t dev` — FRED
   MORTGAGE30US into `silver.market_rates_weekly`.
9. `databricks bundle run mip_refresh_silver -t dev` — Cotality Delta
   Share → `mip.silver.*`; geography coverage is discovered from source
   rows with non-null states.
10. `databricks bundle run mip_refresh_scores -t dev` — CTAS chain:
   `property_owner_bridge` → `evidence_events` → `borrower_360` →
   `lead_scores` → `lead_population` → `segment_population` →
   `lockin_cohort` → `borrower_dossier` → **`refresh_semantics_views`**
   (the three `mip.semantics.*` metric views Genie binds to).
11. The deploy script runs the warehouse lifecycle sync and records the daily
   funnel snapshot. The lifecycle table is sparse; borrowers with no event
   resolve to `pending` / `none` through consumer `LEFT JOIN` + `COALESCE`.
   After deploy, accepted approval/rejection hooks apply a cheap changed-row
   `MERGE` through FastAPI `BackgroundTasks` and skip the population-wide
   snapshot. If that warehouse call fails, the backend submits
   `mip_sync_lifecycle_state`; Databricks queues the run and retries the sync
   task twice, so failure state is durable and operator-visible. A fallback
   schedule is defined but ships **PAUSED in every target**. Only
   unpause it for a customer-approved production cadence; otherwise
   use the Admin Data operations button when a refresh is needed.
   Deploy and durable repair runs remove only legacy untouched `pending` /
   `none` rows before the sparse MERGE. Per-event hooks skip that cleanup, so
   user actions never scan or rewrite the population-wide table.
12. `databricks bundle run mip_growth_agent_monitor_scheduler -t dev` —
   optional draft-only Growth Agent automation. It calls the deployed app's
   admin-gated `/api/v1/growth-agent/monitors/run-due-all` endpoint, refreshes
   due saved watchlists for their original owners, and creates Slack/Teams
   review drafts. It never sends messages or connector writes. Its weekday
   schedule ships **PAUSED in every target**; run it manually or unpause only
   after the customer approves a cadence. The Databricks job run identity must
   be admitted by the app's admin policy (`MIP_ADMIN_IDENTITIES` or
   `MIP_ADMIN_EMAILS`) before the job is unpaused; otherwise the
   all-actor endpoint fails closed with `403 {"detail":"forbidden"}`.
13. `python tools/databricks/provision_genie_space.py` — reads
   `genie/mortgage_lead_intelligence_space.yml`, creates or updates
   the Genie Space, binds trusted assets, writes `genie/space_id.txt`.
14. `./scripts/smoke_live.sh` — verify the app and all four deps up.

Flags on `scripts/deploy.sh` for partial re-runs:

| Flag | Effect |
|---|---|
| `--dry-run` | print the plan, make no changes |
| `--skip-silver` | skip steps 8–9 (fast path when silver is already fresh) |
| `--skip-smoke` | skip step 14 |
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
4. **Sync workflow state** uses the same sparse warehouse `MERGE` when
   approvals or outreach state need repair, then refreshes the daily funnel
   snapshot. The bundle job is the durable retry path when the app's warehouse
   attempt fails.

Buttons 1 to 3 run the deployed jobs. Press them only where the deployed jobs
match the current bundle: on an install whose governed deploy is NO-GO, never
run them; use "Interim refresh (governed deploy NO-GO)" below.

Each operation is admin-only and writes a Lakebase audit row before compute.
Job-backed operations refuse duplicate active runs and show the latest
Databricks run state. Lifecycle sync executes the sparse warehouse MERGE
synchronously; when an app-hook MERGE fails, its queued repair job provides
the durable run state. If the audit ledger is unavailable, Admin Data
operations do not launch compute.
The bundle-defined FRED, lifecycle fallback, and Growth Agent monitor
schedules deploy **paused by default** in dev and prod so
intermittent development and demo workspaces do not burn recurring
warehouse/Lakebase compute. If a customer later wants scheduled refreshes or
scheduled watchlist-draft creation, unpause the schedule explicitly in that
customer workspace and document the approved cadence. The Growth Agent
scheduler is draft-only: it creates Slack/Teams review drafts and never sends
messages.

Run **Refresh market rates** only together with **Rebuild scoring snapshot**.
A FRED run on its own re-creates the audit C1 stale-rate skew (gold keeps
scoring against the old rate) until gold runs again.

**No manual UI step is required after the one-time credential/account
prerequisites are satisfied.** The previous runbook called for
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

### Weekly scheduled refresh (dev live install)

The one exception to the paused default is `mip_scheduled_refresh` on the dev
target's **live** install. It is one job that runs the existing jobs in the
deploy order, each through `run_job_task` so every child keeps its single
definition and its own `PAUSED` schedule:

1. `mip_fred_rates_ingest` (FRED MORTGAGE30US into `silver.market_rates_weekly`);
2. `mip_refresh_scores` (the gold CTAS chain);
3. `mip_sync_lifecycle_state` (sparse lifecycle MERGE plus the daily funnel snapshot);
4. `mip_kpi_snapshot` (one `kpi_snapshots` row per day);
5. after gold, a SELECT-only outcome gate
   (`sql/transformations/assert_scheduled_refresh_outcome.sql`): gold scored at
   silver's `is_latest` rate, and that rate is a FRED print within 14 days.

It runs Fridays 06:00 America/Chicago, the morning after FRED prints
MORTGAGE30US. Avoid demos between 06:00 and 06:30 CT on Fridays. Silver
(`mip_refresh_silver`) is not in the chain, because the Cotality eval share is
static. `mip_init_catalog_schemas`, the only job that publishes the three
reviewed Growth Agent functions, is never reachable from it.

How it is switched on:

- `databricks.yml` declares `scheduled_refresh_pause_status` with the default
  `PAUSED`, and no target sets it.
- The `scripts/deploy.sh` preflight exports `UNPAUSED` only for `-t dev` on the
  default bundle root (empty `install_suffix`). `MIP_SCHEDULED_REFRESH=off`
  (shell or `.env.local`) keeps it paused. The CI staging install
  (`deploy-dev.yml`, leaf `dev-ci`) is always paused. `-t prod` is always
  paused and refuses `MIP_SCHEDULED_REFRESH=on`: a prod cadence is a customer
  decision that needs a reviewed change. `tools/databricks/bundle_env.py`
  refuses an `UNPAUSED` export for any other install.
- A failed run emails the deploying user, on the live install only.
  `bundle_env.py` writes that recipient into
  `.databricks/bundle/dev/variable-overrides.json`, because the CLI refuses a
  `BUNDLE_VAR_` export for a list variable. The `dev-ci` leaf and prod get no
  recipient: the CI deployer is a service principal whose user name is an
  application id, not an email address. The live install and the `dev-ci`
  leaf share that file in one checkout (both are target `dev`), and
  `bundle_env.py` rewrites it before every CLI call, so run one install's
  bundle commands at a time per checkout. CI deploys `dev-ci` from its own
  runner.

**How it lands.** The schedule reaches a workspace only through the bundle
apply inside a full governed `./scripts/deploy.sh -t dev` run. A Jobs-UI
unpause is reverted by the next deploy, and there is no other delivery path.
That run is GO only when all of these hold:

- a clean checkout exactly at `origin/main`, the operator's own, with its own
  `.env.local`;
- the per-run M2M identities (the `DATABRICKS_*_CLIENT_*` and `ACCOUNT_*`
  values) and both signing keys exported in the operator's shell (see
  `docs/deployment.md`, "Per-run M2M identities");
- no foreign catalog that grants `account users` (the step-4 foreign-UC
  audit), each one resolved with its catalog owner;
- no unrelated serving-endpoint grant on the App service principal;
- no Genie space that the App service principal reaches through a group grant,
  such as workspace `users` on another team's space, granted on the space or
  inherited from `/Workspace/Shared`. Step 0's read-only pre-stop probe lists
  every such grant (also for the agent-runtime and verifier service principals)
  and refuses before the App stop. Resolve each one by its source with the
  space owner's consent (`docs/deployment.md`, "Pre-stop probes"); never exempt
  the group in the audits;
- a smoke script whose geo-drill probe checks the shipped state → ZIP drill,
  not the retired county grain (`scripts/smoke_live.sh` does since the
  post-W5 live geo gate);
- a configured `MIP_COTALITY_ID_MASK_SECRET` equal to the stored
  `cotality-id-mask-v1` (step 0 refuses a mismatch before the App stop), the
  owner's read of the report-only `genie-action-current` note, and the
  `SALESFORCE_*` values supplied or the connector accepted as disabled: an
  unset or placeholder input keeps the stored `salesforce-*` key but leaves it
  unbound (`docs/deployment.md`, the `provision_runtime_secrets.py` notes);
- a deploy interpreter whose packages equal the `requirements.txt` pins: the
  checkout's `.venv/bin/python`, or a venv built outside the checkout and
  selected with `MIP_DEPLOY_PYTHON`. Step 0 refuses pin drift with exit 2
  (`docs/deployment.md`, "Deploy interpreter");
- the owner's decision on a human-created Supervisor that holds the canonical
  name: the first agentic cutover retires it and deletes its serving endpoint,
  which cannot be undone (`docs/deployment.md`, the first migration from a
  human-owned Supervisor). Accept that by exporting
  `MIP_RETIRE_HUMAN_SUPERVISOR=<supervisor id>` in the operator shell for the
  run, or rename it off the canonical name first. Without either, step 0's
  pre-stop probe refuses before the App stop;
- an accepted App outage of 3.5 to 6 hours, with quarantine.

The first signed install over an existing unsigned App also exports
`MIP_REBASE_UNVERIFIED_APP=1` once, and the flag comes off after the first
signed capture (`docs/deployment.md`). Step 0a stops and quarantines
the live App before the bundle apply, so a later failure without a signed
last-good record leaves the App stopped. On 2026-10-27 that run was NO-GO on
the dev workspace. Until a GO run applies the job, the workspace keeps its
older job definitions and nothing refreshes dev on a schedule. `checked_at`
(gold's refresh anchor, the newest `mip.ref.refresh_run_state` row) moves only
when gold is rebuilt: by a full governed `deploy.sh` run, or by an
operator-run interim refresh of FRED and gold that leaves out every
`publish_fn_*` task. One such interim refresh ran on 2026-10-27 and moved it to
06:48 UTC that day. Between rebuilds it ages, and `tools/verify_live.py` flags
it once it is more than 3 days old: its default threshold fails closed at 3
days and widens to 8 (the 7-day cadence plus 1) only when it reads
`mip_scheduled_refresh` back `UNPAUSED` on the weekly cron. That flag is
correct.

Never unpause, `run-now` or Admin-trigger a deployed child job whose definition
predates the current bundle. For example, a gold job that still carries
`publish_fn_*` tasks republishes the reviewed functions and drops their
`EXECUTE` grants.

**Risks and residuals:**

- **Static-share decay, not freshness.** The Cotality eval share has not
  changed since 2026-06-12. Each weekly run re-anchors gold's time-relative
  windows (the 12-month listing window, the 365-day evidence windows, loan age)
  on that frozen snapshot, so Listed-for-Sale and the evidence windows drain a
  little every week. Measured on dev on 2026-10-26: about 4% fewer
  listed-for-sale borrowers at the first Friday run, about 12% fewer by late
  January 2027, and none by about June 2027 unless the share is re-delivered.
  The new FRED rate is real freshness; this drain is decay.
- **FRED gap (closed 2026-10-27).** On 2026-10-26 the dev `is_latest`
  MORTGAGE30US row was the week of 2026-08-03, so 11 weekly prints (13 August
  to 22 October 2026) were missing. The 2026-10-27 interim refresh loaded all
  11: `is_latest` is now the week of 2026-10-19 (7.32%), and gold scores at
  that rate. In-the-Money, rate spreads, segment counts and the rate window
  moved with it. Until a GO deploy lands the schedule, each later Thursday
  print reaches dev only through another FRED plus gold refresh.
- **Treatment-table DDL race.** The FRED job's `init_ddl` task runs
  `001_catalogs_schemas.sql`, which re-asserts
  `ALTER TABLE <catalog>.audit.campaign_treatment_snapshot SET TBLPROPERTIES`
  (a new Delta commit). `scripts/deploy.sh` runs that DDL only after it
  quiesces App treatment writes. The App pins a treatment materialization with
  `DESCRIBE HISTORY ... LIMIT 1` right after its append. A scheduled run that
  races a materialization can pin the ALTER's version, or fail the append with
  a metadata conflict. The Admin **Refresh market rates** button already has
  this exposure; the schedule makes it weekly and unattended. Do not
  materialize campaign treatments around Friday 06:00 CT, and re-check any
  materialization that ran then. Moving the treatment DDL out of FRED's
  `init_ddl` is an open follow-up.
- **Silent re-pause.** Any bundle apply outside `scripts/deploy.sh` resolves the
  variable to its `PAUSED` default and re-pauses the schedule without an error.
  The repo does not support such an apply. Dev then goes stale without an
  error; `tools/verify_live.py` reads the job back `PAUSED`, falls back to its
  fail-closed 3-day threshold, and flags `checked_at` 3 days after the last
  rebuild. Re-run the governed deploy to restore it.
- **Collision with a deploy.** The orchestrator and all four children declare
  `max_concurrent_runs: 1` with queueing, so a deploy that holds one at Friday
  06:00 delays the chain instead of skipping it: the scheduled child run waits
  in the queue for the deploy's run of the same job. Only a hold that pushes
  the whole chain past the orchestrator's 5400 s (90 min) timeout fails the run
  and sends the failure email; re-run the orchestrator after the deploy.
- **Cross-job interleaving.** The queue serializes runs of the same job only.
  A governed deploy that overlaps a running chain interleaves with it across
  jobs: the deploy's step-6 `mip_refresh_silver` full refresh while scheduled
  gold reads silver, its step-9 `tools.sync_lifecycle_warehouse` MERGE beside
  the scheduled `mip_sync_lifecycle_state`, and its step-4 apply replacing
  child job definitions mid-chain. Do not start a governed deploy (or the
  interim refresh below) within about an hour of Friday 06:00 CT, and check
  that no `mip_scheduled_refresh` run is active
  (`databricks jobs list-runs --job-id <id> --active-only`) before starting.
- **Run-as identity.** Scheduled runs execute as the dev run-as user (the
  deploying user). If that identity loses access, runs fail and the email
  fires. Deploy the live install as a user: with the schedule `UNPAUSED`, the
  step 9c postflight refuses a failure recipient that is not an email address
  (a service-principal deployer's application id).
  That refusal comes after the App promotion, and nothing predicts it yet: it
  needs a live read of the bound deployer's user name. The preflight's
  scheduled-refresh block stays pure so it also runs in a dry run, and step
  0's read-only pre-stop probe (`tools/databricks/deploy_prestop_probes.py`),
  where that read belongs, checks Supervisor retirement and Genie group grants
  but not the deployer's user name. Until it does, check the deploying
  identity is a user before a GO run.
- **Piecewise-render trap.** The scheduled gold run reads the rendered SQL in
  the bundle's `files/sql/_rendered/` tree. A piecewise upload of a render made
  without `MIP_ENABLE_DEMO_FIRST_PARTY_FEEDS=1` (the stand-alone
  `tools/render_sql.py` default is off) empties the Summit demo first-party
  feeds at the next Friday run, unattended. Render every piecewise upload for
  dev with `MIP_ENABLE_DEMO_FIRST_PARTY_FEEDS=1`; `deploy.sh` defaults it on
  for `-t dev`. `tools/verify_live.py` detects the mistake as first-party
  sources with `status=not_configured expected live/demo_synthetic`.
- **Shared catalog.** The dev schedule rebuilds gold in the dev install's
  catalog every week. If a prod install ever shares catalog `mip` in the same
  workspace, it would rebuild prod's gold too. Give prod its own catalog
  (for example `mip_prod`, see `docs/runbook-multi-catalog.md`), or set
  `MIP_SCHEDULED_REFRESH=off` for dev.
- **Postflight failure (step 9c).** Step 9c reads the stored job back after the
  step-4 apply, the App promotion, the data refresh and the KPI snapshot,
  while the deploy still holds the App's user access quarantined. The SDK
  retries transient connection and timeout errors, so treat a failure as real:
  the stored job is not what the deploy intended, or it cannot be read, and the
  deploy stops there. If it reports `UNPAUSED` where `PAUSED` was expected,
  pause `mip_scheduled_refresh` in the Jobs UI first (the next governed deploy
  re-applies the intended state). Then fix the cause and re-run the governed
  deploy, with `MIP_SCHEDULED_REFRESH=off` if the schedule itself is the
  problem. The App follows the deploy's failure handling
  (`docs/deployment.md`); without a signed last-good record it stays stopped.
- **Numbers move weekly**, as the "follow current refreshed coverage" posture
  intends.
- **Cost.** About $1.35 to $1.9 per run at list price (warehouse time for
  gold plus serverless jobs), about $6 to $8 a month.

**Verify after a GO deploy** (read-only):

- `databricks jobs list --name "[dev <user>] mip_scheduled_refresh"` shows the
  schedule `UNPAUSED`.
- `SELECT MAX(refresh_at) FROM mip.ref.refresh_run_state` returns the deploy
  time, then each Friday's run time.
- `tools/verify_live.py` shows no `checked_at is stale` flag, and its report
  header shows the 8-day threshold (the 7-day cadence plus 1) with the basis
  `mip_scheduled_refresh job <id> is UNPAUSED on the weekly cron`. A header
  that still reads `fail-closed` (3 days) means the job was not read back
  `UNPAUSED` on that cron, or that the workspace holds more than one
  `mip_scheduled_refresh` (another deployer's dev install, say), which leaves
  this install's unproven.

### Interim refresh (governed deploy NO-GO)

Use this only on an install where the governed `./scripts/deploy.sh -t dev`
is NO-GO (the GO list above) and dev data must move anyway. It rebuilds FRED
and gold from the current `main` render with the App left running. It changes
data and, only when step 1 finds a FRED source file that differs from main's
render, that file under the bundle root; never a job definition, grant, ACL,
secret, pipeline, dashboard, Genie space, App or Lakebase schema. It ran once,
on 2026-10-27 (FRED 6.69% to 7.32%, In-the-Money 74,335 to 33,585, zero grant
differences, no upload). Step 4's verifier was substituted on 2026-10-27: that
install had no agent-runtime or agent-proxy principal and no direct grants
left on the reviewed functions, so the gate was the zero-difference grant diff
plus the App SP's effective `EXECUTE`, as step 4 now prescribes.

**Never `run-now`, unpause or Admin-trigger the deployed gold job
(`mip_refresh_scores`) or the deployed lifecycle job (`mip_sync_lifecycle_state`)
on an install whose job definitions predate the current bundle.** A stale gold
job that still carries `publish_fn_*` tasks runs `CREATE OR REPLACE FUNCTION`
on the three reviewed Growth Agent functions (`fn_build_cohort`,
`fn_segment_counts`, `fn_lead_queue_url`), which drops the App, agent-runtime
and agent-proxy `EXECUTE` grants. A stale lifecycle job still runs the retired
`seed_default_state`. On such an install, press no Admin **Data operations**
button that runs a deployed job either: **Rebuild scoring snapshot** runs the
deployed gold job, **Refresh market rates** runs the deployed FRED job without
step 1's parity proof, and **Refresh source features** runs the deployed
silver job. Rebuild gold with steps 2 to 4 below instead.

**Sync workflow state** runs the warehouse lifecycle MERGE inside the App (the
same work as step 6), not the deployed job. But an approve, reject or revoke
whose warehouse MERGE fails falls back to run-now of the deployed
`mip_sync_lifecycle_state` (`backend/services/job_trigger.py`,
`trigger_lifecycle_sync`), and `MIP_LIFECYCLE_SYNC_MODE=job` sends every one of
them there. On a stale install, keep that variable unset, avoid approvals while
the warehouse is degraded, and check the App log for `lifecycle_sync_error`
followed by `job_trigger_fired`.

Before you start:

- Take a `git archive` of `origin/main` into a scratch directory, never the
  operator's checkout.
- Have the install's CLI profile, its SQL warehouse id and its bundle root
  (`/Workspace/Users/<deployer>/.bundle/mortgage-intelligence-platform/dev/files`).
- Render in the scratch tree with
  `MIP_ENABLE_DEMO_FIRST_PARTY_FEEDS=1 python tools/render_sql.py`. The
  stand-alone default is off and would empty the Summit demo feeds (the
  piecewise-render trap above).
- Do not start within about an hour of Friday 06:00 CT once the schedule is
  live (cross-job interleaving above).

Steps:

1. **FRED.** First prove that the deployed `mip_fred_rates_ingest` equals
   main's: the same four tasks (`init_ddl`, `init_silver_ddl`, `seed_if_empty`,
   `refresh_from_fred`), dependencies and source paths (`databricks jobs get
   <id>` against `databricks.yml`). Also prove every file it reads under the
   bundle root is byte-equal to the scratch render (`databricks workspace
   export` round trip, then `cmp`). Upload the render of any file that
   differs, then round-trip it again. Its `init_ddl` re-ALTERs
   `<catalog>.audit.campaign_treatment_snapshot` (the treatment-table DDL race
   above), so read `DESCRIBE HISTORY` on that table first and confirm no
   treatment materialization is in flight. Then `databricks jobs run-now <id>`
   and wait for `SUCCESS`. Prove silver's `is_latest` MORTGAGE30US row moved to
   the newest Thursday print.
2. **Grant snapshot, before.** For every object step 3 re-creates (functions,
   tables, views, metric views), save `SHOW GRANTS ON <object>`. For an
   `init_fn_*` function or a view with an object-level grant, compare the live
   body (`DESCRIBE FUNCTION EXTENDED`, the view text) with the render. Skip the
   statement when it is unchanged. Stop when it changed, because a re-create
   drops the grant. Skip unchanged views and metric views.
3. **Gold.** Execute main's `mip_refresh_scores` SQL tasks from the scratch
   render (38 tasks in `databricks.yml`) in a topological order of their
   `depends_on`, one statement per Statements API call
   (`POST /api/2.0/sql/statements` on the warehouse). Never include a
   `publish_fn_*` task or a `fn_build_cohort`, `fn_segment_counts` or
   `fn_lead_queue_url` file. The Statements API runs each statement in its own
   session, and `transformations/demo_first_party_feeds.sql`
   (`seed_demo_first_party_feeds`) opens with a `TEMP VIEW` that its CTAS read,
   so split the run in three:
   - **A**, every statement before that task, through the Statements API: the
     `init_fn_*` statements step 2 did not skip (eleven at most),
     `003_gold_tables.sql`, `capture_refresh_timestamp` (it records this run's
     `refresh_at`) and `ctas_property_owner_bridge`.
   - **The demo feeds** as ONE `sql_task` run on the bundle root's render of
     that file, which keeps one session:
     `databricks jobs submit --no-wait --json '{"run_name": "interim-refresh-demo-feeds", "idempotency_token": "<unique per refresh>", "tasks": [{"task_key": "seed_demo_first_party_feeds", "sql_task": {"warehouse_id": "<warehouse>", "file": {"path": "<bundle root>/sql/_rendered/transformations/demo_first_party_feeds.sql", "source": "WORKSPACE"}}}]}'`.
     Poll `databricks jobs get-run <run_id>` until it terminates, and require
     `SUCCESS`. The idempotency token makes a retried submit return the same
     run, never a second one. The bundle root's file must equal the scratch
     render byte for byte; when it differs, stop, because this procedure
     uploads only step 1's FRED files.
   - **B**, every statement after that task, through the Statements API.

   Before executing, prove that A, the demo file and B together equal the
   planned statement list in order (compare statement hashes), with the plan
   computed after step 2's skips. On a failure, stop and resume from the
   failing statement; never improvise one.
4. **Grant snapshot, after, and a strict diff.** Read `SHOW GRANTS` again on
   every touched object and diff it against step 2. Any added, removed or
   changed row fails. So does an object whose effective `EXECUTE` cannot be
   read on either side: two unreadable snapshots compare equal, and once that
   passed vacuously. Then run
   `python -m tools.databricks.verify_reviewed_function_execute_grants` with
   the App, agent-runtime and agent-proxy application ids, as
   `scripts/lib/deploy_step_lakebase_and_uc_grants.sh` does. It requires one
   direct `EXECUTE` per consumer. If the reviewed functions already lack
   direct `EXECUTE` grants (a stale `publish_fn_*` run dropped them) or the
   agent-runtime or agent-proxy principal does not exist, the verifier cannot
   run or fails by design. Record its output, never hand-grant, and treat the
   strict before/after diff above plus the App SP's effective `EXECUTE` on
   the three reviewed functions as the gate. Only the governed deploy restores
   the direct grants.
5. **Rate alignment.** Run `python -m tools.databricks.verify_market_rate_alignment
   --warehouse-id <warehouse> --catalog <catalog>`, as deploy step 8 does. Gold
   must score at silver's `is_latest` rate.
6. **Lifecycle.** Run `python -m tools.sync_lifecycle_warehouse --catalog
   <catalog> --lakebase-instance <instance> --lakebase-database <database>`, as
   deploy step 9 does. If it needs a credential you do not hold, record the
   step as skipped. Never run-now the deployed lifecycle job instead.
7. **KPI.** Run-now the deployed `mip_kpi_snapshot` only after proving that its
   one task (`snapshot_headline_kpis`) and its source equal main's. Otherwise
   skip it and record why.

**Post-refresh proofs** (read-only, GET requests only):

- `SELECT MAX(refresh_at) FROM <catalog>.ref.refresh_run_state` is this run's
  step-3 time, and `gold.source_readiness` shows FRED and the core sources
  `live`.
- Probe each App endpoint twice and read the second answer. The gold read
  cache is stale-while-revalidate, so the first hit per hot key can return the
  pre-refresh numbers while it refreshes in the background.
- `/api/v1/health` can stay `degraded` with the Genie breaker `half_open`
  after the refresh. The health ping bypasses the breaker, so only a real
  Genie turn or an App restart closes it. That is not a refresh failure.
- The state to ZIP drill reconciles for the drilled states:
  `sum(zip.addressable_borrowers) == state.addressable - state.zip_unassigned_count`
  (`GET /api/v1/geo/state-rollups` and `/api/v1/geo/zip-rollups?state=XX`).
- Record before and after numbers for In-the-Money, rate spread,
  Listed-for-Sale and the segment counts, so the moved numbers are explained.
  A rate shift moves In-the-Money, and the static share drains Listed-for-Sale
  a little at each refresh.

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

Re-run them only where the deployed jobs match the current bundle: on an
install whose governed deploy is NO-GO, never run them; use "Interim refresh
(governed deploy NO-GO)" in §4.

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

The geo step drills the map the way the UI does, state -> ZIP: it takes
the largest populated state from `/geo/state-rollups`, requires a
populated, in-state, 5-digit-ZIP layer from `/geo/zip-rollups?state=`,
and requires the drill to reconcile exactly
(`sum(zip.addressable_borrowers) == state.addressable -
state.zip_unassigned_count`). `/geo/county-rollups` is the retired county
grain: it must answer 200 for the drilled state and never ship a row
without a 5-digit `fips_5`; its empty list is expected (see
`docs/testing.md`, "Live endpoint verifier").

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
# Warehouse: operator stops the SQL warehouse; a stop is not an outage, so
# the script checks health stays ok and a read resumes it (delivery-01).
# The degraded warehouse path is proved by --target warehouse-sim.
./tools/kill_drill/run_drill.sh --target warehouse

# Lakebase: operator stops the database instance (or rotates password)
./tools/kill_drill/run_drill.sh --target lakebase

# Genie: simulated -- script forks a private backend with a bogus space id
./tools/kill_drill/run_drill.sh --target genie

# Token: simulated -- script unsets DATABRICKS_TOKEN in a subshell
./tools/kill_drill/run_drill.sh --target token

# While a degrading drill is in flight (not a warehouse stop), verify the UI:
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
evidence above is only as fresh as the last warehouse refresh. Run the
commands below only where the deployed jobs match the current bundle: on an
install whose governed deploy is NO-GO, never run them; use "Interim refresh
(governed deploy NO-GO)" in §4.

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

## 11. Admin and auditor RBAC compatibility headers for local dev

The `/api/v1/admin/*` endpoints are gated by
[`backend/services/rbac.py`](../backend/services/rbac.py). Sandbox and
production admission requires an exact resolved actor in
`MIP_ADMIN_IDENTITIES` or `MIP_ADMIN_EMAILS`. The deploy workflow derives the
automation identities from the dedicated admin and candidate release-probe
service-principal client IDs. The release probe has no persistent App
`CAN_USE`; it is reachable only during the signed-capture gate for an explicit
unsigned-App rebase.

The audit ledger (`/api/v1/audit/events`, `/events/page`, `/rollups`,
`/facets`, `/count`, and another actor's `/audit/receipt/{id}`) also admits
the read-only **Auditor** role: an exact identity in `MIP_AUDITOR_EMAILS` or
`MIP_AUDITOR_IDENTITIES` (comma-separated, case-insensitive). Empty means no
auditors; there is no deployed group path. An auditor gets no administrator
or approver power. Every served ledger read, by an admin or an auditor, is
itself recorded as one `VIEW_AUDIT_LEDGER` row. Both variables ride the
deploy payload from the environment or `.env.local`; a bare UI deploy drops
them, which fails closed to no auditors.

`X-Forwarded-Groups` is not a documented Databricks Apps identity-header
contract. The configured group (default `mip-admin`), the `admins` fallback
and `mip-auditor` therefore work only as local/test compatibility helpers;
they are never an authoritative deployed authorization path.

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

A read-only auditor's ledger page, locally (the read writes one
`VIEW_AUDIT_LEDGER` row):

```bash
curl -s -H "X-Forwarded-Groups: mip-auditor" \
     -H "X-Forwarded-Email: auditor@example.com" \
     "http://localhost:8000/api/v1/audit/events/page?limit=25" | jq .
```

Missing header returns `403 {"detail": "forbidden"}` — that exact body
string is what the frontend's admin 403 banner keys off of.

**Refusal reports and consented questions (D-audit-reads-d).**
Administrators and auditors open them on `/audit-ledger` with "Show
refusal reports" (nothing loads with the page): the list
(`GET /api/v1/audit/refusal-reports`) writes one `VIEW_AUDIT_LEDGER` row per
served page and never carries question text; "Show question"
(`GET /api/v1/audit/refusal-reports/{report_id}/question`) writes a
fail-closed `VIEW_REFUSAL_REPORT_TEXT` row before it answers, or answers 503
with no text. Locally:

```bash
curl -s -H "X-Forwarded-Groups: mip-auditor" \
     -H "X-Forwarded-Email: auditor@example.com" \
     "http://localhost:8000/api/v1/audit/refusal-reports?limit=25" | jq .
```

`MIP_GENIE_REFUSAL_TEXT_CAPTURE` (`enabled` | `disabled`) is the lender's
switch for "Report with my question". The runtime default is `disabled`; the
deploy payload ships `enabled` unless the environment or `.env.local` sets
`disabled`, and `./scripts/deploy.sh` step 0 refuses any other value. A bare
UI deploy drops every payload env var, so capture turns OFF there (never
on): a lender who set `disabled` keeps it, but one who wants capture must
deploy with `./scripts/deploy.sh`. Switching capture off does not purge
questions already kept; they remain readable to administrators and auditors
until their 90-day expiry. Kept questions expire after 90 days: an
hourly App loop (structured event `refusal_text_purged`, a count only;
`refusal_text_purge_failed` on an error) plus a bounded sweep on every report
and auditor read null the text in place; the report and its audit rows stay.
Purged text remains in Lakebase backups and point-in-time recovery for the
backup window.

The admin health body (`/api/v1/admin/health`) carries
`auditor_role_overlap`: how many configured auditors also hold the
administrator or approver role (a count, never an identity). Non-zero is a
segregation-of-duties finding; `./scripts/deploy.sh` step 0 warns about the
same count.

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
   scoring chain from §4 step 10, only where the deployed jobs match the
   current bundle: on an install whose governed deploy is NO-GO, never run
   it; use "Interim refresh (governed deploy NO-GO)" in §4:
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

2. **RBAC denied the approval call** — the resolved actor is absent from
   `MIP_APPROVER_IDENTITIES`, `MIP_APPROVER_EMAILS`, or the admin allowlists,
   so `POST /api/v1/outreach/approve` returns 403.

   Confirm in the browser devtools Network panel: the approve POST
   should be 200. If it's 403 with body `{"detail":"forbidden"}`, RBAC
   is rejecting. For local-only diagnosis, replay with an email configured in
   `MIP_APPROVER_EMAILS`:
   ```bash
   BORROWER_ID="$(curl -s "$MIP_APP_URL/api/v1/leads?limit=1" | jq -r '.[0].borrower_id')"
   curl -s -X POST "$MIP_APP_URL/api/v1/outreach/approve" \
     -H "X-Forwarded-Email: you@entrada.ai" \
     -H "Content-Type: application/json" \
     -d "{\"borrower_id\":\"$BORROWER_ID\",\"offer_code\":\"RATE_TERM_REFI\"}" | jq
   ```
   Fix: inspect `/api/v1/session` under the same authenticated identity and
   reconcile the exact server-owned allowlist. Do not grant deployed access by
   trusting a caller-supplied group header.

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

### R4-08 Map won't drill from state to ZIP

**Symptom:** the user clicks a state on the map and the ZIP layer hangs on
its loading line, shows no data, or shows ZIP tiles instead of ZIP-area
polygons.

The map drills state → ZIP (PR #182). The county grain is retired: the
Cotality share carries one county FIPS per state, so `gold.county_rollup`
emits a NULL `fips_5` and `/api/v1/geo/county-rollups` answers an empty list
by design. An empty county list is not this incident.

**Likely causes, in order:**

1. **`/api/v1/geo/zip-rollups` is 503, or `mip.gold.zip_rollup` is empty or
   stale** — warehouse down, warehouse breaker open, or gold not rebuilt.

   Confirm:
   ```bash
   curl -s -o /dev/null -w '%{http_code}\n' \
     "$MIP_APP_URL/api/v1/geo/zip-rollups?state=IL"
   curl -s "$MIP_APP_URL/api/v1/health" \
     | jq '{warehouse_dep: .dependencies.warehouse, warehouse_breaker: .circuit_breakers.warehouse}'
   databricks api post /api/2.0/sql/statements \
     --json '{"statement":"SELECT state, COUNT(*) AS zips, MAX(snapshot_date) AS snapshot FROM mip.gold.zip_rollup GROUP BY state","warehouse_id":"'"$DATABRICKS_WAREHOUSE_ID"'"}' | jq
   ```
   Warehouse down ⇒ §1.1. Empty or stale per-state rows ⇒ rebuild gold with
   the governed `./scripts/deploy.sh -t dev` or, where that run is NO-GO, the
   "Interim refresh (governed deploy NO-GO)" procedure (§4). **Never
   `databricks bundle run` or `run-now` the deployed `mip_refresh_scores`,
   and never press Admin → Rebuild scoring snapshot, on an install whose
   deployed jobs predate the current bundle:** a stale gold job still carrying
   `publish_fn_*` tasks republishes the three reviewed Growth Agent functions
   and drops their `EXECUTE` grants.

2. **The drill does not reconcile** — for the drilled state,
   `sum(zip.addressable_borrowers)` differs from
   `state.addressable - state.zip_unassigned_count`, so the ZIP and state
   rows come from different rebuilds (a partial gold run). Confirm with
   `GET /api/v1/geo/state-rollups` and `GET /api/v1/geo/zip-rollups?state=XX`;
   `tools/verify_live.py` and `scripts/smoke_live.sh` run the same check on
   the largest populated state. Fix: one complete gold rebuild, as in cause 1.

3. **The ZIP-area geometry did not load** — the ZIP rows arrive but the map
   shows ZIP tiles, its fallback, instead of polygons. Each state's geometry
   is a hashed static asset built from `frontend/src/geo/zcta/<USPS>.topo.json`
   and fetched once from `/assets/` on the first drill into that state (no API
   call, no audit row). Confirm in the browser's network panel that the
   state's `.topo` asset answers 200 with JSON. An HTML answer means the SPA
   catch-all served `index.html` instead (the static mount in
   `backend/runtime.py` regressed); a 404 means the App source lacks the
   built asset. Fix: redeploy the App payload from a full frontend build
   (`./scripts/deploy.sh`, or the payload deploy path the install uses).

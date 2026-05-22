# Disaster Recovery Runbook

**Scope:** Module 0 Databricks App deployment for one lender workspace. The
tenancy model is one workspace + one UC catalog + one Lakebase instance + one
Genie space per lender, so recovery is per deployment boundary.

Use [`docs/runbook.md`](runbook.md) for cold starts and degraded dependency
triage. Use this file when state or deploy artifacts are corrupted, deleted, or
rolled back.

All API probes below use canonical `/api/v1/*` paths. Deprecated `/api/*`
aliases still exist today for compatibility, but DR procedures should not rely
on them.

## Targets

| Surface | Recovery primitive | RPO | RTO target |
|---|---|---:|---:|
| Databricks App source/frontend/backend | Prior app snapshot or prior git SHA redeploy | Last successful deploy | 15 min for source rollback; 1 h for full redeploy |
| Lakebase app state (`mip_app.*`) | Databricks-managed Lakebase PITR, 7-day window | Up to 24 h depending on selected restore point | 15 min restore + 5 min schema head check |
| UC gold tables (`mip.gold.*`) | CTAS rebuild from silver/ref/source | Last silver/ref refresh | 10 min on warm warehouse |
| UC silver tables (`mip.silver.*`) | Cotality Delta Share + FRED refresh jobs | Last upstream source availability; FRED weekly is acceptable | 30 min |
| Genie space | Re-provision from `genie/mortgage_lead_intelligence_space.yml` | Last committed Genie YAML | 10 min |
| Governed action tokens | HMAC current/previous key grace window | No data loss; in-flight token TTL is 2 h | One redeploy |
| Audit ledger archive | Quarterly JSONL.GZ export + archive run ledger | 24 h for archive job; Lakebase PITR covers 7 d | 30 min export verification |

## First 10 Minutes

1. Freeze the failing deployment id and git SHA:

   ```bash
   databricks apps get mip-app -o json | jq '{name, url, active_deployment, state}'
   git rev-parse HEAD
   git status --short
   ```

2. Determine the damaged surface:

   ```bash
   curl -fsS "$MIP_APP_URL/api/v1/health" | jq
   curl -fsS "$MIP_APP_URL/api/v1/admin/health" \
     -H "Authorization: Bearer $MIP_BEARER_TOKEN" | jq
   ```

3. Do not run destructive cleanup. The Lakebase `action_audit` ledger is
   append-only; recovery actions should create new proof rows, not edit old
   ones.

## Scenario 1: Lakebase Corrupt Or Rolled Back

Symptoms:
- `/api/v1/health` returns `lakebase: down`.
- approvals, workspace saves, or audit reads fail while warehouse reads work.
- known recent rows are missing after a database incident.

Recovery:

1. Capture the current instance metadata:

   ```bash
   databricks database get-database-instance mip-app-state -o json | jq
   ```

2. Restore `mip-app-state` using the Databricks Lakebase point-in-time restore
   control for the customer workspace. The CLI version in this repo exposes
   database instance get/list/update commands but not a restore subcommand, so
   use the Databricks UI/API PITR workflow for the restore itself.

3. Re-apply the schema head and seed disclosures/campaign fixtures. This is
   idempotent and safe after a PITR restore:

   ```bash
   databricks bundle run mip_lakebase_migrate -t dev
   ```

4. Verify the migration ledger and audit table:

   ```bash
   psql "host=$LAKEBASE_HOST port=$LAKEBASE_PORT dbname=$LAKEBASE_DATABASE user=$LAKEBASE_USER sslmode=$LAKEBASE_SSLMODE" -c \
     "SELECT version, applied_at FROM mip_app.schema_migrations ORDER BY applied_at DESC;"
   psql "host=$LAKEBASE_HOST port=$LAKEBASE_PORT dbname=$LAKEBASE_DATABASE user=$LAKEBASE_USER sslmode=$LAKEBASE_SSLMODE" -c \
     "SELECT count(*) AS audit_rows, max(event_at) AS newest FROM mip_app.action_audit;"
   ```

5. Smoke the deployed app:

   ```bash
   curl -fsS "$MIP_APP_URL/api/v1/health" | jq
   curl -fsS "$MIP_APP_URL/api/v1/audit/events?limit=1" \
     -H "Authorization: Bearer $MIP_BEARER_TOKEN" | jq
   ```

Expected outcome: Lakebase is `up`, schema head includes
`2026_05_18_dr_backup_contract`, and the app accepts new audited actions.

## Scenario 2: Gold Tables Corrupt Or Bad Refresh

Symptoms:
- API reads succeed but counts are clearly stale or wrong.
- Genie and Segment Intelligence disagree with the latest source-readiness row.
- a nightly parity or live segment count gate fails after a refresh.

Recovery:

```bash
databricks warehouses start "$DATABRICKS_WAREHOUSE_ID"
databricks bundle run mip_ref_seed -t dev
databricks bundle run mip_refresh_silver -t dev
databricks bundle run mip_refresh_scores -t dev
```

Then validate:

```bash
databricks api post /api/2.0/sql/statements \
  --json '{"statement":"SELECT count(*) AS n, max(refreshed_at) AS refreshed_at FROM mip.gold.borrower_360","warehouse_id":"'"$DATABRICKS_WAREHOUSE_ID"'"}' | jq

.venv/bin/python -m pytest -q tests/integration/test_segment_count_parity.py
```

If a single Delta table needs rollback instead of a rebuild, use Delta time
travel from the Databricks SQL editor:

```sql
RESTORE TABLE mip.gold.borrower_360 TO TIMESTAMP AS OF '2026-05-18T12:00:00Z';
```

Record the restored version in the incident notes and immediately run the parity
gate above.

## Scenario 3: Bad App Snapshot Or Frontend Regression

Symptoms:
- API health is green but the SPA is blank, broken, or serving bad route code.
- the regression is in source code or built `frontend/dist/**`, not in bundle
  resources.

Recovery path A, source-only rollback:

```bash
databricks apps list-deployments mip-app -o json | jq '.deployments[] | {id, state, create_time}'
databricks apps get-deployment mip-app "<prior-good-deployment-id>" -o json | jq
```

If the prior deployment is the right source snapshot, promote it from the
Databricks Apps deployment UI. If the CLI in your workspace supports direct
promotion for prior snapshots, use the equivalent CLI path and capture its
output in the incident ticket.

Recovery path B, source or frontend rebuild from prior SHA:

```bash
git fetch origin
git checkout <prior-good-sha>
CI=1 ./scripts/deploy.sh -t dev --no-confirm
```

Why this matters: `frontend/dist/**` is gitignored and rebuilt on each deploy.
If current source is bad, re-running the deploy from the same checkout rebuilds
the same bad bundle. Use a prior git SHA for frontend/source regressions.

## Scenario 4: Bundle Resource Regression

Symptoms:
- a job task, SQL file path, warehouse binding, Lakebase binding, or Genie
  resource changed and the app snapshot rollback does not repair it.

Recovery:

```bash
git fetch origin
git checkout <prior-good-sha>
.venv/bin/python tools/databricks/bundle_env.py validate -t dev
.venv/bin/python tools/databricks/bundle_env.py plan -t dev
CI=1 ./scripts/deploy.sh -t dev --no-confirm
```

Validate the resource graph and live routes:

```bash
databricks bundle summary -t dev
databricks apps get mip-app -o json | jq '{state, active_deployment}'
curl -fsS "$MIP_APP_URL/api/v1/health" | jq
curl -fsS "$MIP_APP_URL/api/v1/config/options" \
  -H "Authorization: Bearer $MIP_BEARER_TOKEN" | jq '{lender_name, target_lender_refs_status}'
```

## Scenario 5: Genie Space Deleted Or Misconfigured

Symptoms:
- `/api/v1/health` shows `genie: down`.
- Ask Genie returns `source: "degraded"` or policy-blocked responses for all
  trusted sample questions.

Recovery:

```bash
.venv/bin/python tools/databricks/provision_genie_space.py \
  --profile DEFAULT \
  --warehouse-id "$DATABRICKS_WAREHOUSE_ID" \
  --spec genie/mortgage_lead_intelligence_space.yml \
  --smoke-test

# Copy the returned space id into .env.local or BUNDLE_VAR_genie_space_id,
# then rebind app resources.
CI=1 ./scripts/deploy.sh -t dev --no-confirm
```

Validate with a safe prompt:

```bash
curl -fsS -X POST "$MIP_APP_URL/api/v1/genie/message" \
  -H "Authorization: Bearer $MIP_BEARER_TOKEN" \
  -H "content-type: application/json" \
  -d '{"question":"How many borrowers are currently in-the-money?"}' | jq
```

Expected outcome: response cites only trusted `mip.gold.*` or
`mip.semantics.*` assets and does not expose PII.

## Governed Action Secret Rotation

The app now signs Genie governed-action confirmation tokens with a `kid`
claim and verifies against a current key plus an optional previous key. Token
TTL is 2 hours; keep the previous key configured for at least 2 hours, or 24
hours during customer-facing maintenance windows.

Rotation procedure:

1. Before deploy, move the current value to previous and install a new current:

   ```bash
   MIP_GENIE_ACTION_SECRET_PREVIOUS="$MIP_GENIE_ACTION_SECRET_CURRENT"
   MIP_GENIE_ACTION_SECRET_PREVIOUS_KID="$MIP_GENIE_ACTION_SECRET_KID"
   MIP_GENIE_ACTION_SECRET_CURRENT="<new-random-secret>"
   MIP_GENIE_ACTION_SECRET_KID="v2"
   ```

   If the deployment still uses the legacy `MIP_GENIE_ACTION_SECRET`, treat it
   as the current key and migrate to `MIP_GENIE_ACTION_SECRET_CURRENT`.

2. Redeploy:

   ```bash
   CI=1 ./scripts/deploy.sh -t dev --no-confirm
   ```

3. After the grace window, remove `MIP_GENIE_ACTION_SECRET_PREVIOUS` and
   `MIP_GENIE_ACTION_SECRET_PREVIOUS_KID`, then redeploy again.

Validation:

```bash
.venv/bin/python -m pytest -q tests/unit/test_genie_actions_api.py -k "token or confirmation"
```

## Audit Ledger Archival

Retention policy:
- `mip_app.action_audit` remains append-only in Lakebase. Do not DELETE from it
  during Module 0 operations.
- Quarterly, export rows older than 365 days to compressed JSONL and move that
  file into the customer's governed cold-storage location, typically a UC
  Volume or customer archive bucket.
- Each export records a row in `mip_app.action_audit_archive_runs` with the
  cutoff, destination, row count, and operator identity.

Command:

```bash
.venv/bin/python tools/databricks/export_action_audit.py \
  --cutoff-days 365 \
  --output artifacts/action_audit/action_audit_before_$(date -u +%Y%m%dT%H%M%SZ).jsonl.gz \
  --requested-by "$USER"
```

Verify:

```bash
gzip -t artifacts/action_audit/*.jsonl.gz
psql "host=$LAKEBASE_HOST port=$LAKEBASE_PORT dbname=$LAKEBASE_DATABASE user=$LAKEBASE_USER sslmode=$LAKEBASE_SSLMODE" -c \
  "SELECT cutoff_event_at, destination_uri, row_count, completed_at FROM mip_app.action_audit_archive_runs ORDER BY completed_at DESC LIMIT 5;"
```

This is an archive copy, not a destructive retention compaction. Any future
Lakebase pruning needs a signed customer retention policy because the audit
ledger is a compliance record.

## Production Lakebase HA

The base `dev` resource stays cost-minimal (`CU_1`, no readable secondaries).
The `prod` and `prod_otlp` bundle targets enable readable secondaries for
Lakebase so a production customer target has a failover posture by default.
Validate before customer go-live:

```bash
.venv/bin/python tools/databricks/bundle_env.py validate -t prod
.venv/bin/python tools/databricks/bundle_env.py validate -t prod_otlp
```

## Incident Closeout

Close the incident only after all applicable gates pass:

```bash
.venv/bin/python -m pytest -q tests/unit/test_disaster_recovery_contract.py
.venv/bin/python -m pytest -q tests/unit/test_supply_chain_licenses.py tests/unit/test_architecture_boundaries.py
npm --prefix frontend run build
npm --prefix frontend run budget
curl -fsS "$MIP_APP_URL/api/v1/health" | jq
curl -fsS "$MIP_APP_URL/api/v1/admin/health" \
  -H "Authorization: Bearer $MIP_BEARER_TOKEN" | jq
```

Record the app deployment id, git SHA, restore timestamp, and validation output
in the incident ticket.

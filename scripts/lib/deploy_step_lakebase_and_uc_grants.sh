# shellcheck shell=bash
# Deploy step file. Steps 4b-4d: Lakebase migration, synced-catalog quiesce, reviewed-function
# EXECUTE grants, treatment DDL, app UC grants, the observed-lineage view, pii-salt and
# rollback scopes.
# Sourced by scripts/deploy.sh in reviewed order; it runs in the entrypoint's
# shell with its strict mode, traps, and private variables, never on its own.
# shellcheck disable=SC2034  # top-level state assigned here is read by later step files.
[[ "${BASH_SOURCE[0]}" != "$0" ]] || {
  echo "[deploy] ${BASH_SOURCE[0]} is a step file; run ./scripts/deploy.sh instead." >&2
  exit 2
}

# Ordering matters (2026-06-11, observed live): when migration ran AFTER the
# app snapshot promotion, the freshly restarted app raced the migrate job's
# schema work, tripped the lakebase circuit breaker, and showed the audit
# feed's degraded banner for ~30s. Migrating first means the restarted app
# boots against an already-migrated schema. The stopped first-install identity
# and every existing App remain unmodified throughout this migration; source
# activation occurs only through deploy_app_snapshot after convergence.
# Requires only step 4 (the bundle apply defines the job + Lakebase instance).
step "migrate Lakebase — schema.sql + seed_campaigns.sql (idempotent)"
run_job_with_retry databricks bundle run mip_lakebase_migrate -t "$TARGET"
LAKEBASE_RUNTIME_ACCESS_PROVEN=1

# Historical releases granted the App's UC identity directly on the Lakebase
# ``public``/``mip_app`` schemas.  Remove that residue (and any pre-existing
# sync-schema access) before rebuilding the reviewed sync target.  The helper
# resolves nested groups and proves the effective boundary, so a broader group
# grant or ownership path fails the release instead of being hidden by a
# successful direct REVOKE.
step "quiesce legacy and target Lakebase synced-catalog access"
run "$PYTHON" -m tools.databricks.converge_app_lakebase_sync_access \
  --mode quiesce \
  --warehouse-id "$_GRANTS_WAREHOUSE_ID" \
  --app-application-id "$APP_SP_CLIENT_ID" \
  --app-scim-id "$APP_SP_SCIM_ID" \
  --sync-catalog "$DEPLOYMENT_SYNC_CATALOG" \
  --sync-schema "$DEPLOYMENT_SYNC_SCHEMA" \
  --sync-tables "$DEPLOYMENT_SYNC_TABLES"

apply_uc_grant() {
  local _grant_stmt="$1"
  local _grant_state=""
  local _grant_resp=""
  local _grant_try
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "  would grant: ${_grant_stmt}"
    return 0
  fi
  # Re-audit 2026-06-11: a single 50s/CANCEL attempt reported a cold or
  # queued warehouse as a misleading "grant failed". Retry the statement
  # up to 3 attempts (the wait_timeout API ceiling is 50s per call) so
  # warm-up latency is absorbed; a genuine authority failure still exits.
  for _grant_try in 1 2 3; do
    _grant_resp="$(databricks api post /api/2.0/sql/statements/ --json "$(
      "$PYTHON" -c 'import json,sys; print(json.dumps({"warehouse_id": sys.argv[1], "statement": sys.argv[2], "wait_timeout": "50s", "on_wait_timeout": "CANCEL"}))' \
        "$_GRANTS_WAREHOUSE_ID" "$_grant_stmt"
    )")"
    _grant_state="$(printf '%s' "$_grant_resp" | "$PYTHON" -c 'import json,sys; d=json.load(sys.stdin); print(d.get("status",{}).get("state",""))')"
    [[ "$_grant_state" == "SUCCEEDED" ]] && break
    if [[ "$_grant_try" -lt 3 ]]; then
      echo "  grant attempt ${_grant_try} ended ${_grant_state:-no-state} (warehouse warming?) — retrying"
      sleep 5
    fi
  done
  if [[ "$_grant_state" != "SUCCEEDED" ]]; then
    echo "${RED}[deploy] UC grant failed after 3 attempts (${_grant_state:-no-state}): ${_grant_stmt}${RST}" >&2
    printf '%s\n' "$_grant_resp" | "$PYTHON" -c 'import json,sys; d=json.load(sys.stdin); print(json.dumps(d.get("status",{}).get("error",{}), indent=2)[:600])' >&2 || true
    echo "  Likely cause: the deploying identity lacks GRANT authority on catalog '${_GRANTS_CATALOG}'." >&2
    echo "  A metastore admin can apply docs/security/GRANTS.md once; reruns are idempotent." >&2
    return 4
  fi
  echo "  granted: ${_grant_stmt}"
}

# The pre-refresh bootstrap is the sole publisher of the reviewed helper
# functions. Both it and the gold refresh pass through the same reconciliation
# boundary so grants remain proven after publication and any future job-graph
# regression fails closed before either production identity can continue.
reconcile_reviewed_function_execute_grants() {
  local _function_name
  local _principal
  local _grant_failed=0
  local _postflight_failed=0
  for _principal in \
    "$APP_SP_CLIENT_ID" \
    "$DATABRICKS_AGENT_RUNTIME_CLIENT_ID" \
    "$DATABRICKS_AGENT_PROXY_CLIENT_ID"; do
    for _function_name in fn_build_cohort fn_segment_counts fn_lead_queue_url; do
      if ! apply_uc_grant \
        "GRANT EXECUTE ON FUNCTION ${_GRANTS_CATALOG}.gold.${_function_name} TO \`${_principal}\`"; then
        _grant_failed=1
      fi
    done
  done
  if ! run "$PYTHON" -m tools.databricks.verify_reviewed_function_execute_grants \
    --catalog "$_GRANTS_CATALOG" \
    --app-application-id "$APP_SP_CLIENT_ID" \
    --agent-runtime-application-id "$DATABRICKS_AGENT_RUNTIME_CLIENT_ID" \
    --agent-proxy-application-id "$DATABRICKS_AGENT_PROXY_CLIENT_ID"; then
    _postflight_failed=1
  fi
  if [[ "$_grant_failed" -ne 0 || "$_postflight_failed" -ne 0 ]]; then
    echo "${RED}[deploy] reviewed function EXECUTE reconciliation is unproven.${RST}" >&2
    return 4
  fi
  REVIEWED_FUNCTION_GRANTS_PROVEN=1
}

run_job_and_reconcile_reviewed_function_grants() {
  local _job_label="$1"
  local _job_rc=0
  local _reconcile_rc=0
  shift
  REVIEWED_FUNCTION_GRANTS_PROVEN=0
  step "$_job_label"
  run_job_with_retry "$@" || _job_rc=$?
  step "reconcile and prove reviewed function EXECUTE grants after governed job"
  reconcile_reviewed_function_execute_grants || _reconcile_rc=$?
  if [[ "$_job_rc" -ne 0 ]]; then
    if [[ "$_reconcile_rc" -ne 0 ]]; then
      echo "${RED}[deploy] governed job and reviewed function grant repair both failed.${RST}" >&2
    fi
    return "$_job_rc"
  fi
  return "$_reconcile_rc"
}

initialize_uc_targets_and_reconcile_function_grants() {
  run_job_and_reconcile_reviewed_function_grants \
    "initialize every pre-refresh UC grant target (idempotent)" \
    databricks bundle run mip_init_catalog_schemas -t "$TARGET"
}

refresh_gold_and_reconcile_function_grants() {
  run_job_and_reconcile_reviewed_function_grants \
    "refresh gold — borrower_360, lead_scores, *_population, dossier, + mip.semantics.*" \
    databricks bundle run mip_refresh_scores -t "$TARGET"
}

# Run the dedicated, idempotent UC bootstrap before object-level grants. Its
# DAG creates the governed treatment table, the ref/gold contracts, and the
# three reviewed Growth Agent functions. Customer-triggerable silver/gold jobs
# never republish those functions; grant ordering must not depend on refresh
# policy.
step "quiesce app treatment writes immediately before treatment-table DDL"
run_with_account_identity run_with_proof_signing_authority \
  "$PYTHON" -m tools.databricks.converge_campaign_treatment_access \
  --warehouse-id "$_GRANTS_WAREHOUSE_ID" \
  --catalog "$_GRANTS_CATALOG" \
  --principal "$APP_SP_CLIENT_ID" \
  --mode quiesce
TREATMENT_RUNTIME_QUIESCED=1
initialize_uc_targets_and_reconcile_function_grants

# CHECK constraints must be added through ALTER TABLE in Databricks SQL; the
# CREATE TABLE bootstrap cannot declare them inline. Inspect Delta's persisted
# constraint properties, add only missing exact definitions, and fail closed
# on drift before granting the app access to the treatment table.
step "converge governed campaign treatment Delta constraints (idempotent)"
run "$PYTHON" -m tools.databricks.ensure_campaign_treatment_table \
  --warehouse-id "$_GRANTS_WAREHOUSE_ID" \
  --catalog "$_GRANTS_CATALOG"
step "keep treatment writes quiesced until the green App is proven and captured"

# -----------------------------------------------------------------------------
# Step 4c: UC grants for the app service principal (audit P1-3, zero-click)
# -----------------------------------------------------------------------------
# On a FRESH workspace the bundle creates the app + its service principal,
# but nothing granted that SP read access to the UC objects — the app booted
# to PERMISSION_DENIED on every endpoint and docs/security/GRANTS.md was a
# manual copy-paste runbook (CLAUDE.md calls that exact pattern a packaging
# bug). This step applies the GRANTS.md §catalog/§gold/§ref/§audit
# base statements idempotently (GRANT is a no-op when already granted)
# against the deploy warehouse, addressed to the SP's client id. AI
# Gateway table-level SELECT is applied after agentic provisioning once
# the concrete inference table prefix is known. Failures are FATAL with
# a pointer to GRANTS.md: a deploy that cannot grant is a deploy whose
# app cannot read, and hiding that would violate the fail-visibly contract.
# GRANTS.md remains the audit-readable matrix; Lakebase role grants are
# applied by jobs/lakebase_migrate.py in step 4b.
step "apply UC grants to the app service principal (idempotent)"
if [[ "$DRY_RUN" -eq 0 ]]; then
  # These two schema-creation privileges exist only while the dedicated runtime
  # creates/updates its exact registered model and Gateway inference table.
  # The EXIT compensation revokes them on both success and failure.
  AGENT_RUNTIME_BOOTSTRAP_GRANTS_ACTIVE=1
fi
while IFS= read -r _grant_stmt; do
  [[ -z "$_grant_stmt" ]] && continue
  apply_uc_grant "$_grant_stmt"
done <<GRANTS_EOF
GRANT USE CATALOG ON CATALOG ${_GRANTS_CATALOG} TO \`${APP_SP_CLIENT_ID}\`
GRANT USE SCHEMA, SELECT ON SCHEMA ${_GRANTS_CATALOG}.gold TO \`${APP_SP_CLIENT_ID}\`
GRANT MODIFY ON TABLE ${_GRANTS_CATALOG}.gold.borrower_lifecycle_state TO \`${APP_SP_CLIENT_ID}\`
GRANT MODIFY ON TABLE ${_GRANTS_CATALOG}.gold.funnel_snapshot_daily TO \`${APP_SP_CLIENT_ID}\`
GRANT USE SCHEMA, SELECT ON SCHEMA ${_GRANTS_CATALOG}.ref TO \`${APP_SP_CLIENT_ID}\`
GRANT USE SCHEMA ON SCHEMA ${_GRANTS_CATALOG}.audit TO \`${APP_SP_CLIENT_ID}\`
GRANT EXECUTE ON FUNCTION ${_GRANTS_CATALOG}.gold.fn_build_cohort TO \`${APP_SP_CLIENT_ID}\`
GRANT EXECUTE ON FUNCTION ${_GRANTS_CATALOG}.gold.fn_segment_counts TO \`${APP_SP_CLIENT_ID}\`
GRANT EXECUTE ON FUNCTION ${_GRANTS_CATALOG}.gold.fn_lead_queue_url TO \`${APP_SP_CLIENT_ID}\`
GRANT USE CATALOG ON CATALOG ${_GRANTS_CATALOG} TO \`${DATABRICKS_AGENT_RUNTIME_CLIENT_ID}\`
GRANT USE SCHEMA ON SCHEMA ${_GRANTS_CATALOG}.gold TO \`${DATABRICKS_AGENT_RUNTIME_CLIENT_ID}\`
GRANT EXECUTE ON FUNCTION ${_GRANTS_CATALOG}.gold.fn_build_cohort TO \`${DATABRICKS_AGENT_RUNTIME_CLIENT_ID}\`
GRANT EXECUTE ON FUNCTION ${_GRANTS_CATALOG}.gold.fn_segment_counts TO \`${DATABRICKS_AGENT_RUNTIME_CLIENT_ID}\`
GRANT EXECUTE ON FUNCTION ${_GRANTS_CATALOG}.gold.fn_lead_queue_url TO \`${DATABRICKS_AGENT_RUNTIME_CLIENT_ID}\`
GRANT USE CATALOG ON CATALOG ${_GRANTS_CATALOG} TO \`${DATABRICKS_AGENT_PROXY_CLIENT_ID}\`
GRANT USE SCHEMA ON SCHEMA ${_GRANTS_CATALOG}.gold TO \`${DATABRICKS_AGENT_PROXY_CLIENT_ID}\`
GRANT EXECUTE ON FUNCTION ${_GRANTS_CATALOG}.gold.fn_build_cohort TO \`${DATABRICKS_AGENT_PROXY_CLIENT_ID}\`
GRANT EXECUTE ON FUNCTION ${_GRANTS_CATALOG}.gold.fn_segment_counts TO \`${DATABRICKS_AGENT_PROXY_CLIENT_ID}\`
GRANT EXECUTE ON FUNCTION ${_GRANTS_CATALOG}.gold.fn_lead_queue_url TO \`${DATABRICKS_AGENT_PROXY_CLIENT_ID}\`
GRANT USE SCHEMA ON SCHEMA ${_GRANTS_CATALOG}.audit TO \`${DATABRICKS_AGENT_RUNTIME_CLIENT_ID}\`
GRANT CREATE MODEL ON SCHEMA ${_GRANTS_CATALOG}.audit TO \`${DATABRICKS_AGENT_RUNTIME_CLIENT_ID}\`
GRANT CREATE TABLE ON SCHEMA ${_GRANTS_CATALOG}.audit TO \`${DATABRICKS_AGENT_RUNTIME_CLIENT_ID}\`
GRANTS_EOF

_treatment_properties_stmt="SHOW TBLPROPERTIES ${_GRANTS_CATALOG}.audit.campaign_treatment_snapshot"
if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "  would verify: campaign treatment table append-only and exact retention properties"
else
_treatment_properties_resp="$(databricks api post /api/2.0/sql/statements/ --json "$(
  "$PYTHON" -c 'import json,sys; print(json.dumps({"warehouse_id": sys.argv[1], "statement": sys.argv[2], "wait_timeout": "50s", "on_wait_timeout": "CANCEL"}))' \
    "$_GRANTS_WAREHOUSE_ID" "$_treatment_properties_stmt"
)")"
if ! printf '%s' "$_treatment_properties_resp" | "$PYTHON" -c '
import json, sys
body = json.load(sys.stdin)
if body.get("status", {}).get("state") != "SUCCEEDED":
    raise SystemExit(1)
rows = body.get("result", {}).get("data_array", [])
actual = {str(row[0]): str(row[1]) for row in rows if len(row) >= 2}
expected = {
    "delta.appendOnly": "true",
    "delta.logRetentionDuration": "interval 2555 days",
    "delta.deletedFileRetentionDuration": "interval 2555 days",
}
if any(actual.get(key) != value for key, value in expected.items()):
    raise SystemExit(1)
'; then
  echo "${RED}[deploy] campaign treatment table property postflight failed.${RST}" >&2
  exit 4
fi
echo "  verified: campaign treatment table is append-only with exact log and deleted-file retention"
fi

# >>> observed lineage view: publish >>>
# The asset-detail lineage section reads <catalog>.audit.asset_lineage, a
# four-column view over system.access.table_lineage limited to edges into this
# catalog from this catalog or the Cotality share catalog
# (sql/ddl/audit_asset_lineage_view.sql). Unity Catalog evaluates
# it with its OWNER's system-table access, so the App gets SELECT on this one
# view and never a grant on the metastore-wide system schema. Lineage is an
# optional evidence source: a deployer that cannot read the system table (exit
# 3) skips the view and the grant with a warning, and the App keeps its explicit
# "Observed lineage unavailable" state -- the same honest-degraded convention as
# the pending AI Gateway inference-table grants. The skip touches nothing: a view
# and App grant an earlier owner left stay as they were, unverified by this
# deploy, and a new App service principal is not granted. Any other failure is
# fatal, like every UC grant above.
step "publish the observed-lineage view and grant the App SELECT on it (optional evidence)"
_asset_lineage_rc=0
run "$PYTHON" -m tools.databricks.ensure_asset_lineage_view \
  --warehouse-id "$_GRANTS_WAREHOUSE_ID" \
  --catalog "$_GRANTS_CATALOG" || _asset_lineage_rc=$?
if [[ "$_asset_lineage_rc" -eq 0 ]]; then
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "  plan: the view publish and the grant below run only if the deploying identity can read system.access.table_lineage; otherwise a real run warns and skips both"
  fi
  apply_uc_grant "GRANT SELECT ON TABLE ${_GRANTS_CATALOG}.audit.asset_lineage TO \`${APP_SP_CLIENT_ID}\`"
elif [[ "$_asset_lineage_rc" -eq 3 ]]; then
  echo "${YLW}[deploy] WARNING: observed-lineage view not published: the deploying identity lacks USE CATALOG on system, USE SCHEMA on system.access or SELECT on system.access.table_lineage, so this deploy neither published nor verified ${_GRANTS_CATALOG}.audit.asset_lineage and granted nothing on it. A view and App grant an earlier owner left are unchanged and unverified, and a new App service principal is not granted; without them the App shows its explicit 'Observed lineage unavailable' gap.${RST}" >&2
else
  echo "${RED}[deploy] observed-lineage view publish failed (exit ${_asset_lineage_rc}); see the error above.${RST}" >&2
  exit "$_asset_lineage_rc"
fi
# <<< observed lineage view: publish <<<

# -----------------------------------------------------------------------------
# Step 4d: provision the PII-salt secret scope (audit P1-4, zero-click)
# -----------------------------------------------------------------------------
# pipelines/lakeflow/mip_feature_pipeline.py and the silver warehouse path
# hash PII columns with secret('mip', 'pii-salt-v1'). Nothing provisioned
# that scope: on a fresh workspace the DLT path failed mid-silver-refresh
# with an unexplained secret error, and the SQL path silently fell back to
# a source-committed constant (predictable hashing — worse than failing).
# Create-if-missing ONLY; an existing salt is NEVER rotated, because
# rotating it changes every masked identifier across refreshes and breaks
# join stability between gold snapshots. The Secrets API has no create-only
# put, so tools/databricks/pii_salt_scope.py writes only after an
# authoritative "listed, key absent": any list-scopes or list-secrets error
# exits 2 before a create or put, where the earlier inline check read a
# failed listing as "absent" and would have overwritten the live salt. The
# value never leaves the tool's process. Step 0 ran the same inventory
# read-only before the lease.
step "provision pii-salt secret scope (create-if-missing, never rotate)"
if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "  would inspect/create: scope ${MIP_PII_SALT_SCOPE} and write-once pii-salt-v1"
else
  run "$PYTHON" -m tools.databricks.pii_salt_scope ensure \
    --scope "$MIP_PII_SALT_SCOPE" \
    --require-existing "$PII_SALT_REQUIRE_EXISTING"
fi

step "provision dedicated signed App rollback-contract secret scope"
if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "  would re-audit: scope ${APP_ROLLBACK_SECRET_SCOPE}"
else
  run "$PYTHON" -m tools.databricks.app_rollback_secret_scope assert \
    --app-name "$_GRANTS_APP_NAME" \
    --scope "$APP_ROLLBACK_SECRET_SCOPE"
fi

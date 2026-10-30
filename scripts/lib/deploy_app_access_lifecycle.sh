# shellcheck shell=bash
# App identity pin, deployment sync-contract restore, and App treatment/release
# access convergence.
# Sourced by scripts/deploy.sh; this file must not change shell options or traps.

assert_expected_app_identity() {
  local app_name="${1:?App name is required for identity verification}"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    return 0
  fi
  if [[ "${#APP_EXPECTED_IDENTITY_ARGS[@]}" -ne 6 ]]; then
    echo "${RED}[deploy] complete expected App identity is required before name-addressed mutation.${RST}" >&2
    return 1
  fi
  "$PYTHON" -m tools.databricks.stop_app_fail_closed \
    --app-name "$app_name" \
    "${APP_EXPECTED_IDENTITY_ARGS[@]}" \
    --assert-identity-only
}

restore_deployment_sync_contract() {
  local source_label="${1:-agentic environment}"
  if [[ "${MIP_LAKEBASE_SYNC_CATALOG:-}" != "$DEPLOYMENT_SYNC_CATALOG" || \
        "${MIP_LAKEBASE_SYNC_SCHEMA:-}" != "$DEPLOYMENT_SYNC_SCHEMA" || \
        "${MIP_LAKEBASE_SYNC_TABLES:-}" != "$DEPLOYMENT_SYNC_TABLES" ]]; then
    echo "[deploy] ignoring stale Lakebase Sync names from ${source_label}; deployment controls are authoritative"
  fi
  MIP_LAKEBASE_SYNC_CATALOG="$DEPLOYMENT_SYNC_CATALOG"
  MIP_LAKEBASE_SYNC_SCHEMA="$DEPLOYMENT_SYNC_SCHEMA"
  MIP_LAKEBASE_SYNC_TABLES="$DEPLOYMENT_SYNC_TABLES"
  export MIP_LAKEBASE_SYNC_CATALOG MIP_LAKEBASE_SYNC_SCHEMA MIP_LAKEBASE_SYNC_TABLES
}

converge_app_treatment_access() {
  local mode="$1" principal
  principal="${APP_SP_CLIENT_ID:-${_EXISTING_APP_SP_CLIENT_ID:-}}"
  if [[ -z "$principal" || -z "${_GRANTS_WAREHOUSE_ID:-}" || \
        -z "${_GRANTS_CATALOG:-}" ]]; then
    echo "${RED}[deploy] treatment convergence lacks an App identity or warehouse.${RST}" >&2
    return 1
  fi
  run_with_account_identity run_with_proof_signing_authority \
    "$PYTHON" -m tools.databricks.converge_campaign_treatment_access \
    --warehouse-id "$_GRANTS_WAREHOUSE_ID" \
    --catalog "$_GRANTS_CATALOG" \
    --principal "$principal" \
    --mode "$mode"
}

mint_signed_app_proof_token() {
  if [[ "${APP_ACCESS_QUARANTINED:-0}" -eq 1 ]]; then
    mint_m2m_token MIP_BEARER_TOKEN \
      DATABRICKS_RELEASE_PROBE_CLIENT_ID DATABRICKS_RELEASE_PROBE_CLIENT_SECRET
  else
    mint_m2m_token MIP_BEARER_TOKEN DATABRICKS_CLIENT_ID DATABRICKS_CLIENT_SECRET
  fi
}

converge_runtime_app_release_access() {
  if [[ "${APP_ACCESS_QUARANTINED:-0}" -eq 1 ]]; then
    "$PYTHON" -m tools.databricks.converge_app_release_access \
      --mode runtime \
      --app-name "$APP_FAIL_CLOSED_NAME" \
      --release-probe-application-id "$DATABRICKS_RELEASE_PROBE_CLIENT_ID" \
      --normal-application-id "$DATABRICKS_CLIENT_ID" \
      --operator2-application-id "$DATABRICKS_OPERATOR2_CLIENT_ID" \
      --admin-application-id "$DATABRICKS_ADMIN_CLIENT_ID" \
      "${APP_EXPECTED_IDENTITY_ARGS[@]}" || return 1
  fi
}

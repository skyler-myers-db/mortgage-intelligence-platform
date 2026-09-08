# shellcheck shell=bash
# App identity pin, foreign-catalog binding remediation, deployment sync-contract
# restore, and App treatment/release access convergence.
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

run_foreign_catalog_binding_remediation() {
  if [[ -z "${MIP_UC_FOREIGN_CATALOG_BINDING_POLICY:-}" ]]; then
    echo "${RED}[deploy] foreign-catalog remediation requires the reviewed binding policy.${RST}" >&2
    return 4
  fi
  FOREIGN_CATALOG_BINDING_DIR="$(mktemp -d -t mip-foreign-catalog.XXXXXX)"
  local manifest="$FOREIGN_CATALOG_BINDING_DIR/manifest.json"
  local parent_manifest="$FOREIGN_CATALOG_BINDING_DIR/parent.json"
  local action="apply"
  local recovery_candidate="" recovery_rc=0 recovered=0
  local -a recovery_candidates=()
  local -a common_args=(
    --app-name "$_GRANTS_APP_NAME"
    --application-id "$DATABRICKS_AGENT_RUNTIME_CLIENT_ID"
    --expected-inventory-principal "$DEPLOY_INVENTORY_PRINCIPAL"
    --expected-account-id "$DATABRICKS_ACCOUNT_ID"
    --expected-account-client-id "$DATABRICKS_ACCOUNT_CLIENT_ID"
    --mip-catalog "${MIP_DEFAULT_CATALOG:-mip}"
    --lease-id "$MIP_APP_DEPLOYMENT_LEASE_ID"
  )
  if [[ -n "${MIP_APP_DEPLOYMENT_RECOVERY_CANDIDATES:-}" ]]; then
    step "recover the signed interrupted foreign-catalog manifest"
    IFS=',' read -r -a recovery_candidates \
      <<< "$MIP_APP_DEPLOYMENT_RECOVERY_CANDIDATES"
    for recovery_candidate in "${recovery_candidates[@]}"; do
      if run_with_account_identity run_with_proof_signing_authority \
          "$PYTHON" -m tools.databricks.converge_foreign_catalog_workspace_bindings \
          recover-local "${common_args[@]}" \
          --parent-lease-id "$recovery_candidate" \
          --manifest "$parent_manifest"; then
        recovered=1
        break
      else
        recovery_rc=$?
      fi
      if [[ "$recovery_rc" -eq 5 ]]; then
        break
      elif [[ "$recovery_rc" -ne 3 ]]; then
        return "$recovery_rc"
      fi
    done
    if [[ "$recovered" -eq 1 ]]; then
      step "reauthorize interrupted foreign-catalog remediation under the current lease"
      if run_with_account_identity run_with_proof_signing_authority \
        "$PYTHON" -m tools.databricks.converge_foreign_catalog_workspace_bindings \
        reauthorize "${common_args[@]}" \
        --manifest "$parent_manifest" \
        --out-manifest "$manifest"; then
        :
      else
        recovery_rc=$?
        return "$recovery_rc"
      fi
      action="resume"
    else
      step "no interrupted foreign-catalog manifest; snapshot fresh signed pre-state"
      if run_with_account_identity run_with_proof_signing_authority \
        "$PYTHON" -m tools.databricks.converge_foreign_catalog_workspace_bindings \
        snapshot "${common_args[@]}" --manifest "$manifest"; then
        :
      else
        recovery_rc=$?
        return "$recovery_rc"
      fi
    fi
  else
    step "snapshot the exact signed foreign-catalog binding pre-state"
    if run_with_account_identity run_with_proof_signing_authority \
      "$PYTHON" -m tools.databricks.converge_foreign_catalog_workspace_bindings \
      snapshot "${common_args[@]}" --manifest "$manifest"; then
      :
    else
      recovery_rc=$?
      return "$recovery_rc"
    fi
  fi
  step "$action the reviewed foreign-catalog workspace isolation"
  if run_with_account_identity run_with_proof_signing_authority \
    "$PYTHON" -m tools.databricks.converge_foreign_catalog_workspace_bindings \
    "$action" "${common_args[@]}" --manifest "$manifest"; then
    :
  else
    recovery_rc=$?
    return "$recovery_rc"
  fi
  step "verify the complete foreign-catalog workspace isolation policy"
  if run_with_account_identity run_with_proof_signing_authority \
    "$PYTHON" -m tools.databricks.converge_foreign_catalog_workspace_bindings \
    verify "${common_args[@]}" --manifest "$manifest"; then
    :
  else
    recovery_rc=$?
    return "$recovery_rc"
  fi
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

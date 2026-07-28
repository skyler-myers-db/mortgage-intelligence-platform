# shellcheck shell=bash
# Verifier Gateway identity proof and fail-closed access compensation.
# Sourced by scripts/deploy.sh; this file must not change shell options or traps.

prove_exact_verifier_boundary() {
  local endpoint="${1:?Gateway endpoint is required for verifier proof}"
  local relation_prefix="${2:?Gateway inference-table prefix is required for verifier proof}"
  shift 2
  run_with_verifier_credentials \
    "$PYTHON" -m tools.databricks.verify_verifier_identity_boundary \
    --expected-application-id "$DATABRICKS_VERIFIER_CLIENT_ID" \
    --account-host "${DATABRICKS_ACCOUNT_HOST:-https://accounts.cloud.databricks.com}" \
    --account-id "$DATABRICKS_ACCOUNT_ID" \
    --app-name "$_GRANTS_APP_NAME" \
    --app-url "${MIP_APP_URL:?deployed App URL is required for verifier proof}" \
    --protected-service-principal-id "$APP_SP_SCIM_ID" \
    --warehouse-id "$_GRANTS_WAREHOUSE_ID" \
    --relation-prefix "$relation_prefix" \
    --endpoint "$endpoint" \
    --allow-attested-app-401 \
    "$@"
}

capture_verifier_identity() {
  [[ -z "${MIP_VERIFIER_SCIM_ID:-}" ]] || return 0
  VERIFIER_IDENTITY_CAPTURE_ENV="$(mktemp -t mip-verifier-identity.XXXXXX.env)"
  "$PYTHON" -m tools.databricks.converge_verifier_gateway_access capture \
    --application-id "$DATABRICKS_VERIFIER_CLIENT_ID" \
    --expected-inventory-principal "$DEPLOY_INVENTORY_PRINCIPAL" \
    --out-env "$VERIFIER_IDENTITY_CAPTURE_ENV" || return 1
  unset MIP_VERIFIER_SCIM_ID
  set -a
  # shellcheck disable=SC1090
  . "$VERIFIER_IDENTITY_CAPTURE_ENV"
  set +a
  rm -f "$VERIFIER_IDENTITY_CAPTURE_ENV"
  VERIFIER_IDENTITY_CAPTURE_ENV=""
  if [[ -z "${MIP_VERIFIER_SCIM_ID:-}" ]]; then
    echo "${RED}[deploy] verifier immutable SCIM ID capture returned no identity.${RST}" >&2
    return 1
  fi
}

compensate_verifier_gateway_access() {
  [[ "$DRY_RUN" -eq 0 && "$VERIFIER_GATEWAY_CUTOVER_MUTATED" -eq 1 ]] || return 0
  local failed=0 old_gateway_mode="none"
  local -a captured_audit_args=() captured_proof_args=()
  if [[ -z "$MIP_VERIFIER_SCIM_ID" || -z "${MIP_AI_GATEWAY_ENDPOINT:-}" ]]; then
    echo "${RED}[deploy] verifier Gateway compensation lacks its pinned identity or green endpoint.${RST}" >&2
    return 1
  fi
  if [[ "${APP_UPGRADE_STATE:-first_install}" == \
        "green_captured_cleanup_pending" ]]; then
    if ! load_captured_live_old_resources; then
      echo "${RED}[deploy] captured-green compensation could not authenticate the cutover journal.${RST}" >&2
      failed=1
    elif [[ -z "${MIP_APP_ROLLBACK_GATEWAY_ENDPOINT:-}" || \
          -z "${MIP_APP_ROLLBACK_GATEWAY_INFERENCE_TABLE_PREFIX:-}" || \
          -z "${MIP_AI_GATEWAY_INFERENCE_TABLE:-}" || \
          "$MIP_APP_ROLLBACK_GATEWAY_ENDPOINT" != "$MIP_AI_GATEWAY_ENDPOINT" || \
          ( "$MIP_AI_GATEWAY_INFERENCE_TABLE" != \
              "$MIP_APP_ROLLBACK_GATEWAY_INFERENCE_TABLE_PREFIX" && \
            "$MIP_AI_GATEWAY_INFERENCE_TABLE" != \
              "$MIP_APP_ROLLBACK_GATEWAY_INFERENCE_TABLE_PREFIX"_* ) ]]; then
      echo "${RED}[deploy] captured-green verifier compensation lacks its exact signed Gateway binding.${RST}" >&2
      failed=1
    else
      captured_audit_args=(
        -m tools.databricks.audit_global_m2m_access
        --app-name "${MIP_APP_NAME:?App name is required}" \
        --application-id "$DATABRICKS_VERIFIER_CLIENT_ID" \
        --expected-inventory-principal "$DEPLOY_INVENTORY_PRINCIPAL" \
        --account-id "$DATABRICKS_ACCOUNT_ID" \
        --expected-serving-permission CAN_QUERY \
        --forbid-all-genie \
        --serving-endpoint "$MIP_APP_ROLLBACK_GATEWAY_ENDPOINT"
      )
      if [[ "$CAPTURED_OLD_GATEWAY_LIVE" -eq 1 ]]; then
        old_gateway_mode="$(pinned_query_access_mode \
          "$DATABRICKS_VERIFIER_CLIENT_ID" \
          "$MIP_REPLACED_AGENT_GATEWAY_ENDPOINT")" || failed=1
        if [[ "$old_gateway_mode" != "none" ]]; then
          captured_audit_args+=(
            --serving-endpoint "$MIP_REPLACED_AGENT_GATEWAY_ENDPOINT"
            --legacy-pinned-serving-endpoint "$MIP_REPLACED_AGENT_GATEWAY_ENDPOINT"
          )
          captured_proof_args+=(
            --preserve-endpoint "$MIP_REPLACED_AGENT_GATEWAY_ENDPOINT"
          )
        fi
      fi
      run_with_account_identity run_with_proof_signing_authority \
        "$PYTHON" "${captured_audit_args[@]}" || failed=1
      prove_exact_verifier_boundary \
        "$MIP_APP_ROLLBACK_GATEWAY_ENDPOINT" \
        "$MIP_APP_ROLLBACK_GATEWAY_INFERENCE_TABLE_PREFIX" \
        "${captured_proof_args[@]}" || failed=1
      if [[ "$failed" -eq 0 ]]; then
        CAPTURED_VERIFIER_BOUNDARY_PROVEN=1
      fi
    fi
  else
    run_with_account_identity run_with_proof_signing_authority \
      "$PYTHON" -m tools.databricks.converge_verifier_gateway_access revoke-managed \
      --app-name "${MIP_APP_NAME:?App name is required}" \
      --deployment-lease-id \
        "${MIP_APP_DEPLOYMENT_LEASE_ID:?deployment lease is required}" \
      --deployment-source-git-sha \
        "${MIP_DEPLOYMENT_SOURCE_GIT_SHA:?deployment source is required}" \
      --endpoint "$MIP_AI_GATEWAY_ENDPOINT" \
      --application-id "$DATABRICKS_VERIFIER_CLIENT_ID" \
      --expected-scim-id "$MIP_VERIFIER_SCIM_ID" \
      --expected-inventory-principal "$DEPLOY_INVENTORY_PRINCIPAL" || failed=1
  fi
  if [[ "${APP_UPGRADE_STATE:-first_install}" != \
        "green_captured_cleanup_pending" ]]; then
    if [[ "$APP_SIGNED_BLUE_AVAILABLE" -eq 1 ]]; then
      if [[ -z "${MIP_APP_ROLLBACK_GATEWAY_ENDPOINT:-}" || \
            -z "${MIP_APP_ROLLBACK_GATEWAY_INFERENCE_TABLE_PREFIX:-}" ]]; then
        echo "${RED}[deploy] signed-blue verifier compensation lacks its Gateway proof binding.${RST}" >&2
        failed=1
      else
        run_with_account_identity run_with_proof_signing_authority \
          "$PYTHON" -m tools.databricks.audit_global_m2m_access \
          --app-name "${MIP_APP_NAME:?App name is required}" \
          --application-id "$DATABRICKS_VERIFIER_CLIENT_ID" \
          --expected-inventory-principal "$DEPLOY_INVENTORY_PRINCIPAL" \
          --account-id "$DATABRICKS_ACCOUNT_ID" \
          --expected-serving-permission CAN_QUERY \
          --forbid-all-genie \
          --serving-endpoint "$MIP_APP_ROLLBACK_GATEWAY_ENDPOINT" \
          --legacy-pinned-serving-endpoint \
          "$MIP_APP_ROLLBACK_GATEWAY_ENDPOINT" || failed=1
        prove_exact_verifier_boundary \
          "$MIP_APP_ROLLBACK_GATEWAY_ENDPOINT" \
          "$MIP_APP_ROLLBACK_GATEWAY_INFERENCE_TABLE_PREFIX" || failed=1
      fi
    else
      run_with_account_identity run_with_proof_signing_authority \
        "$PYTHON" -m tools.databricks.audit_global_m2m_access \
        --app-name "${MIP_APP_NAME:?App name is required}" \
        --application-id "$DATABRICKS_VERIFIER_CLIENT_ID" \
        --expected-inventory-principal "$DEPLOY_INVENTORY_PRINCIPAL" \
        --account-id "$DATABRICKS_ACCOUNT_ID" \
        --forbid-customer-serving \
        --forbid-all-genie || failed=1
      run_with_verifier_credentials \
        "$PYTHON" -m tools.databricks.verify_verifier_identity_boundary \
        --expected-application-id "$DATABRICKS_VERIFIER_CLIENT_ID" \
        --expected-inventory-principal "$DEPLOY_INVENTORY_PRINCIPAL" \
        --customer-resource-denial || failed=1
    fi
  fi
  if [[ "$failed" -eq 0 && "${APP_UPGRADE_STATE:-first_install}" != \
        "green_captured_cleanup_pending" ]]; then
    VERIFIER_GATEWAY_CUTOVER_MUTATED=0
  fi
  return "$failed"
}

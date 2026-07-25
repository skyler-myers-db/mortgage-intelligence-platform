# shellcheck shell=bash
# Agent-proxy access convergence, immutable proof, and failure compensation.
# Sourced by scripts/deploy.sh; this file must not change shell options or traps.

converge_agent_proxy_boundary() {
  local mode="${1:?agent-proxy mode is required}"
  local supervisor_id="${2:?Supervisor ID is required}"
  local supervisor_endpoint="${3:?Supervisor endpoint is required}"
  local supervisor_endpoint_id="${4:?Supervisor endpoint ID is required}"
  shift 4
  "$PYTHON" -m tools.databricks.agent_proxy_access \
    --mode "$mode" \
    --supervisor-id "$supervisor_id" \
    --supervisor-endpoint "$supervisor_endpoint" \
    --supervisor-endpoint-id "$supervisor_endpoint_id" \
    --genie-space-id "${GENIE_SPACE_ID:-$(< genie/space_id.txt)}" \
    --application-id "${DATABRICKS_AGENT_PROXY_CLIENT_ID:?agent-proxy identity is required}" \
    --runtime-application-id "${DATABRICKS_AGENT_RUNTIME_CLIENT_ID:?agent-runtime identity is required}" \
    --expected-inventory-principal "${DEPLOY_INVENTORY_PRINCIPAL:?inventory principal is required}" \
    "$@"
}

refresh_signed_blue_binding() {
  local refreshed_env
  refreshed_env="$(mktemp -t mip-app-blue-binding-refresh.XXXXXX.env)"
  if ! run_with_account_identity \
    run_with_proof_signing_authority \
      "$PYTHON" -m tools.databricks.app_deployment_rollback inspect \
    --app-name "$APP_FAIL_CLOSED_NAME" \
    --scope "$APP_ROLLBACK_SECRET_SCOPE" \
    --base-url "${MIP_APP_URL:?App URL is required for signed rollback inspection}" \
    --token-env MIP_BEARER_TOKEN \
    --out-env "$refreshed_env"; then
    rm -f "$refreshed_env"
    return 1
  fi
  set -a
  # shellcheck disable=SC1090
  . "$refreshed_env"
  set +a
  rm -f "$refreshed_env"
}

converge_signed_blue_agent_proxy_boundary() {
  case "$MIP_APP_ROLLBACK_PROXY_MODE" in
    legacy-proxyless)
      [[ "$MIP_APP_ROLLBACK_RECORD_VERSION" == "5" && \
         -n "$MIP_APP_ROLLBACK_DEPLOYMENT_ID" ]] || return 1
      deny_all_agent_proxy_access
      ;;
    exact-proxy)
      if [[ "$MIP_APP_ROLLBACK_RECORD_VERSION" != "6" || \
            -z "$MIP_APP_ROLLBACK_DEPLOYMENT_ID" || \
            -z "$MIP_APP_ROLLBACK_SUPERVISOR_ID" || \
            -z "$MIP_APP_ROLLBACK_SUPERVISOR_ENDPOINT" || \
            -z "$MIP_APP_ROLLBACK_SUPERVISOR_ENDPOINT_ID" || \
            -z "$MIP_APP_ROLLBACK_GENIE_SPACE_ID" || \
            -z "$MIP_APP_ROLLBACK_PROXY_APPLICATION_ID" || \
            "$MIP_APP_ROLLBACK_SUPERVISOR_CREATOR" != \
              "${DATABRICKS_AGENT_RUNTIME_CLIENT_ID:-}" || \
            "$MIP_APP_ROLLBACK_RUNTIME_APPLICATION_ID" != \
              "${DATABRICKS_AGENT_RUNTIME_CLIENT_ID:-}" || \
            "$MIP_APP_ROLLBACK_PROXY_APPLICATION_ID" != \
              "${DATABRICKS_AGENT_PROXY_CLIENT_ID:-}" || \
            "$MIP_APP_ROLLBACK_GENIE_SPACE_ID" != \
              "${GENIE_SPACE_ID:-$(< genie/space_id.txt)}" ]]; then
        echo "${RED}[deploy] signed-blue exact proxy binding is incomplete or drifted.${RST}" >&2
        return 1
      fi
      converge_agent_proxy_boundary \
        converge \
        "$MIP_APP_ROLLBACK_SUPERVISOR_ID" \
        "$MIP_APP_ROLLBACK_SUPERVISOR_ENDPOINT" \
        "$MIP_APP_ROLLBACK_SUPERVISOR_ENDPOINT_ID" \
        --legacy-pinned-supervisor-endpoint \
        "$MIP_APP_ROLLBACK_SUPERVISOR_ENDPOINT"
      ;;
    *)
      echo "${RED}[deploy] signed-blue proxy rollback mode is invalid.${RST}" >&2
      return 1
      ;;
  esac
}

deny_all_agent_proxy_access() {
  local failed=0
  "$PYTHON" -m tools.databricks.agent_proxy_access \
    --mode deny-all \
    --application-id "${DATABRICKS_AGENT_PROXY_CLIENT_ID:?agent-proxy identity is required}" \
    --expected-inventory-principal "${DEPLOY_INVENTORY_PRINCIPAL:?inventory principal is required}" \
    || failed=1
  run_with_account_identity \
    run_with_agent_proxy_credentials \
      "$PYTHON" -m tools.databricks.verify_agent_proxy_identity_boundary \
    --expected-application-id "$DATABRICKS_AGENT_PROXY_CLIENT_ID" \
    --expected-inventory-principal "$DEPLOY_INVENTORY_PRINCIPAL" \
    --account-id "$DATABRICKS_ACCOUNT_ID" \
    --customer-resource-denial || failed=1
  return "$failed"
}

prove_exact_agent_proxy_boundary() {
  local supervisor_id="${1:?Supervisor ID is required for proxy proof}"
  local supervisor_endpoint="${2:?Supervisor endpoint is required for proxy proof}"
  local supervisor_endpoint_id="${3:?Supervisor endpoint ID is required for proxy proof}"
  shift 3
  run_with_account_identity \
    run_with_agent_proxy_credentials \
      "$PYTHON" -m tools.databricks.verify_agent_proxy_identity_boundary \
    --expected-application-id "$DATABRICKS_AGENT_PROXY_CLIENT_ID" \
    --account-host "$DATABRICKS_ACCOUNT_HOST" \
    --account-id "$DATABRICKS_ACCOUNT_ID" \
    --app-name "$APP_FAIL_CLOSED_NAME" \
    --app-url "${MIP_APP_URL:?App URL is required for exact proxy proof}" \
    --lakebase-instance "$MIP_LAKEBASE_INSTANCE" \
    --warehouse-id "$_GRANTS_WAREHOUSE_ID" \
    --supervisor-id "$supervisor_id" \
    --supervisor-endpoint "$supervisor_endpoint" \
    --supervisor-endpoint-id "$supervisor_endpoint_id" \
    --genie-space-id "${GENIE_SPACE_ID:-$(< genie/space_id.txt)}" \
    --allow-attested-app-401 \
    "$@"
}

compensate_agent_proxy_access() {
  [[ "$DRY_RUN" -eq 0 && "$AGENT_PROXY_ACCESS_MUTATED" -eq 1 ]] || return 0
  local old_supervisor_mode="none" captured_boundary_mode="converge"
  local -a captured_preserve_args=()
  case "${APP_UPGRADE_STATE:-first_install}" in
    green_captured_cleanup_pending)
      load_captured_live_old_resources || return 1
      if [[ "$CAPTURED_OLD_SUPERVISOR_LIVE" -eq 1 ]]; then
        old_supervisor_mode="$(pinned_query_access_mode \
          "$DATABRICKS_AGENT_PROXY_CLIENT_ID" \
          "$MIP_REPLACED_AGENT_SUPERVISOR_ENDPOINT")" || return 1
        if [[ "$old_supervisor_mode" == "direct" || \
              "$old_supervisor_mode" == "mixed" ]]; then
          captured_boundary_mode="audit"
          captured_preserve_args=(
            --preserve-supervisor-id "$MIP_REPLACED_AGENT_SUPERVISOR_ID"
            --preserve-supervisor-endpoint "$MIP_REPLACED_AGENT_SUPERVISOR_ENDPOINT"
            --preserve-supervisor-endpoint-id "$MIP_REPLACED_AGENT_SUPERVISOR_ENDPOINT_ID"
            --legacy-pinned-supervisor-endpoint "$MIP_REPLACED_AGENT_SUPERVISOR_ENDPOINT"
          )
        fi
      fi
      converge_agent_proxy_boundary \
        "$captured_boundary_mode" \
        "${MIP_AGENT_SUPERVISOR_ID:?green Supervisor ID is required}" \
        "${MIP_AGENT_SUPERVISOR_ENDPOINT:?green Supervisor endpoint is required}" \
        "${MIP_AGENT_SUPERVISOR_ENDPOINT_ID:?green Supervisor endpoint ID is required}" \
        "${captured_preserve_args[@]}" || return 1
      prove_exact_agent_proxy_boundary \
        "$MIP_AGENT_SUPERVISOR_ID" \
        "$MIP_AGENT_SUPERVISOR_ENDPOINT" \
        "$MIP_AGENT_SUPERVISOR_ENDPOINT_ID" \
        "${captured_preserve_args[@]:0:6}" || return 1
      CAPTURED_PROXY_BOUNDARY_PROVEN=1
      ;;
    green_verified)
      converge_agent_proxy_boundary \
        converge \
        "${MIP_AGENT_SUPERVISOR_ID:?green Supervisor ID is required}" \
        "${MIP_AGENT_SUPERVISOR_ENDPOINT:?green Supervisor endpoint is required}" \
        "${MIP_AGENT_SUPERVISOR_ENDPOINT_ID:?green Supervisor endpoint ID is required}" || return 1
      prove_exact_agent_proxy_boundary \
        "$MIP_AGENT_SUPERVISOR_ID" \
        "$MIP_AGENT_SUPERVISOR_ENDPOINT" \
        "$MIP_AGENT_SUPERVISOR_ENDPOINT_ID" || return 1
      ;;
    *)
      if [[ "${APP_SIGNED_BLUE_AVAILABLE:-0}" -eq 1 ]]; then
        refresh_signed_blue_binding || return 1
        converge_signed_blue_agent_proxy_boundary || return 1
        if [[ "$MIP_APP_ROLLBACK_PROXY_MODE" == "exact-proxy" ]]; then
          prove_exact_agent_proxy_boundary \
            "$MIP_APP_ROLLBACK_SUPERVISOR_ID" \
            "$MIP_APP_ROLLBACK_SUPERVISOR_ENDPOINT" \
            "$MIP_APP_ROLLBACK_SUPERVISOR_ENDPOINT_ID" || return 1
        fi
      else
        deny_all_agent_proxy_access || return 1
      fi
      ;;
  esac
  if [[ "${APP_UPGRADE_STATE:-first_install}" != \
        "green_captured_cleanup_pending" ]]; then
    AGENT_PROXY_ACCESS_MUTATED=0
  fi
}

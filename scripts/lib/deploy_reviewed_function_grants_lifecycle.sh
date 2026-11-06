# shellcheck shell=bash
# The governed UC grant statement and the reviewed-function EXECUTE reconcile:
# moved verbatim out of scripts/lib/deploy_step_lakebase_and_uc_grants.sh so
# the data phase (M1-PR6, deploy_governed_phases_lifecycle.sh) runs the same
# reconcile and read-only verify after every gold-touching job (attack O11).
# Sourced by scripts/deploy.sh; this file must not change shell options or traps.
# shellcheck disable=SC2034  # REVIEWED_FUNCTION_GRANTS_PROVEN is deploy-wide state the EXIT trap reads.

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
# The consumers are the tier's (m2m_identity_contract.required_roles): the App,
# plus the agent runtime and the Supervisor proxy in the agentic tier only.
reconcile_reviewed_function_execute_grants() {
  local _function_name
  local _principal
  local _grant_failed=0
  local _postflight_failed=0
  local -a _consumers=("$APP_SP_CLIENT_ID")
  local -a _verify_args=(--app-application-id "$APP_SP_CLIENT_ID" --agentic-tier off)
  if tier_is_agentic; then
    _consumers+=("$DATABRICKS_AGENT_RUNTIME_CLIENT_ID" "$DATABRICKS_AGENT_PROXY_CLIENT_ID")
    _verify_args=(
      --app-application-id "$APP_SP_CLIENT_ID"
      --agent-runtime-application-id "$DATABRICKS_AGENT_RUNTIME_CLIENT_ID"
      --agent-proxy-application-id "$DATABRICKS_AGENT_PROXY_CLIENT_ID"
    )
  fi
  for _principal in "${_consumers[@]}"; do
    for _function_name in fn_build_cohort fn_segment_counts fn_lead_queue_url; do
      if ! apply_uc_grant \
        "GRANT EXECUTE ON FUNCTION ${_GRANTS_CATALOG}.gold.${_function_name} TO \`${_principal}\`"; then
        _grant_failed=1
      fi
    done
  done
  if ! run "$PYTHON" -m tools.databricks.verify_reviewed_function_execute_grants \
    --catalog "$_GRANTS_CATALOG" \
    "${_verify_args[@]}"; then
    _postflight_failed=1
  fi
  if [[ "$_grant_failed" -ne 0 || "$_postflight_failed" -ne 0 ]]; then
    echo "${RED}[deploy] reviewed function EXECUTE reconciliation is unproven.${RST}" >&2
    return 4
  fi
  REVIEWED_FUNCTION_GRANTS_PROVEN=1
}

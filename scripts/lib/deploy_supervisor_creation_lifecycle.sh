# shellcheck shell=bash
# Signed managed-Supervisor creation lifecycle. Sourced by scripts/deploy.sh.

set_supervisor_creation_common_args() {
  SUPERVISOR_CREATION_COMMON_ARGS=(
    --app-name "$_GRANTS_APP_NAME" \
    --runtime-application-id "$DATABRICKS_AGENT_RUNTIME_CLIENT_ID" \
    --deployment-lease-id "$MIP_APP_DEPLOYMENT_LEASE_ID" \
    --deployment-source-git-sha "$SOURCE_GIT_SHA"
  )
}

supervisor_creation_status() {
  local destination="$1"
  set_supervisor_creation_common_args
  run "$PYTHON" -m tools.databricks.supervisor_creation_control status \
    "${SUPERVISOR_CREATION_COMMON_ARGS[@]}" \
    --out-json "$destination"
}

recover_pending_supervisor_creation() {
  [[ "$DRY_RUN" -eq 0 ]] || return 0
  local status_json status completion_json policy_json policy
  status_json="$(mktemp -t mip-supervisor-creation-status.XXXXXX.json)"
  completion_json="$(mktemp -t mip-supervisor-creation-complete.XXXXXX.json)"
  policy_json="$(mktemp -t mip-supervisor-creation-policy.XXXXXX.json)"
  set_supervisor_creation_common_args
  supervisor_creation_status "$status_json" || return 1
  status="$("$PYTHON" -c \
    'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["status"])' \
    "$status_json")" || return 1
  [[ "$status" != "absent" ]] || return 0

  step "adopt signed pending Supervisor creation into the held recovery lease"
  run_with_proof_signing_authority \
    "$PYTHON" -m tools.databricks.supervisor_creation_control adopt \
    "${SUPERVISOR_CREATION_COMMON_ARGS[@]}" \
    --canonical-name "${MIP_AGENT_SUPERVISOR_NAME:-Mortgage Growth Agent}" \
    --genie-space-id "${GENIE_SPACE_ID:-$(< genie/space_id.txt)}" \
    --catalog "${MIP_DEFAULT_CATALOG:-mip}" || return 1
  supervisor_creation_status "$status_json" || return 1
  status="$("$PYTHON" -c \
    'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["status"])' \
    "$status_json")" || return 1
  if [[ "$status" == "intent" ]]; then
    step "recover ambiguous Supervisor creation from authoritative system audit"
    run_with_proof_signing_authority \
      "$PYTHON" -m tools.databricks.supervisor_creation_control recover \
      "${SUPERVISOR_CREATION_COMMON_ARGS[@]}" \
      --warehouse-id "$_GRANTS_WAREHOUSE_ID" || return 1
    supervisor_creation_status "$status_json" || return 1
    status="$("$PYTHON" -c \
      'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["status"])' \
      "$status_json")" || return 1
  fi
  [[ "$status" != "absent" ]] || return 0
  if [[ "$status" != "claimed" ]]; then
    echo "${RED}[deploy] pending Supervisor creation has an invalid state.${RST}" >&2
    return 1
  fi
  step "classify the signed Supervisor intent against current reviewed policy"
  run "$PYTHON" -m tools.databricks.supervisor_creation_control classify-policy \
    "${SUPERVISOR_CREATION_COMMON_ARGS[@]}" \
    --canonical-name "${MIP_AGENT_SUPERVISOR_NAME:-Mortgage Growth Agent}" \
    --genie-space-id "${GENIE_SPACE_ID:-$(< genie/space_id.txt)}" \
    --catalog "${MIP_DEFAULT_CATALOG:-mip}" \
    --out-json "$policy_json" || return 1
  policy="$("$PYTHON" -c \
    'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["policy"])' \
    "$policy_json")" || return 1
  case "$policy" in
    current)
      ;;
    historical)
      step "defer the revoked historical Supervisor tuple to exact cleanup"
      return 0
      ;;
    *)
      echo "${RED}[deploy] Supervisor creation policy classification is invalid.${RST}" >&2
      return 1
      ;;
  esac
  step "complete only missing tools on the claimed Supervisor creation tuple"
  run_as_m2m_identity \
    agent-runtime \
    DATABRICKS_AGENT_RUNTIME_CLIENT_ID \
    DATABRICKS_AGENT_RUNTIME_CLIENT_SECRET \
    "$PYTHON" -m tools.databricks.supervisor_creation_runtime complete \
    "${SUPERVISOR_CREATION_COMMON_ARGS[@]}" \
    --canonical-name "${MIP_AGENT_SUPERVISOR_NAME:-Mortgage Growth Agent}" \
    --genie-space-id "${GENIE_SPACE_ID:-$(< genie/space_id.txt)}" \
    --catalog "${MIP_DEFAULT_CATALOG:-mip}" \
    --out-json "$completion_json" || return 1
  step "prove full Supervisor contract and retain the journal through binding handoff"
  run_with_proof_signing_authority \
    "$PYTHON" -m tools.databricks.supervisor_creation_control verify-complete \
    "${SUPERVISOR_CREATION_COMMON_ARGS[@]}" || return 1
}

create_planned_supervisor_if_needed() {
  [[ "$DRY_RUN" -eq 0 ]] || return 0
  local finalization_json plan_json action result_json completion_json
  finalization_json="$(mktemp -t mip-supervisor-blue-finalization.XXXXXX.json)"
  plan_json="$(mktemp -t mip-supervisor-creation-plan.XXXXXX.json)"
  result_json="$(mktemp -t mip-supervisor-creation-result.XXXXXX.json)"
  completion_json="$(mktemp -t mip-supervisor-creation-complete.XXXXXX.json)"
  set_supervisor_creation_common_args
  step "finalize only an exact signed-blue Supervisor predecessor before planning"
  MIP_CUTOVER_SIGNED_BLUE_SUPERVISOR_PIN_JSON="${MIP_APP_ROLLBACK_SUPERVISOR_PIN_JSON:-}" \
    run_as_m2m_identity \
      agent-runtime \
      DATABRICKS_AGENT_RUNTIME_CLIENT_ID \
      DATABRICKS_AGENT_RUNTIME_CLIENT_SECRET \
      "$PYTHON" -m tools.databricks.supervisor_creation_runtime finalize-signed-blue \
      "${SUPERVISOR_CREATION_COMMON_ARGS[@]}" \
      --canonical-name "${MIP_AGENT_SUPERVISOR_NAME:-Mortgage Growth Agent}" \
      --genie-space-id "${GENIE_SPACE_ID:-$(< genie/space_id.txt)}" \
      --catalog "${MIP_DEFAULT_CATALOG:-mip}" \
      --proxy-application-id "$DATABRICKS_AGENT_PROXY_CLIENT_ID" \
      --approved-query-application-id "$APP_SP_CLIENT_ID" \
      --out-json "$finalization_json" || return 1
  step "plan and sign any required managed-Supervisor creation intent"
  run_with_proof_signing_authority \
    "$PYTHON" -m tools.databricks.supervisor_creation_control plan-prepare \
    "${SUPERVISOR_CREATION_COMMON_ARGS[@]}" \
    --canonical-name "${MIP_AGENT_SUPERVISOR_NAME:-Mortgage Growth Agent}" \
    --genie-space-id "${GENIE_SPACE_ID:-$(< genie/space_id.txt)}" \
    --catalog "${MIP_DEFAULT_CATALOG:-mip}" \
    --proxy-application-id "$DATABRICKS_AGENT_PROXY_CLIENT_ID" \
    --approved-query-application-id "$APP_SP_CLIENT_ID" \
    --out-json "$plan_json" || return 1
  action="$("$PYTHON" -c \
    'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["action"])' \
    "$plan_json")" || return 1
  case "$action" in
    reuse)
      return 0
      ;;
    create)
      ;;
    resume)
      recover_pending_supervisor_creation
      return $?
      ;;
    handoff_required)
      echo "${RED}[deploy] revoked Supervisor creation was not handed off to cleanup.${RST}" >&2
      return 1
      ;;
    *)
      echo "${RED}[deploy] Supervisor creation planner returned an invalid action.${RST}" >&2
      return 1
      ;;
  esac
  step "create only the UUID-marked Supervisor authorized by signed intent"
  run_as_m2m_identity \
    agent-runtime \
    DATABRICKS_AGENT_RUNTIME_CLIENT_ID \
    DATABRICKS_AGENT_RUNTIME_CLIENT_SECRET \
    "$PYTHON" -m tools.databricks.supervisor_creation_runtime create \
    "${SUPERVISOR_CREATION_COMMON_ARGS[@]}" \
    --canonical-name "${MIP_AGENT_SUPERVISOR_NAME:-Mortgage Growth Agent}" \
    --genie-space-id "${GENIE_SPACE_ID:-$(< genie/space_id.txt)}" \
    --catalog "${MIP_DEFAULT_CATALOG:-mip}" \
    --out-json "$result_json" || return 1
  step "claim the exact created Supervisor tuple under proof authority"
  run_with_proof_signing_authority \
    "$PYTHON" -m tools.databricks.supervisor_creation_control claim-result \
    "${SUPERVISOR_CREATION_COMMON_ARGS[@]}" \
    --result-json "$result_json" || return 1
  step "complete reviewed tools and deterministic Supervisor name under runtime authority"
  run_as_m2m_identity \
    agent-runtime \
    DATABRICKS_AGENT_RUNTIME_CLIENT_ID \
    DATABRICKS_AGENT_RUNTIME_CLIENT_SECRET \
    "$PYTHON" -m tools.databricks.supervisor_creation_runtime complete \
    "${SUPERVISOR_CREATION_COMMON_ARGS[@]}" \
    --canonical-name "${MIP_AGENT_SUPERVISOR_NAME:-Mortgage Growth Agent}" \
    --genie-space-id "${GENIE_SPACE_ID:-$(< genie/space_id.txt)}" \
    --catalog "${MIP_DEFAULT_CATALOG:-mip}" \
    --out-json "$completion_json" || return 1
  step "prove full Supervisor contract and retain the journal through binding handoff"
  run_with_proof_signing_authority \
    "$PYTHON" -m tools.databricks.supervisor_creation_control verify-complete \
    "${SUPERVISOR_CREATION_COMMON_ARGS[@]}" || return 1
}

finalize_supervisor_creation_handoff() {
  [[ "$DRY_RUN" -eq 0 ]] || return 0
  local status_json status
  status_json="$(mktemp -t mip-supervisor-creation-handoff.XXXXXX.json)"
  set_supervisor_creation_common_args
  supervisor_creation_status "$status_json" || return 1
  status="$("$PYTHON" -c \
    'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["status"])' \
    "$status_json")" || return 1
  [[ "$status" != "absent" ]] || return 0
  if [[ "$status" != "claimed" ]]; then
    echo "${RED}[deploy] Supervisor creation handoff has an invalid state.${RST}" >&2
    return 1
  fi
  step "clear the signed Supervisor creation journal after exact binding handoff"
  run_with_proof_signing_authority \
    "$PYTHON" -m tools.databricks.supervisor_creation_control complete \
    "${SUPERVISOR_CREATION_COMMON_ARGS[@]}" || return 1
}

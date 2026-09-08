# shellcheck shell=bash
# Signed-blue restore, candidate quiesce/stop, captured App and Gateway ACL
# convergence, failed-deploy compensation, and bootstrap-grant revocation.
# Sourced by scripts/deploy.sh; this file must not change shell options or traps.
# shellcheck disable=SC2034  # CAPTURED_APP_BOUNDARY_PROVEN is deploy-wide state read by later step files.

restore_signed_blue_while_quiesced() {
  [[ "$APP_SIGNED_BLUE_AVAILABLE" -eq 1 ]] || return 1
  [[ "${LAKEBASE_RUNTIME_ACCESS_PROVEN:-0}" -eq 1 ]] || return 1
  local endpoint
  local -a revoke_args=()
  if declare -F mint_m2m_token >/dev/null; then
    mint_signed_app_proof_token || return 1
  fi
  refresh_signed_blue_binding || return 1
  converge_signed_blue_agent_proxy_boundary || return 1
  if [[ "$MIP_APP_ROLLBACK_PROXY_MODE" == "exact-proxy" ]]; then
    prove_exact_agent_proxy_boundary \
      "$MIP_APP_ROLLBACK_SUPERVISOR_ID" \
      "$MIP_APP_ROLLBACK_SUPERVISOR_ENDPOINT" \
      "$MIP_APP_ROLLBACK_SUPERVISOR_ENDPOINT_ID" || return 1
  fi
  for endpoint in "${PREACTIVATION_APP_REVOKE_ENDPOINTS[@]}"; do
    if [[ -n "$endpoint" && \
          "$endpoint" != "$MIP_APP_ROLLBACK_GATEWAY_ENDPOINT" && \
          "$endpoint" != "$MIP_APP_ROLLBACK_SUPERVISOR_ENDPOINT" ]]; then
      revoke_args+=(--revoke-endpoint "$endpoint")
    fi
  done
  run_with_account_identity \
    run_with_proof_signing_authority \
      "$PYTHON" -m tools.databricks.app_deployment_rollback restore \
    --app-name "$APP_FAIL_CLOSED_NAME" \
    --scope "$APP_ROLLBACK_SECRET_SCOPE" \
    --base-url "${MIP_APP_URL:?App URL is required for exact rollback proof}" \
    --token-env MIP_BEARER_TOKEN \
    --deployment-lease-id "$MIP_APP_DEPLOYMENT_LEASE_ID" \
    --deployment-source-git-sha "$SOURCE_GIT_SHA" \
    --treatment-warehouse-id "$_GRANTS_WAREHOUSE_ID" \
    --treatment-catalog "$_GRANTS_CATALOG" \
    --expected-rollback-deployment-id "$MIP_APP_ROLLBACK_DEPLOYMENT_ID" \
    "${revoke_args[@]}" || return 1
  converge_runtime_app_release_access || return 1
  converge_app_treatment_access runtime || return 1
  APP_ACCESS_QUARANTINED=0
  TREATMENT_RUNTIME_QUIESCED=0
  PREACTIVATION_APP_REVOKE_ENDPOINTS=()
  PREACTIVATION_APP_ACL_MUTATED=0
  APP_UPGRADE_STATE="blue_active"
}

compensate_preactivation_app_acl() {
  [[ "$DRY_RUN" -eq 0 && "$PREACTIVATION_APP_ACL_MUTATED" -eq 1 ]] || return 0
  if [[ "${APP_UPGRADE_STATE:-first_install}" != "blue_active" ]]; then
    return 0
  fi
  restore_signed_blue_while_quiesced
}

stop_and_quiesce_unproven_app() {
  local failed=0 outcome_file outcome_line="" extra_line="" outcome="" principal app_json
  local -a stop_identity_args=()
  if [[ "${FIRST_INSTALL_COMPENSATION_AUTHORIZED:-0}" -eq 1 ]]; then
    stop_identity_args=(
      --expected-app-id "${FIRST_INSTALL_APP_ID:?claimed first-install App ID is required}"
      --expected-client-id "${FIRST_INSTALL_APP_CLIENT_ID:?claimed App client ID is required}"
      --expected-scim-id "${FIRST_INSTALL_APP_SCIM_ID:?claimed App SCIM ID is required}"
    )
  elif [[ "${#APP_EXPECTED_IDENTITY_ARGS[@]}" -gt 0 ]]; then
    stop_identity_args=("${APP_EXPECTED_IDENTITY_ARGS[@]}")
  fi
  outcome_file="$(mktemp -t mip-app-stop-outcome.XXXXXX.env)"
  chmod 600 "$outcome_file"
  if ! "$PYTHON" -m tools.databricks.stop_app_fail_closed \
    --app-name "$APP_FAIL_CLOSED_NAME" \
    "${stop_identity_args[@]}" \
    --out-env "$outcome_file"; then
    rm -f "$outcome_file"
    return 1
  fi
  {
    IFS= read -r outcome_line || true
    if IFS= read -r extra_line || [[ -n "$extra_line" ]]; then
      outcome_line=""
    fi
  } < "$outcome_file"
  rm -f "$outcome_file"
  case "$outcome_line" in
    MIP_APP_STOP_OUTCOME=absent) outcome="absent" ;;
    MIP_APP_STOP_OUTCOME=stopped) outcome="stopped" ;;
  esac
  if [[ -z "$outcome" ]]; then
    echo "${RED}[deploy] fail-closed App stop returned no authenticated outcome.${RST}" >&2
    return 1
  fi
  # Authoritative absence proves that no target App identity can write the
  # treatment table. Requiring a nonexistent principal here turns a safe
  # first-install bundle failure into an unprovable secondary failure.
  if [[ "$outcome" == "absent" ]]; then
    TREATMENT_RUNTIME_QUIESCED=1
    return 0
  fi
  if [[ "${APP_ACCESS_QUARANTINED:-0}" -eq 1 ]]; then
    if "$PYTHON" -m tools.databricks.converge_app_release_access \
      --mode quarantine \
      --app-name "$APP_FAIL_CLOSED_NAME" \
      --release-probe-application-id "$DATABRICKS_RELEASE_PROBE_CLIENT_ID" \
      --normal-application-id "$DATABRICKS_CLIENT_ID" \
      --operator2-application-id "$DATABRICKS_OPERATOR2_CLIENT_ID" \
      --admin-application-id "$DATABRICKS_ADMIN_CLIENT_ID" \
      "${APP_EXPECTED_IDENTITY_ARGS[@]}"; then
      APP_ACCESS_QUARANTINED=1
    else
      echo "${RED}[deploy] failed to re-quarantine temporary App release access.${RST}" >&2
      failed=1
    fi
  fi
  principal="${APP_SP_CLIENT_ID:-${_EXISTING_APP_SP_CLIENT_ID:-}}"
  if [[ -z "$principal" ]]; then
    app_json="$(databricks apps get "$APP_FAIL_CLOSED_NAME" -o json 2>/dev/null || true)"
    if [[ -n "$app_json" ]]; then
      principal="$(printf '%s' "$app_json" | "$PYTHON" -c '
import json, sys
print((json.load(sys.stdin).get("service_principal_client_id") or "").strip())
' 2>/dev/null || true)"
    fi
    APP_SP_CLIENT_ID="$principal"
  fi
  if converge_app_treatment_access quiesce; then
    TREATMENT_RUNTIME_QUIESCED=1
  else
    failed=1
  fi
  return "$failed"
}

converge_captured_app_gateway_acl() {
  local old_gateway="${1:-}"
  local old_gateway_id="${2:-}"
  local old_gateway_creator="${3:-}"
  run_with_proof_signing_authority "$PYTHON" - \
    "$APP_FAIL_CLOSED_NAME" \
    "$MIP_APP_DEPLOYMENT_LEASE_ID" \
    "$SOURCE_GIT_SHA" \
    "${MIP_AI_GATEWAY_ENDPOINT:?green Gateway is required}" \
    "${MIP_AGENT_SUPERVISOR_ENDPOINT:?green Supervisor is required}" \
    "$old_gateway" \
    "$old_gateway_id" \
    "$old_gateway_creator" <<'PYEOF'
import sys

from databricks.sdk import WorkspaceClient
from tools.databricks import app_deployment_lease
from tools.databricks.app_gateway_access_mode import (
    app_service_principal_identity,
    inspect_app_gateway_access_mode,
)
from tools.databricks.provision_agentic_resources import (
    _converge_app_gateway_permissions,
)
(
    app_name,
    lease_id,
    source_sha,
    green_gateway,
    green_supervisor,
    old_gateway,
    old_gateway_id,
    old_gateway_creator,
) = sys.argv[1:]
workspace = WorkspaceClient()
lease = app_deployment_lease.held_assertion(
    workspace,
    app_name=app_name,
    lease_id=lease_id,
    source_git_sha=source_sha,
)
lease()
preserve = (old_gateway,) if old_gateway else ()
_converge_app_gateway_permissions(
    workspace,
    gateway_endpoint=green_gateway,
    supervisor_endpoint=green_supervisor,
    app_name=app_name,
    deployment_lease_id=lease_id,
    deployment_source_git_sha=source_sha,
    preserve_endpoints=preserve,
    assert_single_writer=lease,
)
if old_gateway:
    details = workspace.serving_endpoints.get(old_gateway)
    actual = (
        str(getattr(details, "id", "") or "").strip(),
        str(getattr(details, "creator", "") or "").strip(),
    )
    if actual != (old_gateway_id, old_gateway_creator):
        raise RuntimeError("signed old Gateway changed before App ACL compensation")
    app_client_id, app_scim_id = app_service_principal_identity(
        workspace,
        app_name=app_name,
    )
    mode = inspect_app_gateway_access_mode(
        workspace,
        app_name=app_name,
        endpoint_name=old_gateway,
        app_client_id=app_client_id,
        app_scim_id=app_scim_id,
        legacy_pinned=True,
    )
    if mode == "none":
        raise RuntimeError("old Gateway was preserved despite having no App query access")
PYEOF
}

converge_green_only_app_access() {
  local app_old_gateway_mode="none" app_old_supervisor_mode="none"
  local -a app_audit_args=()
  converge_runtime_app_release_access || return 1
  load_captured_live_old_resources || return 1
  if [[ "$CAPTURED_OLD_GATEWAY_LIVE" -eq 1 ]]; then
    app_old_gateway_mode="$(pinned_query_access_mode \
      "$APP_SP_CLIENT_ID" \
      "$MIP_REPLACED_AGENT_GATEWAY_ENDPOINT")" || return 1
  fi
  if [[ "$CAPTURED_OLD_SUPERVISOR_LIVE" -eq 1 ]]; then
    app_old_supervisor_mode="$(pinned_query_access_mode \
      "$APP_SP_CLIENT_ID" \
      "$MIP_REPLACED_AGENT_SUPERVISOR_ENDPOINT")" || return 1
  fi
  if [[ "$app_old_gateway_mode" != "none" ]]; then
    converge_captured_app_gateway_acl \
      "$MIP_REPLACED_AGENT_GATEWAY_ENDPOINT" \
      "$MIP_REPLACED_AGENT_GATEWAY_ENDPOINT_ID" \
      "$MIP_REPLACED_AGENT_GATEWAY_CREATOR" || return 1
  else
    converge_captured_app_gateway_acl || return 1
  fi
  app_audit_args=(
    -m tools.databricks.audit_global_m2m_access
    --app-name "${MIP_APP_NAME:?App name is required}" \
    --application-id "${APP_SP_CLIENT_ID:?App service principal is required}" \
    --expected-inventory-principal "${DEPLOY_INVENTORY_PRINCIPAL:?}" \
    --account-id "$DATABRICKS_ACCOUNT_ID" \
    --expected-serving-permission CAN_QUERY \
    --genie-space-id "${GENIE_SPACE_ID:-$(< genie/space_id.txt)}" \
    --serving-endpoint "$MIP_AI_GATEWAY_ENDPOINT"
  )
  if [[ "$app_old_gateway_mode" != "none" ]]; then
    app_audit_args+=(
      --serving-endpoint "$MIP_REPLACED_AGENT_GATEWAY_ENDPOINT"
      --legacy-pinned-serving-endpoint "$MIP_REPLACED_AGENT_GATEWAY_ENDPOINT"
    )
  fi
  if [[ "$app_old_supervisor_mode" != "none" ]]; then
    app_audit_args+=(
      --serving-endpoint "$MIP_REPLACED_AGENT_SUPERVISOR_ENDPOINT"
      --legacy-pinned-serving-endpoint "$MIP_REPLACED_AGENT_SUPERVISOR_ENDPOINT"
    )
  fi
  run_with_account_identity run_with_proof_signing_authority \
    "$PYTHON" "${app_audit_args[@]}" || return 1
  APP_ACCESS_QUARANTINED=0
  CAPTURED_APP_BOUNDARY_PROVEN=1
}

stop_app_after_failed_deploy() {
  [[ "$DRY_RUN" -eq 0 && "$APP_FAIL_CLOSED_ARMED" -eq 1 && \
     -n "$APP_FAIL_CLOSED_NAME" ]] || return 0
  if [[ "${FIRST_INSTALL_APP_CREATED:-0}" -eq 1 ]]; then
    if ! refresh_first_install_journal_status; then
      echo "${RED}[deploy] could not authenticate first-install App before stop compensation.${RST}" >&2
      return 1
    fi
    case "$FIRST_INSTALL_JOURNAL_STATUS" in
      recover)
        FIRST_INSTALL_COMPENSATION_AUTHORIZED=1
        ;;
      orphan_claimed)
        # Authoritative App absence needs no name-based stop or quiescence.
        FIRST_INSTALL_COMPENSATION_AUTHORIZED=1
        TREATMENT_RUNTIME_QUIESCED=1
        return 0
        ;;
      *)
        echo "${RED}[deploy] first-install stop compensation is not authorized for journal state ${FIRST_INSTALL_JOURNAL_STATUS:-unknown}.${RST}" >&2
        return 1
        ;;
    esac
  fi
  if [[ "${LAKEBASE_RUNTIME_ACCESS_PROVEN:-0}" -ne 1 ]]; then
    echo "${RED}[deploy] Lakebase runtime access is unproven; stopping and quiescing instead of restoring signed blue.${RST}" >&2
    stop_and_quiesce_unproven_app
    return $?
  fi
  if [[ "${REVIEWED_FUNCTION_GRANTS_PROVEN:-0}" -ne 1 ]]; then
    echo "${RED}[deploy] reviewed function grants are unproven; stopping and quiescing instead of restoring signed blue.${RST}" >&2
    stop_and_quiesce_unproven_app
    return $?
  fi
  case "${APP_UPGRADE_STATE:-first_install}" in
    blue_active)
      if [[ "$TREATMENT_RUNTIME_QUIESCED" -eq 1 ]]; then
        echo "${YLW}[deploy] restoring verified-blue treatment authority after a pre-activation failure.${RST}" >&2
        if converge_app_treatment_access runtime; then
          TREATMENT_RUNTIME_QUIESCED=0
          return 0
        fi
        echo "${RED}[deploy] verified-blue treatment restoration failed; stopping and quiescing.${RST}" >&2
        stop_and_quiesce_unproven_app
        return $?
      fi
      echo "${YLW}[deploy] preserving the verified blue App after a pre-activation failure.${RST}" >&2
      return 0
      ;;
    green_verified)
      echo "${YLW}[deploy] preserving the already-verified green App after a later deployment failure.${RST}" >&2
      return 0
      ;;
    green_captured_cleanup_pending)
      echo "${YLW}[deploy] green is signed; preserving every still-live journaled endpoint after cleanup failure.${RST}" >&2
      if converge_green_only_app_access; then
        return 0
      fi
      echo "${RED}[deploy] green-only App ACL convergence failed; stopping and quiescing.${RST}" >&2
      stop_and_quiesce_unproven_app || true
      # The captured release remains durable, but its signed old-resource
      # retirement is unresolved. Retain the deployment lease even after a
      # successful stop/quiesce so the next retry must resume this journal.
      return 1
      ;;
    blue_quiescing)
      echo "${YLW}[deploy] green activation did not begin; restoring verified-blue treatment authority.${RST}" >&2
      if converge_app_treatment_access runtime; then
        TREATMENT_RUNTIME_QUIESCED=0
        APP_UPGRADE_STATE="blue_active"
        return 0
      fi
      stop_and_quiesce_unproven_app
      return $?
      ;;
    blue_quiesced|green_activating_quiesced)
      echo "${YLW}[deploy] green App proof failed; restoring signed blue while treatment remains quiesced.${RST}" >&2
      if restore_signed_blue_while_quiesced; then
        return 0
      fi
      echo "${RED}[deploy] exact quiesced-blue restore failed; applying stop/quiesce compensation.${RST}" >&2
      stop_and_quiesce_unproven_app
      return $?
      ;;
    green_treatment_pending_capture)
      echo "${RED}[deploy] green capture failed after treatment restoration; stopping and quiescing before rollback.${RST}" >&2
      if ! stop_and_quiesce_unproven_app; then
        return 1
      fi
      if [[ "$APP_SIGNED_BLUE_AVAILABLE" -eq 1 ]]; then
        restore_signed_blue_while_quiesced
        return $?
      fi
      return 0
      ;;
  esac
  echo "${YLW}[deploy] deployment failed without a signed release; stopping and quiescing the App.${RST}" >&2
  stop_and_quiesce_unproven_app
}

quiesce_app_treatment_after_failed_stop() {
  if [[ "${FIRST_INSTALL_APP_CREATED:-0}" -eq 1 && \
        "${FIRST_INSTALL_COMPENSATION_AUTHORIZED:-0}" -ne 1 ]]; then
    echo "${RED}[deploy] refusing name-based treatment quiescence without an authenticated first-install App identity.${RST}" >&2
    return 1
  fi
  local principal="${APP_SP_CLIENT_ID:-${_EXISTING_APP_SP_CLIENT_ID:-}}" app_json=""
  if [[ "${#APP_EXPECTED_IDENTITY_ARGS[@]}" -gt 0 ]]; then
    if ! "$PYTHON" -m tools.databricks.stop_app_fail_closed \
      --app-name "$APP_FAIL_CLOSED_NAME" \
      "${APP_EXPECTED_IDENTITY_ARGS[@]}" \
      --assert-identity-only; then
      echo "${RED}[deploy] App identity drifted after failed stop; refusing secondary treatment mutation.${RST}" >&2
      return 1
    fi
  fi
  if [[ -z "$principal" && -n "$APP_FAIL_CLOSED_NAME" ]]; then
    app_json="$(databricks apps get "$APP_FAIL_CLOSED_NAME" -o json 2>/dev/null || true)"
    if [[ -n "$app_json" ]]; then
      principal="$(printf '%s' "$app_json" | "$PYTHON" -c '
import json, sys
print((json.load(sys.stdin).get("service_principal_client_id") or "").strip())
' 2>/dev/null || true)"
    fi
  fi
  if [[ -z "$principal" || -z "${_GRANTS_WAREHOUSE_ID:-}" || \
        -z "${_GRANTS_CATALOG:-}" ]]; then
    echo "${RED}[deploy] secondary treatment quiescence lacks a resolved App identity or warehouse.${RST}" >&2
    return 1
  fi
  echo "${YLW}[deploy] App stop is unproven; attempting secondary treatment-write quiescence.${RST}" >&2
  run_with_account_identity run_with_proof_signing_authority \
    "$PYTHON" -m tools.databricks.converge_campaign_treatment_access \
    --warehouse-id "$_GRANTS_WAREHOUSE_ID" \
    --catalog "$_GRANTS_CATALOG" \
    --principal "$principal" \
    --mode quiesce
}

revoke_agent_runtime_bootstrap_grants() {
  [[ "$DRY_RUN" -eq 0 && "$AGENT_RUNTIME_BOOTSTRAP_GRANTS_ACTIVE" -eq 1 ]] || return 0
  local statement response state schema_count failed=0
  echo "${DIM}[deploy] revoking temporary agent-runtime CREATE MODEL/TABLE grants.${RST}" >&2
  statement="SELECT COUNT(*) FROM system.information_schema.schemata WHERE catalog_name = '${_GRANTS_CATALOG//\'/\'\'}' AND schema_name = 'audit'"
  response="$(databricks api post /api/2.0/sql/statements/ --json "$(
    "$PYTHON" -c 'import json,sys; print(json.dumps({"warehouse_id": sys.argv[1], "statement": sys.argv[2], "wait_timeout": "50s", "on_wait_timeout": "CANCEL"}))' \
      "$_GRANTS_WAREHOUSE_ID" "$statement"
  )" 2>/dev/null || true)"
  state="$(printf '%s' "$response" | "$PYTHON" -c 'import json,sys; print((json.load(sys.stdin).get("status") or {}).get("state", ""))' 2>/dev/null || true)"
  schema_count="$(printf '%s' "$response" | "$PYTHON" -c 'import json,sys; rows=(json.load(sys.stdin).get("result") or {}).get("data_array", []); print(rows[0][0] if len(rows) == 1 and len(rows[0]) == 1 else "")' 2>/dev/null || true)"
  if [[ "$state" != "SUCCEEDED" || ! "$schema_count" =~ ^[0-9]+$ ]]; then
    echo "${RED}[deploy] could not determine whether the agent-runtime bootstrap schema exists.${RST}" >&2
    return 1
  fi
  if [[ "$schema_count" == "0" ]]; then
    AGENT_RUNTIME_BOOTSTRAP_GRANTS_ACTIVE=0
    return 0
  fi
  for statement in \
    "REVOKE CREATE MODEL ON SCHEMA ${_GRANTS_CATALOG}.audit FROM \`${DATABRICKS_AGENT_RUNTIME_CLIENT_ID}\`" \
    "REVOKE CREATE TABLE ON SCHEMA ${_GRANTS_CATALOG}.audit FROM \`${DATABRICKS_AGENT_RUNTIME_CLIENT_ID}\`"; do
    response="$(databricks api post /api/2.0/sql/statements/ --json "$(
      "$PYTHON" -c 'import json,sys; print(json.dumps({"warehouse_id": sys.argv[1], "statement": sys.argv[2], "wait_timeout": "50s", "on_wait_timeout": "CANCEL"}))' \
        "$_GRANTS_WAREHOUSE_ID" "$statement"
    )" 2>/dev/null || true)"
    state="$(printf '%s' "$response" | "$PYTHON" -c 'import json,sys; print((json.load(sys.stdin).get("status") or {}).get("state", ""))' 2>/dev/null || true)"
    if [[ "$state" != "SUCCEEDED" ]]; then
      echo "${RED}[deploy] failed to revoke temporary agent-runtime privilege: ${statement}${RST}" >&2
      failed=1
    fi
  done
  if [[ "$failed" -eq 0 ]]; then
    statement="SHOW GRANTS \`${DATABRICKS_AGENT_RUNTIME_CLIENT_ID}\` ON SCHEMA ${_GRANTS_CATALOG}.audit"
    response="$(databricks api post /api/2.0/sql/statements/ --json "$(
      "$PYTHON" -c 'import json,sys; print(json.dumps({"warehouse_id": sys.argv[1], "statement": sys.argv[2], "wait_timeout": "50s", "on_wait_timeout": "CANCEL"}))' \
        "$_GRANTS_WAREHOUSE_ID" "$statement"
    )" 2>/dev/null || true)"
    state="$(printf '%s' "$response" | "$PYTHON" -c 'import json,sys; print((json.load(sys.stdin).get("status") or {}).get("state", ""))' 2>/dev/null || true)"
    if [[ "$state" != "SUCCEEDED" ]] || ! printf '%s' "$response" | "$PYTHON" -c '
import json, sys
body = json.load(sys.stdin)
catalog = sys.argv[1].casefold()
for row in (body.get("result") or {}).get("data_array", []):
    cells = [str(value or "").casefold() for value in row]
    joined = "|".join(cells)
    if (("create model" in joined or "create table" in joined)
            and catalog in joined and "audit" in joined):
        raise SystemExit(1)
' "$_GRANTS_CATALOG"
    then
      echo "${RED}[deploy] temporary agent-runtime CREATE privileges remain effective.${RST}" >&2
      failed=1
    else
      AGENT_RUNTIME_BOOTSTRAP_GRANTS_ACTIVE=0
    fi
  fi
  return "$failed"
}

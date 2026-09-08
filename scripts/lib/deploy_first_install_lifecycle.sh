# shellcheck shell=bash
# First-install journal capture, refresh, recovery, and cleanup, plus the
# fail-closed EXIT compensation handler (restore_rendered_sql_fail_closed).
# Sourced by scripts/deploy.sh; this file must not change shell options or traps.
# shellcheck disable=SC2034  # Recovery here assigns deploy-wide state (APP_UPGRADE_STATE, APP_SP_CLIENT_ID, ...) read by later step files.

finalize_signed_first_install_capture() {
  # Capture makes this durable signed customer state. Change the compensation
  # boundary before journal retirement because retirement can fail after an
  # ambiguous Workspace Files delete without invalidating the signed release.
  FIRST_INSTALL_APP_CREATED=0
  FIRST_INSTALL_APP_BOUND=0
  TREATMENT_RUNTIME_QUIESCED=0
  APP_UPGRADE_STATE="green_captured_cleanup_pending"
  if [[ "$FIRST_INSTALL_JOURNAL_STATUS" != "absent" ]]; then
    step "retire signed first-install ownership journal after last-good capture"
    run_with_proof_signing_authority \
      "$PYTHON" -m tools.databricks.app_first_install_journal complete \
      --app-name "$APP_NAME" \
      --lease-id "$MIP_APP_DEPLOYMENT_LEASE_ID" \
      --source-git-sha "$SOURCE_GIT_SHA" \
      --rollback-scope "$APP_ROLLBACK_SECRET_SCOPE" \
      --lakebase-instance "$MIP_LAKEBASE_INSTANCE"
    FIRST_INSTALL_JOURNAL_STATUS="absent"
  fi
}

refresh_first_install_journal_status() {
  local status_env
  status_env="$(mktemp -t mip-app-first-install-status.XXXXXX.env)"
  chmod 600 "$status_env"
  if ! run_with_proof_signing_authority \
    "$PYTHON" -m tools.databricks.app_first_install_journal status \
    --app-name "$APP_FAIL_CLOSED_NAME" \
    --lease-id "${MIP_APP_DEPLOYMENT_LEASE_ID:?deployment lease is required}" \
    --source-git-sha "${SOURCE_GIT_SHA:?source SHA is required}" \
    --rollback-scope "$APP_ROLLBACK_SECRET_SCOPE" \
    --lakebase-instance "$MIP_LAKEBASE_INSTANCE" \
    --out-env "$status_env"; then
    rm -f "$status_env"
    return 1
  fi
  unset MIP_FIRST_INSTALL_JOURNAL_STATUS MIP_FIRST_INSTALL_APP_ID
  unset MIP_FIRST_INSTALL_APP_CLIENT_ID MIP_FIRST_INSTALL_APP_SCIM_ID
  # The producer emits shell-escaped assignments so empty values are written
  # as ''. Source the owner-only temporary file to decode those assignments;
  # raw line slicing would mistake the quoting syntax for a real identity.
  # shellcheck disable=SC1090
  . "$status_env"
  FIRST_INSTALL_JOURNAL_STATUS="${MIP_FIRST_INSTALL_JOURNAL_STATUS-}"
  FIRST_INSTALL_APP_ID="${MIP_FIRST_INSTALL_APP_ID-}"
  FIRST_INSTALL_APP_CLIENT_ID="${MIP_FIRST_INSTALL_APP_CLIENT_ID-}"
  FIRST_INSTALL_APP_SCIM_ID="${MIP_FIRST_INSTALL_APP_SCIM_ID-}"
  unset MIP_FIRST_INSTALL_JOURNAL_STATUS MIP_FIRST_INSTALL_APP_ID
  unset MIP_FIRST_INSTALL_APP_CLIENT_ID MIP_FIRST_INSTALL_APP_SCIM_ID
  rm -f "$status_env"
  if [[ ( "$FIRST_INSTALL_JOURNAL_STATUS" == "recover" || \
          "$FIRST_INSTALL_JOURNAL_STATUS" == "orphan_claimed" ) ]] && \
     [[ -z "$FIRST_INSTALL_APP_ID" || -z "$FIRST_INSTALL_APP_CLIENT_ID" || \
        -z "$FIRST_INSTALL_APP_SCIM_ID" ]]; then
    return 1
  fi
  if [[ "$FIRST_INSTALL_JOURNAL_STATUS" == "recover" ]]; then
    APP_SP_CLIENT_ID="$FIRST_INSTALL_APP_CLIENT_ID"
  fi
  [[ -n "$FIRST_INSTALL_JOURNAL_STATUS" ]]
}

recover_journaled_first_install_lakebase_bootstrap() {
  if [[ -z "$FIRST_INSTALL_APP_CLIENT_ID" ]]; then
    echo "${RED}[deploy] journaled first-install App client ID is required for Lakebase recovery.${RST}" >&2
    return 1
  fi
  run_with_lakebase_bootstrap_authority \
    "$PYTHON" -m tools.databricks.converge_lakebase_oauth_role \
    --lakebase-instance "$MIP_LAKEBASE_INSTANCE" \
    --lakebase-database "$LAKEBASE_DATABASE" \
    --application-id "$FIRST_INSTALL_APP_CLIENT_ID" \
    --role-contract app \
    --recover-bootstrap-only
}

recover_interrupted_first_install_app() {
  if [[ "$FIRST_INSTALL_JOURNAL_STATUS" != "recover" || \
        -z "$FIRST_INSTALL_APP_ID" || -z "$FIRST_INSTALL_APP_CLIENT_ID" || \
        -z "$FIRST_INSTALL_APP_SCIM_ID" || \
        "$FIRST_INSTALL_APP_CLIENT_ID" != "$_EXISTING_APP_SP_CLIENT_ID" ]]; then
    echo "${RED}[deploy] signed first-install recovery identity is incomplete or conflicts with inventory.${RST}" >&2
    return 1
  fi
  APP_FAIL_CLOSED_NAME="$_GRANTS_APP_NAME"
  step "stop journal-owned unsigned first-install App after interrupted deployment"
  run "$PYTHON" -m tools.databricks.stop_app_fail_closed \
    --app-name "$_GRANTS_APP_NAME" \
    --expected-app-id "$FIRST_INSTALL_APP_ID" \
    --expected-client-id "$FIRST_INSTALL_APP_CLIENT_ID" \
    --expected-scim-id "$FIRST_INSTALL_APP_SCIM_ID"
  step "quarantine journal-owned App access before exact recovery deletion"
  run "$PYTHON" -m tools.databricks.converge_app_release_access \
    --mode quarantine \
    --app-name "$_GRANTS_APP_NAME" \
    --release-probe-application-id "$DATABRICKS_RELEASE_PROBE_CLIENT_ID" \
    --normal-application-id "$DATABRICKS_CLIENT_ID" \
    --operator2-application-id "$DATABRICKS_OPERATOR2_CLIENT_ID" \
    --admin-application-id "$DATABRICKS_ADMIN_CLIENT_ID" \
    --expected-app-id "$FIRST_INSTALL_APP_ID" \
    --expected-client-id "$FIRST_INSTALL_APP_CLIENT_ID" \
    --expected-scim-id "$FIRST_INSTALL_APP_SCIM_ID"
  APP_ACCESS_QUARANTINED=1
  step "quiesce journal-owned App treatment authority before recovery deletion"
  run_with_account_identity run_with_proof_signing_authority \
    "$PYTHON" -m tools.databricks.converge_campaign_treatment_access \
    --warehouse-id "$_GRANTS_WAREHOUSE_ID" \
    --catalog "$_GRANTS_CATALOG" \
    --principal "$FIRST_INSTALL_APP_CLIENT_ID" \
    --mode quiesce
  TREATMENT_RUNTIME_QUIESCED=1
  step "recover journal-owned Lakebase bootstrap before App deletion"
  recover_journaled_first_install_lakebase_bootstrap
  step "normalize and remove any interrupted first-install bundle binding"
  run "$PYTHON" -m tools.databricks.bundle_env deployment bind \
    mip_app "$_GRANTS_APP_NAME" -t "$TARGET" --auto-approve
  run "$PYTHON" -m tools.databricks.bundle_env deployment unbind \
    mip_app -t "$TARGET"
  step "delete only the App authenticated by the signed first-install journal"
  run_with_proof_signing_authority \
    "$PYTHON" -m tools.databricks.app_first_install_journal delete \
    --app-name "$_GRANTS_APP_NAME" \
    --lease-id "$MIP_APP_DEPLOYMENT_LEASE_ID" \
    --source-git-sha "$SOURCE_GIT_SHA" \
    --rollback-scope "$APP_ROLLBACK_SECRET_SCOPE" \
    --lakebase-instance "$MIP_LAKEBASE_INSTANCE"
  _EXISTING_APPS_JSON="[]"
  _EXISTING_APP_ID=""
  _EXISTING_APP_SP_CLIENT_ID=""
  _EXISTING_APP_SP_SCIM_ID=""
  APP_EXPECTED_IDENTITY_ARGS=()
  FIRST_INSTALL_JOURNAL_STATUS="absent"
  FIRST_INSTALL_APP_ID=""
  FIRST_INSTALL_APP_CLIENT_ID=""
  FIRST_INSTALL_APP_SCIM_ID=""
  APP_ACCESS_QUARANTINED=0
  TREATMENT_RUNTIME_QUIESCED=0
}

cleanup_failed_first_install_app() {
  [[ "$DRY_RUN" -eq 0 && "$FIRST_INSTALL_APP_CREATED" -eq 1 ]] || return 0
  # Re-authenticate the signed journal before touching bundle state. The helper
  # refuses signed customer state and any App whose creator, marker, exact
  # resources, or immutable service-principal IDs no longer match.
  if ! refresh_first_install_journal_status; then
    echo "${RED}[deploy] could not authenticate failed first-install ownership.${RST}" >&2
    return 1
  fi
  if [[ "$FIRST_INSTALL_JOURNAL_STATUS" != "recover" && \
        "$FIRST_INSTALL_JOURNAL_STATUS" != "orphan_claimed" ]]; then
    echo "${RED}[deploy] first-install cleanup is not authorized for journal state ${FIRST_INSTALL_JOURNAL_STATUS:-unknown}.${RST}" >&2
    return 1
  fi
  echo "${YLW}[deploy] recovering journal-owned Lakebase bootstrap before failed-App cleanup.${RST}" >&2
  if ! recover_journaled_first_install_lakebase_bootstrap; then
    echo "${RED}[deploy] failed first-install Lakebase recovery did not converge; retaining App and journal.${RST}" >&2
    return 1
  fi
  # FIRST_INSTALL_APP_BOUND is armed before bind, so both pre-commit and
  # commit-then-error outcomes take this path. A failed/ambiguous unbind leaves
  # the signed journal and App intact for the next lease to reconcile.
  if [[ "$FIRST_INSTALL_APP_BOUND" -eq 1 ]]; then
    echo "${YLW}[deploy] unbinding failed first-install App from bundle state before cleanup.${RST}" >&2
    if ! "$PYTHON" -m tools.databricks.bundle_env deployment unbind \
      mip_app -t "$TARGET"; then
      echo "${RED}[deploy] failed to unbind the unverified first-install App; refusing API deletion.${RST}" >&2
      return 1
    fi
    FIRST_INSTALL_APP_BOUND=0
  fi
  echo "${YLW}[deploy] deleting only the unsigned App authenticated by the signed first-install journal.${RST}" >&2
  if ! run_with_proof_signing_authority \
    "$PYTHON" -m tools.databricks.app_first_install_journal delete \
    --app-name "$APP_FAIL_CLOSED_NAME" \
    --lease-id "${MIP_APP_DEPLOYMENT_LEASE_ID:?deployment lease is required}" \
    --source-git-sha "${SOURCE_GIT_SHA:?source SHA is required}" \
    --rollback-scope "$APP_ROLLBACK_SECRET_SCOPE" \
    --lakebase-instance "$MIP_LAKEBASE_INSTANCE"; then
    echo "${RED}[deploy] authenticated failed first-install App deletion did not converge.${RST}" >&2
    return 1
  fi
  FIRST_INSTALL_APP_CREATED=0
  FIRST_INSTALL_JOURNAL_STATUS="absent"
}

restore_rendered_sql_fail_closed() {
  local rc=$? compensation_failed=0 app_stopped=0
  if [[ "$rc" -ne 0 ]]; then
    if stop_app_after_failed_deploy; then
      app_stopped=1
    else
      compensation_failed=1
      if ! quiesce_app_treatment_after_failed_stop; then
        echo "${RED}[deploy] secondary treatment-write quiescence also failed.${RST}" >&2
      fi
    fi
    if [[ "$app_stopped" -eq 1 ]] && \
       declare -F cleanup_failed_first_install_app >/dev/null && \
       ! cleanup_failed_first_install_app; then
        compensation_failed=1
    fi
    if declare -F compensate_preactivation_app_acl >/dev/null && \
       ! compensate_preactivation_app_acl; then
      echo "${RED}[deploy] pre-activation App serving ACL compensation failed.${RST}" >&2
      compensation_failed=1
    fi
    if declare -F compensate_agent_proxy_access >/dev/null && \
       ! compensate_agent_proxy_access; then
      echo "${RED}[deploy] agent-proxy capability compensation failed.${RST}" >&2
      compensation_failed=1
    fi
    if declare -F compensate_verifier_gateway_access >/dev/null && \
       ! compensate_verifier_gateway_access; then
      echo "${RED}[deploy] verifier Gateway capability compensation failed.${RST}" >&2
      compensation_failed=1
    fi
  fi
  if declare -F revoke_agent_runtime_bootstrap_grants >/dev/null && \
     ! revoke_agent_runtime_bootstrap_grants; then
    compensation_failed=1
  fi
  if [[ "$compensation_failed" -eq 0 ]] && \
     declare -F complete_captured_runtime_retirement_journal >/dev/null && \
     ! complete_captured_runtime_retirement_journal; then
    echo "${RED}[deploy] captured runtime retirement journal completion failed.${RST}" >&2
    compensation_failed=1
  fi
  if [[ -n "${OAUTH_CREDENTIAL_QUARANTINE_FILE:-}" && \
        -s "$OAUTH_CREDENTIAL_QUARANTINE_FILE" ]]; then
    echo "${RED}[deploy] OAuth credential cleanup is unproven; retaining the signed deployment lease and durable workspace quarantine.${RST}" >&2
    compensation_failed=1
  fi
  if [[ -n "${APP_DEPLOYMENT_LEASE_HEARTBEAT_PID:-}" ]]; then
    kill "$APP_DEPLOYMENT_LEASE_HEARTBEAT_PID" 2>/dev/null || true
    wait "$APP_DEPLOYMENT_LEASE_HEARTBEAT_PID" 2>/dev/null || true
    APP_DEPLOYMENT_LEASE_HEARTBEAT_PID=""
  fi
  if [[ "$compensation_failed" -eq 0 && "$DRY_RUN" -eq 0 && \
        -n "${APP_DEPLOYMENT_LEASE_ID:-}" && \
        -n "${_GRANTS_APP_NAME:-}" ]]; then
    if ! run_with_proof_signing_authority \
      "$PYTHON" -m tools.databricks.app_deployment_lease release \
      --app-name "$_GRANTS_APP_NAME" \
      --lease-id "$APP_DEPLOYMENT_LEASE_ID"; then
      echo "${RED}[deploy] failed to release the signed workspace App deployment lease.${RST}" >&2
      compensation_failed=1
    fi
    APP_DEPLOYMENT_LEASE_ID=""
  elif [[ "$compensation_failed" -eq 1 && \
          -n "${APP_DEPLOYMENT_LEASE_ID:-}" ]]; then
    echo "${RED}[deploy] retaining the signed deployment lease until expiry because compensation is unproven.${RST}" >&2
  fi
  if [[ -n "${APP_DEPLOY_PAYLOAD:-}" ]]; then
    rm -f "$APP_DEPLOY_PAYLOAD"
  fi
  if [[ -n "${APP_LAST_DEPLOY_PAYLOAD:-}" ]]; then
    rm -f "$APP_LAST_DEPLOY_PAYLOAD"
  fi
  if [[ -n "${APP_BUNDLE_SUMMARY:-}" ]]; then
    rm -f "$APP_BUNDLE_SUMMARY"
  fi
  if [[ -n "${APP_ROLLBACK_BINDING_ENV:-}" ]]; then
    rm -f "$APP_ROLLBACK_BINDING_ENV"
  fi
  if [[ -n "${AGENTIC_ENV_FILE:-}" ]]; then
    rm -f "$AGENTIC_ENV_FILE"
  fi
  if [[ -n "${AGENT_EVAL_ENV_FILE:-}" ]]; then
    rm -f "$AGENT_EVAL_ENV_FILE"
  fi
  if [[ -n "${CUTOVER_JOURNAL_ENV_FILE:-}" ]]; then
    rm -f "$CUTOVER_JOURNAL_ENV_FILE"
  fi
  if [[ -n "${APP_DEPLOYMENT_LEASE_ENV:-}" ]]; then
    rm -f "$APP_DEPLOYMENT_LEASE_ENV"
  fi
  if [[ -n "${APP_LEASE_RECOVERY_ENV:-}" ]]; then
    rm -f "$APP_LEASE_RECOVERY_ENV"
  fi
  if [[ -n "${OAUTH_CREDENTIAL_QUARANTINE_FILE:-}" ]]; then
    rm -f "$OAUTH_CREDENTIAL_QUARANTINE_FILE"
  fi
  if [[ -n "${FOREIGN_CATALOG_BINDING_DIR:-}" ]]; then
    rm -rf "$FOREIGN_CATALOG_BINDING_DIR"
  fi
  if [[ -n "${APP_RESOURCE_BINDING_SUMMARY:-}" ]]; then
    rm -f "$APP_RESOURCE_BINDING_SUMMARY"
  fi
  if [[ -n "${APP_RESOURCE_BINDING_PAYLOAD:-}" ]]; then
    rm -f "$APP_RESOURCE_BINDING_PAYLOAD"
  fi
  if [[ -n "${APP_RESOURCE_BINDING_BEFORE:-}" ]]; then
    rm -f "$APP_RESOURCE_BINDING_BEFORE"
  fi
  if [[ -n "${APP_RESOURCE_BINDING_AFTER:-}" ]]; then
    rm -f "$APP_RESOURCE_BINDING_AFTER"
  fi
  if [[ -n "${APP_CREATE_RESULT:-}" ]]; then
    rm -f "$APP_CREATE_RESULT"
  fi
  if [[ -n "${FIRST_INSTALL_JOURNAL_ENV:-}" ]]; then
    rm -f "$FIRST_INSTALL_JOURNAL_ENV"
  fi
  if [[ -n "${VERIFIER_IDENTITY_CAPTURE_ENV:-}" ]]; then
    rm -f "$VERIFIER_IDENTITY_CAPTURE_ENV"
  fi
  if [[ -n "${HISTORICAL_ENDPOINT_INVENTORY:-}" ]]; then
    rm -f "$HISTORICAL_ENDPOINT_INVENTORY"
  fi
  if [[ -n "${HISTORICAL_CUTOVER_JOURNAL_ENV:-}" ]]; then
    rm -f "$HISTORICAL_CUTOVER_JOURNAL_ENV"
  fi
  if [[ -n "${FIRST_INSTALL_MARKED_PAYLOAD:-}" ]]; then
    rm -f "$FIRST_INSTALL_MARKED_PAYLOAD"
  fi
  if [[ -n "${_PII_SECRET_PAYLOAD:-}" ]]; then
    rm -f "$_PII_SECRET_PAYLOAD"
  fi
  if [[ "$DRY_RUN" -eq 0 && "$RESTORE_RENDERED_SQL_FAIL_CLOSED" -eq 1 ]]; then
    if MIP_ENABLE_DEMO_FIRST_PARTY_FEEDS=0 "$PYTHON" tools/render_sql.py \
      --catalog "${MIP_DEFAULT_CATALOG:-mip}" >/dev/null 2>&1; then
      echo "${DIM}[deploy] restored sql/_rendered with demo first-party feeds disabled.${RST}" >&2
    else
      echo "${YLW}[deploy] warning: failed to restore sql/_rendered with demo first-party feeds disabled.${RST}" >&2
    fi
  fi
  if [[ "$compensation_failed" -eq 1 ]]; then
    echo "${RED}[deploy] original failure was followed by unproven App shutdown or temporary-privilege revocation.${RST}" >&2
    trap - EXIT
    exit 90
  fi
  return "$rc"
}

# shellcheck shell=bash
# Deploy step file. Step 10b: Lakebase Sync proof, verifier identity capture, historical runtime
# retirement, Supervisor, proxy-secret, and Gateway provisioning and audits.
# Sourced by scripts/deploy.sh in reviewed order; it runs in the entrypoint's
# shell with its strict mode, traps, and private variables, never on its own.
# shellcheck disable=SC2034  # top-level state assigned here is read by later step files.
[[ "${BASH_SOURCE[0]}" != "$0" ]] || {
  echo "[deploy] ${BASH_SOURCE[0]} is a step file; run ./scripts/deploy.sh instead." >&2
  exit 2
}

step "prove agentic Lakebase Sync under deployer authority"
AGENTIC_ENV_FILE="$(mktemp -t mip-agentic.XXXXXX.env)"
run "$PYTHON" -m tools.databricks.provision_agentic_resources \
  --app-name "$_GRANTS_APP_NAME" \
  --deployment-lease-id "$MIP_APP_DEPLOYMENT_LEASE_ID" \
  --deployment-source-git-sha "$SOURCE_GIT_SHA" \
  --catalog "${MIP_DEFAULT_CATALOG:-mip}" \
  --genie-space-id "${GENIE_SPACE_ID:-$(< genie/space_id.txt)}" \
  --lakebase-catalog "$DEPLOYMENT_SYNC_CATALOG" \
  --lakebase-schema "$DEPLOYMENT_SYNC_SCHEMA" \
  --lakebase-sync-tables "$DEPLOYMENT_SYNC_TABLES" \
  --database-instance "$MIP_LAKEBASE_INSTANCE" \
  --logical-database "$LAKEBASE_DATABASE" \
  --capture-reviewed-function-owner \
  --skip-supervisor \
  --skip-gateway \
  --out-env "$AGENTIC_ENV_FILE"
if [[ "$DRY_RUN" -eq 0 ]]; then
  set -a
  # shellcheck disable=SC1090
  . "$AGENTIC_ENV_FILE"
  set +a
else
  MIP_REVIEWED_FUNCTION_OWNER="dry-run-reviewed-function-owner"
fi
step "converge exact app read-only access to proven Lakebase synced tables"
run "$PYTHON" -m tools.databricks.converge_app_lakebase_sync_access \
  --mode runtime \
  --warehouse-id "$_GRANTS_WAREHOUSE_ID" \
  --app-application-id "$APP_SP_CLIENT_ID" \
  --app-scim-id "$APP_SP_SCIM_ID" \
  --sync-catalog "$DEPLOYMENT_SYNC_CATALOG" \
  --sync-schema "$DEPLOYMENT_SYNC_SCHEMA" \
  --sync-tables "$DEPLOYMENT_SYNC_TABLES"
step "preflight agent-runtime foreign UC access before runtime-owned UC mutations"
run_with_account_identity run_with_proof_signing_authority \
  "$PYTHON" -m tools.databricks.audit_agent_runtime_foreign_uc_access \
  --application-id "$DATABRICKS_AGENT_RUNTIME_CLIENT_ID" \
  --catalog "${MIP_DEFAULT_CATALOG:-mip}" \
  --expected-inventory-principal "$DEPLOY_INVENTORY_PRINCIPAL"
step "grant exact Genie CAN_RUN to the dedicated agent-runtime identity"
run "$PYTHON" -m tools.databricks.agent_runtime_access \
  --genie-space-id "${GENIE_SPACE_ID:-$(< genie/space_id.txt)}" \
  --application-id "$DATABRICKS_AGENT_RUNTIME_CLIENT_ID"
step "capture the verifier immutable identity before retirement admission"
run capture_verifier_identity
HISTORICAL_ENDPOINT_PRESERVE_ARGS=()
if [[ "$APP_SIGNED_BLUE_AVAILABLE" -eq 1 ]]; then
  if [[ -z "${MIP_APP_ROLLBACK_GATEWAY_PIN_JSON:-}" || \
        -z "${MIP_APP_ROLLBACK_SUPERVISOR_PIN_JSON:-}" ]]; then
    echo "${RED}[deploy] signed blue lacks immutable runtime pins for historical preservation.${RST}" >&2
    exit 1
  fi
  HISTORICAL_ENDPOINT_PRESERVE_ARGS+=(
    --preserve-gateway-json "$MIP_APP_ROLLBACK_GATEWAY_PIN_JSON"
    --preserve-supervisor-json "$MIP_APP_ROLLBACK_SUPERVISOR_PIN_JSON"
  )
fi
HISTORICAL_CUTOVER_JOURNAL_ENV="$(mktemp -t mip-historical-cutover.XXXXXX.env)"
run_as_m2m_identity \
  agent-runtime \
  DATABRICKS_AGENT_RUNTIME_CLIENT_ID \
  DATABRICKS_AGENT_RUNTIME_CLIENT_SECRET \
  "$PYTHON" -m tools.databricks.cutover_agent_runtime_supervisor export-journal \
  --runtime-application-id "$DATABRICKS_AGENT_RUNTIME_CLIENT_ID" \
  --out-env "$HISTORICAL_CUTOVER_JOURNAL_ENV"
if [[ -s "$HISTORICAL_CUTOVER_JOURNAL_ENV" ]]; then
  HISTORICAL_CUTOVER_JOURNAL_PRESENT=1
  unset \
    MIP_REPLACED_AGENT_SUPERVISOR_ID \
    MIP_REPLACED_AGENT_SUPERVISOR_ENDPOINT \
    MIP_REPLACED_AGENT_SUPERVISOR_ENDPOINT_ID \
    MIP_REPLACED_AGENT_SUPERVISOR_CREATOR \
    MIP_REPLACED_AGENT_SUPERVISOR_CREATE_TIME \
    MIP_REPLACED_AGENT_SUPERVISOR_PIN_JSON \
    MIP_REPLACED_AGENT_GATEWAY_ENDPOINT \
    MIP_REPLACED_AGENT_GATEWAY_ENDPOINT_ID \
    MIP_REPLACED_AGENT_GATEWAY_CREATOR \
    MIP_REPLACED_AGENT_GATEWAY_DELETE_ALLOWED \
    MIP_REPLACED_AGENT_GATEWAY_PIN_JSON
  set -a
  # shellcheck disable=SC1090
  . "$HISTORICAL_CUTOVER_JOURNAL_ENV"
  set +a
  if [[ -n "${MIP_REPLACED_AGENT_GATEWAY_PIN_JSON:-}" ]]; then
    HISTORICAL_CUTOVER_GATEWAY_PRESENT=1
  fi
  if [[ "$APP_SIGNED_BLUE_AVAILABLE" -eq 1 ]]; then
    merge_historical_cutover_journal_preservation
  else
    if [[ -n "${MIP_REPLACED_AGENT_GATEWAY_PIN_JSON:-}" ]]; then
      HISTORICAL_ENDPOINT_PRESERVE_ARGS+=(
        --preserve-retirement-gateway-json \
        "$MIP_REPLACED_AGENT_GATEWAY_PIN_JSON"
      )
    fi
    if [[ -n "${MIP_REPLACED_AGENT_SUPERVISOR_PIN_JSON:-}" ]]; then
      HISTORICAL_ENDPOINT_PRESERVE_ARGS+=(
        --preserve-retirement-supervisor-json \
        "$MIP_REPLACED_AGENT_SUPERVISOR_PIN_JSON"
      )
    fi
  fi
fi
run recover_pending_supervisor_creation
if [[ "$STALE_CUTOVER_JOURNAL_PENDING" -eq 1 ]]; then
  step "resume exact stale runtime retirement under the signed-blue boundary"
  MIP_CUTOVER_SIGNED_BLUE_GATEWAY_PIN_JSON="$MIP_APP_ROLLBACK_GATEWAY_PIN_JSON" \
  MIP_CUTOVER_SIGNED_BLUE_SUPERVISOR_PIN_JSON="$MIP_APP_ROLLBACK_SUPERVISOR_PIN_JSON" \
    run "$PYTHON" -m tools.databricks.cutover_agent_runtime_supervisor \
    resume-stale-journal \
    --runtime-application-id "$DATABRICKS_AGENT_RUNTIME_CLIENT_ID" \
    --app-application-id "$APP_SP_CLIENT_ID" \
    --verifier-application-id "$DATABRICKS_VERIFIER_CLIENT_ID" \
    --verifier-scim-id "$MIP_VERIFIER_SCIM_ID" \
    --proxy-application-id "$DATABRICKS_AGENT_PROXY_CLIENT_ID" \
    --app-name "$_GRANTS_APP_NAME" \
    --deployment-lease-id "$MIP_APP_DEPLOYMENT_LEASE_ID" \
    --deployment-source-git-sha "$SOURCE_GIT_SHA"
fi
HISTORICAL_ENDPOINT_INVENTORY="$(mktemp -t mip-historical-agent-endpoints.XXXXXX.json)"
step "attest and retire every unrelated historical runtime endpoint before green provisioning"
run_with_proof_signing_authority \
  "$PYTHON" -m tools.databricks.reconcile_historical_agent_endpoints cleanup \
  --runtime-application-id "$DATABRICKS_AGENT_RUNTIME_CLIENT_ID" \
  --catalog "${MIP_DEFAULT_CATALOG:-mip}" \
  --genie-space-id "${GENIE_SPACE_ID:-$(< genie/space_id.txt)}" \
  --supervisor-name "${MIP_AGENT_SUPERVISOR_NAME:-Mortgage Growth Agent}" \
  --app-name "$_GRANTS_APP_NAME" \
  --rollback-scope "$APP_ROLLBACK_SECRET_SCOPE" \
  --deployment-lease-id "$MIP_APP_DEPLOYMENT_LEASE_ID" \
  --deployment-source-git-sha "$SOURCE_GIT_SHA" \
  --app-application-id "$APP_SP_CLIENT_ID" \
  --app-scim-id "$APP_SP_SCIM_ID" \
  --verifier-application-id "$DATABRICKS_VERIFIER_CLIENT_ID" \
  --verifier-scim-id "$MIP_VERIFIER_SCIM_ID" \
  --proxy-application-id "$DATABRICKS_AGENT_PROXY_CLIENT_ID" \
  "${HISTORICAL_ENDPOINT_PRESERVE_ARGS[@]}" \
  --out-json "$HISTORICAL_ENDPOINT_INVENTORY"
if [[ "$STALE_CUTOVER_JOURNAL_PENDING" -eq 1 ]]; then
  step "prove historical retirement and clear only the stale signed cutover journal"
  MIP_CUTOVER_SIGNED_BLUE_GATEWAY_PIN_JSON="$MIP_APP_ROLLBACK_GATEWAY_PIN_JSON" \
  MIP_CUTOVER_SIGNED_BLUE_SUPERVISOR_PIN_JSON="$MIP_APP_ROLLBACK_SUPERVISOR_PIN_JSON" \
    run_as_m2m_identity \
      agent-runtime \
      DATABRICKS_AGENT_RUNTIME_CLIENT_ID \
      DATABRICKS_AGENT_RUNTIME_CLIENT_SECRET \
      "$PYTHON" -m tools.databricks.cutover_agent_runtime_supervisor clear-journal \
    --runtime-application-id "$DATABRICKS_AGENT_RUNTIME_CLIENT_ID" \
    --app-application-id "$APP_SP_CLIENT_ID" \
    --app-scim-id "$APP_SP_SCIM_ID" \
    --verifier-application-id "$DATABRICKS_VERIFIER_CLIENT_ID" \
    --verifier-scim-id "$MIP_VERIFIER_SCIM_ID" \
    --proxy-application-id "$DATABRICKS_AGENT_PROXY_CLIENT_ID" \
    --app-name "$_GRANTS_APP_NAME" \
    --deployment-lease-id "$MIP_APP_DEPLOYMENT_LEASE_ID" \
    --deployment-source-git-sha "$SOURCE_GIT_SHA"
  STALE_CUTOVER_JOURNAL_PENDING=0
  HISTORICAL_CUTOVER_JOURNAL_PRESENT=0
fi
if [[ "$HISTORICAL_CUTOVER_JOURNAL_PRESENT" -eq 0 ]]; then
  step "archive every unprotected historical Gateway model before green provisioning"
  reconcile_gateway_model_archives
else
  step "defer Gateway model archival while the authenticated current journal protects retry"
fi
step "reconcile retry-only App access on non-blue reserved Supervisor candidates"
run reconcile_retry_supervisor_app_acl
run create_planned_supervisor_if_needed
step "provision the managed Supervisor under the dedicated agent-runtime identity"
MIP_CUTOVER_SIGNED_BLUE_SUPERVISOR_PIN_JSON="${MIP_APP_ROLLBACK_SUPERVISOR_PIN_JSON:-}" \
  MIP_ALLOW_RUNTIME_MODEL_ATTESTATION_SIGNING=1 run_as_m2m_identity \
  agent-runtime \
  DATABRICKS_AGENT_RUNTIME_CLIENT_ID \
  DATABRICKS_AGENT_RUNTIME_CLIENT_SECRET \
  "$PYTHON" -m tools.databricks.provision_agentic_resources \
  --app-name "$_GRANTS_APP_NAME" \
  --catalog "${MIP_DEFAULT_CATALOG:-mip}" \
  --genie-space-id "${GENIE_SPACE_ID:-$(< genie/space_id.txt)}" \
  --expected-runtime-application-id "$DATABRICKS_AGENT_RUNTIME_CLIENT_ID" \
  --proxy-caller-application-id "$DATABRICKS_AGENT_PROXY_CLIENT_ID" \
  --approved-query-application-id "$APP_SP_CLIENT_ID" \
  --reviewed-function-owner "$MIP_REVIEWED_FUNCTION_OWNER" \
  --deployment-lease-id "$MIP_APP_DEPLOYMENT_LEASE_ID" \
  --deployment-source-git-sha "$SOURCE_GIT_SHA" \
  --gateway-endpoint "${MIP_APP_ROLLBACK_GATEWAY_ENDPOINT:-mip-growth-agent-gateway}" \
  --gateway-agent-model "${MIP_AI_GATEWAY_AGENT_MODEL_FAMILY:-${MIP_DEFAULT_CATALOG:-mip}.audit.mortgage_growth_supervisor_proxy}" \
  --gateway-agent-experiment "${MIP_AI_GATEWAY_AGENT_EXPERIMENT_BASE:-mip-agent-runtime-gateway-proxy}" \
  --gateway-table-prefix "${MIP_AI_GATEWAY_TABLE_PREFIX:-mip_agent_gateway_growth_agent}" \
  --lakebase-catalog "$DEPLOYMENT_SYNC_CATALOG" \
  --lakebase-schema "$DEPLOYMENT_SYNC_SCHEMA" \
  --lakebase-sync-tables "$DEPLOYMENT_SYNC_TABLES" \
  --skip-sync \
  --skip-gateway \
  --skip-app-permissions \
  --out-env "$AGENTIC_ENV_FILE"
run finalize_supervisor_creation_handoff
if [[ "$DRY_RUN" -eq 0 ]]; then
  set -a
  # shellcheck disable=SC1090
  . "$AGENTIC_ENV_FILE"
  set +a
fi
AGENT_PROXY_PRESERVE_ARGS=()
AGENT_PROXY_ACCESS_PRESERVE_ARGS=()
AGENT_PROXY_UC_PRESERVE_ARGS=()
if [[ "$APP_SIGNED_BLUE_AVAILABLE" -eq 1 ]]; then
  case "$MIP_APP_ROLLBACK_PROXY_MODE" in
    legacy-proxyless)
      if [[ "$MIP_APP_ROLLBACK_RECORD_VERSION" != "5" ]]; then
        echo "${RED}[deploy] signed legacy rollback mode has the wrong record version.${RST}" >&2
        exit 1
      fi
      ;;
    exact-proxy)
      if [[ "$MIP_APP_ROLLBACK_RECORD_VERSION" != "6" || \
            -z "$MIP_APP_ROLLBACK_SUPERVISOR_ID" || \
            -z "$MIP_APP_ROLLBACK_SUPERVISOR_ENDPOINT" || \
            -z "$MIP_APP_ROLLBACK_SUPERVISOR_ENDPOINT_ID" || \
            "$MIP_APP_ROLLBACK_SUPERVISOR_CREATOR" != \
              "$DATABRICKS_AGENT_RUNTIME_CLIENT_ID" || \
            "$MIP_APP_ROLLBACK_RUNTIME_APPLICATION_ID" != \
              "$DATABRICKS_AGENT_RUNTIME_CLIENT_ID" || \
            "$MIP_APP_ROLLBACK_PROXY_APPLICATION_ID" != \
              "$DATABRICKS_AGENT_PROXY_CLIENT_ID" || \
            "$MIP_APP_ROLLBACK_GENIE_SPACE_ID" != \
              "${GENIE_SPACE_ID:-$(< genie/space_id.txt)}" ]]; then
        echo "${RED}[deploy] signed-blue agent-proxy binding is incomplete or drifted.${RST}" >&2
        exit 1
      fi
      if [[ "$MIP_APP_ROLLBACK_SUPERVISOR_ID" == \
            "${MIP_AGENT_SUPERVISOR_ID:-dry-run-supervisor}" && \
            ( "$MIP_APP_ROLLBACK_SUPERVISOR_ENDPOINT" != \
                "${MIP_AGENT_SUPERVISOR_ENDPOINT:-dry-run-supervisor-endpoint}" || \
              "$MIP_APP_ROLLBACK_SUPERVISOR_ENDPOINT_ID" != \
                "${MIP_AGENT_SUPERVISOR_ENDPOINT_ID:-dry-run-supervisor-endpoint-id}" ) ]]; then
        echo "${RED}[deploy] one Supervisor ID cannot bind different blue and green endpoints.${RST}" >&2
        exit 1
      elif [[ "$MIP_APP_ROLLBACK_SUPERVISOR_ID" != \
              "${MIP_AGENT_SUPERVISOR_ID:-dry-run-supervisor}" ]]; then
        AGENT_PROXY_PRESERVE_ARGS+=(
          --preserve-supervisor-id "$MIP_APP_ROLLBACK_SUPERVISOR_ID"
          --preserve-supervisor-endpoint "$MIP_APP_ROLLBACK_SUPERVISOR_ENDPOINT"
          --preserve-supervisor-endpoint-id "$MIP_APP_ROLLBACK_SUPERVISOR_ENDPOINT_ID"
        )
        AGENT_PROXY_ACCESS_PRESERVE_ARGS+=(
          --preserve-supervisor-id "$MIP_APP_ROLLBACK_SUPERVISOR_ID"
          --preserve-supervisor-endpoint "$MIP_APP_ROLLBACK_SUPERVISOR_ENDPOINT"
          --preserve-supervisor-endpoint-id "$MIP_APP_ROLLBACK_SUPERVISOR_ENDPOINT_ID"
        )
        AGENT_PROXY_UC_PRESERVE_ARGS+=(
          --supervisor-id "$MIP_APP_ROLLBACK_SUPERVISOR_ID"
          --supervisor-endpoint-id "$MIP_APP_ROLLBACK_SUPERVISOR_ENDPOINT_ID"
        )
      fi
      AGENT_PROXY_ACCESS_PRESERVE_ARGS+=(
        --legacy-pinned-supervisor-endpoint
        "$MIP_APP_ROLLBACK_SUPERVISOR_ENDPOINT"
      )
      ;;
    *)
      echo "${RED}[deploy] signed-blue proxy rollback mode is invalid.${RST}" >&2
      exit 1
      ;;
  esac
fi
if [[ -n "${_EXISTING_APP_ID:-}" && "$APP_UPGRADE_STATE" == "first_install" ]]; then
  step "stop the exact unsigned rebase App before legacy proxy ACL migration"
  run "$PYTHON" -m tools.databricks.stop_app_fail_closed \
    --app-name "$_GRANTS_APP_NAME" \
    "${APP_EXPECTED_IDENTITY_ARGS[@]}"
fi
step "grant and globally audit the dedicated Supervisor proxy caller"
if [[ "$DRY_RUN" -eq 0 ]]; then
  AGENT_PROXY_ACCESS_MUTATED=1
fi
run converge_agent_proxy_boundary \
  converge \
  "${MIP_AGENT_SUPERVISOR_ID:-dry-run-supervisor}" \
  "${MIP_AGENT_SUPERVISOR_ENDPOINT:-dry-run-supervisor-endpoint}" \
  "${MIP_AGENT_SUPERVISOR_ENDPOINT_ID:-dry-run-supervisor-endpoint-id}" \
  "${AGENT_PROXY_ACCESS_PRESERVE_ARGS[@]}"
step "provision the credential-versioned Supervisor proxy secret reference"
run_with_agent_proxy_credentials \
  "$PYTHON" -m tools.databricks.provision_agent_proxy_secret \
  --app-name "$_GRANTS_APP_NAME" \
  --scope "$MIP_AGENT_PROXY_SECRET_SCOPE" \
  --runtime-application-id "$DATABRICKS_AGENT_RUNTIME_CLIENT_ID" \
  --out-env "$AGENTIC_ENV_FILE"
if [[ "$DRY_RUN" -eq 0 ]]; then
  set -a
  # shellcheck disable=SC1090
  . "$AGENTIC_ENV_FILE"
  set +a
fi
_AGENT_PROXY_SECRET_REFERENCE="${MIP_AGENT_PROXY_SECRET_REFERENCE:-}"
if [[ "$DRY_RUN" -eq 1 && -z "$_AGENT_PROXY_SECRET_REFERENCE" ]]; then
  _AGENT_PROXY_SECRET_REFERENCE='{{secrets/dry-run/oauth-client-secret-dry-run}}'
fi
step "provision the governed outer Gateway under agent-runtime authority"
MIP_ALLOW_RUNTIME_MODEL_ATTESTATION_SIGNING=1 run_as_m2m_identity \
  agent-runtime \
  DATABRICKS_AGENT_RUNTIME_CLIENT_ID \
  DATABRICKS_AGENT_RUNTIME_CLIENT_SECRET \
  "$PYTHON" -m tools.databricks.provision_agentic_resources \
  --app-name "$_GRANTS_APP_NAME" \
  --catalog "${MIP_DEFAULT_CATALOG:-mip}" \
  --genie-space-id "${GENIE_SPACE_ID:-$(< genie/space_id.txt)}" \
  --expected-runtime-application-id "$DATABRICKS_AGENT_RUNTIME_CLIENT_ID" \
  --reviewed-function-owner "$MIP_REVIEWED_FUNCTION_OWNER" \
  --supervisor-id "${MIP_AGENT_SUPERVISOR_ID:-dry-run-supervisor}" \
  --supervisor-endpoint "${MIP_AGENT_SUPERVISOR_ENDPOINT:-dry-run-supervisor-endpoint}" \
  --proxy-caller-application-id "$DATABRICKS_AGENT_PROXY_CLIENT_ID" \
  --proxy-caller-credential-id "$DATABRICKS_AGENT_PROXY_CREDENTIAL_ID" \
  --proxy-caller-secret-reference "$_AGENT_PROXY_SECRET_REFERENCE" \
  --approved-query-application-id "$APP_SP_CLIENT_ID" \
  --approved-query-application-id "$DATABRICKS_VERIFIER_CLIENT_ID" \
  --deployment-lease-id "$MIP_APP_DEPLOYMENT_LEASE_ID" \
  --deployment-source-git-sha "$SOURCE_GIT_SHA" \
  --gateway-endpoint "${MIP_APP_ROLLBACK_GATEWAY_ENDPOINT:-mip-growth-agent-gateway}" \
  --gateway-agent-model "${MIP_AI_GATEWAY_AGENT_MODEL_FAMILY:-${MIP_DEFAULT_CATALOG:-mip}.audit.mortgage_growth_supervisor_proxy}" \
  --gateway-agent-experiment "${MIP_AI_GATEWAY_AGENT_EXPERIMENT_BASE:-mip-agent-runtime-gateway-proxy}" \
  --gateway-table-prefix "${MIP_AI_GATEWAY_TABLE_PREFIX:-mip_agent_gateway_growth_agent}" \
  --lakebase-catalog "$DEPLOYMENT_SYNC_CATALOG" \
  --lakebase-schema "$DEPLOYMENT_SYNC_SCHEMA" \
  --lakebase-sync-tables "$DEPLOYMENT_SYNC_TABLES" \
  --skip-sync \
  --skip-supervisor \
  --skip-app-permissions \
  --merge-out-env \
  --out-env "$AGENTIC_ENV_FILE"
step "re-audit the Supervisor proxy caller after Gateway provisioning"
run converge_agent_proxy_boundary \
  audit \
  "${MIP_AGENT_SUPERVISOR_ID:-dry-run-supervisor}" \
  "${MIP_AGENT_SUPERVISOR_ENDPOINT:-dry-run-supervisor-endpoint}" \
  "${MIP_AGENT_SUPERVISOR_ENDPOINT_ID:-dry-run-supervisor-endpoint-id}" \
  "${AGENT_PROXY_ACCESS_PRESERVE_ARGS[@]}"
step "prove dual-authority agent-proxy Unity Catalog boundary"
run_with_account_identity \
  run_with_proof_signing_authority \
    run_with_agent_proxy_credentials \
    "$PYTHON" -m tools.databricks.verify_agent_proxy_uc_boundary_dual_authority \
  --app-name "$_GRANTS_APP_NAME" \
  --application-id "$DATABRICKS_AGENT_PROXY_CLIENT_ID" \
  --expected-inventory-principal "$DEPLOY_INVENTORY_PRINCIPAL" \
  --catalog "${MIP_DEFAULT_CATALOG:-mip}" \
  --supervisor-id "${MIP_AGENT_SUPERVISOR_ID:-dry-run-supervisor}" \
  --supervisor-endpoint-id \
    "${MIP_AGENT_SUPERVISOR_ENDPOINT_ID:-dry-run-supervisor-endpoint-id}" \
  "${AGENT_PROXY_UC_PRESERVE_ARGS[@]}" \
  --genie-space-id "${GENIE_SPACE_ID:-$(< genie/space_id.txt)}"

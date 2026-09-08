# shellcheck shell=bash
# Deploy step file. Step 10b (continued): agent-proxy boundary proof and the signed cutover of
# the App to the green agentic runtime.
# Sourced by scripts/deploy.sh in reviewed order; it runs in the entrypoint's
# shell with its strict mode, traps, and private variables, never on its own.
# shellcheck disable=SC2034  # top-level state assigned here is read by later step files.
[[ "${BASH_SOURCE[0]}" != "$0" ]] || {
  echo "[deploy] ${BASH_SOURCE[0]} is a step file; run ./scripts/deploy.sh instead." >&2
  exit 2
}

_AGENT_PROXY_BOUNDARY_APP_URL="${MIP_APP_URL:-}"
if [[ "$DRY_RUN" -eq 1 && -z "$_AGENT_PROXY_BOUNDARY_APP_URL" ]]; then
  _AGENT_PROXY_BOUNDARY_APP_URL="https://dry-run.databricksapps.com"
fi
step "prove agent-proxy target query and negative authorization boundary before cutover"
run_with_account_identity \
  run_with_agent_proxy_credentials \
    "$PYTHON" -m tools.databricks.verify_agent_proxy_identity_boundary \
  --expected-application-id "$DATABRICKS_AGENT_PROXY_CLIENT_ID" \
  --account-host "$DATABRICKS_ACCOUNT_HOST" \
  --account-id "$DATABRICKS_ACCOUNT_ID" \
  --app-name "$_GRANTS_APP_NAME" \
  --app-url "${_AGENT_PROXY_BOUNDARY_APP_URL:?deployed App URL is required}" \
  --lakebase-instance "$MIP_LAKEBASE_INSTANCE" \
  --warehouse-id "$_GRANTS_WAREHOUSE_ID" \
  --supervisor-id "${MIP_AGENT_SUPERVISOR_ID:-dry-run-supervisor}" \
  --supervisor-endpoint "${MIP_AGENT_SUPERVISOR_ENDPOINT:-dry-run-supervisor-endpoint}" \
  --supervisor-endpoint-id "${MIP_AGENT_SUPERVISOR_ENDPOINT_ID:-dry-run-supervisor-endpoint-id}" \
  "${AGENT_PROXY_PRESERVE_ARGS[@]}" \
  --genie-space-id "${GENIE_SPACE_ID:-$(< genie/space_id.txt)}" \
  --allow-attested-app-401 \
  --allow-attested-stopped-app-503
if ! revoke_agent_runtime_bootstrap_grants; then
  echo "${RED}[deploy] temporary agent-runtime schema privileges remain; refusing deployment.${RST}" >&2
  exit 1
fi
if [[ "$DRY_RUN" -eq 0 ]]; then
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
  . "$AGENTIC_ENV_FILE"
  set +a
  step "export the exact live Gateway resource contract under runtime authority"
  run_as_m2m_identity \
    agent-runtime \
    DATABRICKS_AGENT_RUNTIME_CLIENT_ID \
    DATABRICKS_AGENT_RUNTIME_CLIENT_SECRET \
    "$PYTHON" -m tools.databricks.export_gateway_runtime_contract \
    --shell-env "$AGENTIC_ENV_FILE" \
    --supervisor-name "$MIP_AGENT_SUPERVISOR_NAME" \
    --supervisor-id "$MIP_AGENT_SUPERVISOR_ID" \
    --gateway-endpoint "$MIP_AI_GATEWAY_ENDPOINT" \
    --gateway-model-family "$MIP_AI_GATEWAY_AGENT_MODEL_FAMILY" \
    --gateway-experiment-base "$MIP_AI_GATEWAY_AGENT_EXPERIMENT_BASE" \
    --gateway-table-prefix "$MIP_AI_GATEWAY_TABLE_PREFIX" \
    --catalog "${MIP_DEFAULT_CATALOG:-mip}" \
    --genie-space-id "${GENIE_SPACE_ID:-$(< genie/space_id.txt)}" \
    --runtime-application-id "$DATABRICKS_AGENT_RUNTIME_CLIENT_ID" \
    --reviewed-function-owner "$MIP_REVIEWED_FUNCTION_OWNER" \
    --proxy-caller-application-id "$DATABRICKS_AGENT_PROXY_CLIENT_ID" \
    --proxy-caller-credential-id "$DATABRICKS_AGENT_PROXY_CREDENTIAL_ID" \
    --proxy-caller-secret-reference "$MIP_AGENT_PROXY_SECRET_REFERENCE"
  set -a
  # shellcheck disable=SC1090
  . "$AGENTIC_ENV_FILE"
  set +a
  if [[ -n "${MIP_AI_GATEWAY_INFERENCE_TABLE:-}" && -n "${MIP_AI_GATEWAY_ENDPOINT:-}" ]]; then
    if [[ "$APP_SIGNED_BLUE_AVAILABLE" -eq 0 && \
          "$HISTORICAL_CUTOVER_JOURNAL_PRESENT" -eq 1 && \
          "$HISTORICAL_CUTOVER_GATEWAY_PRESENT" -eq 0 ]]; then
      step "archive unprotected Gateway models after the supervisor-only journal handoff"
      reconcile_gateway_model_archives
    fi
    step "prove dual-authority agent-runtime UC boundary before cutover"
    prove_agent_runtime_dual_uc_boundary
    CUTOVER_JOURNAL_ENV_FILE="$(mktemp -t mip-agent-cutover.XXXXXX.env)"
    run_as_m2m_identity \
      agent-runtime \
      DATABRICKS_AGENT_RUNTIME_CLIENT_ID \
      DATABRICKS_AGENT_RUNTIME_CLIENT_SECRET \
      "$PYTHON" -m tools.databricks.cutover_agent_runtime_supervisor export-journal \
      --runtime-application-id "$DATABRICKS_AGENT_RUNTIME_CLIENT_ID" \
      --out-env "$CUTOVER_JOURNAL_ENV_FILE"
    run_with_proof_signing_authority \
      "$PYTHON" -m tools.databricks.cutover_agent_runtime_supervisor \
      refresh-journal-attestation \
      --runtime-application-id "$DATABRICKS_AGENT_RUNTIME_CLIENT_ID" \
      --app-name "$_GRANTS_APP_NAME" \
      --deployment-lease-id "$MIP_APP_DEPLOYMENT_LEASE_ID" \
      --deployment-source-git-sha "$SOURCE_GIT_SHA"
    if [[ -s "$CUTOVER_JOURNAL_ENV_FILE" ]]; then
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
      . "$CUTOVER_JOURNAL_ENV_FILE"
      set +a
    elif [[ -n "${MIP_REPLACED_AGENT_SUPERVISOR_ID:-}" || \
            ( -n "${MIP_APP_ROLLBACK_GATEWAY_ENDPOINT:-}" && \
              "${MIP_APP_ROLLBACK_GATEWAY_ENDPOINT}" != "$MIP_AI_GATEWAY_ENDPOINT" ) ]]; then
      AGENT_RUNTIME_PIN_ARGS=(
        -m tools.databricks.cutover_agent_runtime_supervisor pin-journal
        --runtime-application-id "$DATABRICKS_AGENT_RUNTIME_CLIENT_ID"
        --app-name "$_GRANTS_APP_NAME"
        --deployment-lease-id "$MIP_APP_DEPLOYMENT_LEASE_ID"
        --deployment-source-git-sha "$SOURCE_GIT_SHA"
      )
      if [[ -n "${MIP_REPLACED_AGENT_SUPERVISOR_ID:-}" ]]; then
        AGENT_RUNTIME_PIN_ARGS+=(
          --old-id "$MIP_REPLACED_AGENT_SUPERVISOR_ID"
          --old-endpoint "$MIP_REPLACED_AGENT_SUPERVISOR_ENDPOINT"
          --old-creator "$MIP_REPLACED_AGENT_SUPERVISOR_CREATOR"
          --old-create-time "$MIP_REPLACED_AGENT_SUPERVISOR_CREATE_TIME"
        )
      fi
      if [[ -n "${MIP_APP_ROLLBACK_GATEWAY_ENDPOINT:-}" && \
            "${MIP_APP_ROLLBACK_GATEWAY_ENDPOINT}" != "$MIP_AI_GATEWAY_ENDPOINT" ]]; then
        AGENT_RUNTIME_PIN_ARGS+=(
          --old-gateway-endpoint "$MIP_APP_ROLLBACK_GATEWAY_ENDPOINT"
        )
      fi
      step "sign and pin the destructive cutover tuple under deployer authority"
      run_with_proof_signing_authority "$PYTHON" "${AGENT_RUNTIME_PIN_ARGS[@]}"
      run_as_m2m_identity \
        agent-runtime \
        DATABRICKS_AGENT_RUNTIME_CLIENT_ID \
        DATABRICKS_AGENT_RUNTIME_CLIENT_SECRET \
        "$PYTHON" -m tools.databricks.cutover_agent_runtime_supervisor export-journal \
        --runtime-application-id "$DATABRICKS_AGENT_RUNTIME_CLIENT_ID" \
        --out-env "$CUTOVER_JOURNAL_ENV_FILE"
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
      . "$CUTOVER_JOURNAL_ENV_FILE"
      set +a
    fi
    AGENT_RUNTIME_GREEN_ARGS=(
      --replacement-id "$MIP_AGENT_SUPERVISOR_ID"
      --replacement-endpoint "$MIP_AGENT_SUPERVISOR_ENDPOINT"
      --gateway-endpoint "$MIP_AI_GATEWAY_ENDPOINT"
      --gateway-model "$MIP_AI_GATEWAY_AGENT_MODEL"
      --gateway-model-version "$MIP_AI_GATEWAY_AGENT_MODEL_VERSION"
      --gateway-inference-table "$MIP_AI_GATEWAY_INFERENCE_TABLE"
      --gateway-model-family "$MIP_AI_GATEWAY_AGENT_MODEL_FAMILY"
      --gateway-experiment-base "$MIP_AI_GATEWAY_AGENT_EXPERIMENT_BASE"
      --gateway-table-prefix "$MIP_AI_GATEWAY_TABLE_PREFIX"
      --catalog "${MIP_DEFAULT_CATALOG:-mip}"
      --genie-space-id "${GENIE_SPACE_ID:-$(< genie/space_id.txt)}"
      --app-name "$_GRANTS_APP_NAME"
      --deployment-lease-id "$MIP_APP_DEPLOYMENT_LEASE_ID"
      --deployment-source-git-sha "$SOURCE_GIT_SHA"
      --runtime-application-id "$DATABRICKS_AGENT_RUNTIME_CLIENT_ID"
      --preserve-endpoint "${MIP_APP_ROLLBACK_GATEWAY_ENDPOINT:-}"
    )
    step "classify signed old Supervisor App access from the authenticated journal"
    run classify_journaled_old_supervisor_app_access
    if [[ "$OLD_SUPERVISOR_APP_ACCESS_MODE" == "direct" || \
          "$OLD_SUPERVISOR_APP_ACCESS_MODE" == "mixed" ]]; then
      AGENT_RUNTIME_GREEN_ARGS+=(
        --preserve-endpoint "$MIP_REPLACED_AGENT_SUPERVISOR_ENDPOINT"
      )
    fi
    step "prepare runtime-owned Gateway access while preserving the live old Supervisor"
    journal_preactivation_app_acl_endpoint "$MIP_AI_GATEWAY_ENDPOINT"
    journal_preactivation_app_acl_endpoint "$MIP_AGENT_SUPERVISOR_ENDPOINT"
    run_with_proof_signing_authority \
      "$PYTHON" -m tools.databricks.cutover_agent_runtime_supervisor prepare \
      "${AGENT_RUNTIME_GREEN_ARGS[@]}" \
      --verifier-application-id "$DATABRICKS_VERIFIER_CLIENT_ID" \
      --verifier-scim-id "$MIP_VERIFIER_SCIM_ID"
    step "converge dedicated verifier access to the green Gateway before cutover"
    VERIFIER_GATEWAY_PRESERVE_ARGS=()
    VERIFIER_BOUNDARY_PRESERVE_ARGS=()
    if [[ "$APP_SIGNED_BLUE_AVAILABLE" -eq 1 && \
          -n "${MIP_APP_ROLLBACK_GATEWAY_ENDPOINT:-}" && \
          "$MIP_APP_ROLLBACK_GATEWAY_ENDPOINT" != "$MIP_AI_GATEWAY_ENDPOINT" ]]; then
      VERIFIER_GATEWAY_PRESERVE_ARGS+=(
        --preserve-gateway-endpoint "$MIP_APP_ROLLBACK_GATEWAY_ENDPOINT"
      )
      VERIFIER_BOUNDARY_PRESERVE_ARGS+=(
        --preserve-endpoint "$MIP_APP_ROLLBACK_GATEWAY_ENDPOINT"
      )
    fi
    VERIFIER_GATEWAY_CUTOVER_MUTATED=1
    run_with_proof_signing_authority \
      "$PYTHON" -m tools.databricks.provision_m2m_oauth \
      --identity-role verifier \
      --expected-application-id "$DATABRICKS_VERIFIER_CLIENT_ID" \
      --lakebase-instance "$MIP_LAKEBASE_INSTANCE" \
      --gateway-endpoint "$MIP_AI_GATEWAY_ENDPOINT" \
      --deployment-lease-id "$MIP_APP_DEPLOYMENT_LEASE_ID" \
      --deployment-source-git-sha "$MIP_DEPLOYMENT_SOURCE_GIT_SHA" \
      --revoke-gateway-endpoint "${MIP_AGENT_SUPERVISOR_ENDPOINT:-}" \
      --revoke-gateway-endpoint "mip-agent-gateway" \
      --warehouse-id "$_GRANTS_WAREHOUSE_ID" \
      --no-mint-secret \
      "${VERIFIER_GATEWAY_PRESERVE_ARGS[@]}"
    RUNTIME_GLOBAL_ACCESS_ARGS=(
      -m tools.databricks.audit_global_m2m_access
      --app-name "${MIP_APP_NAME:?App name is required}"
      --application-id "$DATABRICKS_AGENT_RUNTIME_CLIENT_ID"
      --expected-inventory-principal "$DEPLOY_INVENTORY_PRINCIPAL"
      --account-id "$DATABRICKS_ACCOUNT_ID"
      --expected-serving-permission CAN_MANAGE
      --genie-space-id "${GENIE_SPACE_ID:-$(< genie/space_id.txt)}"
      --serving-endpoint "$MIP_AGENT_SUPERVISOR_ENDPOINT"
      --serving-endpoint "$MIP_AI_GATEWAY_ENDPOINT"
    )
    if [[ "${MIP_REPLACED_AGENT_SUPERVISOR_CREATOR:-}" == \
          "$DATABRICKS_AGENT_RUNTIME_CLIENT_ID" && \
          -n "${MIP_REPLACED_AGENT_SUPERVISOR_ENDPOINT:-}" && \
          "$MIP_REPLACED_AGENT_SUPERVISOR_ENDPOINT" != "$MIP_AGENT_SUPERVISOR_ENDPOINT" && \
          "$MIP_REPLACED_AGENT_SUPERVISOR_ENDPOINT" != "$MIP_AI_GATEWAY_ENDPOINT" ]]; then
      RUNTIME_GLOBAL_ACCESS_ARGS+=(
        --serving-endpoint "$MIP_REPLACED_AGENT_SUPERVISOR_ENDPOINT"
      )
    fi
    if [[ "${MIP_REPLACED_AGENT_GATEWAY_CREATOR:-}" == \
          "$DATABRICKS_AGENT_RUNTIME_CLIENT_ID" && \
          -n "${MIP_REPLACED_AGENT_GATEWAY_ENDPOINT:-}" && \
          "$MIP_REPLACED_AGENT_GATEWAY_ENDPOINT" != "$MIP_AGENT_SUPERVISOR_ENDPOINT" && \
          "$MIP_REPLACED_AGENT_GATEWAY_ENDPOINT" != "$MIP_AI_GATEWAY_ENDPOINT" ]]; then
      RUNTIME_GLOBAL_ACCESS_ARGS+=(
        --serving-endpoint "$MIP_REPLACED_AGENT_GATEWAY_ENDPOINT"
      )
    fi
    step "audit agent-runtime access across every visible Genie and serving resource"
    run_with_account_identity run_with_proof_signing_authority \
      "$PYTHON" "${RUNTIME_GLOBAL_ACCESS_ARGS[@]}"
    step "audit verifier access across every visible serving resource"
    VERIFIER_GLOBAL_ACCESS_ARGS=(
      -m tools.databricks.audit_global_m2m_access
      --app-name "${MIP_APP_NAME:?App name is required}"
      --application-id "$DATABRICKS_VERIFIER_CLIENT_ID"
      --expected-inventory-principal "$DEPLOY_INVENTORY_PRINCIPAL"
      --account-id "$DATABRICKS_ACCOUNT_ID"
      --expected-serving-permission CAN_QUERY
      --forbid-all-genie
      --serving-endpoint "$MIP_AI_GATEWAY_ENDPOINT"
    )
    if [[ "$APP_SIGNED_BLUE_AVAILABLE" -eq 1 && \
          -n "${MIP_APP_ROLLBACK_GATEWAY_ENDPOINT:-}" && \
          "$MIP_APP_ROLLBACK_GATEWAY_ENDPOINT" != "$MIP_AI_GATEWAY_ENDPOINT" ]]; then
      VERIFIER_GLOBAL_ACCESS_ARGS+=(
        --serving-endpoint "$MIP_APP_ROLLBACK_GATEWAY_ENDPOINT"
        --legacy-pinned-serving-endpoint "$MIP_APP_ROLLBACK_GATEWAY_ENDPOINT"
      )
    fi
    run_with_account_identity run_with_proof_signing_authority \
      "$PYTHON" "${VERIFIER_GLOBAL_ACCESS_ARGS[@]}"
    APP_GLOBAL_ACCESS_ARGS=(
      -m tools.databricks.audit_global_m2m_access
      --app-name "${MIP_APP_NAME:?App name is required}"
      --application-id "$APP_SP_CLIENT_ID"
      --expected-inventory-principal "$DEPLOY_INVENTORY_PRINCIPAL"
      --account-id "$DATABRICKS_ACCOUNT_ID"
      --expected-serving-permission CAN_QUERY
      --genie-space-id "${GENIE_SPACE_ID:-$(< genie/space_id.txt)}"
      --serving-endpoint "$MIP_AI_GATEWAY_ENDPOINT"
    )
    if [[ -n "${MIP_APP_ROLLBACK_GATEWAY_ENDPOINT:-}" && \
          "$MIP_APP_ROLLBACK_GATEWAY_ENDPOINT" != "$MIP_AI_GATEWAY_ENDPOINT" ]]; then
      APP_GLOBAL_ACCESS_ARGS+=(
        --serving-endpoint "$MIP_APP_ROLLBACK_GATEWAY_ENDPOINT"
        --legacy-pinned-serving-endpoint "$MIP_APP_ROLLBACK_GATEWAY_ENDPOINT"
      )
    fi
    if [[ "$OLD_SUPERVISOR_APP_ACCESS_MODE" == "direct" || \
          "$OLD_SUPERVISOR_APP_ACCESS_MODE" == "mixed" ]]; then
      APP_GLOBAL_ACCESS_ARGS+=(
        --serving-endpoint "$MIP_REPLACED_AGENT_SUPERVISOR_ENDPOINT"
        --legacy-pinned-serving-endpoint "$MIP_REPLACED_AGENT_SUPERVISOR_ENDPOINT"
      )
    fi
    step "audit App access across every visible serving resource during cutover"
    run_with_account_identity run_with_proof_signing_authority \
      "$PYTHON" "${APP_GLOBAL_ACCESS_ARGS[@]}"
    # Activate the new contract before any destructive old-resource action.
    # Proof-ledger claimability is dynamic; this snapshot is intentionally
    # healthy-but-unclaimable until the verifier step below observes a row.
    if [[ "$APP_UPGRADE_STATE" == "blue_active" || \
          "$APP_UPGRADE_STATE" == "blue_quiesced" ]]; then
      APP_UPGRADE_STATE="blue_quiescing"
      step "quiesce verified-blue treatment authority immediately before green activation"
      run converge_app_treatment_access quiesce
      TREATMENT_RUNTIME_QUIESCED=1
      APP_UPGRADE_STATE="green_activating_quiesced"
    fi
    wait_for_app_deployable
    mint_m2m_token MIP_BEARER_TOKEN DATABRICKS_CLIENT_ID DATABRICKS_CLIENT_SECRET
    deploy_app_snapshot "activate App snapshot on the runtime-owned Gateway before retirement"
    step "prove agent-runtime negative authorization boundary before positive App probes"
    run_with_agent_runtime_credentials \
      "$PYTHON" -m tools.databricks.verify_agent_runtime_identity_boundary \
      --expected-application-id "$DATABRICKS_AGENT_RUNTIME_CLIENT_ID" \
      --app-name "$_GRANTS_APP_NAME" \
      --app-url "${MIP_APP_URL:?deployed app URL is required}" \
      --protected-service-principal-id "$APP_SP_SCIM_ID" \
      --warehouse-id "$_GRANTS_WAREHOUSE_ID" \
      --allow-attested-app-401
    if [[ "$APP_ACCESS_QUARANTINED" -eq 1 ]]; then
      step "grant only the dedicated release probe temporary candidate access"
      # shellcheck disable=SC2031  # Parent-shell normal client ID is unchanged by mint subshells.
      run "$PYTHON" -m tools.databricks.converge_app_release_access \
        --mode probe \
        --app-name "$_GRANTS_APP_NAME" \
        --release-probe-application-id "$DATABRICKS_RELEASE_PROBE_CLIENT_ID" \
        --normal-application-id "$DATABRICKS_CLIENT_ID" \
        --operator2-application-id "$DATABRICKS_OPERATOR2_CLIENT_ID" \
        --admin-application-id "$DATABRICKS_ADMIN_CLIENT_ID" \
        "${APP_EXPECTED_IDENTITY_ARGS[@]}"
      mint_app_automation_tokens
    fi
    AGENT_RUNTIME_BINDING_SHA256="$($PYTHON - \
      "$MIP_AGENT_SERVING_ENDPOINT" \
      "$MIP_AGENT_SUPERVISOR_ID" \
      "$MIP_AGENT_SUPERVISOR_ENDPOINT" \
      "$MIP_AGENT_RUNTIME_CLIENT_ID" \
      "$MIP_AI_GATEWAY_AGENT_MODEL" \
      "$MIP_AI_GATEWAY_AGENT_MODEL_VERSION" \
      "$MIP_AI_GATEWAY_INFERENCE_TABLE" \
      "$MIP_AGENT_PROXY_CLIENT_ID" \
      "$MIP_AGENT_PROXY_CREDENTIAL_ID" \
      "$MIP_AGENT_PROXY_SECRET_REFERENCE" <<'PYEOF'
import sys
from backend.agents.gateway_contract import gateway_runtime_binding_hash

print(gateway_runtime_binding_hash(
    endpoint=sys.argv[1],
    supervisor_id=sys.argv[2],
    upstream_endpoint=sys.argv[3],
    runtime_application_id=sys.argv[4],
    model_name=sys.argv[5],
    model_version=int(sys.argv[6]),
    inference_table=sys.argv[7],
    proxy_caller_application_id=sys.argv[8],
    proxy_caller_credential_id=sys.argv[9],
    proxy_caller_secret_reference=sys.argv[10],
))
PYEOF
)"
    step "prove the active App snapshot is bound to the green runtime contract"
    run "$PYTHON" -m tools.verify_deployed_app_contract \
      --base-url "$MIP_APP_URL" \
      --app-name "$APP_NAME" \
      --token-env MIP_BEARER_TOKEN \
      --git-sha "$APP_GIT_SHA" \
      --gateway-binding-sha256 "$AGENT_RUNTIME_BINDING_SHA256" \
      --deployment-lease-id "${MIP_APP_DEPLOYMENT_LEASE_ID:?App deployment lease is required}"
    step "prove the App reaches green Agent Responses and its reviewed planner/data path"
    run "$PYTHON" -m tools.verify_app_agent_green_path \
      --base-url "$MIP_APP_URL" \
      --app-name "$APP_NAME" \
      --token-env MIP_BEARER_TOKEN \
      --expected-endpoint "$MIP_AI_GATEWAY_ENDPOINT" \
      --deployment-lease-id "${MIP_APP_DEPLOYMENT_LEASE_ID:?App deployment lease is required}"
    step "read independent governed fn_build_cohort expectation before cutover"
    AGENT_TOOL_EXPECTED_COUNT="$(
      "$PYTHON" -m tools.databricks.read_agent_tool_probe_expectation \
        --warehouse-id "$_GRANTS_WAREHOUSE_ID" \
        --catalog "${MIP_DEFAULT_CATALOG:-mip}" \
        --state CA
    )"
    if [[ ! "$AGENT_TOOL_EXPECTED_COUNT" =~ ^[0-9]+$ ]]; then
      echo "${RED}[deploy] independent fn_build_cohort expectation is invalid.${RST}" >&2
      exit 1
    fi
    step "prove exact hosted build_cohort execution through the green Gateway"
    run_as_m2m_identity \
      verifier \
      DATABRICKS_VERIFIER_CLIENT_ID \
      DATABRICKS_VERIFIER_CLIENT_SECRET \
      "$PYTHON" -m tools.databricks.verify_hosted_agent_tool_execution \
      --endpoint "$MIP_AI_GATEWAY_ENDPOINT" \
      --expected-count "$AGENT_TOOL_EXPECTED_COUNT" \
      --catalog "${MIP_DEFAULT_CATALOG:-mip}"
    # The pre-activation mip_lakebase_migrate job already reconciled the exact
    # App and verifier Lakebase grants after both OAuth roles were bootstrapped.
    # Never rerun the full schema transaction after activating an App snapshot:
    # doing so races live requests and can trip the Lakebase circuit breaker.
    AI_GATEWAY_GRANTS_READY=1
    step "grant least-privilege AI Gateway inference-table access to the app service principal"
    if ! run "$PYTHON" -m tools.databricks.grant_ai_gateway_inference_table \
      --warehouse-id "$_GRANTS_WAREHOUSE_ID" \
      --relation-prefix "$MIP_AI_GATEWAY_INFERENCE_TABLE" \
      --endpoint "$MIP_AI_GATEWAY_ENDPOINT" \
      --principal "$APP_SP_CLIENT_ID"; then
      AI_GATEWAY_GRANTS_READY=0
    fi
    if [[ "$AI_GATEWAY_GRANTS_READY" -eq 1 ]]; then
      step "grant read-only AI Gateway inference-table access to the verifier service principal"
      if ! run "$PYTHON" -m tools.databricks.grant_ai_gateway_inference_table \
        --warehouse-id "$_GRANTS_WAREHOUSE_ID" \
        --relation-prefix "$MIP_AI_GATEWAY_INFERENCE_TABLE" \
        --endpoint "$MIP_AI_GATEWAY_ENDPOINT" \
        --principal "$DATABRICKS_VERIFIER_CLIENT_ID"; then
        AI_GATEWAY_GRANTS_READY=0
      fi
    fi
    if [[ "$AI_GATEWAY_GRANTS_READY" -eq 0 ]]; then
      if [[ "${MIP_REQUIRE_AI_GATEWAY_CLAIMABLE:-0}" == "1" ]]; then
        echo "${RED}[deploy] strict AI Gateway inference-table grant convergence failed.${RST}" >&2
        exit 1
      fi
      echo "${YLW}[deploy] AI Gateway inference-table delivery/grants are pending; skipping exact proof and continuing with the capability honestly configured/unavailable.${RST}" >&2
    else
      DATABRICKS_ACCOUNT_HOST="${DATABRICKS_ACCOUNT_HOST:-https://accounts.cloud.databricks.com}"
      if [[ -z "${DATABRICKS_ACCOUNT_ID:-}" ]]; then
        echo "${RED}[deploy] DATABRICKS_ACCOUNT_ID is required before the verifier can write an exact Gateway proof.${RST}" >&2
        exit 1
      fi
      step "prove verifier effective authorization boundary before exact Gateway proof"
      run_as_m2m_identity \
        verifier \
        DATABRICKS_VERIFIER_CLIENT_ID \
        DATABRICKS_VERIFIER_CLIENT_SECRET \
        "$PYTHON" -m tools.databricks.verify_lakebase_oauth_identity \
        --expected-application-id "$DATABRICKS_VERIFIER_CLIENT_ID" \
        --lakebase-instance "$MIP_LAKEBASE_INSTANCE" \
        --lakebase-database "$LAKEBASE_DATABASE"
      prove_exact_verifier_boundary \
        "$MIP_AI_GATEWAY_ENDPOINT" \
        "$MIP_AI_GATEWAY_INFERENCE_TABLE" \
        "${VERIFIER_BOUNDARY_PRESERVE_ARGS[@]}"
      step "verify AI Gateway exact inference-row proof with dedicated verifier identity"
      AI_GATEWAY_PROOF_ARGS=(
        -m
        tools.databricks.verify_ai_gateway_exact_proof
        send
        --wait
        --require-verifier-derived-auth
        --warehouse-id "$_GRANTS_WAREHOUSE_ID"
        --lakebase-instance "$MIP_LAKEBASE_INSTANCE"
        --lakebase-database "$LAKEBASE_DATABASE"
        --git-sha "$APP_GIT_SHA"
        --endpoint "$MIP_AI_GATEWAY_ENDPOINT"
        --inference-table "$MIP_AI_GATEWAY_INFERENCE_TABLE"
        --expected-tool-count "$AGENT_TOOL_EXPECTED_COUNT"
      )
      if [[ "${MIP_REQUIRE_AI_GATEWAY_CLAIMABLE:-0}" == "1" ]]; then
        AI_GATEWAY_PROOF_ARGS+=(--require-verified)
      fi
      if ! run_as_m2m_identity \
        verifier \
        DATABRICKS_VERIFIER_CLIENT_ID \
        DATABRICKS_VERIFIER_CLIENT_SECRET \
        "$PYTHON" "${AI_GATEWAY_PROOF_ARGS[@]}"; then
        if [[ "${MIP_REQUIRE_AI_GATEWAY_CLAIMABLE:-0}" == "1" ]]; then
          echo "${RED}[deploy] strict AI Gateway exact proof failed.${RST}" >&2
          exit 1
        fi
        echo "${YLW}[deploy] AI Gateway exact proof is not claimable; continuing with the capability honestly configured/unavailable.${RST}" >&2
      fi
    fi
  fi
fi

# The App already runs the exact agentic env before retirement. The verifier
# ledger is read dynamically, so successful proof does not require another
# deployment here.

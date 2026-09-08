# shellcheck shell=bash
# Deploy step file. Steps 10c-11: live Agent Evaluation, redeploy with the eval run id, live
# smoke, and fail-closed restore of the signed last-good App.
# Sourced by scripts/deploy.sh in reviewed order; it runs in the entrypoint's
# shell with its strict mode, traps, and private variables, never on its own.
# shellcheck disable=SC2034  # top-level state assigned here is read by later step files.
[[ "${BASH_SOURCE[0]}" != "$0" ]] || {
  echo "[deploy] ${BASH_SOURCE[0]} is a step file; run ./scripts/deploy.sh instead." >&2
  exit 2
}

if [[ "$DRY_RUN" -eq 0 ]]; then
  # A full deploy can exceed the workspace OAuth TTL before eval starts.
  # Remint both identities immediately before the proof and never substitute
  # the deployment PAT for either app-facing role.
  mint_app_automation_tokens
fi
step "run live Agent Evaluation — golden Growth Agent workflows"
mkdir -p dist
AGENT_EVAL_ENV_FILE="$(mktemp -t mip-agent-eval.XXXXXX.env)"
run "$PYTHON" -m tools.databricks.run_agent_eval \
  --app-url "${MIP_APP_URL:-}" \
  --require-mlflow-genai-evaluate \
  --out-env "$AGENT_EVAL_ENV_FILE" \
  --out-json dist/agent-eval.json
if [[ "$DRY_RUN" -eq 0 ]]; then
  set -a
  # shellcheck disable=SC1090
  . "$AGENT_EVAL_ENV_FILE"
  set +a
fi

if [[ "$DRY_RUN" -eq 0 ]]; then
  mint_app_automation_tokens
  wait_for_app_deployable
fi
deploy_app_snapshot "deploy Databricks App snapshot with Agent Evaluation proof"

# -----------------------------------------------------------------------------
# Step 11 (optional): live smoke test
# -----------------------------------------------------------------------------
FINAL_APP_PROVEN=0
if [[ "$SKIP_SMOKE" -eq 1 ]]; then
  step "live smoke — SKIPPED (--skip-smoke)"
else
  if [[ -x scripts/smoke_live.sh ]]; then
    # Re-mint UNCONDITIONALLY: a token minted before the eval step expired
    # mid-sweep on the longest pipeline run (gw17, 2026-07-08 — the verifier
    # flush-wait plus a slow warehouse pushed total runtime past the token
    # TTL, and smoke 401'd on geo rollups after passing nine checks). The
    # smoke sweep always deserves a fresh full-lifetime bearer.
    if [[ "$DRY_RUN" -eq 0 && -n "${MIP_APP_URL:-}" ]]; then
      mint_app_automation_tokens
    fi
    step "live smoke — scripts/smoke_live.sh against the deployed app"
    export MIP_EXPECT_AGENTIC_CAPABILITIES="${MIP_EXPECT_AGENTIC_CAPABILITIES:-1}"
    export MIP_EXPECT_GIT_SHA="$APP_GIT_SHA"
    if ! run ./scripts/smoke_live.sh; then
      if [[ "${ALLOW_SMOKE_FAILURE:-0}" == "1" ]]; then
        echo "${YLW}[deploy] smoke test failed — override will restore the signed last-good App.${RST}" >&2
        echo "${YLW}[deploy] the failed candidate will not be recorded as verified.${RST}" >&2
      else
        echo "${RED}[deploy] smoke test failed — deployed source is not customer-release-ready.${RST}" >&2
        echo "${RED}[deploy] set ALLOW_SMOKE_FAILURE=1 only for an intentional manual emergency override.${RST}" >&2
        exit 1
      fi
    else
      FINAL_APP_PROVEN=1
    fi
  else
    step "live smoke — scripts/smoke_live.sh not executable; skipping"
  fi
fi

if [[ "$DRY_RUN" -eq 0 && "$FINAL_APP_PROVEN" -eq 1 ]]; then
  mint_app_automation_tokens
  APP_UPGRADE_STATE="green_treatment_pending_capture"
  step "atomically restore treatment authority and persist the last-good App contract"
  capture_last_good_app "${AGENT_RUNTIME_BINDING_SHA256:-}"
  finalize_signed_first_install_capture
  if [[ "$APP_ACCESS_QUARANTINED" -eq 1 ]]; then
    step "replace release-probe access with exact runtime operator access after signed capture"
    # shellcheck disable=SC2031  # Parent-shell normal client ID is unchanged by mint subshells.
    run "$PYTHON" -m tools.databricks.converge_app_release_access \
      --mode runtime \
      --app-name "$_GRANTS_APP_NAME" \
      --release-probe-application-id "$DATABRICKS_RELEASE_PROBE_CLIENT_ID" \
      --normal-application-id "$DATABRICKS_CLIENT_ID" \
      --operator2-application-id "$DATABRICKS_OPERATOR2_CLIENT_ID" \
      --admin-application-id "$DATABRICKS_ADMIN_CLIENT_ID" \
      "${APP_EXPECTED_IDENTITY_ARGS[@]}"
    APP_ACCESS_QUARANTINED=0
  fi
  step "retire pinned blue runtime resources only after every green release gate"
  AGENT_RUNTIME_RETIRE_ARGS=(
    -m tools.databricks.cutover_agent_runtime_supervisor retire
    "${AGENT_RUNTIME_GREEN_ARGS[@]}"
    --verifier-application-id "$DATABRICKS_VERIFIER_CLIENT_ID"
    --verifier-scim-id "$MIP_VERIFIER_SCIM_ID"
    --proxy-application-id "$DATABRICKS_AGENT_PROXY_CLIENT_ID"
  )
  if [[ -n "${MIP_REPLACED_AGENT_SUPERVISOR_ID:-}" ]]; then
    AGENT_RUNTIME_RETIRE_ARGS+=(
      --old-id "$MIP_REPLACED_AGENT_SUPERVISOR_ID"
      --old-endpoint "$MIP_REPLACED_AGENT_SUPERVISOR_ENDPOINT"
      --old-endpoint-id "$MIP_REPLACED_AGENT_SUPERVISOR_ENDPOINT_ID"
      --old-creator "$MIP_REPLACED_AGENT_SUPERVISOR_CREATOR"
      --old-create-time "$MIP_REPLACED_AGENT_SUPERVISOR_CREATE_TIME"
    )
  fi
  if [[ -n "${MIP_REPLACED_AGENT_GATEWAY_ENDPOINT:-}" ]]; then
    AGENT_RUNTIME_RETIRE_ARGS+=(
      --old-gateway-endpoint "$MIP_REPLACED_AGENT_GATEWAY_ENDPOINT"
      --old-gateway-endpoint-id "$MIP_REPLACED_AGENT_GATEWAY_ENDPOINT_ID"
      --old-gateway-creator "$MIP_REPLACED_AGENT_GATEWAY_CREATOR"
    )
    if [[ "${MIP_REPLACED_AGENT_GATEWAY_DELETE_ALLOWED:-0}" == "1" ]]; then
      AGENT_RUNTIME_RETIRE_ARGS+=(--old-gateway-delete-allowed)
    fi
  fi
  if [[ -n "${MIP_REPLACED_AGENT_SUPERVISOR_ID:-}" || \
        -n "${MIP_REPLACED_AGENT_GATEWAY_ENDPOINT:-}" ]]; then
    run "$PYTHON" "${AGENT_RUNTIME_RETIRE_ARGS[@]}"
  else
    step "no signed blue runtime resource is pinned; skipping destructive retirement"
  fi
  step "finalize the runtime-owned Supervisor canonical name"
  run_as_m2m_identity \
    agent-runtime \
    DATABRICKS_AGENT_RUNTIME_CLIENT_ID \
    DATABRICKS_AGENT_RUNTIME_CLIENT_SECRET \
    "$PYTHON" -m tools.databricks.cutover_agent_runtime_supervisor finalize \
    --replacement-id "$MIP_AGENT_SUPERVISOR_ID" \
    --replacement-endpoint "$MIP_AGENT_SUPERVISOR_ENDPOINT" \
    --runtime-application-id "$DATABRICKS_AGENT_RUNTIME_CLIENT_ID" \
    --app-name "$_GRANTS_APP_NAME" \
    --deployment-lease-id "$MIP_APP_DEPLOYMENT_LEASE_ID" \
    --deployment-source-git-sha "$SOURCE_GIT_SHA" \
    --catalog "${MIP_DEFAULT_CATALOG:-mip}" \
    --genie-space-id "${GENIE_SPACE_ID:-$(< genie/space_id.txt)}"
  step "re-audit final agent-runtime global access after blue retirement"
  run_with_account_identity run_with_proof_signing_authority \
    "$PYTHON" -m tools.databricks.audit_global_m2m_access \
    --app-name "${MIP_APP_NAME:?App name is required}" \
    --application-id "$DATABRICKS_AGENT_RUNTIME_CLIENT_ID" \
    --expected-inventory-principal "$DEPLOY_INVENTORY_PRINCIPAL" \
    --account-id "$DATABRICKS_ACCOUNT_ID" \
    --expected-serving-permission CAN_MANAGE \
    --genie-space-id "${GENIE_SPACE_ID:-$(< genie/space_id.txt)}" \
    --serving-endpoint "$MIP_AGENT_SUPERVISOR_ENDPOINT" \
    --serving-endpoint "$MIP_AI_GATEWAY_ENDPOINT"
  step "re-audit final Supervisor proxy caller access after blue retirement"
  run converge_agent_proxy_boundary \
    converge \
    "$MIP_AGENT_SUPERVISOR_ID" \
    "$MIP_AGENT_SUPERVISOR_ENDPOINT" \
    "$MIP_AGENT_SUPERVISOR_ENDPOINT_ID"
  step "re-prove final dual-authority agent-proxy Unity Catalog boundary"
  run_with_account_identity \
    run_with_proof_signing_authority \
      run_with_agent_proxy_credentials \
      "$PYTHON" -m tools.databricks.verify_agent_proxy_uc_boundary_dual_authority \
    --app-name "$_GRANTS_APP_NAME" \
    --application-id "$DATABRICKS_AGENT_PROXY_CLIENT_ID" \
    --expected-inventory-principal "$DEPLOY_INVENTORY_PRINCIPAL" \
    --catalog "${MIP_DEFAULT_CATALOG:-mip}" \
    --supervisor-id "$MIP_AGENT_SUPERVISOR_ID" \
    --supervisor-endpoint-id "$MIP_AGENT_SUPERVISOR_ENDPOINT_ID" \
    --genie-space-id "${GENIE_SPACE_ID:-$(< genie/space_id.txt)}"
  step "re-prove final agent-proxy target query and negative boundary after blue retirement"
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
    --genie-space-id "${GENIE_SPACE_ID:-$(< genie/space_id.txt)}" \
    --allow-attested-app-401
  AGENT_PROXY_SIGNED_BLUE_RETIRE_ARGS=()
  if [[ "$APP_SIGNED_BLUE_AVAILABLE" -eq 1 && \
        -n "$MIP_APP_ROLLBACK_PROXY_CREDENTIAL_IDS" ]]; then
    IFS=',' read -r -a _AGENT_PROXY_SIGNED_BLUE_IDS \
      <<< "$MIP_APP_ROLLBACK_PROXY_CREDENTIAL_IDS"
    for _AGENT_PROXY_SIGNED_BLUE_ID in "${_AGENT_PROXY_SIGNED_BLUE_IDS[@]}"; do
      if [[ ! "$_AGENT_PROXY_SIGNED_BLUE_ID" =~ ^[A-Za-z0-9._-]{1,128}$ ]]; then
        echo "${RED}[deploy] signed-blue agent-proxy credential ID is invalid.${RST}" >&2
        exit 1
      fi
      AGENT_PROXY_SIGNED_BLUE_RETIRE_ARGS+=(
        --signed-blue-credential-id "$_AGENT_PROXY_SIGNED_BLUE_ID"
      )
    done
  fi
  step "remove retired Supervisor proxy OAuth credentials and secret versions"
  run_with_proof_signing_authority \
    run_with_agent_proxy_binding \
      "$PYTHON" -m tools.databricks.provision_agent_proxy_secret \
    --app-name "$_GRANTS_APP_NAME" \
    --scope "$MIP_AGENT_PROXY_SECRET_SCOPE" \
    --rollback-scope "$APP_ROLLBACK_SECRET_SCOPE" \
    --runtime-application-id "$DATABRICKS_AGENT_RUNTIME_CLIENT_ID" \
    --cleanup-signed-blue \
    "${AGENT_PROXY_SIGNED_BLUE_RETIRE_ARGS[@]}"
  step "prove exact green Gateway inference after proxy credential retirement"
  mint_app_automation_tokens
  run "$PYTHON" -m tools.verify_app_agent_green_path \
    --base-url "$MIP_APP_URL" \
    --app-name "$APP_NAME" \
    --token-env MIP_BEARER_TOKEN \
    --expected-endpoint "$MIP_AI_GATEWAY_ENDPOINT" \
    --deployment-lease-id "${MIP_APP_DEPLOYMENT_LEASE_ID:?App deployment lease is required}"
  run_as_m2m_identity \
    verifier \
    DATABRICKS_VERIFIER_CLIENT_ID \
    DATABRICKS_VERIFIER_CLIENT_SECRET \
    "$PYTHON" -m tools.databricks.verify_hosted_agent_tool_execution \
    --endpoint "$MIP_AI_GATEWAY_ENDPOINT" \
    --expected-count "$AGENT_TOOL_EXPECTED_COUNT" \
    --catalog "${MIP_DEFAULT_CATALOG:-mip}"
  step "re-audit final verifier global access after blue retirement"
  run_with_account_identity run_with_proof_signing_authority \
    "$PYTHON" -m tools.databricks.audit_global_m2m_access \
    --app-name "${MIP_APP_NAME:?App name is required}" \
    --application-id "$DATABRICKS_VERIFIER_CLIENT_ID" \
    --expected-inventory-principal "$DEPLOY_INVENTORY_PRINCIPAL" \
    --account-id "$DATABRICKS_ACCOUNT_ID" \
    --expected-serving-permission CAN_QUERY \
    --forbid-all-genie \
    --serving-endpoint "$MIP_AI_GATEWAY_ENDPOINT"
  step "re-audit final App global serving access after blue retirement"
  run_with_account_identity run_with_proof_signing_authority \
    "$PYTHON" -m tools.databricks.audit_global_m2m_access \
    --app-name "${MIP_APP_NAME:?App name is required}" \
    --application-id "$APP_SP_CLIENT_ID" \
    --expected-inventory-principal "$DEPLOY_INVENTORY_PRINCIPAL" \
    --account-id "$DATABRICKS_ACCOUNT_ID" \
    --expected-serving-permission CAN_QUERY \
    --genie-space-id "${GENIE_SPACE_ID:-$(< genie/space_id.txt)}" \
    --serving-endpoint "$MIP_AI_GATEWAY_ENDPOINT"
  step "clear the authenticated cutover journal after every final boundary proof"
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
  step "archive the retired blue Gateway model after authenticated journal clearance"
  reconcile_gateway_model_archives
  step "re-prove final dual-authority agent-runtime UC boundary after model archival"
  prove_agent_runtime_dual_uc_boundary
  VERIFIER_GATEWAY_CUTOVER_MUTATED=0
  # Persist only after retirement/finalization. Keeping the prior cache until
  # then preserves the pinned old identity across an interrupted cleanup.
  # Values only name resources; no secrets are written.
  mkdir -p .databricks
  sed '/^MIP_REPLACED_AGENT_SUPERVISOR_/d' \
    "$AGENTIC_ENV_FILE" > "$AGENTIC_ENV_CACHE"
  APP_UPGRADE_STATE="green_verified"
elif [[ "$DRY_RUN" -eq 0 ]]; then
  step "stop the unproven candidate before signed-blue rollback"
  run "$PYTHON" -m tools.databricks.stop_app_fail_closed \
    --app-name "$APP_NAME" \
    "${APP_EXPECTED_IDENTITY_ARGS[@]}"
  step "prove treatment authority remains quiesced before signed-blue rollback"
  run converge_app_treatment_access quiesce
  TREATMENT_RUNTIME_QUIESCED=1
  if [[ "$APP_SIGNED_BLUE_AVAILABLE" -ne 1 ]]; then
    echo "${RED}[deploy] final smoke proof is absent and no signed-blue rollback exists; leaving the first-install App stopped and quiesced.${RST}" >&2
    exit 1
  fi
  APP_UPGRADE_STATE="green_activating_quiesced"
  step "restore the signed last-good App because final smoke proof is absent"
  run restore_signed_blue_while_quiesced
fi

# shellcheck shell=bash
# Deploy step file. Steps 10b-11 for the core tier (MIP_AGENTIC_TIER=off, M2-PR7b): one activation
# rollover, the live smoke and a signed v7 core capture, or the core signed-blue restore.
# Sourced by scripts/deploy.sh in reviewed order; it runs in the entrypoint's
# shell with its strict mode, traps, and private variables, never on its own.
# shellcheck disable=SC2034  # top-level state assigned here is read by later step files.
[[ "${BASH_SOURCE[0]}" != "$0" ]] || {
  echo "[deploy] ${BASH_SOURCE[0]} is a step file; run ./scripts/deploy.sh instead." >&2
  exit 2
}

# >>> core tier: activation and capture >>>
# The core release uses the four core identities only (normal, operator2,
# admin, release probe) and the deployer. Every agentic step is skipped here,
# by name: the agentic tier's Lakebase Sync proof, verifier capture,
# historical endpoint retirement, Supervisor and proxy provisioning, Gateway
# provisioning and audits, cutover journal, Agent Evaluation and the agentic
# retirement after capture (deploy_step_agentic_*.sh and
# deploy_step_agent_eval_and_smoke.sh, which refuse this tier).
if tier_is_agentic; then
  echo "${RED}[deploy] the core activation step runs in the core tier only.${RST}" >&2
  exit 2
fi
step "core tier: skip the agentic provisioning, cutover and Agent Evaluation (agentic-only; no verifier, agent runtime, Supervisor proxy or Gateway)"

# The one activation rollover. A first install already activated its snapshot
# in step 5. Over signed blue it happens here, as the agentic cutover's does:
# blue's treatment authority quiesced immediately before it and the quiesce
# window superseded. The pre-activation rollback proof (attack O2, with blue's
# restore payload rebuilt for an adopted blue) runs first, before either: a
# refusal then exits while blue still serves in its step-0a state, which the
# EXIT trap compensates with blue up, never by restoring blue over itself
# (deploy_app_snapshot's own call is then a no-op).
if [[ "$APP_UPGRADE_STATE" == "blue_active" || \
      "$APP_UPGRADE_STATE" == "blue_quiesced" ]]; then
  prove_blue_restorable_before_activation
  APP_UPGRADE_STATE="blue_quiescing"
  step "quiesce verified-blue treatment authority immediately before green activation"
  run converge_app_treatment_access quiesce
  TREATMENT_RUNTIME_QUIESCED=1
  APP_UPGRADE_STATE="green_activating_quiesced"
  # Green activation supersedes the quiesce window states (M0-PR8).
  end_quiesce_window_at_activation
  if [[ "$DRY_RUN" -eq 0 ]]; then
    wait_for_app_deployable
    mint_m2m_token MIP_BEARER_TOKEN DATABRICKS_CLIENT_ID DATABRICKS_CLIENT_SECRET
  fi
  deploy_app_snapshot "activate the core App snapshot (the one activation rollover)"
fi
if [[ "$DRY_RUN" -eq 0 && "$APP_ACCESS_QUARANTINED" -eq 1 ]]; then
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
fi

# The live smoke, Genie included (a Genie space is a core resource). The core
# App claims no agentic capability, so none is required: the two agentic
# switches ship "0" (tools/databricks/app_deploy_payload.py).
FINAL_APP_PROVEN=0
if [[ "$SKIP_SMOKE" -eq 1 ]]; then
  step "live smoke — SKIPPED (--skip-smoke)"
elif [[ -x scripts/smoke_live.sh ]]; then
  if [[ "$DRY_RUN" -eq 0 && -n "${MIP_APP_URL:-}" ]]; then
    mint_app_automation_tokens
  fi
  step "live smoke — scripts/smoke_live.sh against the deployed core App (Genie included, no agentic capability)"
  export MIP_EXPECT_AGENTIC_CAPABILITIES=0
  export MIP_REQUIRE_AI_GATEWAY_CLAIMABLE=0
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

# The signed core capture: a v7 core record (provenance captured) over the
# core resource proof, written while treatment is quiesced, then treatment
# converged to runtime (tools/databricks/core_blue_capture.py). Its restore
# source is this release's own folder and its restore payload is the one just
# deployed, so a later tier-b restore rebuilds nothing.
capture_core_last_good_app() {
  local -a args=(
    -m tools.databricks.app_core_rollback capture
    --app-name "$APP_NAME"
    --scope "$APP_ROLLBACK_SECRET_SCOPE"
    --lakebase-instance "$MIP_LAKEBASE_INSTANCE"
    --agentic-tier "$CLIENT_AGENTIC_TIER"
    --base-url "${MIP_APP_URL:?App URL is required for exact last-good capture}"
    --token-env MIP_BEARER_TOKEN
    --payload "${APP_LAST_DEPLOY_PAYLOAD:?App deployment payload is required}"
    --app-resource-payload "${APP_RESOURCE_BINDING_PAYLOAD:?Resolved App resources are required}"
    --expected-git-sha "$APP_GIT_SHA"
    --deployment-lease-id "${MIP_APP_DEPLOYMENT_LEASE_ID:?App deployment lease is required}"
    --genie-space-id "${GENIE_SPACE_ID:-$(< genie/space_id.txt)}"
    --release-id "$APP_RELEASE_ID"
    --stage-dir "$APP_RELEASE_STAGE_ROOT/source"
    --treatment-warehouse-id "$_GRANTS_WAREHOUSE_ID"
    --treatment-catalog "$_GRANTS_CATALOG"
  )
  if [[ -z "$APP_ROLLBACK_BINDING_ENV" ]]; then
    APP_ROLLBACK_BINDING_ENV="$(mktemp -t mip-app-blue-binding.XXXXXX.env)"
  fi
  args+=(--out-env "$APP_ROLLBACK_BINDING_ENV")
  run_with_account_identity \
    run_with_proof_signing_authority "$PYTHON" "${args[@]}"
  set -a
  # shellcheck disable=SC1090
  . "$APP_ROLLBACK_BINDING_ENV"
  set +a
}

if [[ "$DRY_RUN" -eq 1 ]]; then
  step "signed core capture — would sign a v7 core record (provenance captured) over the core resource proof"
  echo "  would run: $PYTHON -m tools.databricks.app_core_rollback capture --agentic-tier ${CLIENT_AGENTIC_TIER} (no Gateway binding, contract or proxy journal)"
elif [[ "$FINAL_APP_PROVEN" -eq 1 ]]; then
  mint_app_automation_tokens
  APP_UPGRADE_STATE="green_treatment_pending_capture"
  step "atomically restore treatment authority and persist the signed core last-good App"
  capture_core_last_good_app
  # A contract quiesce window closes only now that green is the signed record.
  close_contract_window_after_capture
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
  step "core tier: no blue runtime resource to retire (no Supervisor, Gateway or proxy credential)"
  APP_UPGRADE_STATE="green_verified"
else
  step "stop the unproven candidate before signed-blue rollback"
  run stop_unproven_candidate_before_rollback
  step "prove treatment authority remains quiesced before signed-blue rollback"
  run converge_app_treatment_access quiesce
  TREATMENT_RUNTIME_QUIESCED=1
  if [[ "$APP_SIGNED_BLUE_AVAILABLE" -ne 1 ]]; then
    echo "${RED}[deploy] final smoke proof is absent and no signed-blue rollback exists; leaving the first-install App stopped and quiesced.${RST}" >&2
    exit 1
  fi
  APP_UPGRADE_STATE="green_activating_quiesced"
  step "restore the signed core last-good App (tier b) because final smoke proof is absent"
  run restore_signed_blue_while_quiesced
fi
# <<< core tier: activation and capture <<<

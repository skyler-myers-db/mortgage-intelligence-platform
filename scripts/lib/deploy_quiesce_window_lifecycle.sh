# shellcheck shell=bash
# The quiesce window (M0-PR8): a release keeps blue serving and stops the App
# only inside one bounded window, for a reason the read-only plan predicted.
# This file and the EXIT-trap compensation (deploy_app_release_lifecycle.sh)
# are the only shell files that stop or start the App, and the only callers of
# a Python tool mode that does (tools/databricks/stop_primitive_map.py; the
# lexical and AST contracts in tests/unit/test_quiesce_window_lexical_contract.py
# and tests/unit/test_stop_primitive_ast_contract.py). Every other stop the
# release needs is a named function here that the step files call.
# Sourced by scripts/deploy.sh; this file must not change shell options or traps.
# shellcheck disable=SC2034  # QUIESCE_WINDOW_* and the proof flags are deploy-wide state.

# QUIESCE_WINDOW_STATE is the EXIT trap's window state (stop_app_after_failed_deploy):
#   ""            not governed (first install, unsigned rebase, unverified App);
#   blue_serving  signed blue serves and nothing it depends on was mutated:
#                 a failure never stops it, it only converges runtime access;
#   blue_degraded blue serves while a dependency is mutated (sync access, the
#                 reviewed functions): a failure converges them back, App up;
#   window_open   the App was stopped on purpose (journaled): a failure starts
#                 and re-pins blue if proven, else leaves the window open for
#                 the dead-man (M0-PR9). A contract migration
#                 (blue_restorable=0) leaves it stopped until green activates.

# ---------------------------------------------------------------------------
# Read-only planning
# ---------------------------------------------------------------------------

# The catalog bootstrap skip rule (attack O12): exit 0 skip, 3 run. Any other
# outcome is reported as "run"; it never skips on a failure.
check_catalog_bootstrap_skip_rule() {
  local out="${1:?skip-rule report path is required}" rc=0 warehouse
  # Before the lease step 0a has not resolved _GRANTS_WAREHOUSE_ID yet.
  warehouse="${_GRANTS_WAREHOUSE_ID:-${DATABRICKS_WAREHOUSE_ID:-$(dotenv_value DATABRICKS_WAREHOUSE_ID)}}"
  [[ -n "$warehouse" ]] || return 3
  "$PYTHON" -m tools.databricks.catalog_bootstrap_skip_rule check \
    --warehouse-id "$warehouse" \
    --out "$out" || rc=$?
  if [[ "$rc" -eq 0 ]]; then
    return 0
  fi
  return 3
}

# The source-free binding payload the release would apply (WR1). A summary or
# build failure leaves no file, which the plan reads as an unresolved binding:
# a window, never a silent skip.
collect_quiesce_window_binding_payload() {
  local out="${1:?binding payload path is required}" summary
  summary="$(mktemp -t mip-quiesce-window-summary.XXXXXX.json)"
  chmod 600 "$summary"
  if "$PYTHON" -m tools.databricks.bundle_env summary -t "$TARGET" -o json > "$summary" && \
     "$PYTHON" -m tools.databricks.app_resource_bindings build \
       --bundle-summary "$summary" \
       --app-name "$_GRANTS_APP_NAME" \
       "${OTEL_RESOURCE_BINDING_ARGS[@]}" \
       --out "$out"; then
    rm -f "$summary"
    return 0
  fi
  rm -f "$summary" "$out"
  return 1
}

# Plan the window from live facts (tools/databricks/quiesce_window_plan.py).
# pre-lease predicts it before the lease; pre-apply recomputes it under the
# lease right before the non-App apply (the TOCTOU recheck). A window needs
# MIP_ACK_QUIESCE_WINDOW equal to its sha256 at both positions, so the reasons
# it opens are exactly the predicted ones. A refusal exits 2 with the App
# untouched.
plan_quiesce_window() {
  local position="${1:?window plan position is required}"
  local gate_report="${2:?bundle plan gate report is required}"
  local probe_dir role_rc=0 plan_rc=0
  local -a plan_args=(--position "$position")
  step "plan the quiesce window (${position}, read-only)"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "  would plan read-only: WR1 binding diff, WR2 App role --check, WR3 Lakebase migration plan, WR4 acked instance update; a window opens only with MIP_ACK_QUIESCE_WINDOW equal to its sha256"
    return 0
  fi
  probe_dir="$(mktemp -d -t mip-quiesce-window.XXXXXX)"
  chmod 700 "$probe_dir"
  "$PYTHON" -m tools.databricks.quiesce_window applicable \
    --app-name "$_GRANTS_APP_NAME" \
    --out-env "$probe_dir/applicable.env"
  unset MIP_QUIESCE_WINDOW_APP_PRESENT MIP_QUIESCE_WINDOW_APP_CLIENT_ID
  # No file reads as "no App": the plan tool then reads the App itself and
  # refuses an installed signed blue that arrives without its probes.
  if [[ -s "$probe_dir/applicable.env" ]]; then
    # shellcheck disable=SC1091
    . "$probe_dir/applicable.env"
  fi
  if [[ "${MIP_QUIESCE_WINDOW_APP_PRESENT:-0}" == "1" && \
        "${MIP_REBASE_UNVERIFIED_APP:-0}" != "1" ]]; then
    if collect_quiesce_window_binding_payload "$probe_dir/binding.json"; then
      plan_args+=(--binding-payload "$probe_dir/binding.json")
    fi
    run_with_lakebase_bootstrap_authority \
      "$PYTHON" -m tools.databricks.converge_lakebase_oauth_role --check \
      --lakebase-instance "$MIP_LAKEBASE_INSTANCE" \
      --lakebase-database "$LAKEBASE_DATABASE" \
      --application-id "$MIP_QUIESCE_WINDOW_APP_CLIENT_ID" \
      --role-contract app || role_rc=$?
    case "$role_rc" in
      0) plan_args+=(--role-check exact) ;;
      3) plan_args+=(--role-check drift) ;;
      *)
        echo "${RED}[deploy] the App role check could not decide (exit ${role_rc}); nothing was changed.${RST}" >&2
        exit 2
        ;;
    esac
    # plan exits 2 for a refusing plan and still prints its report.
    "$PYTHON" jobs/lakebase_migrate.py --mode plan \
      --app-name "$_GRANTS_APP_NAME" \
      --lakebase-instance "$MIP_LAKEBASE_INSTANCE" \
      --lakebase-database "$LAKEBASE_DATABASE" \
      --lender-name "$MIP_LENDER_NAME" \
      --lender-nmls-id "$MIP_LENDER_NMLS_ID" \
      --tenant-id "$MIP_TENANT_ID" \
      --ai-gateway-verifier-client-id "$DATABRICKS_VERIFIER_CLIENT_ID" \
      --require-ai-gateway-verifier > "$probe_dir/migration.json" || true
    plan_args+=(--migration-plan "$probe_dir/migration.json")
  fi
  QUIESCE_WINDOW_PLAN_JSON="$probe_dir/plan.json"
  "$PYTHON" -m tools.databricks.quiesce_window plan \
    --app-name "$_GRANTS_APP_NAME" \
    --scope "$APP_ROLLBACK_SECRET_SCOPE" \
    --lakebase-instance "$MIP_LAKEBASE_INSTANCE" \
    --plan-gate-report "$gate_report" \
    --rebase-unverified-app "${MIP_REBASE_UNVERIFIED_APP:-0}" \
    --out "$QUIESCE_WINDOW_PLAN_JSON" \
    "${plan_args[@]}" || plan_rc=$?
  if [[ "$plan_rc" -ne 0 ]]; then
    echo "${RED}[deploy] the quiesce window plan refused (${position}); its private plan is ${QUIESCE_WINDOW_PLAN_JSON}. Nothing was changed.${RST}" >&2
    exit 2
  fi
  if ! read -r QUIESCE_WINDOW_CODE QUIESCE_WINDOW_REASONS < <("$PYTHON" -c '
import json, sys
plan = json.load(open(sys.argv[1], encoding="utf-8"))
print(plan["code"], ",".join(plan["reasons"]) or "-")
' "$QUIESCE_WINDOW_PLAN_JSON"); then
    echo "${RED}[deploy] the quiesce window plan wrote no readable report (${position}); nothing was changed.${RST}" >&2
    exit 2
  fi
  echo "  quiesce window plan (${position}, private): ${QUIESCE_WINDOW_PLAN_JSON}"
}

# ---------------------------------------------------------------------------
# The window
# ---------------------------------------------------------------------------

quiesce_window_has_reason() {
  [[ ",${QUIESCE_WINDOW_REASONS:-}," == *",${1:?reason is required},"* ]]
}

# Open the window at the first mutation that needs it: WR4 before the non-App
# apply, WR1 before the binding update, WR2 before the role convergence and
# WR3 before the migration. Ungoverned runs and an open window return at once.
ensure_quiesce_window_open_for() {
  local reason
  [[ "${QUIESCE_WINDOW_STATE:-}" == "blue_serving" || \
     "${QUIESCE_WINDOW_STATE:-}" == "blue_degraded" ]] || return 0
  for reason in "$@"; do
    if quiesce_window_has_reason "$reason"; then
      open_quiesce_window
      return $?
    fi
  done
  return 0
}

# TOCTOU-checked open: the tool re-validates the pre-apply plan, its digest and
# the exact ack, re-proves blue is the App's last deployment, journals
# window_open and only then stops the App, identity-pinned. The state switches
# first, so a failure after the journal write reaches the window compensation,
# which reads the journal rather than trusting this variable.
open_quiesce_window() {
  local env_file
  step "open the quiesce window for ${QUIESCE_WINDOW_REASONS} (journal window_open, then stop)"
  env_file="$(mktemp -t mip-quiesce-window-open.XXXXXX.env)"
  chmod 600 "$env_file"
  QUIESCE_WINDOW_STATE="window_open"
  run_with_proof_signing_authority \
    "$PYTHON" -m tools.databricks.quiesce_window open \
    --app-name "$_GRANTS_APP_NAME" \
    --plan "${QUIESCE_WINDOW_PLAN_JSON:?pre-apply window plan is required}" \
    --deployment-lease-id "$MIP_APP_DEPLOYMENT_LEASE_ID" \
    --deployment-source-git-sha "$SOURCE_GIT_SHA" \
    --expected-app-id "$_EXISTING_APP_ID" \
    --expected-client-id "$_EXISTING_APP_SP_CLIENT_ID" \
    --expected-scim-id "$_EXISTING_APP_SP_SCIM_ID" \
    --out-env "$env_file" || return 1
  # shellcheck disable=SC1090
  . "$env_file"
  rm -f "$env_file"
  QUIESCE_WINDOW_BLUE_RESTORABLE="$MIP_QUIESCE_WINDOW_BLUE_RESTORABLE"
  QUIESCE_WINDOW_BLUE_DEPLOYMENT_ID="$MIP_QUIESCE_WINDOW_BLUE_DEPLOYMENT_ID"
  QUIESCE_WINDOW_TREE_SHA256="$MIP_QUIESCE_WINDOW_TREE_SHA256"
}

# Start, wait for exactly the deployment the start created, require SUCCEEDED,
# prove it is blue (source, unchanged folder digest, snapshot digest, env-name
# set), re-pin the signed record to it under the lease, check health against
# the re-pinned id, then close the journal. A contract window
# (blue_restorable=0) stays open: blue cannot run on the migrated schema, and
# green activation closes it (end_quiesce_window_at_activation).
close_quiesce_window() {
  local env_file
  [[ "${QUIESCE_WINDOW_STATE:-}" == "window_open" ]] || return 0
  if [[ "${QUIESCE_WINDOW_BLUE_RESTORABLE:-0}" != "1" ]]; then
    step "keep the App stopped: a contract migration ran, so only green activation closes this window"
    return 0
  fi
  env_file="$(mktemp -t mip-quiesce-window-close.XXXXXX.env)"
  chmod 600 "$env_file"
  step "close the quiesce window: start blue and wait for the deployment the start created"
  run_with_proof_signing_authority \
    "$PYTHON" -m tools.databricks.quiesce_window start \
    --app-name "$_GRANTS_APP_NAME" \
    --deployment-lease-id "$MIP_APP_DEPLOYMENT_LEASE_ID" \
    --deployment-source-git-sha "$SOURCE_GIT_SHA" \
    --out-env "$env_file" || return 1
  repin_proven_quiesce_window "$env_file"
}

# await, repin, close: shared by the normal close and the EXIT compensation.
repin_proven_quiesce_window() {
  local env_file="${1:?window env file is required}"
  step "prove the start deployment is blue (SUCCEEDED, source, digests, env names)"
  run_with_proof_signing_authority \
    "$PYTHON" -m tools.databricks.quiesce_window await \
    --app-name "$_GRANTS_APP_NAME" \
    --scope "$APP_ROLLBACK_SECRET_SCOPE" \
    --lakebase-instance "$MIP_LAKEBASE_INSTANCE" \
    --deployment-lease-id "$MIP_APP_DEPLOYMENT_LEASE_ID" \
    --deployment-source-git-sha "$SOURCE_GIT_SHA" \
    --expected-app-id "$_EXISTING_APP_ID" \
    --expected-client-id "$_EXISTING_APP_SP_CLIENT_ID" \
    --expected-scim-id "$_EXISTING_APP_SP_SCIM_ID" \
    --out-env "$env_file" || return 1
  # shellcheck disable=SC1090
  . "$env_file"
  rm -f "$env_file"
  repin_and_close_quiesce_window "$MIP_QUIESCE_WINDOW_PROVEN_DEPLOYMENT_ID"
}

# The proven start deployment becomes the signed record (same provenance,
# predecessor_deployment_id), health is checked against it, the journal closes.
repin_and_close_quiesce_window() {
  local proven="${1:?proven start deployment id is required}"
  mint_m2m_token MIP_BEARER_TOKEN DATABRICKS_CLIENT_ID DATABRICKS_CLIENT_SECRET || return 1
  step "re-pin the signed record to the proven start deployment and check its health"
  run_with_account_identity run_with_proof_signing_authority \
    "$PYTHON" -m tools.databricks.app_deployment_rollback repin \
    --app-name "$_GRANTS_APP_NAME" \
    --scope "$APP_ROLLBACK_SECRET_SCOPE" \
    --base-url "${MIP_APP_URL:?App URL is required for the re-pin health proof}" \
    --token-env MIP_BEARER_TOKEN \
    --to "$proven" \
    --expected-rollback-deployment-id "${QUIESCE_WINDOW_BLUE_DEPLOYMENT_ID:?}" \
    --expected-tree-sha256 "${QUIESCE_WINDOW_TREE_SHA256:?}" \
    --deployment-lease-id "$MIP_APP_DEPLOYMENT_LEASE_ID" \
    --deployment-source-git-sha "$SOURCE_GIT_SHA" \
    --out-env "${APP_ROLLBACK_BINDING_ENV:?}" || return 1
  set -a
  # shellcheck disable=SC1090
  . "$APP_ROLLBACK_BINDING_ENV"
  set +a
  run_with_proof_signing_authority \
    "$PYTHON" -m tools.databricks.quiesce_window close \
    --app-name "$_GRANTS_APP_NAME" \
    --scope "$APP_ROLLBACK_SECRET_SCOPE" \
    --lakebase-instance "$MIP_LAKEBASE_INSTANCE" \
    --repinned "$proven" \
    --deployment-lease-id "$MIP_APP_DEPLOYMENT_LEASE_ID" \
    --deployment-source-git-sha "$SOURCE_GIT_SHA" || return 1
  QUIESCE_WINDOW_STATE="blue_serving"
  QUIESCE_WINDOW_BLUE_DEPLOYMENT_ID="$proven"
}

# Blue keeps serving while a dependency it reads is mutated; a failure from
# here converges that dependency back with the App up.
mark_blue_degraded() {
  if [[ "${QUIESCE_WINDOW_STATE:-}" == "blue_serving" ]]; then
    QUIESCE_WINDOW_STATE="blue_degraded"
    echo "  blue keeps serving, degraded: ${1:-a dependency is being mutated}"
  fi
}

# Green activation supersedes every window state; a contract window's journal
# closes here, the App being started only to activate green.
end_quiesce_window_at_activation() {
  if [[ "${QUIESCE_WINDOW_STATE:-}" == "window_open" ]]; then
    run run_with_proof_signing_authority \
      "$PYTHON" -m tools.databricks.quiesce_window close \
      --app-name "$_GRANTS_APP_NAME" \
      --superseded-by-activation \
      --deployment-lease-id "$MIP_APP_DEPLOYMENT_LEASE_ID" \
      --deployment-source-git-sha "$SOURCE_GIT_SHA"
  fi
  QUIESCE_WINDOW_STATE=""
}

# ---------------------------------------------------------------------------
# EXIT-trap compensation for the window states (stop_app_after_failed_deploy)
# ---------------------------------------------------------------------------

# window_open: start and re-pin if every proof holds; otherwise leave the App
# as it is with the journal open. Never a redeploy, never another stop here
# (await itself stops a start deployment it cannot prove).
compensate_open_quiesce_window() {
  local env_file state
  env_file="$(mktemp -t mip-quiesce-window-status.XXXXXX.env)"
  chmod 600 "$env_file"
  if ! "$PYTHON" -m tools.databricks.quiesce_window status \
    --app-name "$_GRANTS_APP_NAME" \
    --out-env "$env_file"; then
    rm -f "$env_file"
    return 1
  fi
  # shellcheck disable=SC1090
  . "$env_file"
  state="$MIP_QUIESCE_WINDOW_JOURNAL_STATE"
  QUIESCE_WINDOW_BLUE_RESTORABLE="$MIP_QUIESCE_WINDOW_BLUE_RESTORABLE"
  QUIESCE_WINDOW_BLUE_DEPLOYMENT_ID="$MIP_QUIESCE_WINDOW_BLUE_DEPLOYMENT_ID"
  QUIESCE_WINDOW_TREE_SHA256="$MIP_QUIESCE_WINDOW_TREE_SHA256"
  case "$state" in
    absent|closed)
      # The open refused before its journal write: the App was never stopped.
      rm -f "$env_file"
      QUIESCE_WINDOW_STATE="blue_serving"
      return 0
      ;;
    window_open)
      if [[ "$QUIESCE_WINDOW_BLUE_RESTORABLE" != "1" ]]; then
        echo "${RED}[deploy] a contract migration window is open: the App stays stopped; the dead-man (M0-PR9) leaves it stopped.${RST}" >&2
        rm -f "$env_file"
        return 1
      fi
      rm -f "$env_file"
      close_quiesce_window
      return $?
      ;;
    window_started)
      repin_proven_quiesce_window "$env_file"
      return $?
      ;;
    window_proven)
      # Proven before the failure, not yet re-pinned: the re-pin re-proves it.
      rm -f "$env_file"
      repin_and_close_quiesce_window "$MIP_QUIESCE_WINDOW_START_DEPLOYMENT_ID"
      return $?
      ;;
  esac
  echo "${RED}[deploy] the quiesce window journal is ${state}: the App is left as it is for the dead-man (M0-PR9).${RST}" >&2
  rm -f "$env_file"
  return 1
}

# blue_serving and blue_degraded: prove blue still serves the signed record,
# then converge runtime access with the App up. Never a stop.
compensate_serving_blue() {
  local failed=0
  if ! "$PYTHON" -m tools.databricks.quiesce_window assert-serving \
    --app-name "$_GRANTS_APP_NAME" \
    --scope "$APP_ROLLBACK_SECRET_SCOPE" \
    --lakebase-instance "$MIP_LAKEBASE_INSTANCE"; then
    echo "${RED}[deploy] the App is not serving the signed record; runtime access stays quiesced and the App is left up.${RST}" >&2
    return 1
  fi
  if [[ "${QUIESCE_WINDOW_STATE:-}" == "blue_degraded" ]]; then
    if [[ "${REVIEWED_FUNCTION_GRANTS_PROVEN:-0}" -ne 1 ]] && \
       ! reconcile_reviewed_function_execute_grants; then
      failed=1
    fi
    if ! "$PYTHON" -m tools.databricks.converge_app_lakebase_sync_access \
      --mode runtime \
      --warehouse-id "$_GRANTS_WAREHOUSE_ID" \
      --app-application-id "$APP_SP_CLIENT_ID" \
      --app-scim-id "$APP_SP_SCIM_ID" \
      --sync-catalog "$DEPLOYMENT_SYNC_CATALOG" \
      --sync-schema "$DEPLOYMENT_SYNC_SCHEMA" \
      --sync-tables "$DEPLOYMENT_SYNC_TABLES"; then
      failed=1
    fi
  fi
  if ! converge_runtime_app_release_access; then
    failed=1
  fi
  if [[ "${TREATMENT_RUNTIME_QUIESCED:-0}" -eq 1 ]]; then
    if converge_app_treatment_access runtime; then
      TREATMENT_RUNTIME_QUIESCED=0
    else
      failed=1
    fi
  fi
  if [[ "$failed" -ne 0 ]]; then
    echo "${RED}[deploy] blue keeps serving, but its runtime access did not converge back; the lease is retained.${RST}" >&2
  fi
  return "$failed"
}

compensate_quiesce_window_state() {
  if [[ "${QUIESCE_WINDOW_STATE:-}" == "window_open" ]] && ! compensate_open_quiesce_window; then
    echo "${RED}[deploy] the quiesce window stays open; the dead-man (M0-PR9) restarts blue only when it is restorable.${RST}" >&2
    return 1
  fi
  compensate_serving_blue
}

# ---------------------------------------------------------------------------
# Named stops and starts the step files call
# ---------------------------------------------------------------------------

# Step 0a: prove the signed last-good App, or restore it (tier b), or start and
# re-pin it when it is stopped and still its last deployment (tier a).
prove_or_restore_signed_blue() {
  local out_env="${1:?binding env path is required}"
  run_with_account_identity \
    run_with_proof_signing_authority \
      "$PYTHON" -m tools.databricks.app_deployment_rollback ensure \
    --app-name "$_GRANTS_APP_NAME" \
    --scope "$APP_ROLLBACK_SECRET_SCOPE" \
    --base-url "${MIP_APP_URL:?existing App URL is required}" \
    --token-env MIP_BEARER_TOKEN \
    --deployment-lease-id "$MIP_APP_DEPLOYMENT_LEASE_ID" \
    --deployment-source-git-sha "$SOURCE_GIT_SHA" \
    --treatment-warehouse-id "$_GRANTS_WAREHOUSE_ID" \
    --treatment-catalog "$_GRANTS_CATALOG" \
    --out-env "$out_env"
}

# The unsigned rebase stops the exact legacy App for the whole run (no blue).
stop_unsigned_rebase_app() {
  "$PYTHON" -m tools.databricks.stop_app_fail_closed \
    --app-name "$_GRANTS_APP_NAME" \
    "${APP_EXPECTED_IDENTITY_ARGS[@]}"
}

# The interrupted first install the signed journal owns, before its deletion.
stop_journal_owned_first_install_app() {
  "$PYTHON" -m tools.databricks.stop_app_fail_closed \
    --app-name "$_GRANTS_APP_NAME" \
    --expected-app-id "$FIRST_INSTALL_APP_ID" \
    --expected-client-id "$FIRST_INSTALL_APP_CLIENT_ID" \
    --expected-scim-id "$FIRST_INSTALL_APP_SCIM_ID"
}

# An existing App no window governs (the unsigned rebase) is stopped before its
# binding update, identity-pinned, exactly as before M0-PR8.
stop_ungoverned_existing_app_before_binding_update() {
  "$PYTHON" -m tools.databricks.stop_app_fail_closed \
    --app-name "$_GRANTS_APP_NAME" \
    --expected-app-id "$_BINDING_APP_ID" \
    --expected-client-id "$_BINDING_APP_CLIENT_ID" \
    --expected-scim-id "$_BINDING_APP_SCIM_ID"
}

# Green failed its final proof after activation: stop it before tier b.
stop_unproven_candidate_before_rollback() {
  "$PYTHON" -m tools.databricks.stop_app_fail_closed \
    --app-name "$APP_NAME" \
    "${APP_EXPECTED_IDENTITY_ARGS[@]}"
}

# A STOPPED App before an activation deploy (first install, rebase, idle stop,
# or a contract window): the start's own deployment is superseded at once by
# the activation, so it is never re-pinned.
start_stopped_app_before_activation() {
  databricks apps start "$APP_NAME"
}

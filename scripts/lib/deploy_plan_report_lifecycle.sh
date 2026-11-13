# shellcheck shell=bash
# The plan's reports and the release's deployment pin (M1-PR7).
#
# --phase plan in deploy-dev.yml publishes its PlanReport in two halves
# (tools/databricks/plan_report_publish.py): PlanReport.public_summary() to
# the job's step summary, and PlanReport.full(), which names resolved objects
# and principals, create-only to the deployer's own bundle root in the client
# workspace (<root>/plan-reports/<run id>-<attempt>.json), whether the gate
# passed or refused. That create-only write is the plan phase's only one (no
# governed resource changes), and it happens under GitHub Actions only. No artifact carries
# either half. The plan also prints the sha256 of the App's active deployment
# id, and a --phase release given MIP_EXPECTED_ACTIVE_DEPLOYMENT_SHA256 (the
# dispatch input expected_active_deployment_sha256, one run only) refuses
# before the lease unless the live digest is still that one
# (tools/databricks/active_deployment_pin.py).
# Sourced by scripts/deploy.sh; this file must not change shell options or traps.
# shellcheck disable=SC2034  # run_bundle_plan_gate sets these for publish_plan_report.

BUNDLE_PLAN_REPORT_JSON=""
BUNDLE_PLAN_PUBLIC_JSON=""
BUNDLE_PLAN_REPORT_AFTER_EDIT=0

# --phase adopt and --phase unadopt (wave 7f) publish install_state_adopt's
# private report create-only to the same path, under GitHub Actions only: the
# bind plan, the post-bind gate, and for a refused plan the parser's message
# and the document's value-free skeleton, or the CLI's own output
# (cli_errors), none of which the public log carries. One per run: the plan's
# when the run ends at the plan (a refusal, or no MIP_ADOPT_PLAN_ACK), the
# apply's when it reaches the apply; neither has a public half. After the
# apply (BUNDLE_PLAN_REPORT_AFTER_EDIT=1) a failed write never changes the
# run's verdict: the state edits stand as the tool reported them, and the
# report stays on the runner only.
publish_plan_report() {
  case "${DEPLOY_PHASE:-}" in
    plan|adopt|unadopt) ;;
    *) return 0 ;;
  esac
  [[ -n "$BUNDLE_PLAN_REPORT_JSON" ]] || return 0
  if [[ "$DRY_RUN" -eq 1 ]]; then
    if [[ -n "$BUNDLE_PLAN_PUBLIC_JSON" ]]; then
      echo "  would publish: the public summary to the step summary, the full report to the deployer's plan-reports folder (CI only)"
    else
      echo "  would publish: the private ${DEPLOY_PHASE} report to the deployer's plan-reports folder (CI only)"
    fi
    return 0
  fi
  if [[ "${DEPLOY_PHASE:-}" != "plan" && ! -s "$BUNDLE_PLAN_REPORT_JSON" ]]; then
    echo "  no ${DEPLOY_PHASE} report to publish: the tool wrote none (its refusal is above)"
    return 0
  fi
  if [[ -n "$BUNDLE_PLAN_PUBLIC_JSON" && -n "${GITHUB_STEP_SUMMARY:-}" ]] && \
     ! "$PYTHON" -m tools.databricks.plan_report_publish summary --public "$BUNDLE_PLAN_PUBLIC_JSON" --out "$GITHUB_STEP_SUMMARY"; then
    echo "${RED}[deploy] the plan's public summary could not be written; no governed resource was changed.${RST}" >&2
    exit 2
  fi
  [[ "${GITHUB_ACTIONS:-}" == "true" && -n "${GITHUB_RUN_ID:-}" ]] || return 0
  if ! run "$PYTHON" -m tools.databricks.plan_report_publish publish \
      --full "$BUNDLE_PLAN_REPORT_JSON" \
      --principal "$CLIENT_DEPLOYER" \
      --client "$CLIENT_KEY" \
      --run-id "$GITHUB_RUN_ID" \
      --run-attempt "${GITHUB_RUN_ATTEMPT:-1}"; then
    if [[ "$BUNDLE_PLAN_REPORT_AFTER_EDIT" -eq 1 ]]; then
      echo "${YLW}[deploy] the private ${DEPLOY_PHASE} report could not be written to the deployer's plan-reports folder; it stays on the runner, and the ${DEPLOY_PHASE}'s verdict below stands.${RST}" >&2
      return 0
    fi
    echo "${RED}[deploy] the full plan report could not be written to the deployer's plan-reports folder; no governed resource was changed.${RST}" >&2
    exit 2
  fi
}

print_active_deployment_digest() {
  echo "${DIM}\$ $PYTHON -m tools.databricks.active_deployment_pin digest --app-name ${_GRANTS_APP_NAME}${RST}"
  [[ "$DRY_RUN" -eq 0 ]] || return 0
  if ! "$PYTHON" -m tools.databricks.active_deployment_pin digest --app-name "$_GRANTS_APP_NAME"; then
    echo "${RED}[deploy] the App's active deployment could not be read; no governed resource was changed.${RST}" >&2
    exit 2
  fi
}

check_expected_active_deployment() {
  [[ -n "${MIP_EXPECTED_ACTIVE_DEPLOYMENT_SHA256:-}" ]] || return 0
  step "the active deployment is still the one the dispatch expected (read-only)"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "  would check read-only: the sha256 of ${_GRANTS_APP_NAME}'s active deployment id is ${MIP_EXPECTED_ACTIVE_DEPLOYMENT_SHA256}"
    return 0
  fi
  if ! "$PYTHON" -m tools.databricks.active_deployment_pin check --app-name "$_GRANTS_APP_NAME" \
      --expect "$MIP_EXPECTED_ACTIVE_DEPLOYMENT_SHA256"; then
    echo "${RED}[deploy] the release refused before the lease; nothing was changed.${RST}" >&2
    exit 2
  fi
}

# The core release's pre-activation rollback proof, predicted read-only before
# the lease (B3, tools/databricks/activation_prediction.py): the plan prints
# the restore_unverified_frontend digest an adopted blue's activation needs,
# and a release (or a run with no --phase) refuses here, with nothing changed,
# what the proof at step 10b would refuse after the apply and the window. Its
# payload flags are rebuild_core_blue_restore_payload's, byte for byte. No
# signing authority: the verify key alone checks the record. MIP_ACK_NO_ROLLBACK_FOR
# (shell only) defers its verdicts to the proof; its value is never passed.
predict_activation_rollback() {
  local -a args=()
  ! tier_is_agentic || return 0
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "  would predict read-only, before the lease: blue's restore payload and the restore_unverified_frontend ack its activation needs"
    return 0
  fi
  args=(
    --phase "${DEPLOY_PHASE:-complete}"
    --app-name "$_GRANTS_APP_NAME"
    --scope "$APP_ROLLBACK_SECRET_SCOPE"
    --lakebase-instance "$MIP_LAKEBASE_INSTANCE"
    --lakebase-database "$LAKEBASE_DATABASE"
    --agentic-tier "$CLIENT_AGENTIC_TIER"
    --install-class "$CLIENT_INSTALL_CLASS"
    --app-env "$APP_RUNTIME_ENV"
    --catalog "${MIP_DEFAULT_CATALOG:-mip}"
    --schema "${MIP_DEFAULT_SCHEMA:-gold}"
    --source-git-sha "$SOURCE_GIT_SHA"
  )
  [[ -z "${MIP_ACK_RESTORE_UNVERIFIED_FRONTEND:-}" ]] || \
    args+=(--restore-unverified-frontend-ack "$MIP_ACK_RESTORE_UNVERIFIED_FRONTEND")
  [[ -z "${MIP_ACK_NO_ROLLBACK_FOR:-}" || "${DEPLOY_PHASE:-}" == "plan" ]] || args+=(--no-rollback-ack-given)
  [[ "${DEPLOY_PHASE:-}" != "plan" || -z "${GITHUB_STEP_SUMMARY:-}" ]] || \
    args+=(--step-summary "$GITHUB_STEP_SUMMARY")
  if ! run "$PYTHON" -m tools.databricks.activation_prediction predict "${args[@]}"; then
    publish_plan_report
    echo "${RED}[deploy] the pre-activation rollback proof would refuse (see above); refusing before the lease. Nothing was changed.${RST}" >&2
    exit 2
  fi
}

# A plan before an adoption (the cutover's plans #1 and #2) ends at the client
# live checks, before any step above runs. Before that exit it measures, names
# only, whether this profile rebuilds the live App's newest deployment's
# env-name set, which step 12's tier-b restore payload needs (the cutover's
# step-7 precondition). The tool proves the workspace and the deployer itself
# first and prints names and a verdict, never a value; the plan exits 2 anyway.
measure_blue_env_names_before_adoption() {
  local -a args=()
  [[ "${DEPLOY_PHASE:-}" == "plan" ]] || return 0
  ! tier_is_agentic || return 0
  [[ -z "${GITHUB_STEP_SUMMARY:-}" ]] || args=(--step-summary "$GITHUB_STEP_SUMMARY")
  run "$PYTHON" -m tools.databricks.activation_prediction env-names "${args[@]}" || true
}

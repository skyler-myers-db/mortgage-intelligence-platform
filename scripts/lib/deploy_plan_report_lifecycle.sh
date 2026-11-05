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

publish_plan_report() {
  [[ "${DEPLOY_PHASE:-}" == "plan" && -n "$BUNDLE_PLAN_REPORT_JSON" ]] || return 0
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "  would publish: the public summary to the step summary, the full report to the deployer's plan-reports folder (CI only)"
    return 0
  fi
  if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]] && \
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

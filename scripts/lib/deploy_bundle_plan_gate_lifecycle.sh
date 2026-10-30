# shellcheck shell=bash
# The bundle plan gate (tools/databricks/bundle_plan_gate.py): plan, then refuse
# a delete, recreate, identity rename, unknown kind, name collision, unadopted
# install or unacknowledged Lakebase instance update.
# Sourced by scripts/deploy.sh; this file must not change shell options or traps.

# Plan the bundle read-only and judge the plan. pre-lease runs before the signed
# lease and every App stop, and may plan before a first install provisions the
# governed Genie space (bundle_env binds the placeholder only when no space has
# the governed title). pre-apply re-plans under the lease, after provisioning,
# immediately before the non-App bundle apply, so a plan that changed since the
# first gate (time of check, time of use) is judged again. A refusal exits 2.
run_bundle_plan_gate() {
  local position="${1:?bundle plan gate position is required}"
  local plan_json report_json
  local -a genie_args=()
  case "$position" in
    pre-lease) genie_args=(--allow-unprovisioned-genie) ;;
    pre-apply) ;;
    *)
      echo "${RED}[deploy] unknown bundle plan gate position '${position}'.${RST}" >&2
      exit 2
      ;;
  esac
  step "plan direct deployment against -t ${TARGET} and gate it (${position})"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    plan_json="/tmp/mip-dry-run-bundle-plan-${position}.json"
    report_json="/tmp/mip-dry-run-bundle-plan-report-${position}.json"
  else
    plan_json="$(mktemp -t mip-bundle-plan.XXXXXX.json)"
    report_json="$(mktemp -t mip-bundle-plan-report.XXXXXX.json)"
    chmod 600 "$plan_json" "$report_json"
  fi
  if ! run_json_to_file "$plan_json" \
      "$PYTHON" -m tools.databricks.bundle_env plan -t "$TARGET" -o json "${genie_args[@]}"; then
    [[ "$DRY_RUN" -eq 1 ]] || rm -f "$plan_json"
    echo "${RED}[deploy] the bundle plan could not be computed (${position}); nothing was applied.${RST}" >&2
    exit 2
  fi
  # The raw plan holds remote state; only the gate's report outlives this call.
  local gate_rc=0
  run "$PYTHON" -m tools.databricks.bundle_plan_gate check \
    --plan "$plan_json" \
    --position "$position" \
    --app-key mip_app \
    --report-out "$report_json" || gate_rc=$?
  [[ "$DRY_RUN" -eq 1 ]] || rm -f "$plan_json"
  if [[ "$gate_rc" -ne 0 ]]; then
    echo "${RED}[deploy] the bundle plan gate refused (${position}); its full report, which names resources and stays private, is ${report_json}.${RST}" >&2
    exit 2
  fi
  [[ "$DRY_RUN" -eq 1 ]] || echo "  bundle plan report (${position}, private): ${report_json}"
}

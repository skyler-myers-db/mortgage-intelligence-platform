# shellcheck shell=bash
# The adopt and unadopt phases (M2-PR6): bind an existing install's legacy
# bundle state into this deployer's state, or unbind exactly that again
# (tools/databricks/install_state_adopt.py). Neither stops, starts or deploys
# the App, changes a grant or deletes anything: the only mutation is this
# deployer's own bundle state, under the signed lease. Signing the live
# deployment as an adopted blue is M2-PR7's (adopt-live), not these phases'.
# Sourced by scripts/deploy.sh; this file must not change shell options or traps.

# The read-only plan, before the signed lease, in place of the pre-lease bundle
# plan gate (which refuses an unadopted install as not_adopted). It prints the
# plan's sha256. Without MIP_ADOPT_PLAN_ACK (shell only, never .env.local) the
# run ends here, read-only; with it, the plan must be exactly that digest
# (the deferred owner attestation's substitute) and the run continues to the
# lease. A dry run prints both commands and stops before the lease.
run_adopt_phase_plan() {
  local phase="${1:?adopt phase is required}"
  local report_json
  local -a ack_args=()
  [[ -z "${MIP_ADOPT_PLAN_ACK:-}" ]] || ack_args=(--ack-plan "$MIP_ADOPT_PLAN_ACK")
  step "plan the ${phase} of this install's bundle state read-only (the App keeps serving)"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    report_json="/tmp/mip-dry-run-${phase}-plan-report.json"
  else
    report_json="$(mktemp -t "mip-${phase}-plan-report.XXXXXX.json")"
    chmod 600 "$report_json"
  fi
  if ! run "$PYTHON" -m tools.databricks.install_state_adopt plan \
      --phase "$phase" --report-out "$report_json" "${ack_args[@]}"; then
    echo "${RED}[deploy] the ${phase} plan refused before the lease; its full report, which names resources and stays private, is ${report_json}. Nothing was changed.${RST}" >&2
    exit 2
  fi
  [[ "$DRY_RUN" -eq 1 ]] || echo "  ${phase} plan report (private): ${report_json}"
  if [[ "${#ack_args[@]}" -eq 0 ]]; then
    echo "${GRN}[deploy] ${phase} plan complete: read-only, stopped before the lease; nothing was changed. Review it, then re-run with MIP_ADOPT_PLAN_ACK=<its sha256> exported in the shell.${RST}"
    exit 0
  fi
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "  would take the signed lease, then: $PYTHON -m tools.databricks.install_state_adopt apply --phase ${phase} --ack-plan ${MIP_ADOPT_PLAN_ACK} (recomputed under the lease; the App is never stopped)"
    echo "${GRN}[deploy] ${phase} dry run complete: stopped before the lease; nothing was changed.${RST}"
    exit 0
  fi
}

# Under the signed lease, before any other mutation of the run: recompute the
# plan, refuse unless it is still the acknowledged digest, then bind (adopt) or
# unbind (unadopt) exactly its keys. adopt judges the plan right after its
# binds with the bundle plan gate's adopt mode and undoes its own binds on any
# refusal. The run then ends; the EXIT trap releases the lease. Exit 3 from
# the tool means the undo is unproven: the report says which keys the state may
# still bind, --phase unadopt recovers that half-bound state (its live checks
# accept an unbound App), and the next release's own plan gate refuses it (its
# unbound legacy keys plan creates of existing names).
run_adopt_phase_apply() {
  local phase="${1:?adopt phase is required}"
  local report_json rc=0
  report_json="$(mktemp -t "mip-${phase}-apply-report.XXXXXX.json")"
  chmod 600 "$report_json"
  step "${phase}: edit exactly the acknowledged bundle state plan under the lease (no App stop, no grant change)"
  run_with_proof_signing_authority \
    "$PYTHON" -m tools.databricks.install_state_adopt apply \
    --phase "$phase" \
    --ack-plan "${MIP_ADOPT_PLAN_ACK:?the ${phase} apply needs the acknowledged plan digest}" \
    --app-name "$_GRANTS_APP_NAME" \
    --lease-id "$MIP_APP_DEPLOYMENT_LEASE_ID" \
    --source-git-sha "$SOURCE_GIT_SHA" \
    --report-out "$report_json" || rc=$?
  echo "  ${phase} report (private): ${report_json}"
  if [[ "$rc" -eq 3 ]]; then
    echo "${RED}[deploy] the ${phase} refused and its undo is unproven; read the report, then run --phase unadopt. The App was never stopped.${RST}" >&2
    exit 3
  fi
  if [[ "$rc" -ne 0 ]]; then
    echo "${RED}[deploy] the ${phase} refused under the lease; every state edit it made was undone and the App was never stopped.${RST}" >&2
    exit 2
  fi
  echo "${GRN}[deploy] ${phase} phase complete: bundle state edited exactly as acknowledged; the App was not stopped and no grant changed.${RST}"
  exit 0
}

# shellcheck shell=bash
# The adopt and unadopt phases (M2-PR6): bind an existing install's legacy
# bundle state into this deployer's state, or unbind exactly that again
# (tools/databricks/install_state_adopt.py), then sign the live deployment as
# an adopted blue, or retire that record (adopt-live, M2-PR7:
# tools/databricks/adopt_live.py). Neither stops, starts or deploys the App,
# changes a grant or deletes anything: the mutations are this deployer's own
# bundle state, the create-only adopted copy, the rollback scope and one
# signed record version, under the signed lease. Also the adopted blue's
# pre-activation rollback proof (attack O2), which the release calls.
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
  run_adopt_live_plan "$phase"
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
  run_adopt_live_apply "$phase"
  echo "${GRN}[deploy] ${phase} phase complete: bundle state edited exactly as acknowledged; the App was not stopped and no grant changed.${RST}"
  exit 0
}

# adopt-live's mode, purpose-named ack and arguments for a phase. adopt signs
# (MIP_ACK_ADOPT_LIVE), or with MIP_ADOPT_LIVE_SUPERSEDE=<sha256 of the
# record's deployment id> re-signs onto a newer active deployment
# (MIP_ACK_SUPERSEDE); unadopt writes the tombstone (MIP_ACK_UNADOPT). Each ack
# is a one-run shell export of the digest adopt-live's plan printed, never a
# .env.local line; MIP_ADOPT_REFERENCE_DIST (shell only, optional) is a dist
# built from the health sha, without which the frontend reads as unverified.
adopt_live_mode() {
  local phase="${1:?adopt phase is required}"
  ADOPT_LIVE_MODE="unadopt"
  ADOPT_LIVE_ACK_ENV="MIP_ACK_UNADOPT"
  if [[ "$phase" == "adopt" && -n "${MIP_ADOPT_LIVE_SUPERSEDE:-}" ]]; then
    ADOPT_LIVE_MODE="supersede"
    ADOPT_LIVE_ACK_ENV="MIP_ACK_SUPERSEDE"
  elif [[ "$phase" == "adopt" ]]; then
    ADOPT_LIVE_MODE="adopt"
    ADOPT_LIVE_ACK_ENV="MIP_ACK_ADOPT_LIVE"
  elif [[ -n "${MIP_ADOPT_LIVE_SUPERSEDE:-}" ]]; then
    echo "${RED}[deploy] MIP_ADOPT_LIVE_SUPERSEDE goes with --phase adopt only; refusing before the lease. Nothing was changed.${RST}" >&2
    exit 2
  fi
  ADOPT_LIVE_ACK="${!ADOPT_LIVE_ACK_ENV:-}"
  ADOPT_LIVE_ARGS=(
    --mode "$ADOPT_LIVE_MODE"
    --app-name "$_GRANTS_APP_NAME"
    --scope "$APP_ROLLBACK_SECRET_SCOPE"
    --lakebase-instance "$MIP_LAKEBASE_INSTANCE"
    --expected-principal "$CLIENT_DEPLOYER"
    --expected-root-leaf "$CLIENT_KEY"
    --agentic-tier "$CLIENT_AGENTIC_TIER"
    --catalog "${MIP_DEFAULT_CATALOG:-mip}"
    --main-ref "$SOURCE_GIT_SHA"
  )
  [[ -z "${MIP_ADOPT_LIVE_SUPERSEDE:-}" ]] || ADOPT_LIVE_ARGS+=(--supersede "$MIP_ADOPT_LIVE_SUPERSEDE")
  [[ -z "${MIP_ADOPT_REFERENCE_DIST:-}" ]] || ADOPT_LIVE_ARGS+=(--reference-dist "$MIP_ADOPT_REFERENCE_DIST")
  [[ -z "$ADOPT_LIVE_ACK" ]] || ADOPT_LIVE_ARGS+=(--ack "$ADOPT_LIVE_ACK")
}

# Before the lease, after the bind plan: adopt-live's read-only preconditions
# and the sha256 of its plan. The bind ack without the adopt-live ack refuses
# here, so the lease is never taken for half an adoption.
run_adopt_live_plan() {
  local phase="${1:?adopt phase is required}"
  adopt_live_mode "$phase"
  step "plan adopt-live (${ADOPT_LIVE_MODE}) read-only: every precondition, the full-tree digest and the record slot"
  if [[ "$DRY_RUN" -eq 0 && "$ADOPT_LIVE_MODE" != "unadopt" ]]; then
    mint_m2m_token MIP_BEARER_TOKEN DATABRICKS_CLIENT_ID DATABRICKS_CLIENT_SECRET
  fi
  if ! run "$PYTHON" -m tools.databricks.adopt_live plan "${ADOPT_LIVE_ARGS[@]}"; then
    echo "${RED}[deploy] adopt-live (${ADOPT_LIVE_MODE}) refused before the lease; nothing was changed.${RST}" >&2
    exit 2
  fi
  if [[ -n "${MIP_ADOPT_PLAN_ACK:-}" && -z "$ADOPT_LIVE_ACK" ]]; then
    echo "${RED}[deploy] the ${phase} needs the adopt-live ack as well: export ${ADOPT_LIVE_ACK_ENV}=<the ${ADOPT_LIVE_MODE} plan sha256 above> for this run. Refusing before the lease; nothing was changed.${RST}" >&2
    exit 2
  fi
}

# Under the lease, after the bind (adopt) or the unbind (unadopt): adopt-live
# recomputes its plan, refuses unless it is the acknowledged digest, then
# copies, re-hashes and signs (or writes the tombstone). A refusal writes no
# record version; the binds the bind plan made stay, exactly as acknowledged.
run_adopt_live_apply() {
  local phase="${1:?adopt phase is required}"
  adopt_live_mode "$phase"
  step "adopt-live (${ADOPT_LIVE_MODE}) under the lease: copy, re-hash and sign one record version (the App is never stopped)"
  if ! run_with_proof_signing_authority "$PYTHON" -m tools.databricks.adopt_live apply \
      "${ADOPT_LIVE_ARGS[@]}" \
      --lease-id "$MIP_APP_DEPLOYMENT_LEASE_ID" \
      --source-git-sha "$SOURCE_GIT_SHA"; then
    echo "${RED}[deploy] adopt-live (${ADOPT_LIVE_MODE}) refused under the lease; no record version was written and the App was never stopped. The ${phase}'s bundle state edits stay as acknowledged: re-run the phase, or run --phase unadopt.${RST}" >&2
    exit 2
  fi
}

# The pre-activation rollback proof (attack O2), once per run, right before
# the first activation (the release's snapshot activation function calls it),
# while blue is still active: blue's tier-b restore payload recorded and
# re-proven (tools/databricks/blue_restore_proof.py), or the exact
# no_rollback_for ack (MIP_ACK_NO_ROLLBACK_FOR, shell only). A v5 or v6
# record's own payload is that payload, so the agentic release is unchanged;
# an adopted v7 blue is the core release path's (M2-PR7b), which builds it.
prove_blue_restorable_before_activation() {
  local -a ack_args=()
  [[ "$BLUE_RESTORE_PROVEN" -ne 1 ]] || return 0
  [[ -z "${MIP_ACK_NO_ROLLBACK_FOR:-}" ]] || ack_args=(--no-rollback-ack "$MIP_ACK_NO_ROLLBACK_FOR")
  step "prove blue's tier-b restore payload before green activation (or the exact no-rollback ack)"
  if ! run_with_proof_signing_authority "$PYTHON" -m tools.databricks.blue_restore_proof prepare-activation \
      --app-name "$_GRANTS_APP_NAME" \
      --scope "$APP_ROLLBACK_SECRET_SCOPE" \
      --lakebase-instance "$MIP_LAKEBASE_INSTANCE" \
      --lease-id "$MIP_APP_DEPLOYMENT_LEASE_ID" \
      --source-git-sha "$SOURCE_GIT_SHA" \
      "${ack_args[@]}"; then
    echo "${RED}[deploy] green activation refused before it began: blue's tier-b restore payload is not proven (see above).${RST}" >&2
    exit 2
  fi
  BLUE_RESTORE_PROVEN=1
}

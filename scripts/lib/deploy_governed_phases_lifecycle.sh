# shellcheck shell=bash
# The governed phases (M1-PR6): the plan phase's budget prediction, the
# release's budget admission and the whole data phase. A run with no --phase
# never calls any of them and keeps today's complete deploy (release, then
# data, in one process).
#
# --phase data runs the data jobs (silver unless --skip-silver, then
# mip_scheduled_refresh: FRED -> gold -> lifecycle -> KPI and its outcome
# gate) only through tools/databricks/job_phase_runner.py, under its own signed
# lease, and reconciles and proves the reviewed function EXECUTE grants after
# every job whose graph touches gold or the UC functions (attack O11). It never
# calls the Apps API but the one read-only `apps get` of the App's identity.
# Sourced by scripts/deploy.sh; this file must not change shell options or traps.
# shellcheck disable=SC2034  # The phases assign deploy-wide state (the first-install and fail-closed flags) read by other files.

DATA_PHASE_PLAN_JSON=""
PHASE_BUDGET_JSON=""

# The jobs --phase data runs, in order (tools/databricks/phase_budget.DATA_JOBS).
data_phase_jobs() {
  case "$SKIP_SILVER" in
    1) printf '%s\n' mip_scheduled_refresh ;;
    *) printf '%s\n' mip_refresh_silver mip_scheduled_refresh ;;
  esac
}

# --phase plan, before its exit (read-only): predict every phase's budget
# against this job's deadline and keep the bodies in one local file. The
# release's prediction reads the pre-lease window plan; nothing refuses here.
predict_phase_budgets() {
  local phase tier
  local -a window_args=() out_args=()
  tier="$(tier_is_agentic && echo on || echo off)"
  [[ "$DRY_RUN" -eq 1 || -z "${QUIESCE_WINDOW_PLAN_JSON:-}" ]] || window_args=(--window-plan "$QUIESCE_WINDOW_PLAN_JSON")
  step "predict each phase's duration against the job deadline (read-only)"
  if [[ "$DRY_RUN" -eq 0 ]]; then
    PHASE_BUDGET_JSON="$(mktemp -d -t mip-phase-budget.XXXXXX)"
  fi
  for phase in plan release data; do
    [[ -z "$PHASE_BUDGET_JSON" ]] || out_args=(--out "$PHASE_BUDGET_JSON/$phase.json")
    "$PYTHON" -m tools.databricks.phase_budget describe \
      --phase "$phase" --agentic-tier "$tier" --skip-silver "$SKIP_SILVER" \
      "${window_args[@]}" --deadline-epoch "${MIP_JOB_DEADLINE_EPOCH:-}" "${out_args[@]}"
  done
  [[ -z "$PHASE_BUDGET_JSON" ]] || echo "  phase budgets (local): ${PHASE_BUDGET_JSON}"
}

# --phase release, before the lease: the release's prediction must fit 330
# minutes and the job deadline, or it refuses with nothing changed.
admit_release_phase_budget() {
  local tier
  local -a window_args=()
  tier="$(tier_is_agentic && echo on || echo off)"
  step "admit the release against the job deadline (read-only)"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    "$PYTHON" -m tools.databricks.phase_budget describe --phase release --agentic-tier "$tier" \
      --deadline-epoch "${MIP_JOB_DEADLINE_EPOCH:-}"
    return 0
  fi
  [[ -z "${QUIESCE_WINDOW_PLAN_JSON:-}" ]] || window_args=(--window-plan "$QUIESCE_WINDOW_PLAN_JSON")
  if ! "$PYTHON" -m tools.databricks.phase_budget admit --phase release --agentic-tier "$tier" \
      "${window_args[@]}" --deadline-epoch "${MIP_JOB_DEADLINE_EPOCH:-}"; then
    echo "${RED}[deploy] the release does not fit the job budget; refusing before the lease. Nothing was changed.${RST}" >&2
    exit 2
  fi
}

# --phase data, before the lease and in place of the pre-stop probes and the
# bundle plan gate (the data phase applies no bundle and never stops the App):
# the read-only data plan. It proves each job is the one this deployer's bundle
# deployed, digests its definition, classifies its graph and admits the phase
# against the job deadline; a refusal exits 2 with nothing changed.
plan_data_phase() {
  step "data phase: plan the data jobs read-only (bound definitions, digests, the job budget)"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    export MIP_APP_DEPLOYMENT_LEASE_ID="dry-run-deployment-lease"
    DATA_PHASE_PLAN_JSON="/tmp/mip-dry-run-data-phase-plan.json"
  else
    DATA_PHASE_PLAN_JSON="$(mktemp -t mip-data-phase-plan.XXXXXX.json)"
    chmod 600 "$DATA_PHASE_PLAN_JSON"
  fi
  echo "${DIM}\$ $PYTHON -m tools.databricks.job_phase_runner plan --app-name ${_GRANTS_APP_NAME} --skip-silver ${SKIP_SILVER} --out ${DATA_PHASE_PLAN_JSON}${RST}"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "  would plan read-only: $(data_phase_jobs | paste -sd ' ' -): each bound in this deployer's bundle state, deployed by its bundle, digested with its job and pipeline closure"
    "$PYTHON" -m tools.databricks.phase_budget describe --phase data --skip-silver "$SKIP_SILVER" \
      --deadline-epoch "${MIP_JOB_DEADLINE_EPOCH:-}"
    return 0
  fi
  if ! "$PYTHON" -m tools.databricks.job_phase_runner plan \
      --app-name "$_GRANTS_APP_NAME" \
      --skip-silver "$SKIP_SILVER" \
      --deadline-epoch "${MIP_JOB_DEADLINE_EPOCH:-}" \
      --out "$DATA_PHASE_PLAN_JSON"; then
    echo "${RED}[deploy] the data plan refused before the lease; nothing was run or changed.${RST}" >&2
    exit 2
  fi
  echo "  data plan (local): ${DATA_PHASE_PLAN_JSON}"
}

# --phase data, under the signed lease (in a dry run, printed): open or resume
# the ledger generation, run each job, reconcile and prove the reviewed
# function grants after each gold-touching one, smoke the data, close the
# generation. It ends the run; the EXIT trap releases the lease. A run still
# going at the deadline exits 4: the next data phase re-attaches to it.
run_data_phase() {
  local job out_env rc touches
  local -a ack_args=()
  _GRANTS_CATALOG="${MIP_DEFAULT_CATALOG:-mip}"
  step "data phase: read the App's service principal (one read-only apps get; the App is never touched)"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    APP_SP_CLIENT_ID="dry-run-app-client-id"
    echo "  would read: databricks apps get ${_GRANTS_APP_NAME} -o json (its service principal, for its EXECUTE grants)"
  else
    APP_SP_CLIENT_ID="$(databricks apps get "$_GRANTS_APP_NAME" -o json | "$PYTHON" -c 'import json,sys; print(str(json.load(sys.stdin).get("service_principal_client_id") or "").strip())')"
    if [[ -z "$APP_SP_CLIENT_ID" ]]; then
      echo "${RED}[deploy] the App has no service principal to reconcile EXECUTE for; run the release first. Nothing was run.${RST}" >&2
      exit 2
    fi
  fi
  # The exact digest of a stale generation's reset plan (shell only, one run,
  # never .env.local): the runner recomputes the plan and refuses a difference.
  if [[ -n "${MIP_ACK_DATA_LEDGER_SUPERSEDE:-}" ]]; then
    if [[ ! "$MIP_ACK_DATA_LEDGER_SUPERSEDE" =~ ^[0-9a-f]{64}$ ]]; then
      echo "${RED}[deploy] MIP_ACK_DATA_LEDGER_SUPERSEDE must be the 64-hex sha256 the stale-generation refusal printed.${RST}" >&2
      exit 2
    fi
    ack_args=(--ack-supersede "$MIP_ACK_DATA_LEDGER_SUPERSEDE")
  fi
  step "data phase: open or resume the signed ledger generation"
  if ! run_with_proof_signing_authority "$PYTHON" -m tools.databricks.job_phase_runner begin \
      --plan "$DATA_PHASE_PLAN_JSON" \
      --lease-id "$MIP_APP_DEPLOYMENT_LEASE_ID" \
      --source-git-sha "$SOURCE_GIT_SHA" \
      "${ack_args[@]}"; then
    echo "${RED}[deploy] the data-phase ledger refused; nothing was run.${RST}" >&2
    exit 2
  fi
  while IFS= read -r job; do
    step "data phase: run ${job} (re-attach, skip or rerun by its ledger entry)"
    out_env=""
    [[ "$DRY_RUN" -eq 1 ]] || { out_env="$(mktemp -t mip-data-phase-run.XXXXXX.env)"; chmod 600 "$out_env"; }
    rc=0
    run_with_proof_signing_authority "$PYTHON" -m tools.databricks.job_phase_runner run \
      --plan "$DATA_PHASE_PLAN_JSON" \
      --lease-id "$MIP_APP_DEPLOYMENT_LEASE_ID" \
      --source-git-sha "$SOURCE_GIT_SHA" \
      --job-key "$job" \
      --deadline-epoch "${MIP_JOB_DEADLINE_EPOCH:-}" \
      --out-env "${out_env:-/tmp/mip-dry-run-data-phase-${job}.env}" || rc=$?
    if [[ "$rc" -eq 4 ]]; then
      echo "${YLW}[deploy] ${job} is still running at the job deadline; re-run --phase data to re-attach (it is never cancelled).${RST}" >&2
      exit 4
    elif [[ "$rc" -ne 0 ]]; then
      echo "${RED}[deploy] ${job} did not succeed (exit ${rc}); re-run --phase data to rerun only the failed job.${RST}" >&2
      exit "$rc"
    fi
    touches=1
    if [[ -n "$out_env" ]]; then
      touches="$(grep -E '^JOB_PHASE_TOUCHES_GOLD=[01]$' "$out_env" | cut -d= -f2 || true)"
      rm -f "$out_env"
    fi
    # Fail closed: a run whose classification did not arrive reconciles too.
    if [[ "$touches" != "0" ]]; then
      step "data phase: reconcile and prove reviewed function EXECUTE grants after ${job} (attack O11)"
      if ! reconcile_reviewed_function_execute_grants; then
        echo "${RED}[deploy] ${job} succeeded but the reviewed function EXECUTE grants are unproven; the data phase refuses success.${RST}" >&2
        exit 4
      fi
    fi
  done < <(data_phase_jobs)
  step "data smoke: gold scored at the rate silver publishes as latest (stale-rate guard)"
  run "$PYTHON" -m tools.databricks.verify_market_rate_alignment \
    --warehouse-id "$_GRANTS_WAREHOUSE_ID" \
    --catalog "$_GRANTS_CATALOG"
  step "data phase: close the ledger generation"
  if ! run_with_proof_signing_authority "$PYTHON" -m tools.databricks.job_phase_runner close \
      --plan "$DATA_PHASE_PLAN_JSON" \
      --lease-id "$MIP_APP_DEPLOYMENT_LEASE_ID" \
      --source-git-sha "$SOURCE_GIT_SHA"; then
    echo "${RED}[deploy] the data is refreshed and its grants proven, but the ledger generation did not close; re-run --phase data (every job skips).${RST}" >&2
    exit 2
  fi
  echo "${GRN}[deploy] data phase complete: every data job succeeded under its planned definition and the reviewed function grants are proven; the App was not touched.${RST}"
  exit 0
}

# ---------------------------------------------------------------------------
# The first install's owner bootstrap (--phase release; plan section 4.10)
# ---------------------------------------------------------------------------
# The account principal manages only the service principals it was enumerated
# for, so the new App's identity needs the owner's human-run
# `python -m tools.clients.bootstrap_account --post-first-install` before any
# App-identity proof (tools/databricks/first_install_bootstrap.py). A release
# that created the App stops there with exit 3; the next release resumes at
# the same point instead of deleting the journal-owned App.

# Step 0a, on a signed first-install journal reading "recover": true when its
# App awaits the owner bootstrap (a valid marker names this exact claim).
first_install_awaits_owner_bootstrap() {
  local state
  [[ "${DEPLOY_PHASE:-}" == "release" && "$DRY_RUN" -eq 0 ]] || return 1
  if ! state="$("$PYTHON" -m tools.databricks.first_install_bootstrap status \
      --app-name "$_GRANTS_APP_NAME" \
      --lease-id "$MIP_APP_DEPLOYMENT_LEASE_ID" \
      --source-git-sha "$SOURCE_GIT_SHA" \
      --rollback-scope "$APP_ROLLBACK_SECRET_SCOPE" \
      --lakebase-instance "$MIP_LAKEBASE_INSTANCE")"; then
    echo "${RED}[deploy] the first-install bootstrap marker could not be proven; nothing was changed.${RST}" >&2
    exit 2
  fi
  [[ "$state" == "resume" ]]
}

resume_first_install_after_owner_bootstrap() {
  step "resume the first install that awaited the owner bootstrap (its App is kept, stopped and source-free)"
  FIRST_INSTALL_RESUMED=1
  APP_UPGRADE_STATE="first_install"
  APP_FAIL_CLOSED_NAME="$_GRANTS_APP_NAME"
}

# Step 4, in place of the App creation: the resumed App's bindings are still
# the ones its journal recorded and this release planned.
resume_first_install_bindings() {
  step "prove the resumed first-install App is bound exactly as its journal recorded"
  if ! run "$PYTHON" -m tools.databricks.first_install_bootstrap verify-resume \
      --app-name "$_GRANTS_APP_NAME" \
      --payload "$APP_RESOURCE_BINDING_PAYLOAD"; then
    echo "${RED}[deploy] the resumed first-install App is not bound as its journal recorded; nothing more was changed.${RST}" >&2
    exit 2
  fi
}

# Step 4, after the App is created (or resumed) and its identity resolved,
# before any App-identity proof. Only --phase release stops here; a run with no
# --phase keeps today's behaviour.
require_first_install_owner_bootstrap() {
  local rc=0
  [[ "${DEPLOY_PHASE:-}" == "release" ]] || return 0
  if [[ "$DRY_RUN" -eq 1 ]]; then
    [[ -z "${_EXISTING_APP_SP_CLIENT_ID:-}" ]] || return 0
    step "first install: prove the owner bootstrap --post-first-install before any App-identity proof"
    echo "  would check read-only: the account principal holds Service Principal Manager on the new App identity; until it does the release stops here with exit 3 (awaiting owner bootstrap --post-first-install) and keeps the App"
    return 0
  fi
  [[ "$FIRST_INSTALL_APP_CREATED" -eq 1 || "${FIRST_INSTALL_RESUMED:-0}" -eq 1 ]] || return 0
  step "first install: prove the owner bootstrap --post-first-install before any App-identity proof"
  run_with_account_identity "$PYTHON" -m tools.databricks.first_install_bootstrap check \
    --app-application-id "$APP_SP_CLIENT_ID" || rc=$?
  if [[ "$rc" -eq 0 ]]; then
    if [[ "${FIRST_INSTALL_RESUMED:-0}" -eq 1 ]]; then
      # An ordinary first install from here: a later failure is compensated as one.
      run_with_proof_signing_authority "$PYTHON" -m tools.databricks.first_install_bootstrap consume \
        --app-name "$_GRANTS_APP_NAME" \
        --lease-id "$MIP_APP_DEPLOYMENT_LEASE_ID" \
        --source-git-sha "$SOURCE_GIT_SHA"
      FIRST_INSTALL_RESUMED=0
      FIRST_INSTALL_APP_CREATED=1
      FIRST_INSTALL_APP_BOUND=1
    fi
    return 0
  fi
  if [[ "$rc" -ne 3 ]]; then
    echo "${RED}[deploy] the owner bootstrap of the new App identity could not be decided (exit ${rc}).${RST}" >&2
    exit 2
  fi
  if [[ "${FIRST_INSTALL_RESUMED:-0}" -ne 1 ]]; then
    run_with_proof_signing_authority "$PYTHON" -m tools.databricks.first_install_bootstrap mark \
      --app-name "$_GRANTS_APP_NAME" \
      --lease-id "$MIP_APP_DEPLOYMENT_LEASE_ID" \
      --source-git-sha "$SOURCE_GIT_SHA" \
      --rollback-scope "$APP_ROLLBACK_SECRET_SCOPE" \
      --lakebase-instance "$MIP_LAKEBASE_INSTANCE"
  fi
  # Keep the App: stopped, source-free and holding no grant yet, it is the one
  # the owner bootstraps. The EXIT trap then only releases the lease.
  FIRST_INSTALL_APP_CREATED=0
  FIRST_INSTALL_APP_BOUND=0
  APP_FAIL_CLOSED_ARMED=0
  echo "${YLW}[deploy] awaiting owner bootstrap --post-first-install: the account principal does not manage the new App identity yet. The App is kept, stopped and source-free. The owner runs python -m tools.clients.bootstrap_account --plan --for post-first-install, then --post-first-install --ack-plan <sha256>; then re-run --phase release, which resumes here.${RST}" >&2
  exit 3
}

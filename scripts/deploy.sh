#!/usr/bin/env bash
# =============================================================================
# scripts/deploy.sh
# -----------------------------------------------------------------------------
# One-command zero-click dev deploy for the Mortgage Intelligence Platform.
#
# What it does, in order, idempotently:
#   0.  Preflight: check .env.local exists + `databricks` CLI + the venv.
#   1.  Build the frontend, then stage the App runtime closure at the deploy
#       commit plus that dist as one per-release source folder + manifest.
#   2.  Before the signed lease and any App stop: render SQL, validate the
#       direct-deployment bundle under `-t dev` (.env.local mapped to BUNDLE_VAR_*
#       via tools/databricks/bundle_env.py), and gate its plan
#       (tools/databricks/bundle_plan_gate.py: no delete, recreate or collision).
#   3.  Gate the plan again under the lease, immediately before step 4.
#   4.  Deploy non-App bundle resources, then apply source-free App resource
#       bindings (first install creates the App stopped with --no-compute).
#   5.  Migrate Lakebase (idempotent schema.sql + seed_campaigns.sql).
#   6.  Publish that folder to <bundle root>/app-src/<release id> (create-only,
#       byte-verified) and promote it only after migration/grant proof.
#   7.  Seed + refresh silver (FRED MORTGAGE30US + Cotality share).
#   8.  Refresh gold (CTAS chain) — the last task in the chain is
#       `refresh_semantics_views`, which lands the four mip.semantics.*
#       metric views Genie depends on.
#   9.  Sync lifecycle state + funnel snapshot so the delta_vs_prior_*
#       view columns resolve on the first dashboard render.
#   9b. Backfill today's headline-KPI snapshot into mip_app.kpi_snapshots
#       (idempotent per-day upsert; S4's last-login deltas never start empty).
#   10. Provision / rebind the Genie space via
#       tools/databricks/provision_genie_space.py.
#   11. Provision MIP-owned agentic resources: Lakebase synced tables,
#       Supervisor Agent orchestration, AI Gateway inference logging, and
#       AI Gateway exact-row proof ledger verification.
#   12. Redeploy with agentic env, run live golden Agent Evaluation, then
#       redeploy with the eval run id.
#   13. Smoke-check the live API via scripts/smoke_live.sh (optional; fail-loud by default).
#   14. After a green capture only, prune unreferenced App release folders (best effort).
#
# Why one script (vs a bundle job that invokes provision_genie_space.py):
# the Genie provisioner reads genie/mortgage_lead_intelligence_space.yml
# from the local repo. Shipping it as a bundle job would require uploading
# that YAML as an artifact; keeping the provisioner local to the deploy
# workstation keeps the source of truth in-repo where code review lives.
#
# Usage:
#   ./scripts/deploy.sh -t dev             # full dev deploy
#   ./scripts/deploy.sh -t dev --dry-run   # print the plan, make no changes
#   ./scripts/deploy.sh -t dev --skip-silver
#                                          # skip silver refresh (FRED + share)
#   ./scripts/deploy.sh -t dev --skip-smoke
#                                          # skip the post-deploy curl smoke test
#   ALLOW_SMOKE_FAILURE=1 ./scripts/deploy.sh -t dev
#                                       # emergency/manual deploy only: warn instead of fail
#   ./scripts/deploy.sh -t dev --no-confirm
#                                       # skip the y/N prompt before deploy
#   ./scripts/deploy.sh --verify-source-only
#                                       # run the exact-source gate and exit
#   ./scripts/deploy.sh -t dev --phase plan --dry-run
#                                       # read-only steps and the bundle plan
#                                       # gate, then exit before the lease
#                                       # (a live --phase plan is CI-only)
#
# Environment:
#   .env.local must set at minimum DATABRICKS_HOST, DATABRICKS_WAREHOUSE_ID.
#   (If GENIE_SPACE_ID is blank on first run, this script provisions the
#   Genie space before bundle deploy so databricks_app.mip_app never binds
#   to the placeholder sentinel. The later Genie step re-runs after gold
#   refresh to bind trusted assets.)
#
# Fail-loud contract:
#   * `set -euo pipefail` — any step that exits non-zero stops the script.
#   * `trap` prints the failing step + recovery hint.
#   * All commands print BEFORE they run, so a scrollback shows exactly
#     where things stopped.
# =============================================================================

set -euo pipefail

# Workflow secrets arrive exported. Retain signing authorities as shell-only
# values before the first child process so ordinary deploy commands can never
# inherit them; run_as_m2m_identity exposes each key only to its bounded role.
MIP_AI_GATEWAY_PROOF_SIGNING_KEY="${MIP_AI_GATEWAY_PROOF_SIGNING_KEY:-}"
MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY="${MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY:-}"
export -n MIP_AI_GATEWAY_PROOF_SIGNING_KEY MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY
for _PRIVATE_CREDENTIAL in \
  DATABRICKS_CLIENT_ID DATABRICKS_CLIENT_SECRET \
  DATABRICKS_OPERATOR2_CLIENT_ID DATABRICKS_OPERATOR2_CLIENT_SECRET \
  DATABRICKS_ADMIN_CLIENT_ID DATABRICKS_ADMIN_CLIENT_SECRET \
  DATABRICKS_RELEASE_PROBE_CLIENT_ID DATABRICKS_RELEASE_PROBE_CLIENT_SECRET \
  DATABRICKS_VERIFIER_CLIENT_ID DATABRICKS_VERIFIER_CLIENT_SECRET \
  DATABRICKS_AGENT_RUNTIME_CLIENT_ID DATABRICKS_AGENT_RUNTIME_CLIENT_SECRET \
  DATABRICKS_AGENT_PROXY_CLIENT_ID DATABRICKS_AGENT_PROXY_CLIENT_SECRET \
  DATABRICKS_AGENT_PROXY_CREDENTIAL_ID DATABRICKS_AGENT_PROXY_CREDENTIAL_BUNDLE \
  DATABRICKS_ACCOUNT_CLIENT_ID DATABRICKS_ACCOUNT_CLIENT_SECRET; do
  export -n "${_PRIVATE_CREDENTIAL?}" 2>/dev/null || true
done
unset _PRIVATE_CREDENTIAL

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# -----------------------------------------------------------------------------
# Argument parsing
# -----------------------------------------------------------------------------
DRY_RUN=0
SKIP_SILVER=0
SKIP_SMOKE=0
NO_CONFIRM=0
VERIFY_SOURCE_ONLY=0
TARGET="dev"
TARGET_SEEN=0
DEPLOY_PHASE=""
PHASE_SEEN=0

# `for arg in "$@"` iterates a pre-expanded snapshot, so an inner
# `shift` to grab `-t <target>`'s value doesn't actually consume the
# next argument from the loop -- it advances `$1..` but the `for`
# variable is already pointing past it. Use an explicit `while` loop
# on `$1` so `-t <target>` (and any future two-arg flag) parses
# reliably (raised by Copilot 2026-04-22).
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)      DRY_RUN=1;       shift ;;
    --skip-silver)  SKIP_SILVER=1;   shift ;;
    --skip-smoke)   SKIP_SMOKE=1;    shift ;;
    --no-confirm)   NO_CONFIRM=1;    shift ;;
    --verify-source-only) VERIFY_SOURCE_ONLY=1; shift ;;
    -t|--target)
      if [[ "$TARGET_SEEN" -eq 1 ]]; then
        echo "[deploy] target may be supplied only once" >&2
        exit 2
      fi
      if [[ $# -lt 2 || -z "$2" ]]; then
        echo "[deploy] missing value for $1 (expected target name, e.g. dev)" >&2
        exit 2
      fi
      TARGET="$2"; TARGET_SEEN=1; shift 2 ;;
    --target=*)
      # The `--target=` form can take an empty value (e.g. user typed
      # `--target=` with nothing after the equals sign). Validate and
      # fail fast so we never pass `-t ""` to `databricks bundle ...`
      # downstream (raised by Copilot 2026-04-22).
      TARGET="${1#--target=}"
      if [[ "$TARGET_SEEN" -eq 1 ]]; then
        echo "[deploy] target may be supplied only once" >&2
        exit 2
      fi
      if [[ -z "$TARGET" ]]; then
        echo "[deploy] missing value for --target= (expected target name, e.g. dev)" >&2
        exit 2
      fi
      TARGET_SEEN=1; shift ;;
    --phase|--phase=*)
      if [[ "$PHASE_SEEN" -eq 1 ]]; then
        echo "[deploy] phase may be supplied only once" >&2
        exit 2
      fi
      if [[ "$1" == --phase=* ]]; then
        DEPLOY_PHASE="${1#--phase=}"; shift
      elif [[ $# -lt 2 ]]; then
        echo "[deploy] missing value for --phase (expected plan)" >&2
        exit 2
      else
        DEPLOY_PHASE="$2"; shift 2
      fi
      PHASE_SEEN=1 ;;
    -h|--help)
      sed -n '2,69p' "$0"
      exit 0
      ;;
    *)
      echo "[deploy] unknown arg: $1 (run with --help)" >&2
      exit 2
      ;;
  esac
done
if [[ "$TARGET" != "dev" && "$TARGET" != "prod" ]]; then
  echo "[deploy] target must be exactly dev or prod; refusing '$TARGET'" >&2
  exit 2
fi
# deploy-dev.yml is parked until a client environment is selected (M1-PR7):
# the CI staging workspace it deployed to was deleted, and the dev target is
# the live install, which no CI run may reach before M1-PR7's per-client
# environment lands. The workflow refuses first; this repeats the refusal
# before any Databricks call, also in DRY_RUN. It keys on the workflow ref,
# never on GITHUB_ACTIONS alone, so CI's own pytest and its deploy.sh dry
# runs are unchanged.
if [[ "${GITHUB_ACTIONS:-}" == "true" && "${GITHUB_WORKFLOW_REF:-}" == *"/.github/workflows/deploy-dev.yml@"* ]]; then
  echo "[deploy] deploy-dev.yml is parked until a client environment is selected (M1-PR7); refusing" >&2
  exit 2
fi
# >>> deploy phase >>>
# --phase plan runs the read-only steps through the bundle plan gate and exits
# before the signed lease. No --phase is today's full run; plan is the only
# phase so far. A live plan is CI-only (attack O13): the client deployer has no
# secret a person can use, and a person's bundle plan against a deployer root
# trips the CLI's run_as check. A dry run makes no Databricks call, so it stays
# available everywhere.
if [[ "$PHASE_SEEN" -eq 1 && "$DEPLOY_PHASE" != "plan" ]]; then
  echo "[deploy] phase must be exactly plan; refusing '${DEPLOY_PHASE}'" >&2
  exit 2
fi
if [[ "$DEPLOY_PHASE" == "plan" && "$DRY_RUN" -eq 0 && "${GITHUB_ACTIONS:-}" != "true" ]]; then
  echo "[deploy] plan is CI-only; use --dry-run or make bundle-validate" >&2
  exit 2
fi
# <<< deploy phase <<<

# -----------------------------------------------------------------------------
# Pretty-print helpers
# -----------------------------------------------------------------------------
BOLD=$'\033[1m'; DIM=$'\033[2m'; RED=$'\033[31m'; GRN=$'\033[32m'; YLW=$'\033[33m'; RST=$'\033[0m'
STEP=0

step() {
  STEP=$((STEP + 1))
  echo
  echo "${BOLD}[deploy] step ${STEP}: $*${RST}"
}

run() {
  echo "${DIM}\$ $*${RST}"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    return 0
  fi
  if [[ -n "${APP_DEPLOYMENT_LEASE_HEARTBEAT_PID:-}" ]] && \
     ! kill -0 "$APP_DEPLOYMENT_LEASE_HEARTBEAT_PID" 2>/dev/null; then
    echo "${RED}[deploy] signed App deployment lease heartbeat is not running.${RST}" >&2
    return 1
  fi
  "$@"
}

run_json_to_file() {
  local output_file="$1"
  local display_file="${output_file:-<dry-run-json-output>}"
  shift
  echo "${DIM}\$ $* > ${display_file}${RST}"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    return 0
  fi
  if [[ -z "$output_file" ]]; then
    echo "${RED}[deploy] JSON command output path is required.${RST}" >&2
    return 1
  fi
  if [[ -n "${APP_DEPLOYMENT_LEASE_HEARTBEAT_PID:-}" ]] && \
     ! kill -0 "$APP_DEPLOYMENT_LEASE_HEARTBEAT_PID" 2>/dev/null; then
    echo "${RED}[deploy] signed App deployment lease heartbeat is not running.${RST}" >&2
    return 1
  fi
  "$@" > "$output_file"
}

run_redacted() {
  local display="$1"
  shift
  echo "${DIM}\$ ${display}${RST}"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    return 0
  fi
  if [[ -n "${APP_DEPLOYMENT_LEASE_HEARTBEAT_PID:-}" ]] && \
     ! kill -0 "$APP_DEPLOYMENT_LEASE_HEARTBEAT_PID" 2>/dev/null; then
    echo "${RED}[deploy] signed App deployment lease heartbeat is not running.${RST}" >&2
    return 1
  fi
  "$@"
}

# For idempotent bundle job runs only. The CLI long-polls the run status,
# and a laptop network flap (VPN reconnect, Wi-Fi handoff) kills that poll
# with "read: can't assign requested address" while the job itself keeps
# succeeding server-side (observed twice on 2026-07-07, deploy step 7, on
# two different local IPs). Re-running the job is safe by design; a real
# job failure still fails the deploy after the retries.
run_job_with_retry() {
  local attempt
  echo "${DIM}\$ $*${RST}"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    return 0
  fi
  for attempt in 1 2 3; do
    if [[ -n "${APP_DEPLOYMENT_LEASE_HEARTBEAT_PID:-}" ]] && \
       ! kill -0 "$APP_DEPLOYMENT_LEASE_HEARTBEAT_PID" 2>/dev/null; then
      echo "${RED}[deploy] signed App deployment lease heartbeat is not running.${RST}" >&2
      return 1
    fi
    if "$@"; then
      return 0
    fi
    if [[ "$attempt" -lt 3 ]]; then
      echo "[deploy] job run attempt $attempt failed (likely a local network flap) — retrying in 15s" >&2
      sleep 15
    fi
  done
  return 1
}

on_error() {
  local rc=$?
  echo
  echo "${RED}[deploy] FAILED at step ${STEP} (exit ${rc}).${RST}" >&2
  echo "${YLW}[deploy] fix the error above and re-run: ./scripts/deploy.sh${RST}" >&2
  echo "${YLW}[deploy] every step is idempotent — re-running picks up where this stopped.${RST}" >&2
  exit "$rc"
}
trap on_error ERR

# The deployment advertises MIP_GIT_SHA from HEAD while Databricks Bundle
# sync uploads the working tree. A dirty tracked file or untracked source can
# otherwise make that SHA a false provenance claim. Standard ignored outputs
# (frontend/dist, sql/_rendered, .databricks, local env files) remain allowed.
SOURCE_GIT_SHA=""
verify_exact_deploy_source() {
  local current_sha source_status
  if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "${RED}[deploy] exact-source gate requires a Git worktree.${RST}" >&2
    exit 2
  fi
  current_sha="$(git rev-parse --verify HEAD 2>/dev/null || true)"
  if [[ -z "$current_sha" ]]; then
    echo "${RED}[deploy] exact-source gate requires a committed HEAD.${RST}" >&2
    exit 2
  fi
  source_status="$(git status --porcelain=v1 --untracked-files=all)"
  if [[ -n "$source_status" ]]; then
    echo "${RED}[deploy] refusing deployment from dirty source.${RST}" >&2
    echo "  Commit or remove every tracked change and untracked non-ignored file first:" >&2
    printf '%s\n' "$source_status" >&2
    exit 2
  fi
  if [[ -n "$SOURCE_GIT_SHA" && "$current_sha" != "$SOURCE_GIT_SHA" ]]; then
    echo "${RED}[deploy] HEAD changed during deployment (${SOURCE_GIT_SHA} -> ${current_sha}).${RST}" >&2
    echo "  Re-run from the new clean revision so uploaded source and MIP_GIT_SHA match." >&2
    exit 2
  fi
  SOURCE_GIT_SHA="$current_sha"
  echo "[deploy] exact source: ${SOURCE_GIT_SHA} (tracked and untracked source clean)"
}

verify_exact_deploy_source
if [[ "$VERIFY_SOURCE_ONLY" -eq 1 ]]; then
  exit 0
fi

# -----------------------------------------------------------------------------
# Resolve python interpreter (same convention as the Makefile)
# -----------------------------------------------------------------------------
# >>> deploy interpreter >>>
# MIP_DEPLOY_PYTHON selects a dedicated interpreter (a venv built from
# requirements.txt outside the checkout). Shell only: .env.local is parsed with
# this interpreter. Step 0 then proves its packages equal the pins.
if [[ -n "${MIP_DEPLOY_PYTHON:-}" ]]; then
  if [[ "$MIP_DEPLOY_PYTHON" != /* || ! -f "$MIP_DEPLOY_PYTHON" || ! -x "$MIP_DEPLOY_PYTHON" ]] || \
     [[ "$("$MIP_DEPLOY_PYTHON" -c 'import sys; print(sys.version_info[0])' 2>/dev/null)" != 3 ]]; then
    echo "${RED}[deploy] MIP_DEPLOY_PYTHON='${MIP_DEPLOY_PYTHON}' is not the absolute path of an executable Python 3.${RST}" >&2
    exit 2
  fi
  PYTHON="$MIP_DEPLOY_PYTHON"
elif [[ -x .venv/bin/python ]]; then
  PYTHON=".venv/bin/python"
else
  PYTHON="python3"
fi
# <<< deploy interpreter <<<

same_identity_casefold() {
  "$PYTHON" - "$1" "$2" <<'PYEOF'
import sys

left, right = (value.strip().casefold() for value in sys.argv[1:])
raise SystemExit(0 if left and right and left == right else 1)
PYEOF
}

RESTORE_RENDERED_SQL_FAIL_CLOSED=0
APP_DEPLOY_PAYLOAD=""
APP_LAST_DEPLOY_PAYLOAD=""
APP_BUNDLE_SUMMARY=""
APP_ROLLBACK_BINDING_ENV=""
AGENTIC_ENV_FILE=""
AGENT_EVAL_ENV_FILE=""
CUTOVER_JOURNAL_ENV_FILE=""
APP_DEPLOYMENT_LEASE_ENV=""
APP_LEASE_RECOVERY_ENV=""
OAUTH_CREDENTIAL_QUARANTINE_FILE=""
APP_RESOURCE_BINDING_SUMMARY=""
APP_RESOURCE_BINDING_PAYLOAD=""
APP_RESOURCE_BINDING_BEFORE=""
APP_RESOURCE_BINDING_AFTER=""
APP_CREATE_RESULT=""
APP_DEPLOYMENT_LEASE_ID=""
APP_DEPLOYMENT_LEASE_HEARTBEAT_PID=""
APP_FAIL_CLOSED_ARMED=0
APP_FAIL_CLOSED_NAME=""
APP_EXPECTED_IDENTITY_ARGS=()
APP_UPGRADE_STATE="first_install"
APP_ROLLBACK_SECRET_SCOPE="${MIP_APP_ROLLBACK_SECRET_SCOPE:-mip-app-rollback}"
MIP_APP_ROLLBACK_PROXY_CREDENTIAL_IDS=""
MIP_APP_ROLLBACK_RECORD_VERSION=""
MIP_APP_ROLLBACK_PROXY_MODE=""
MIP_APP_ROLLBACK_DEPLOYMENT_ID=""
# This block pre-binds only the names bash later dereferences without a `:-`
# default, so `set -u` survives the paths that never source the binding env.
# app_deployment_rollback_cli.py also emits MIP_APP_ROLLBACK_GATEWAY_ENDPOINT_ID
# and MIP_APP_ROLLBACK_GATEWAY_CREATOR, but bash never reads either: the
# preservation guards take the same two fields through the pin JSON below.
MIP_APP_ROLLBACK_GATEWAY_PIN_JSON=""
MIP_APP_ROLLBACK_GATEWAY_INFERENCE_TABLE_PREFIX=""
MIP_APP_ROLLBACK_SUPERVISOR_ID=""
MIP_APP_ROLLBACK_SUPERVISOR_CREATOR=""
MIP_APP_ROLLBACK_SUPERVISOR_ENDPOINT=""
MIP_APP_ROLLBACK_SUPERVISOR_ENDPOINT_ID=""
MIP_APP_ROLLBACK_SUPERVISOR_PIN_JSON=""
MIP_APP_ROLLBACK_RUNTIME_APPLICATION_ID=""
MIP_APP_ROLLBACK_GENIE_SPACE_ID=""
MIP_APP_ROLLBACK_PROXY_APPLICATION_ID=""
AGENT_PROXY_ACCESS_MUTATED=0
VERIFIER_GATEWAY_CUTOVER_MUTATED=0
PREACTIVATION_APP_ACL_MUTATED=0
PREACTIVATION_APP_REVOKE_ENDPOINTS=()
CAPTURED_RUNTIME_RETIREMENT_COMPLETE=0
CAPTURED_APP_BOUNDARY_PROVEN=0
CAPTURED_PROXY_BOUNDARY_PROVEN=0
CAPTURED_VERIFIER_BOUNDARY_PROVEN=0
OLD_SUPERVISOR_APP_ACCESS_MODE="none"
MIP_VERIFIER_SCIM_ID=""
VERIFIER_IDENTITY_CAPTURE_ENV=""
HISTORICAL_ENDPOINT_INVENTORY=""
HISTORICAL_CUTOVER_JOURNAL_ENV=""
STALE_CUTOVER_JOURNAL_PENDING=0
HISTORICAL_CUTOVER_JOURNAL_PRESENT=0
HISTORICAL_CUTOVER_GATEWAY_PRESENT=0
AGENT_RUNTIME_BOOTSTRAP_GRANTS_ACTIVE=0
TREATMENT_RUNTIME_QUIESCED=0
APP_SIGNED_BLUE_AVAILABLE=0
APP_ACCESS_QUARANTINED=0
REVIEWED_FUNCTION_GRANTS_PROVEN=0
# Process-local state is never durable proof across deployment retries. Every
# run starts unproven and may authorize signed-blue restoration only after the
# live migration verifies both OAuth role security and the exact grant matrix.
LAKEBASE_RUNTIME_ACCESS_PROVEN=0
FIRST_INSTALL_APP_CREATED=0
FIRST_INSTALL_APP_BOUND=0
FIRST_INSTALL_COMPENSATION_AUTHORIZED=0
FIRST_INSTALL_JOURNAL_STATUS="absent"
FIRST_INSTALL_APP_ID=""
FIRST_INSTALL_APP_CLIENT_ID=""
FIRST_INSTALL_APP_SCIM_ID=""
FIRST_INSTALL_JOURNAL_ENV=""
FIRST_INSTALL_MARKED_PAYLOAD=""
# Per-release App source (tools/databricks/app_release_contract.py). Step 1
# stages the closure under this gitignored root, outside the exact-source
# gate; step 5 publishes it as <bundle root>/app-src/<APP_RELEASE_ID>, the
# only folder an activation names. APP_BUNDLE_FILES_PATH is the bundle files
# root (workspace.file_path) the release root is derived from.
APP_RELEASE_STAGE_ROOT=".databricks/app-release"
APP_RELEASE_MANIFEST="$APP_RELEASE_STAGE_ROOT/manifest.json"
APP_RELEASE_ID=""
APP_BUNDLE_FILES_PATH=""

# The App-access, App-release, first-install, and identity lifecycle functions
# are sliced verbatim into scripts/lib/deploy_*_lifecycle.sh and sourced here
# in their original order; definitions still precede every step that calls them.
# shellcheck source=scripts/lib/deploy_app_access_lifecycle.sh
. "$REPO_ROOT/scripts/lib/deploy_app_access_lifecycle.sh"

# Security-critical lifecycle libraries declare functions only and are sourced
# before any deploy step can call them. Their dependencies may be declared
# later in this entrypoint; invocation happens only after initialization. They
# intentionally inherit this shell's strict mode, traps, and private variables.
# shellcheck source=scripts/lib/deploy_agent_proxy_lifecycle.sh
. "$REPO_ROOT/scripts/lib/deploy_agent_proxy_lifecycle.sh"
# shellcheck source=scripts/lib/deploy_verifier_gateway_lifecycle.sh
. "$REPO_ROOT/scripts/lib/deploy_verifier_gateway_lifecycle.sh"
# shellcheck source=scripts/lib/deploy_cutover_journal_lifecycle.sh
. "$REPO_ROOT/scripts/lib/deploy_cutover_journal_lifecycle.sh"
# shellcheck source=scripts/lib/deploy_supervisor_creation_lifecycle.sh
. "$REPO_ROOT/scripts/lib/deploy_supervisor_creation_lifecycle.sh"

# shellcheck source=scripts/lib/deploy_app_release_lifecycle.sh
. "$REPO_ROOT/scripts/lib/deploy_app_release_lifecycle.sh"

# shellcheck source=scripts/lib/deploy_first_install_lifecycle.sh
. "$REPO_ROOT/scripts/lib/deploy_first_install_lifecycle.sh"
trap restore_rendered_sql_fail_closed EXIT

# shellcheck source=scripts/lib/deploy_identity_lifecycle.sh
. "$REPO_ROOT/scripts/lib/deploy_identity_lifecycle.sh"

# shellcheck source=scripts/lib/deploy_bundle_plan_gate_lifecycle.sh
. "$REPO_ROOT/scripts/lib/deploy_bundle_plan_gate_lifecycle.sh"

# >>> foreign-catalog remediation refused >>>
# The foreign-catalog binding remediation rewrote the isolation mode and
# workspace bindings of other teams' catalogs on the shared metastore, and
# stopped the App to do it. Its stop branches are gone (M0-PR5) and its tool
# leaves with M4-PR2, so a request for it, from the environment or from
# .env.local, refuses here: before any Databricks call, in a dry run too, with
# the App untouched. The deploy-dev workflow passes the literal 0.
_REMEDIATE_FOREIGN_CATALOG_BINDINGS="$(
  deployment_control_value MIP_REMEDIATE_FOREIGN_CATALOG_BINDINGS 0
)"
if [[ "$_REMEDIATE_FOREIGN_CATALOG_BINDINGS" != "0" ]]; then
  echo "${RED}[deploy] MIP_REMEDIATE_FOREIGN_CATALOG_BINDINGS is refused: the foreign-catalog binding remediation rewrites other teams' metastore-level catalog state and is retired (docs/security/GRANTS.md); unset it. Nothing was changed.${RST}" >&2
  exit 2
fi
unset _REMEDIATE_FOREIGN_CATALOG_BINDINGS
# <<< foreign-catalog remediation refused <<<

# Deploy step files: the orchestration below is sliced verbatim, in reviewed
# order, into scripts/lib/deploy_step_*.sh. Each step file is sourced into this
# shell, so strict mode, the ERR/EXIT traps, and the private variables apply
# unchanged; the banners that follow are the index of that order. A step file
# refuses to run on its own. See docs/maintenance/file-size-refactor-plan.md.
# -----------------------------------------------------------------------------
# Step 0: preflight
# -----------------------------------------------------------------------------
# shellcheck source=scripts/lib/deploy_step_preflight_gates.sh
. "$REPO_ROOT/scripts/lib/deploy_step_preflight_gates.sh"

# shellcheck source=scripts/lib/deploy_step_automation_identities.sh
. "$REPO_ROOT/scripts/lib/deploy_step_automation_identities.sh"

# -----------------------------------------------------------------------------
# Step 0a: prove signed-blue state before any workspace mutation
# -----------------------------------------------------------------------------
# shellcheck source=scripts/lib/deploy_step_signed_blue_proof.sh
. "$REPO_ROOT/scripts/lib/deploy_step_signed_blue_proof.sh"

# -----------------------------------------------------------------------------
# Step 0a: resolve the governed Genie space before any App secret mutation
# -----------------------------------------------------------------------------
# shellcheck source=scripts/lib/deploy_step_bundle_apply.sh
. "$REPO_ROOT/scripts/lib/deploy_step_bundle_apply.sh"

# -----------------------------------------------------------------------------
# Step 4b: Lakebase migration — BEFORE the app snapshot restart
# -----------------------------------------------------------------------------
# shellcheck source=scripts/lib/deploy_step_lakebase_and_uc_grants.sh
. "$REPO_ROOT/scripts/lib/deploy_step_lakebase_and_uc_grants.sh"

# -----------------------------------------------------------------------------
# Step 5: publish the per-release App source and promote it to the App
# -----------------------------------------------------------------------------
# shellcheck source=scripts/lib/deploy_step_app_promotion_and_refresh.sh
. "$REPO_ROOT/scripts/lib/deploy_step_app_promotion_and_refresh.sh"

# -----------------------------------------------------------------------------
# Step 10b: provision MIP-owned agentic resources after gold/Genie assets exist
# -----------------------------------------------------------------------------
# shellcheck source=scripts/lib/deploy_step_agentic_provisioning.sh
. "$REPO_ROOT/scripts/lib/deploy_step_agentic_provisioning.sh"
# shellcheck source=scripts/lib/deploy_step_agentic_cutover.sh
. "$REPO_ROOT/scripts/lib/deploy_step_agentic_cutover.sh"

# -----------------------------------------------------------------------------
# Step 10c: run live Agent Evaluation, then redeploy with the eval run id
# -----------------------------------------------------------------------------
# shellcheck source=scripts/lib/deploy_step_agent_eval_and_smoke.sh
. "$REPO_ROOT/scripts/lib/deploy_step_agent_eval_and_smoke.sh"

# -----------------------------------------------------------------------------
# Done
# -----------------------------------------------------------------------------
APP_FAIL_CLOSED_ARMED=0
echo
echo "${GRN}[deploy] complete.${RST}"
echo "${DIM}  App URL:     ${MIP_APP_URL:-"(check the Databricks workspace → Apps)"}${RST}"
echo "${DIM}  Genie space: genie/space_id.txt (provisioned before bundle deploy, rebound after gold refresh).${RST}"
echo "${DIM}  Re-run any time — every step is idempotent.${RST}"

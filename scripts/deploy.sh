#!/usr/bin/env bash
# =============================================================================
# scripts/deploy.sh
# -----------------------------------------------------------------------------
# One-command zero-click dev deploy for the Mortgage Intelligence Platform.
#
# What it does, in order, idempotently:
#   0.  Preflight: check .env.local exists + `databricks` CLI + the venv.
#   1.  Build the frontend (frontend/dist/** is uploaded with the bundle).
#   2.  Validate the direct-deployment bundle under `-t dev`, with .env.local mapped to
#       BUNDLE_VAR_* via tools/databricks/bundle_env.py.
#   3.  Show the direct deployment plan.
#   4.  Deploy the bundle.
#   5.  Promote the uploaded bundle source to the running Databricks App.
#   6.  Seed + refresh silver (FRED MORTGAGE30US + Cotality share).
#   7.  Migrate Lakebase (idempotent schema.sql + seed_campaigns.sql).
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
      if [[ $# -lt 2 || -z "$2" ]]; then
        echo "[deploy] missing value for $1 (expected target name, e.g. dev)" >&2
        exit 2
      fi
      TARGET="$2"; shift 2 ;;
    --target=*)
      # The `--target=` form can take an empty value (e.g. user typed
      # `--target=` with nothing after the equals sign). Validate and
      # fail fast so we never pass `-t ""` to `databricks bundle ...`
      # downstream (raised by Copilot 2026-04-22).
      TARGET="${1#--target=}"
      if [[ -z "$TARGET" ]]; then
        echo "[deploy] missing value for --target= (expected target name, e.g. dev)" >&2
        exit 2
      fi
      shift ;;
    -h|--help)
      sed -n '2,60p' "$0"
      exit 0
      ;;
    *)
      echo "[deploy] unknown arg: $1 (run with --help)" >&2
      exit 2
      ;;
  esac
done

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
if [[ -x .venv/bin/python ]]; then
  PYTHON=".venv/bin/python"
else
  PYTHON="python3"
fi

RESTORE_RENDERED_SQL_FAIL_CLOSED=0
APP_DEPLOY_PAYLOAD=""
AGENTIC_ENV_FILE=""
AGENT_EVAL_ENV_FILE=""

restore_rendered_sql_fail_closed() {
  if [[ -n "${APP_DEPLOY_PAYLOAD:-}" ]]; then
    rm -f "$APP_DEPLOY_PAYLOAD"
  fi
  if [[ -n "${AGENTIC_ENV_FILE:-}" ]]; then
    rm -f "$AGENTIC_ENV_FILE"
  fi
  if [[ -n "${AGENT_EVAL_ENV_FILE:-}" ]]; then
    rm -f "$AGENT_EVAL_ENV_FILE"
  fi
  if [[ "$DRY_RUN" -eq 1 || "$RESTORE_RENDERED_SQL_FAIL_CLOSED" -ne 1 ]]; then
    return 0
  fi
  MIP_ENABLE_DEMO_FIRST_PARTY_FEEDS=0 "$PYTHON" tools/render_sql.py \
    --catalog "${MIP_DEFAULT_CATALOG:-mip}" >/dev/null 2>&1 || {
      echo "${YLW}[deploy] warning: failed to restore sql/_rendered with demo first-party feeds disabled.${RST}" >&2
      return 0
    }
  echo "${DIM}[deploy] restored sql/_rendered with demo first-party feeds disabled.${RST}" >&2
}
trap restore_rendered_sql_fail_closed EXIT

is_real_bundle_value() {
  local value="${1:-}"
  [[ -n "$value" ]] || return 1
  [[ "$value" != "00000000PLACEHOLDER" ]] || return 1
  [[ ! ( "$value" == \<* && "$value" == *\> ) ]] || return 1
  return 0
}

dotenv_value() {
  local key="$1"
  "$PYTHON" - "$key" <<'PY'
import sys
from pathlib import Path

from dotenv import dotenv_values

key = sys.argv[1]
path = Path(".env.local")
if not path.exists():
    print("")
else:
    print((dotenv_values(path).get(key) or "").strip())
PY
}

resolve_m2m_credential() {
  local name="$1" value
  value="${!name:-}"
  if [[ -z "$value" ]]; then
    value="$(dotenv_value "$name")"
  fi
  printf -v "$name" '%s' "$value"
  export "${name?}"
}

mint_m2m_token() {
  local output_name="$1" client_id_env="$2" client_secret_env="$3"
  local token_file token
  token_file="$(mktemp -t mip-m2m-token.XXXXXX)"
  chmod 600 "$token_file"
  echo "${DIM}\$ $PYTHON tools/oauth_m2m_mint.py --client-id-env $client_id_env --client-secret-env $client_secret_env --output-file [secure-temp]${RST}"
  if ! "$PYTHON" tools/oauth_m2m_mint.py \
    --client-id-env "$client_id_env" \
    --client-secret-env "$client_secret_env" \
    --output-file "$token_file"; then
    rm -f "$token_file"
    return 1
  fi
  if ! IFS= read -r token < "$token_file" || [[ -z "$token" ]]; then
    rm -f "$token_file"
    echo "${RED}[deploy] M2M mint returned an empty bearer for $client_id_env.${RST}" >&2
    return 1
  fi
  rm -f "$token_file"
  printf -v "$output_name" '%s' "$token"
  export "${output_name?}"
}

run_as_m2m_identity() {
  local label="$1" client_id_env="$2" client_secret_env="$3"
  shift 3
  echo "${DIM}\$ $* (${label} M2M identity)${RST}"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    return 0
  fi
  (
    # Intentionally scope these credentials to the child process.
    # shellcheck disable=SC2030
    export DATABRICKS_CLIENT_ID="${!client_id_env}"
    export DATABRICKS_CLIENT_SECRET="${!client_secret_env}"
    export DATABRICKS_AUTH_TYPE="oauth-m2m"
    unset DATABRICKS_TOKEN DATABRICKS_CONFIG_PROFILE
    "$@"
  )
}

# -----------------------------------------------------------------------------
# Step 0: preflight
# -----------------------------------------------------------------------------
step "preflight — check .env.local, databricks CLI, venv"

if [[ ! -f .env.local ]]; then
  echo "${RED}[deploy] .env.local missing.${RST}" >&2
  echo "  copy .env.example to .env.local, then fill in DATABRICKS_HOST + DATABRICKS_WAREHOUSE_ID." >&2
  exit 2
fi

if ! command -v databricks >/dev/null 2>&1; then
  echo "${RED}[deploy] \`databricks\` CLI is not on PATH.${RST}" >&2
  echo "  install: https://docs.databricks.com/en/dev-tools/cli/install.html" >&2
  exit 2
fi

DB_VERSION="$(databricks --version 2>&1 || echo 'unknown')"
echo "  databricks: ${DB_VERSION}"
echo "  python:     ${PYTHON}"
echo "  target:     ${TARGET}"
echo "  dry-run:    ${DRY_RUN}"

if [[ "$DRY_RUN" -eq 0 && "$NO_CONFIRM" -eq 0 ]]; then
  read -r -p "About to DEPLOY to the ${TARGET} target. Continue? [y/N] " ans
  if [[ "$ans" != "y" && "$ans" != "Y" ]]; then
    echo "aborted."
    exit 1
  fi
fi

# Resolve deployment-scoped controls before any helper can read .env.local.
# bundle_env.py intentionally ignores stale local MIP_DEFAULT_CATALOG values
# unless the caller exports one; provision_genie_space.py loads .env.local via
# python-dotenv, so export the same normalized value here to keep the bundle,
# SQL renderer, Python jobs, and Genie table bindings pointed at one catalog.
export MIP_DEFAULT_CATALOG="${MIP_DEFAULT_CATALOG:-mip}"

# Admin-allowlist visibility check (2026-06-11, observed live). Databricks
# Apps deployment env_vars are a FULL REPLACEMENT, `admin_emails` defaults to
# "" in code (no personal identities in source), and the deploy payload
# deliberately does NOT bootstrap the deploying operator into admin (pinned
# by tests/unit/test_app_deploy_payload.py). So a deploy without
# MIP_ADMIN_EMAILS in the environment / .env.local ships an app where EVERY
# admin surface — asset detail, the audit feed, admin ops — returns 403 for
# everyone until the `mip-admin` workspace group exists. That is a valid
# group-based posture, but it must never happen silently. Warn, don't block.
_ADMIN_EMAILS_RESOLVED="${MIP_ADMIN_EMAILS:-$("$PYTHON" - <<'PYEOF'
from pathlib import Path
try:
    from dotenv import dotenv_values
    print((dotenv_values(Path(".env.local")).get("MIP_ADMIN_EMAILS") or "").strip())
except Exception:
    print("")
PYEOF
)}"
if [[ -z "$_ADMIN_EMAILS_RESOLVED" ]]; then
  echo "${YLW}[deploy] WARNING: MIP_ADMIN_EMAILS is not set (env or .env.local).${RST}" >&2
  echo "${YLW}  Admin surfaces (asset detail, audit feed, admin ops) will 403 for every${RST}" >&2
  echo "${YLW}  user unless they are in the '\${MIP_ADMIN_GROUP_NAME:-mip-admin}' workspace group.${RST}" >&2
  echo "${YLW}  To grant explicit admin: add MIP_ADMIN_EMAILS=<operator@email> to .env.local${RST}" >&2
  echo "${YLW}  (or export it for this run) and redeploy.${RST}" >&2
else
  echo "  admin allowlist: configured (MIP_ADMIN_EMAILS set)"
fi

APP_RUNTIME_ENV="${APP_ENV:-}"
if [[ -z "$APP_RUNTIME_ENV" ]]; then
  if [[ "$TARGET" == "dev" ]]; then
    APP_RUNTIME_ENV="sandbox"
  else
    APP_RUNTIME_ENV="$TARGET"
  fi
fi
APP_GIT_SHA="$SOURCE_GIT_SHA"

# Campaign/Genie confirmation tokens must remain verifiable across app
# restarts and replicas. Every deployed runtime, including the shared sandbox,
# requires an operator-owned current secret. Process-local and generated-file
# keys are allowed only by the backend's local/test runtime paths.
_GENIE_ACTION_SECRET_RESOLVED="${MIP_GENIE_ACTION_SECRET_CURRENT:-}"
if [[ -z "$_GENIE_ACTION_SECRET_RESOLVED" ]]; then
  _GENIE_ACTION_SECRET_RESOLVED="$(dotenv_value MIP_GENIE_ACTION_SECRET_CURRENT)"
fi
_GENIE_ACTION_SECRET_NORMALIZED="$(printf '%s' "$_GENIE_ACTION_SECRET_RESOLVED" | tr '[:upper:]' '[:lower:]')"
case "$_GENIE_ACTION_SECRET_NORMALIZED" in
  ""|redacted|changeme|change-me|change_me|placeholder|example|your-secret|your_secret)
    _GENIE_ACTION_SECRET_RESOLVED=""
    ;;
esac
if [[ "$_GENIE_ACTION_SECRET_NORMALIZED" == \<*\> ]]; then
  _GENIE_ACTION_SECRET_RESOLVED=""
fi
if [[ -z "$_GENIE_ACTION_SECRET_RESOLVED" ]]; then
  if [[ "$APP_RUNTIME_ENV" != "local" && "$APP_RUNTIME_ENV" != "test" ]]; then
    echo "${RED}[deploy] ERROR: MIP_GENIE_ACTION_SECRET_CURRENT is required for target '$TARGET' (APP_ENV=${APP_RUNTIME_ENV}).${RST}" >&2
    echo "${RED}  Deployed confirmation and campaign-provenance tokens require a stable, deployment-scoped HMAC key.${RST}" >&2
    exit 1
  fi
  echo "${YLW}[deploy] WARNING: local/test runtime will use its non-durable compatibility key.${RST}" >&2
else
  echo "  genie/campaign HMAC: configured"
fi
if [[ -n "$_GENIE_ACTION_SECRET_RESOLVED" ]]; then
  export MIP_GENIE_ACTION_SECRET_CURRENT="$_GENIE_ACTION_SECRET_RESOLVED"
fi

# Cotality ID-mask HMAC visibility check. The source-known compatibility
# namespace is local/test-only; sandbox and customer runtimes fail before any
# app mutation unless the operator supplies a durable deployment-scoped key.
_ID_MASK_RESOLVED="${MIP_COTALITY_ID_MASK_SECRET:-$("$PYTHON" - <<'PYEOF'
from pathlib import Path
try:
    from dotenv import dotenv_values
    values = dotenv_values(Path(".env.local"))
    print((values.get("MIP_COTALITY_ID_MASK_SECRET") or "").strip())
except Exception:
    print("")
PYEOF
)}"
_ID_MASK_NORMALIZED="$(printf '%s' "$_ID_MASK_RESOLVED" | tr '[:upper:]' '[:lower:]')"
case "$_ID_MASK_NORMALIZED" in
  ""|redacted|changeme|change-me|change_me|placeholder|example|your-secret|your_secret|mip-cotality-id-mask-v1)
    _ID_MASK_RESOLVED=""
    ;;
esac
if [[ "$_ID_MASK_NORMALIZED" == \<*\> ]]; then
  _ID_MASK_RESOLVED=""
fi
if [[ -z "$_ID_MASK_RESOLVED" ]]; then
  if [[ "$APP_RUNTIME_ENV" != "local" && "$APP_RUNTIME_ENV" != "test" ]]; then
    echo "${RED}[deploy] ERROR: MIP_COTALITY_ID_MASK_SECRET is required for target '$TARGET' (APP_ENV=${APP_RUNTIME_ENV}).${RST}" >&2
    echo "${RED}  Deployed runtimes must use a deployment-scoped HMAC secret;${RST}" >&2
    echo "${RED}  the source-known compatibility namespace is allowed only for local/test.${RST}" >&2
    exit 1
  fi
  echo "${YLW}[deploy] WARNING: MIP_COTALITY_ID_MASK_SECRET is not set (env or .env.local).${RST}" >&2
  echo "${YLW}  Cotality ID masking will use the local/test compatibility namespace.${RST}" >&2
else
  echo "  cotality id-mask secret: configured"
fi
if [[ -n "$_ID_MASK_RESOLVED" ]]; then
  export MIP_COTALITY_ID_MASK_SECRET="$_ID_MASK_RESOLVED"
fi

# Exact AI Gateway proof rows use verifier-only Ed25519 signatures. The App
# receives only the derived public key, so the Lakebase proof-writer credential
# cannot manufacture a claimable row by itself.
_AI_GATEWAY_PROOF_SIGNING_KEY_RESOLVED="${MIP_AI_GATEWAY_PROOF_SIGNING_KEY:-}"
if [[ -z "$_AI_GATEWAY_PROOF_SIGNING_KEY_RESOLVED" ]]; then
  _AI_GATEWAY_PROOF_SIGNING_KEY_RESOLVED="$(dotenv_value MIP_AI_GATEWAY_PROOF_SIGNING_KEY)"
fi
if [[ -z "$_AI_GATEWAY_PROOF_SIGNING_KEY_RESOLVED" ]]; then
  if [[ "$APP_RUNTIME_ENV" != "local" && "$APP_RUNTIME_ENV" != "test" ]]; then
    echo "${RED}[deploy] ERROR: MIP_AI_GATEWAY_PROOF_SIGNING_KEY is required for target '$TARGET' (APP_ENV=${APP_RUNTIME_ENV}).${RST}" >&2
    echo "${RED}  AI Gateway exact-row proof requires a verifier-only Ed25519 key.${RST}" >&2
    exit 1
  fi
else
  export MIP_AI_GATEWAY_PROOF_SIGNING_KEY="$_AI_GATEWAY_PROOF_SIGNING_KEY_RESOLVED"
  if ! MIP_AI_GATEWAY_PROOF_VERIFY_KEY="$($PYTHON - <<'PYEOF'
import os
from backend.services.ai_gateway_proof_attestation import derive_gateway_proof_verify_key

print(derive_gateway_proof_verify_key(os.environ["MIP_AI_GATEWAY_PROOF_SIGNING_KEY"]))
PYEOF
)"; then
    echo "${RED}[deploy] ERROR: MIP_AI_GATEWAY_PROOF_SIGNING_KEY is invalid.${RST}" >&2
    exit 1
  fi
  export MIP_AI_GATEWAY_PROOF_VERIFY_KEY
  echo "  AI Gateway proof attestation: verifier private key / runtime public key configured"
fi

# App-facing automation uses three long-lived client credentials but no stored
# bearer tokens. Normal/admin tokens are minted per run and reminted before
# evaluation; the verifier client is used only for deployment-side Gateway
# proof writes. The verifier is intentionally not a member of mip-admin.
for _M2M_NAME in \
  DATABRICKS_CLIENT_ID DATABRICKS_CLIENT_SECRET \
  DATABRICKS_ADMIN_CLIENT_ID DATABRICKS_ADMIN_CLIENT_SECRET \
  DATABRICKS_VERIFIER_CLIENT_ID DATABRICKS_VERIFIER_CLIENT_SECRET; do
  resolve_m2m_credential "$_M2M_NAME"
done
if [[ "$DRY_RUN" -eq 0 ]]; then
  _M2M_MISSING=""
  for _M2M_NAME in \
    DATABRICKS_CLIENT_ID DATABRICKS_CLIENT_SECRET \
    DATABRICKS_ADMIN_CLIENT_ID DATABRICKS_ADMIN_CLIENT_SECRET \
    DATABRICKS_VERIFIER_CLIENT_ID DATABRICKS_VERIFIER_CLIENT_SECRET; do
    if [[ -z "${!_M2M_NAME:-}" ]]; then
      _M2M_MISSING="${_M2M_MISSING} ${_M2M_NAME}"
    fi
  done
  if [[ -n "$_M2M_MISSING" ]]; then
    echo "${RED}[deploy] ERROR: missing required per-run M2M credential(s):${_M2M_MISSING}.${RST}" >&2
    exit 1
  fi
  # run_as_m2m_identity changes DATABRICKS_CLIENT_ID only in a subshell.
  # shellcheck disable=SC2031
  if [[ "$DATABRICKS_CLIENT_ID" == "$DATABRICKS_ADMIN_CLIENT_ID" || \
        "$DATABRICKS_CLIENT_ID" == "$DATABRICKS_VERIFIER_CLIENT_ID" || \
        "$DATABRICKS_ADMIN_CLIENT_ID" == "$DATABRICKS_VERIFIER_CLIENT_ID" ]]; then
    echo "${RED}[deploy] ERROR: normal, admin, and verifier M2M client IDs must be distinct.${RST}" >&2
    exit 1
  fi
  export MIP_AI_GATEWAY_VERIFIER_CLIENT_ID="$DATABRICKS_VERIFIER_CLIENT_ID"
  mint_m2m_token MIP_BEARER_TOKEN DATABRICKS_CLIENT_ID DATABRICKS_CLIENT_SECRET
  mint_m2m_token MIP_ADMIN_BEARER_TOKEN \
    DATABRICKS_ADMIN_CLIENT_ID DATABRICKS_ADMIN_CLIENT_SECRET
  echo "  app automation: distinct per-run normal/admin Bearers minted"
else
  echo "  app automation: normal/admin Bearer mint deferred by --dry-run"
fi

# Provision runtime HMAC values directly into Databricks Secrets before the
# bundle validates its app resource bindings. The later Apps deploy payload
# carries only value_from resource names, never raw secret values.
RUNTIME_SECRET_SCOPE="${MIP_RUNTIME_SECRET_SCOPE:-mip-runtime}"
export BUNDLE_VAR_runtime_secret_scope="$RUNTIME_SECRET_SCOPE"
step "provision Databricks App runtime secret bindings"
run "$PYTHON" tools/databricks/provision_runtime_secrets.py \
  --scope "$RUNTIME_SECRET_SCOPE"

# -----------------------------------------------------------------------------
# Step 0a: ensure the bundle has a real Genie space id before app resource apply
# -----------------------------------------------------------------------------
# The app resource binding validates the Genie space during
# `databricks bundle deploy`. If GENIE_SPACE_ID is blank, the bundle default
# `00000000PLACEHOLDER` can reach Databricks and return the opaque
# error "You need Can View permission to perform this action." Provisioning the
# space here makes the first real app update deterministic. We re-run the same
# provisioner after gold refresh below so the space picks up newly-materialized
# trusted assets.
GENIE_SPACE_ID_FROM_ENV="${GENIE_SPACE_ID:-}"
if ! is_real_bundle_value "$GENIE_SPACE_ID_FROM_ENV"; then
  GENIE_SPACE_ID_FROM_ENV="$(dotenv_value GENIE_SPACE_ID)"
fi

if ! is_real_bundle_value "$GENIE_SPACE_ID_FROM_ENV"; then
  step "provision Genie space before bundle deploy (first-run app binding)"
  run "$PYTHON" tools/databricks/provision_genie_space.py --no-smoke-test
  if [[ "$DRY_RUN" -eq 0 ]]; then
    if [[ ! -s genie/space_id.txt ]]; then
      echo "${RED}[deploy] Genie provisioner did not write genie/space_id.txt.${RST}" >&2
      exit 2
    fi
    GENIE_SPACE_ID="$(< genie/space_id.txt)"
    export GENIE_SPACE_ID
  fi
else
  export GENIE_SPACE_ID="$GENIE_SPACE_ID_FROM_ENV"
fi

# -----------------------------------------------------------------------------
# Step 1a: render SQL for the target UC catalog
# -----------------------------------------------------------------------------
# The bundle's SQL tasks read from sql/_rendered/**/*.sql. The canonical
# sources under sql/** hardcode the default `mip.*` catalog prefix for
# readability + code review; tools/render_sql.py substitutes the five
# documented UC prefixes (mip.gold., mip.silver., mip.ref., mip.semantics.,
# mip.raw., mip.first_party.) for the target catalog before bundle
# validate/deploy read the rendered tree. The renderer also materializes the
# first-party demo-feed switch as a SQL literal because Databricks SQL does not
# allow parameter markers in this DDL path. The stand-alone renderer defaults to
# disabled; this wrapper explicitly opts the Summit dev demo in while keeping
# prod/customer deploys fail-closed.
DEMO_FEEDS_FROM_ENV="${MIP_ENABLE_DEMO_FIRST_PARTY_FEEDS:-}"
if [[ -z "$DEMO_FEEDS_FROM_ENV" ]]; then
  DEMO_FEEDS_FROM_ENV="$(dotenv_value MIP_ENABLE_DEMO_FIRST_PARTY_FEEDS)"
fi
if [[ -z "$DEMO_FEEDS_FROM_ENV" ]]; then
  if [[ "$TARGET" == "dev" ]]; then
    DEMO_FEEDS_FROM_ENV=1
  else
    DEMO_FEEDS_FROM_ENV=0
  fi
fi
if [[ "$TARGET" != "dev" ]]; then
  DEMO_FEEDS_NORMALIZED="$(printf '%s' "$DEMO_FEEDS_FROM_ENV" | tr '[:upper:]' '[:lower:]')"
  if [[ "$DEMO_FEEDS_NORMALIZED" =~ ^(1|true|yes|y|on)$ && "${MIP_ALLOW_DEMO_FIRST_PARTY_IN_PROD:-0}" != "1" ]]; then
    echo "${RED}[deploy] refusing to enable Summit demo first-party feeds for target ${TARGET}.${RST}" >&2
    echo "  Set MIP_ALLOW_DEMO_FIRST_PARTY_IN_PROD=1 only for an approved demo workspace; never for a customer production workspace." >&2
    exit 2
  fi
fi
export MIP_ENABLE_DEMO_FIRST_PARTY_FEEDS="$DEMO_FEEDS_FROM_ENV"
step "render SQL for target UC catalog (MIP_DEFAULT_CATALOG=${MIP_DEFAULT_CATALOG:-mip}, MIP_ENABLE_DEMO_FIRST_PARTY_FEEDS=${MIP_ENABLE_DEMO_FIRST_PARTY_FEEDS})"
run "$PYTHON" tools/render_sql.py --catalog "${MIP_DEFAULT_CATALOG:-mip}"
if [[ "$MIP_ENABLE_DEMO_FIRST_PARTY_FEEDS" =~ ^(1|true|TRUE|yes|YES|y|Y|on|ON)$ ]]; then
  RESTORE_RENDERED_SQL_FAIL_CLOSED=1
fi

# -----------------------------------------------------------------------------
# Step 1: build the frontend
# -----------------------------------------------------------------------------
step "build frontend (frontend/dist/** is uploaded by the bundle sync.include)"
run npm --prefix frontend run build

# -----------------------------------------------------------------------------
# Step 2: validate bundle
# -----------------------------------------------------------------------------
step "validate direct-deployment bundle against -t ${TARGET}"
run "$PYTHON" tools/databricks/bundle_env.py validate -t "$TARGET"

# -----------------------------------------------------------------------------
# Step 3: plan bundle
# -----------------------------------------------------------------------------
step "plan direct deployment against -t ${TARGET}"
run "$PYTHON" tools/databricks/bundle_env.py plan -t "$TARGET"

# -----------------------------------------------------------------------------
# Step 4: deploy bundle
# -----------------------------------------------------------------------------
verify_exact_deploy_source
step "deploy bundle (app + warehouse + jobs + pipelines + Lakebase)"
run "$PYTHON" tools/databricks/bundle_env.py deploy -t "$TARGET"

# The first migration grants the dedicated verifier's proof-ledger role. On a
# fresh workspace that Lakebase OAuth role does not exist merely because the
# workspace service principal exists, so create/reconcile it before the job's
# grant postflight. Endpoint and warehouse grants stay in the later agentic
# convergence step, after those concrete resources are known.
step "bootstrap dedicated AI Gateway verifier Lakebase OAuth role"
run "$PYTHON" tools/databricks/provision_m2m_oauth.py \
  --identity-role verifier \
  --expected-application-id "$DATABRICKS_VERIFIER_CLIENT_ID" \
  --no-mint-secret

# -----------------------------------------------------------------------------
# Step 4b: Lakebase migration — BEFORE the app snapshot restart
# -----------------------------------------------------------------------------
# Ordering matters (2026-06-11, observed live): when migration ran AFTER the
# app snapshot promotion, the freshly restarted app raced the migrate job's
# schema work, tripped the lakebase circuit breaker, and showed the audit
# feed's degraded banner for ~30s. Migrating first means the restarted app
# boots against an already-migrated schema. Bonus: the migrate job's runtime
# gives the bundle-triggered app deployment time to settle, so the
# wait_for_app_deployable() poll below usually finds a clear runway.
# Requires only step 4 (the bundle apply defines the job + Lakebase instance).
step "migrate Lakebase — schema.sql + seed_campaigns.sql (idempotent)"
run_job_with_retry databricks bundle run mip_lakebase_migrate -t "$TARGET"

# -----------------------------------------------------------------------------
# Step 4c: UC grants for the app service principal (audit P1-3, zero-click)
# -----------------------------------------------------------------------------
# On a FRESH workspace the bundle creates the app + its service principal,
# but nothing granted that SP read access to the UC objects — the app booted
# to PERMISSION_DENIED on every endpoint and docs/security/GRANTS.md was a
# manual copy-paste runbook (CLAUDE.md calls that exact pattern a packaging
# bug). This step applies the GRANTS.md §catalog/§gold/§ref/§audit
# base statements idempotently (GRANT is a no-op when already granted)
# against the deploy warehouse, addressed to the SP's client id. AI
# Gateway table-level SELECT is applied after agentic provisioning once
# the concrete inference table prefix is known. Failures are FATAL with
# a pointer to GRANTS.md: a deploy that cannot grant is a deploy whose
# app cannot read, and hiding that would violate the fail-visibly contract.
# GRANTS.md remains the audit-readable matrix; Lakebase role grants are
# applied by jobs/lakebase_migrate.py in step 4b.
step "apply UC grants to the app service principal (idempotent)"
_GRANTS_APP_NAME="${MIP_APP_NAME:-mip-app}"
_GRANTS_WAREHOUSE_ID="${DATABRICKS_WAREHOUSE_ID:-$(dotenv_value DATABRICKS_WAREHOUSE_ID)}"
_GRANTS_CATALOG="${MIP_DEFAULT_CATALOG:-mip}"
_GRANTS_SYNC_CATALOG="${MIP_LAKEBASE_SYNC_CATALOG:-mip_app_state}"
_GRANTS_SYNC_SCHEMA="${MIP_LAKEBASE_SYNC_SCHEMA:-mip_sync}"
APP_RESOURCE_JSON="$(databricks apps get "$_GRANTS_APP_NAME" -o json 2>/dev/null || true)"
APP_SP_CLIENT_ID="$(printf '%s' "$APP_RESOURCE_JSON" | "$PYTHON" -c 'import json,sys; print((json.load(sys.stdin).get("service_principal_client_id") or "").strip())' 2>/dev/null || true)"
APP_SP_SCIM_ID="$(printf '%s' "$APP_RESOURCE_JSON" | "$PYTHON" -c 'import json,sys; print(str(json.load(sys.stdin).get("service_principal_id") or "").strip())' 2>/dev/null || true)"
if [[ -z "$APP_SP_CLIENT_ID" || -z "$APP_SP_SCIM_ID" ]]; then
  echo "${RED}[deploy] could not resolve both service-principal identifiers for app '$_GRANTS_APP_NAME'.${RST}" >&2
  echo "  The bundle apply (step 4) should have created the app. Inspect 'databricks apps get $_GRANTS_APP_NAME'." >&2
  exit 4
fi
if [[ -z "$_GRANTS_WAREHOUSE_ID" ]]; then
  echo "${RED}[deploy] DATABRICKS_WAREHOUSE_ID missing (env or .env.local) — cannot apply UC grants.${RST}" >&2
  exit 4
fi
while IFS= read -r _grant_stmt; do
  [[ -z "$_grant_stmt" ]] && continue
  # Re-audit 2026-06-11: a single 50s/CANCEL attempt reported a cold or
  # queued warehouse as a misleading "grant failed". Retry the statement
  # up to 3 attempts (the wait_timeout API ceiling is 50s per call) so
  # warm-up latency is absorbed; a genuine authority failure still exits.
  _grant_state=""
  for _grant_try in 1 2 3; do
    _grant_resp="$(databricks api post /api/2.0/sql/statements/ --json "$(
      "$PYTHON" -c 'import json,sys; print(json.dumps({"warehouse_id": sys.argv[1], "statement": sys.argv[2], "wait_timeout": "50s", "on_wait_timeout": "CANCEL"}))' \
        "$_GRANTS_WAREHOUSE_ID" "$_grant_stmt"
    )")"
    _grant_state="$(printf '%s' "$_grant_resp" | "$PYTHON" -c 'import json,sys; d=json.load(sys.stdin); print(d.get("status",{}).get("state",""))')"
    [[ "$_grant_state" == "SUCCEEDED" ]] && break
    if [[ "$_grant_try" -lt 3 ]]; then
      echo "  grant attempt ${_grant_try} ended ${_grant_state:-no-state} (warehouse warming?) — retrying"
      sleep 5
    fi
  done
  if [[ "$_grant_state" != "SUCCEEDED" ]]; then
    echo "${RED}[deploy] UC grant failed after 3 attempts (${_grant_state:-no-state}): ${_grant_stmt}${RST}" >&2
    printf '%s\n' "$_grant_resp" | "$PYTHON" -c 'import json,sys; d=json.load(sys.stdin); print(json.dumps(d.get("status",{}).get("error",{}), indent=2)[:600])' >&2 || true
    echo "  Likely cause: the deploying identity lacks GRANT authority on catalog '${_GRANTS_CATALOG}'." >&2
    echo "  A metastore admin can apply docs/security/GRANTS.md once; reruns are idempotent." >&2
    exit 4
  fi
  echo "  granted: ${_grant_stmt}"
done <<GRANTS_EOF
GRANT USE CATALOG ON CATALOG ${_GRANTS_CATALOG} TO \`${APP_SP_CLIENT_ID}\`
GRANT USE SCHEMA, SELECT ON SCHEMA ${_GRANTS_CATALOG}.gold TO \`${APP_SP_CLIENT_ID}\`
GRANT MODIFY ON TABLE ${_GRANTS_CATALOG}.gold.borrower_lifecycle_state TO \`${APP_SP_CLIENT_ID}\`
GRANT MODIFY ON TABLE ${_GRANTS_CATALOG}.gold.funnel_snapshot_daily TO \`${APP_SP_CLIENT_ID}\`
GRANT USE SCHEMA, SELECT ON SCHEMA ${_GRANTS_CATALOG}.ref TO \`${APP_SP_CLIENT_ID}\`
GRANT USE SCHEMA ON SCHEMA ${_GRANTS_CATALOG}.audit TO \`${APP_SP_CLIENT_ID}\`
GRANT SELECT, MODIFY ON TABLE ${_GRANTS_CATALOG}.audit.campaign_treatment_snapshot TO \`${APP_SP_CLIENT_ID}\`
GRANT EXECUTE ON FUNCTION ${_GRANTS_CATALOG}.gold.fn_build_cohort TO \`${APP_SP_CLIENT_ID}\`
GRANT EXECUTE ON FUNCTION ${_GRANTS_CATALOG}.gold.fn_segment_counts TO \`${APP_SP_CLIENT_ID}\`
GRANT EXECUTE ON FUNCTION ${_GRANTS_CATALOG}.gold.fn_lead_queue_url TO \`${APP_SP_CLIENT_ID}\`
GRANT USE CATALOG ON CATALOG ${_GRANTS_SYNC_CATALOG} TO \`${APP_SP_CLIENT_ID}\`
GRANT USE SCHEMA, SELECT ON SCHEMA ${_GRANTS_SYNC_CATALOG}.${_GRANTS_SYNC_SCHEMA} TO \`${APP_SP_CLIENT_ID}\`
GRANTS_EOF

_treatment_grant_stmt="SHOW GRANTS \`${APP_SP_CLIENT_ID}\` ON TABLE ${_GRANTS_CATALOG}.audit.campaign_treatment_snapshot"
_treatment_grant_resp="$(databricks api post /api/2.0/sql/statements/ --json "$(
  "$PYTHON" -c 'import json,sys; print(json.dumps({"warehouse_id": sys.argv[1], "statement": sys.argv[2], "wait_timeout": "50s", "on_wait_timeout": "CANCEL"}))' \
    "$_GRANTS_WAREHOUSE_ID" "$_treatment_grant_stmt"
)")"
if ! printf '%s' "$_treatment_grant_resp" | "$PYTHON" -c '
import json, sys
body = json.load(sys.stdin)
if body.get("status", {}).get("state") != "SUCCEEDED":
    raise SystemExit(1)
cells = {str(cell).upper() for row in body.get("result", {}).get("data_array", []) for cell in row}
if not any("SELECT" in cell for cell in cells) or not any("MODIFY" in cell for cell in cells):
    raise SystemExit(1)
'; then
  echo "${RED}[deploy] campaign treatment table grant postflight failed for app principal.${RST}" >&2
  exit 4
fi
echo "  verified: app principal has SELECT + MODIFY on ${_GRANTS_CATALOG}.audit.campaign_treatment_snapshot"

_treatment_properties_stmt="SHOW TBLPROPERTIES ${_GRANTS_CATALOG}.audit.campaign_treatment_snapshot"
_treatment_properties_resp="$(databricks api post /api/2.0/sql/statements/ --json "$(
  "$PYTHON" -c 'import json,sys; print(json.dumps({"warehouse_id": sys.argv[1], "statement": sys.argv[2], "wait_timeout": "50s", "on_wait_timeout": "CANCEL"}))' \
    "$_GRANTS_WAREHOUSE_ID" "$_treatment_properties_stmt"
)")"
if ! printf '%s' "$_treatment_properties_resp" | "$PYTHON" -c '
import json, sys
body = json.load(sys.stdin)
if body.get("status", {}).get("state") != "SUCCEEDED":
    raise SystemExit(1)
rows = body.get("result", {}).get("data_array", [])
actual = {str(row[0]): str(row[1]) for row in rows if len(row) >= 2}
expected = {
    "delta.appendOnly": "true",
    "delta.logRetentionDuration": "interval 2555 days",
    "delta.deletedFileRetentionDuration": "interval 2555 days",
}
if any(actual.get(key) != value for key, value in expected.items()):
    raise SystemExit(1)
'; then
  echo "${RED}[deploy] campaign treatment table property postflight failed.${RST}" >&2
  exit 4
fi
echo "  verified: campaign treatment table is append-only with exact log and deleted-file retention"

# -----------------------------------------------------------------------------
# Step 4d: provision the PII-salt secret scope (audit P1-4, zero-click)
# -----------------------------------------------------------------------------
# pipelines/lakeflow/mip_feature_pipeline.py and the silver warehouse path
# hash PII columns with secret('mip', 'pii-salt-v1'). Nothing provisioned
# that scope: on a fresh workspace the DLT path failed mid-silver-refresh
# with an unexplained secret error, and the SQL path silently fell back to
# a source-committed constant (predictable hashing — worse than failing).
# Create-if-missing ONLY; an existing salt is NEVER rotated, because
# rotating it changes every masked identifier across refreshes and breaks
# join stability between gold snapshots.
step "provision pii-salt secret scope (create-if-missing, never rotate)"
# CLI JSON shape note (observed live 2026-06-11): `databricks secrets
# list-scopes -o json` / `list-secrets -o json` emit a BARE ARRAY on
# current CLI versions and a wrapped object on older ones — accept both.
if ! databricks secrets list-scopes -o json | "$PYTHON" -c 'import json,sys
data = json.load(sys.stdin)
items = data.get("scopes", []) if isinstance(data, dict) else (data or [])
names = {s.get("name") for s in items if isinstance(s, dict)}
sys.exit(0 if "mip" in names else 1)'; then
  run databricks secrets create-scope mip
fi
if ! databricks secrets list-secrets mip -o json 2>/dev/null | "$PYTHON" -c 'import json,sys
data = json.load(sys.stdin)
items = data.get("secrets", []) if isinstance(data, dict) else (data or [])
keys = {s.get("key") for s in items if isinstance(s, dict)}
sys.exit(0 if "pii-salt-v1" in keys else 1)'; then
  echo "  generating pii-salt-v1 (random 64-hex, write-once)"
  databricks secrets put-secret mip pii-salt-v1 --string-value "$(openssl rand -hex 32)"
else
  echo "  pii-salt-v1 already present — leaving untouched (rotation would break masked-ID stability)"
fi

# -----------------------------------------------------------------------------
# Step 5: promote uploaded source to the running Databricks App
# -----------------------------------------------------------------------------
APP_NAME="${MIP_APP_NAME:-mip-app}"

# Make the snapshot deploy deterministic (2026-06-10 audit fix). Two platform
# races were observed in the wild, each failing this step on a fresh run:
#   1. App STOPPED (idle auto-stop / manual stop) -> "Cannot deploy app ...
#      as it is not in RUNNING state."
#   2. The bundle deploy in the previous step triggers its OWN app deployment
#      (the app is a bundle resource), so an immediate `apps deploy` here
#      collides with it -> "active/pending deployment in progress."
# Both are waitable states, not errors. Start the app if needed, then poll
# until no deployment is in flight before promoting the snapshot.
wait_for_app_deployable() {
  local compute pend active i
  compute="$(databricks apps get "$APP_NAME" -o json 2>/dev/null | "$PYTHON" -c 'import json,sys; print((json.load(sys.stdin).get("compute_status") or {}).get("state",""))' || true)"
  if [[ "$compute" == "STOPPED" || "$compute" == "STOPPING" ]]; then
    step "app compute is ${compute} — starting before snapshot deploy"
    run databricks apps start "$APP_NAME"
  fi
  for i in $(seq 1 90); do
    pend="$(databricks apps get "$APP_NAME" -o json 2>/dev/null | "$PYTHON" -c 'import json,sys; d=json.load(sys.stdin); print(((d.get("pending_deployment") or {}).get("status") or {}).get("state","NONE"))' || echo "UNKNOWN")"
    active="$(databricks apps get "$APP_NAME" -o json 2>/dev/null | "$PYTHON" -c 'import json,sys; d=json.load(sys.stdin); print(((d.get("active_deployment") or {}).get("status") or {}).get("state","NONE"))' || echo "UNKNOWN")"
    if [[ "$pend" == "NONE" && "$active" != "IN_PROGRESS" ]]; then
      return 0
    fi
    echo "  waiting for in-flight app deployment to settle (pending=${pend}, active=${active}) [${i}/90]"
    sleep 10
  done
  echo "${RED}[deploy] app deployment still in flight after 15 minutes; aborting snapshot deploy.${RST}" >&2
  return 1
}

deploy_app_snapshot() {
  local label="$1"
  step "$label"
  APP_DEPLOY_PAYLOAD="$(mktemp -t mip-app-deploy.XXXXXX.json)"
  MIP_GIT_SHA="$APP_GIT_SHA" "$PYTHON" tools/databricks/app_deploy_payload.py \
    --source-code-path "$APP_SOURCE_PATH" \
    --target "$TARGET" \
    --current-user-email "$APP_CURRENT_USER" \
    --app-env "$APP_RUNTIME_ENV" \
    --catalog "${MIP_DEFAULT_CATALOG:-mip}" \
    --schema "${MIP_DEFAULT_SCHEMA:-gold}" \
    > "$APP_DEPLOY_PAYLOAD"
  run databricks apps deploy "$APP_NAME" --json "@$APP_DEPLOY_PAYLOAD" --timeout 20m
  rm -f "$APP_DEPLOY_PAYLOAD"
  APP_DEPLOY_PAYLOAD=""

  if [[ "$DRY_RUN" -eq 0 && -z "${MIP_APP_URL:-}" ]]; then
    DEPLOYED_APP_URL="$(databricks apps get "$APP_NAME" -o json | "$PYTHON" -c 'import json,sys; print(json.load(sys.stdin).get("url",""))')"
    if [[ -n "$DEPLOYED_APP_URL" ]]; then
      export MIP_APP_URL="$DEPLOYED_APP_URL"
      echo "  app url:    ${MIP_APP_URL}"
    else
      echo "${RED}[deploy] deployed app URL could not be resolved; refusing to run a local smoke while claiming deployed proof.${RST}" >&2
      exit 1
    fi
  fi
}

if [[ "$DRY_RUN" -eq 0 ]]; then
  wait_for_app_deployable
fi

APP_DEPLOY_META="$(databricks bundle summary -t "$TARGET" -o json | "$PYTHON" -c 'import json,sys; data=json.load(sys.stdin); ws=data.get("workspace") or {}; print((data.get("resources") or {}).get("apps", {}).get("mip_app", {}).get("source_code_path") or ws.get("file_path") or ""); print((ws.get("current_user") or {}).get("userName") or "")')"
APP_SOURCE_PATH="$(printf '%s\n' "$APP_DEPLOY_META" | sed -n '1p')"
APP_CURRENT_USER="$(printf '%s\n' "$APP_DEPLOY_META" | sed -n '2p')"
if [[ -z "$APP_SOURCE_PATH" ]]; then
  echo "${RED}[deploy] bundle summary did not expose the uploaded app source path.${RST}" >&2
  exit 1
fi

# Roll-forward env continuity (external audits 2026-07-07 tripped on this
# twice): the first snapshot deploy used to ship WITHOUT the agentic env,
# leaving a window until the post-provisioning redeploy where the live app
# reported ai_gateway/agent_* as not provisioned. The agentic provisioner's
# --out-env is persisted under .databricks/ (gitignored) at the end of each
# run; source it here so a re-deploy never forgets what is already
# provisioned. First-ever deploys have no file and keep the two-phase flow.
AGENTIC_ENV_CACHE=".databricks/mip-agentic.env"
if [[ "$DRY_RUN" -eq 0 && -f "$AGENTIC_ENV_CACHE" ]]; then
  echo "[deploy] carrying forward agentic env from $AGENTIC_ENV_CACHE (last provisioning)"
  set -a
  # shellcheck disable=SC1090
  . "$AGENTIC_ENV_CACHE"
  set +a
fi
deploy_app_snapshot "deploy Databricks App snapshot from uploaded bundle source"

# -----------------------------------------------------------------------------
# Step 6: silver refresh (FRED + Cotality share)
# -----------------------------------------------------------------------------
if [[ "$SKIP_SILVER" -eq 1 ]]; then
  step "silver refresh — SKIPPED (--skip-silver)"
else
  step "refresh silver — FRED MORTGAGE30US rates"
  run_job_with_retry databricks bundle run mip_fred_rates_ingest -t "$TARGET"

  step "refresh silver — Cotality share (data-driven geography coverage)"
  run_job_with_retry databricks bundle run mip_refresh_silver -t "$TARGET"
fi

# -----------------------------------------------------------------------------
# (Step 7 removed: Lakebase migration moved to Step 4b, before the app
#  snapshot restart, so the restarted app never races the schema work.)

# -----------------------------------------------------------------------------
# Step 8: gold refresh (CTAS chain, ends with refresh_semantics_views)
# -----------------------------------------------------------------------------
step "refresh gold — borrower_360, lead_scores, *_population, dossier, + mip.semantics.*"
run_job_with_retry databricks bundle run mip_refresh_scores -t "$TARGET"

# -----------------------------------------------------------------------------
# Step 9: lifecycle sync + funnel snapshot (approval / outreach rates)
# -----------------------------------------------------------------------------
step "sync lifecycle state from Lakebase + record daily funnel snapshot"
run "$PYTHON" tools/sync_lifecycle_warehouse.py --catalog "${MIP_DEFAULT_CATALOG:-mip}"

# -----------------------------------------------------------------------------
# Step 9b: KPI snapshot backfill (S3)
# -----------------------------------------------------------------------------
# Upsert today's headline-KPI snapshot into Lakebase mip_app.kpi_snapshots so
# S4's "since your last login" deltas never see an empty table on a fresh
# install. Requires step 4b (Lakebase schema) + step 8 (gold refresh landed
# semantics.portfolio_headline_metric_view). Idempotent: the job keys on
# snapshot_date, so re-deploying the same day refreshes the day's row.
step "record headline KPI snapshot — mip_app.kpi_snapshots backfill (idempotent per-day)"
run_job_with_retry databricks bundle run mip_kpi_snapshot -t "$TARGET"

# -----------------------------------------------------------------------------
# Step 10: rebind the Genie space after gold/semantic assets exist
# -----------------------------------------------------------------------------
step "rebind Genie space — bind trusted assets from genie/mortgage_lead_intelligence_space.yml"
run "$PYTHON" tools/databricks/provision_genie_space.py --no-smoke-test

# -----------------------------------------------------------------------------
# Step 10b: provision MIP-owned agentic resources after gold/Genie assets exist
# -----------------------------------------------------------------------------
step "provision agentic resources — Lakebase sync, AI Gateway, Supervisor Agent"
AGENTIC_ENV_FILE="$(mktemp -t mip-agentic.XXXXXX.env)"
run "$PYTHON" tools/databricks/provision_agentic_resources.py \
  --catalog "${MIP_DEFAULT_CATALOG:-mip}" \
  --genie-space-id "${GENIE_SPACE_ID:-$(< genie/space_id.txt)}" \
  --out-env "$AGENTIC_ENV_FILE"
if [[ "$DRY_RUN" -eq 0 ]]; then
  set -a
  # shellcheck disable=SC1090
  . "$AGENTIC_ENV_FILE"
  set +a
  # Persist for the next roll-forward's first snapshot deploy (env
  # continuity — see AGENTIC_ENV_CACHE above). Values only name resources
  # (endpoint/table/supervisor/experiment ids); no secrets are written.
  mkdir -p .databricks
  cp "$AGENTIC_ENV_FILE" "$AGENTIC_ENV_CACHE"
  if [[ -n "${MIP_AI_GATEWAY_INFERENCE_TABLE:-}" && -n "${MIP_AI_GATEWAY_ENDPOINT:-}" ]]; then
    step "converge dedicated AI Gateway verifier identity and Lakebase OAuth role"
    run "$PYTHON" tools/databricks/provision_m2m_oauth.py \
      --identity-role verifier \
      --expected-application-id "$DATABRICKS_VERIFIER_CLIENT_ID" \
      --gateway-endpoint "$MIP_AI_GATEWAY_ENDPOINT" \
      --revoke-gateway-endpoint "${MIP_AGENT_SUPERVISOR_ENDPOINT:-}" \
      --revoke-gateway-endpoint "mip-agent-gateway" \
      --warehouse-id "$_GRANTS_WAREHOUSE_ID" \
      --no-mint-secret
    step "reconcile runtime read-only and verifier-only Lakebase proof-ledger grants"
    run "$PYTHON" jobs/lakebase_migrate.py
    AI_GATEWAY_GRANTS_READY=1
    step "grant least-privilege AI Gateway inference-table access to the app service principal"
    if ! run "$PYTHON" tools/databricks/grant_ai_gateway_inference_table.py \
      --warehouse-id "$_GRANTS_WAREHOUSE_ID" \
      --relation-prefix "$MIP_AI_GATEWAY_INFERENCE_TABLE" \
      --endpoint "$MIP_AI_GATEWAY_ENDPOINT" \
      --principal "$APP_SP_CLIENT_ID"; then
      AI_GATEWAY_GRANTS_READY=0
    fi
    if [[ "$AI_GATEWAY_GRANTS_READY" -eq 1 ]]; then
      step "grant read-only AI Gateway inference-table access to the verifier service principal"
      if ! run "$PYTHON" tools/databricks/grant_ai_gateway_inference_table.py \
        --warehouse-id "$_GRANTS_WAREHOUSE_ID" \
        --relation-prefix "$MIP_AI_GATEWAY_INFERENCE_TABLE" \
        --endpoint "$MIP_AI_GATEWAY_ENDPOINT" \
        --principal "$DATABRICKS_VERIFIER_CLIENT_ID"; then
        AI_GATEWAY_GRANTS_READY=0
      fi
    fi
    if [[ "$AI_GATEWAY_GRANTS_READY" -eq 0 ]]; then
      if [[ "${MIP_REQUIRE_AI_GATEWAY_CLAIMABLE:-0}" == "1" ]]; then
        echo "${RED}[deploy] strict AI Gateway inference-table grant convergence failed.${RST}" >&2
        exit 1
      fi
      echo "${YLW}[deploy] AI Gateway inference-table delivery/grants are pending; skipping exact proof and continuing with the capability honestly configured/unavailable.${RST}" >&2
    else
      DATABRICKS_ACCOUNT_HOST="${DATABRICKS_ACCOUNT_HOST:-https://accounts.cloud.databricks.com}"
      if [[ -z "${DATABRICKS_ACCOUNT_ID:-}" ]]; then
        echo "${RED}[deploy] DATABRICKS_ACCOUNT_ID is required before the verifier can write an exact Gateway proof.${RST}" >&2
        exit 1
      fi
      step "prove verifier effective authorization boundary before exact Gateway proof"
      run_as_m2m_identity \
        verifier \
        DATABRICKS_VERIFIER_CLIENT_ID \
        DATABRICKS_VERIFIER_CLIENT_SECRET \
        "$PYTHON" tools/databricks/verify_verifier_identity_boundary.py \
        --expected-application-id "$DATABRICKS_VERIFIER_CLIENT_ID" \
        --account-host "$DATABRICKS_ACCOUNT_HOST" \
        --account-id "$DATABRICKS_ACCOUNT_ID" \
        --app-name "$_GRANTS_APP_NAME" \
        --app-url "${MIP_APP_URL:?deployed app URL is required}" \
        --protected-service-principal-id "$APP_SP_SCIM_ID" \
        --warehouse-id "$_GRANTS_WAREHOUSE_ID" \
        --relation-prefix "$MIP_AI_GATEWAY_INFERENCE_TABLE" \
        --endpoint "$MIP_AI_GATEWAY_ENDPOINT"
      step "verify AI Gateway exact inference-row proof with dedicated verifier identity"
      AI_GATEWAY_PROOF_ARGS=(
        tools/databricks/verify_ai_gateway_exact_proof.py
        send
        --wait
        --git-sha "$APP_GIT_SHA"
        --endpoint "$MIP_AI_GATEWAY_ENDPOINT"
        --inference-table "$MIP_AI_GATEWAY_INFERENCE_TABLE"
      )
      if [[ "${MIP_REQUIRE_AI_GATEWAY_CLAIMABLE:-0}" == "1" ]]; then
        AI_GATEWAY_PROOF_ARGS+=(--require-verified)
      fi
      if ! run_as_m2m_identity \
        verifier \
        DATABRICKS_VERIFIER_CLIENT_ID \
        DATABRICKS_VERIFIER_CLIENT_SECRET \
        "$PYTHON" "${AI_GATEWAY_PROOF_ARGS[@]}"; then
        if [[ "${MIP_REQUIRE_AI_GATEWAY_CLAIMABLE:-0}" == "1" ]]; then
          echo "${RED}[deploy] strict AI Gateway exact proof failed.${RST}" >&2
          exit 1
        fi
        echo "${YLW}[deploy] AI Gateway exact proof is not claimable; continuing with the capability honestly configured/unavailable.${RST}" >&2
      fi
    fi
  fi
fi

if [[ "$DRY_RUN" -eq 0 ]]; then
  wait_for_app_deployable
fi
deploy_app_snapshot "deploy Databricks App snapshot with agentic resource env"

# -----------------------------------------------------------------------------
# Step 10c: run live Agent Evaluation, then redeploy with the eval run id
# -----------------------------------------------------------------------------
if [[ "$DRY_RUN" -eq 0 ]]; then
  # A full deploy can exceed the workspace OAuth TTL before eval starts.
  # Remint both identities immediately before the proof and never substitute
  # the deployment PAT for either app-facing role.
  mint_m2m_token MIP_BEARER_TOKEN DATABRICKS_CLIENT_ID DATABRICKS_CLIENT_SECRET
  mint_m2m_token MIP_ADMIN_BEARER_TOKEN \
    DATABRICKS_ADMIN_CLIENT_ID DATABRICKS_ADMIN_CLIENT_SECRET
fi
step "run live Agent Evaluation — golden Growth Agent workflows"
mkdir -p dist
AGENT_EVAL_ENV_FILE="$(mktemp -t mip-agent-eval.XXXXXX.env)"
run "$PYTHON" tools/databricks/run_agent_eval.py \
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
  wait_for_app_deployable
fi
deploy_app_snapshot "deploy Databricks App snapshot with Agent Evaluation proof"

# -----------------------------------------------------------------------------
# Step 11 (optional): live smoke test
# -----------------------------------------------------------------------------
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
      mint_m2m_token MIP_BEARER_TOKEN DATABRICKS_CLIENT_ID DATABRICKS_CLIENT_SECRET
    fi
    step "live smoke — scripts/smoke_live.sh against the deployed app"
    export MIP_EXPECT_AGENTIC_CAPABILITIES="${MIP_EXPECT_AGENTIC_CAPABILITIES:-1}"
    export MIP_EXPECT_GIT_SHA="$APP_GIT_SHA"
    if ! run ./scripts/smoke_live.sh; then
      if [[ "${ALLOW_SMOKE_FAILURE:-0}" == "1" ]]; then
        echo "${YLW}[deploy] smoke test failed — override ALLOW_SMOKE_FAILURE=1 kept the deploy moving.${RST}" >&2
        echo "${YLW}[deploy] this is for manual emergency use only; fix before customer release.${RST}" >&2
      else
        echo "${RED}[deploy] smoke test failed — deployed source is not customer-release-ready.${RST}" >&2
        echo "${RED}[deploy] set ALLOW_SMOKE_FAILURE=1 only for an intentional manual emergency override.${RST}" >&2
        exit 1
      fi
    fi
  else
    step "live smoke — scripts/smoke_live.sh not executable; skipping"
  fi
fi

# -----------------------------------------------------------------------------
# Done
# -----------------------------------------------------------------------------
echo
echo "${GRN}[deploy] complete.${RST}"
echo "${DIM}  App URL:     ${MIP_APP_URL:-"(check the Databricks workspace → Apps)"}${RST}"
echo "${DIM}  Genie space: genie/space_id.txt (provisioned before bundle deploy, rebound after gold refresh).${RST}"
echo "${DIM}  Re-run any time — every step is idempotent.${RST}"

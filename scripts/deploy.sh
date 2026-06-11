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
#       `refresh_semantics_views`, which lands the three mip.semantics.*
#       metric views Genie depends on.
#   9.  Sync lifecycle state + funnel snapshot so the delta_vs_prior_*
#       view columns resolve on the first dashboard render.
#   10. Provision / rebind the Genie space via
#       tools/databricks/provision_genie_space.py.
#   11. Smoke-check the live API via scripts/smoke_live.sh (optional; fail-loud by default).
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

on_error() {
  local rc=$?
  echo
  echo "${RED}[deploy] FAILED at step ${STEP} (exit ${rc}).${RST}" >&2
  echo "${YLW}[deploy] fix the error above and re-run: ./scripts/deploy.sh${RST}" >&2
  echo "${YLW}[deploy] every step is idempotent — re-running picks up where this stopped.${RST}" >&2
  exit "$rc"
}
trap on_error ERR

# -----------------------------------------------------------------------------
# Resolve python interpreter (same convention as the Makefile)
# -----------------------------------------------------------------------------
if [[ -x .venv/bin/python ]]; then
  PYTHON=".venv/bin/python"
else
  PYTHON="python3"
fi

RESTORE_RENDERED_SQL_FAIL_CLOSED=0

restore_rendered_sql_fail_closed() {
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
  read -p "About to DEPLOY to the ${TARGET} target. Continue? [y/N] " ans
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

# Cotality ID-mask HMAC visibility check (audit P3, 2026-06-11). When
# MIP_COTALITY_ID_MASK_SECRET is unset, backend/services/pii_redaction.py
# falls back to a source-committed constant — masked IDs are still stable,
# but anyone with repo access can recompute the mapping. Fine for the
# synthetic-data sandbox; never acceptable for a customer deploy. Warn,
# don't block (mirrors the MIP_ADMIN_EMAILS posture above).
_ID_MASK_RESOLVED="${MIP_COTALITY_ID_MASK_SECRET:-$("$PYTHON" - <<'PYEOF'
from pathlib import Path
try:
    from dotenv import dotenv_values
    values = dotenv_values(Path(".env.local"))
    print((values.get("MIP_COTALITY_ID_MASK_SECRET")
           or values.get("MIP_GENIE_ACTION_SECRET") or "").strip())
except Exception:
    print("")
PYEOF
)}"
if [[ -z "$_ID_MASK_RESOLVED" ]]; then
  echo "${YLW}[deploy] WARNING: MIP_COTALITY_ID_MASK_SECRET is not set (env or .env.local).${RST}" >&2
  echo "${YLW}  Cotality ID masking will use the source-committed fallback constant —${RST}" >&2
  echo "${YLW}  acceptable for the synthetic-data sandbox, NOT for customer deploys.${RST}" >&2
else
  echo "  cotality id-mask secret: configured"
fi

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
    export GENIE_SPACE_ID="$(< genie/space_id.txt)"
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
step "deploy bundle (app + warehouse + jobs + pipelines + Lakebase)"
run "$PYTHON" tools/databricks/bundle_env.py deploy -t "$TARGET"

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
run databricks bundle run mip_lakebase_migrate -t "$TARGET"

# -----------------------------------------------------------------------------
# Step 4c: UC grants for the app service principal (audit P1-3, zero-click)
# -----------------------------------------------------------------------------
# On a FRESH workspace the bundle creates the app + its service principal,
# but nothing granted that SP read access to the UC objects — the app booted
# to PERMISSION_DENIED on every endpoint and docs/security/GRANTS.md was a
# manual copy-paste runbook (CLAUDE.md calls that exact pattern a packaging
# bug). This step applies the GRANTS.md §catalog/§gold/§ref statements
# idempotently (GRANT is a no-op when already granted) against the deploy
# warehouse, addressed to the SP's client id. Failures are FATAL with a
# pointer to GRANTS.md: a deploy that cannot grant is a deploy whose app
# cannot read, and hiding that would violate the fail-visibly contract.
# GRANTS.md remains the audit-readable matrix; Lakebase role grants are
# applied by jobs/lakebase_migrate.py in step 4b.
step "apply UC grants to the app service principal (idempotent)"
_GRANTS_APP_NAME="${MIP_APP_NAME:-mip-app}"
_GRANTS_WAREHOUSE_ID="${DATABRICKS_WAREHOUSE_ID:-$(dotenv_value DATABRICKS_WAREHOUSE_ID)}"
_GRANTS_CATALOG="${MIP_DEFAULT_CATALOG:-mip}"
APP_SP_CLIENT_ID="$(databricks apps get "$_GRANTS_APP_NAME" -o json 2>/dev/null | "$PYTHON" -c 'import json,sys; print((json.load(sys.stdin).get("service_principal_client_id") or "").strip())' || true)"
if [[ -z "$APP_SP_CLIENT_ID" ]]; then
  echo "${RED}[deploy] could not resolve service_principal_client_id for app '$_GRANTS_APP_NAME'.${RST}" >&2
  echo "  The bundle apply (step 4) should have created the app. Inspect 'databricks apps get $_GRANTS_APP_NAME'." >&2
  exit 4
fi
if [[ -z "$_GRANTS_WAREHOUSE_ID" ]]; then
  echo "${RED}[deploy] DATABRICKS_WAREHOUSE_ID missing (env or .env.local) — cannot apply UC grants.${RST}" >&2
  exit 4
fi
while IFS= read -r _grant_stmt; do
  [[ -z "$_grant_stmt" ]] && continue
  _grant_resp="$(databricks api post /api/2.0/sql/statements/ --json "$(
    "$PYTHON" -c 'import json,sys; print(json.dumps({"warehouse_id": sys.argv[1], "statement": sys.argv[2], "wait_timeout": "50s", "on_wait_timeout": "CANCEL"}))' \
      "$_GRANTS_WAREHOUSE_ID" "$_grant_stmt"
  )")"
  _grant_state="$(printf '%s' "$_grant_resp" | "$PYTHON" -c 'import json,sys; d=json.load(sys.stdin); print(d.get("status",{}).get("state",""))')"
  if [[ "$_grant_state" != "SUCCEEDED" ]]; then
    echo "${RED}[deploy] UC grant failed (${_grant_state:-no-state}): ${_grant_stmt}${RST}" >&2
    printf '%s\n' "$_grant_resp" | "$PYTHON" -c 'import json,sys; d=json.load(sys.stdin); print(json.dumps(d.get("status",{}).get("error",{}), indent=2)[:600])' >&2 || true
    echo "  Likely cause: the deploying identity lacks GRANT authority on catalog '${_GRANTS_CATALOG}'." >&2
    echo "  A metastore admin can apply docs/security/GRANTS.md once; reruns are idempotent." >&2
    exit 4
  fi
  echo "  granted: ${_grant_stmt}"
done <<GRANTS_EOF
GRANT USE CATALOG ON CATALOG ${_GRANTS_CATALOG} TO \`${APP_SP_CLIENT_ID}\`
GRANT USE SCHEMA, SELECT ON SCHEMA ${_GRANTS_CATALOG}.gold TO \`${APP_SP_CLIENT_ID}\`
GRANT USE SCHEMA, SELECT ON SCHEMA ${_GRANTS_CATALOG}.ref TO \`${APP_SP_CLIENT_ID}\`
GRANTS_EOF

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
if ! databricks secrets list-scopes -o json | "$PYTHON" -c 'import json,sys; scopes={s.get("name") for s in json.load(sys.stdin).get("scopes",[])}; sys.exit(0 if "mip" in scopes else 1)'; then
  run databricks secrets create-scope mip
fi
if ! databricks secrets list-secrets mip -o json 2>/dev/null | "$PYTHON" -c 'import json,sys; keys={s.get("key") for s in json.load(sys.stdin).get("secrets",[])}; sys.exit(0 if "pii-salt-v1" in keys else 1)'; then
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
if [[ "$DRY_RUN" -eq 0 ]]; then
  wait_for_app_deployable
fi

step "deploy Databricks App snapshot from uploaded bundle source"
APP_DEPLOY_META="$(databricks bundle summary -t "$TARGET" -o json | "$PYTHON" -c 'import json,sys; data=json.load(sys.stdin); ws=data.get("workspace") or {}; print((data.get("resources") or {}).get("apps", {}).get("mip_app", {}).get("source_code_path") or ws.get("file_path") or ""); print((ws.get("current_user") or {}).get("userName") or "")')"
APP_SOURCE_PATH="$(printf '%s\n' "$APP_DEPLOY_META" | sed -n '1p')"
APP_CURRENT_USER="$(printf '%s\n' "$APP_DEPLOY_META" | sed -n '2p')"
if [[ -z "$APP_SOURCE_PATH" ]]; then
  echo "${RED}[deploy] bundle summary did not expose the uploaded app source path.${RST}" >&2
  exit 1
fi
APP_DEPLOY_PAYLOAD="$(mktemp -t mip-app-deploy.XXXXXX.json)"
"$PYTHON" tools/databricks/app_deploy_payload.py \
  --source-code-path "$APP_SOURCE_PATH" \
  --target "$TARGET" \
  --current-user-email "$APP_CURRENT_USER" \
  --app-env sandbox \
  --catalog "${MIP_DEFAULT_CATALOG:-mip}" \
  --schema "${MIP_DEFAULT_SCHEMA:-gold}" \
  > "$APP_DEPLOY_PAYLOAD"
run databricks apps deploy "$APP_NAME" --json "@$APP_DEPLOY_PAYLOAD" --timeout 20m
rm -f "$APP_DEPLOY_PAYLOAD"

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

# -----------------------------------------------------------------------------
# Step 6: silver refresh (FRED + Cotality share)
# -----------------------------------------------------------------------------
if [[ "$SKIP_SILVER" -eq 1 ]]; then
  step "silver refresh — SKIPPED (--skip-silver)"
else
  step "refresh silver — FRED MORTGAGE30US rates"
  run databricks bundle run mip_fred_rates_ingest -t "$TARGET"

  step "refresh silver — Cotality share (data-driven geography coverage)"
  run databricks bundle run mip_refresh_silver -t "$TARGET"
fi

# -----------------------------------------------------------------------------
# (Step 7 removed: Lakebase migration moved to Step 4b, before the app
#  snapshot restart, so the restarted app never races the schema work.)

# -----------------------------------------------------------------------------
# Step 8: gold refresh (CTAS chain, ends with refresh_semantics_views)
# -----------------------------------------------------------------------------
step "refresh gold — borrower_360, lead_scores, *_population, dossier, + mip.semantics.*"
run databricks bundle run mip_refresh_scores -t "$TARGET"

# -----------------------------------------------------------------------------
# Step 9: lifecycle sync + funnel snapshot (approval / outreach rates)
# -----------------------------------------------------------------------------
step "sync lifecycle state from Lakebase + record daily funnel snapshot"
run databricks bundle run mip_sync_lifecycle_state -t "$TARGET"

# -----------------------------------------------------------------------------
# Step 10: rebind the Genie space after gold/semantic assets exist
# -----------------------------------------------------------------------------
step "rebind Genie space — bind trusted assets from genie/mortgage_lead_intelligence_space.yml"
run "$PYTHON" tools/databricks/provision_genie_space.py --no-smoke-test

# -----------------------------------------------------------------------------
# Step 11 (optional): live smoke test
# -----------------------------------------------------------------------------
if [[ "$SKIP_SMOKE" -eq 1 ]]; then
  step "live smoke — SKIPPED (--skip-smoke)"
else
  if [[ -x scripts/smoke_live.sh ]]; then
    if [[ "$DRY_RUN" -eq 0 && -n "${MIP_APP_URL:-}" && -z "${MIP_BEARER_TOKEN:-}" ]]; then
      AUTH_HOST="${DATABRICKS_HOST:-$(dotenv_value DATABRICKS_HOST)}"
      if [[ -n "$AUTH_HOST" ]]; then
        export MIP_BEARER_TOKEN="$(databricks auth token --host "$AUTH_HOST" | "$PYTHON" -c 'import json,sys; print(json.load(sys.stdin).get("access_token",""))')"
      fi
    fi
    step "live smoke — scripts/smoke_live.sh against the deployed app"
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

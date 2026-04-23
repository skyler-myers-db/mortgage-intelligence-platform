#!/usr/bin/env bash
# =============================================================================
# scripts/deploy.sh
# -----------------------------------------------------------------------------
# One-command zero-click dev deploy for the Mortgage Intelligence Platform.
#
# What it does, in order, idempotently:
#   0.  Preflight: check .env.local exists + `databricks` CLI + the venv.
#   1.  Build the frontend (frontend/dist/** is uploaded with the bundle).
#   2.  Validate the bundle under `-t dev`, with .env.local mapped to
#       BUNDLE_VAR_* via tools/databricks/bundle_env.py.
#   3.  Deploy the bundle.
#   4.  Seed + refresh silver (FRED MORTGAGE30US + Cotality share).
#   5.  Migrate Lakebase (idempotent schema.sql + seed_campaigns.sql).
#   6.  Refresh gold (CTAS chain) — the last task in the chain is
#       `refresh_semantics_views`, which lands the three mip.semantics.*
#       metric views Genie depends on.
#   7.  Sync lifecycle state + funnel snapshot so the delta_vs_prior_*
#       view columns resolve on the first dashboard render.
#   8.  Provision / rebind the Genie space via
#       tools/databricks/provision_genie_space.py.
#   9.  Smoke-check the live API via scripts/smoke_live.sh (optional).
#
# Why one script (vs a bundle job that invokes provision_genie_space.py):
# the Genie provisioner reads genie/mortgage_lead_intelligence_space.yml
# from the local repo. Shipping it as a bundle job would require uploading
# that YAML as an artifact; keeping the provisioner local to the deploy
# workstation keeps the source of truth in-repo where code review lives.
#
# Usage:
#   ./scripts/deploy.sh                 # full deploy
#   ./scripts/deploy.sh --dry-run       # print the plan, make no changes
#   ./scripts/deploy.sh --skip-silver   # skip silver refresh (FRED + share)
#   ./scripts/deploy.sh --skip-smoke    # skip the post-deploy curl smoke test
#   ./scripts/deploy.sh --no-confirm    # skip the y/N prompt before deploy
#
# Environment:
#   .env.local must set at minimum DATABRICKS_HOST, DATABRICKS_WAREHOUSE_ID.
#   (GENIE_SPACE_ID is written by step 8 on first run; re-running deploy
#   afterwards picks it up and feeds it to the bundle via BUNDLE_VAR_*.)
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

# -----------------------------------------------------------------------------
# Step 0: preflight
# -----------------------------------------------------------------------------
step "preflight — check .env.local, databricks CLI, venv"

if [[ ! -f .env.local ]]; then
  echo "${RED}[deploy] .env.local missing.${RST}" >&2
  echo "  copy .env.local.example if present, then fill in DATABRICKS_HOST + DATABRICKS_WAREHOUSE_ID." >&2
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

# -----------------------------------------------------------------------------
# Step 1a: render SQL for the target UC catalog
# -----------------------------------------------------------------------------
# The bundle's SQL tasks read from sql/_rendered/**/*.sql. The canonical
# sources under sql/** hardcode the default `mip.*` catalog prefix for
# readability + code review; tools/render_sql.py substitutes the five
# documented UC prefixes (mip.gold., mip.silver., mip.ref., mip.semantics.,
# mip.raw.) for the target catalog before bundle validate/deploy read the
# rendered tree. This is the automated replacement for the old manual
# `sed` workaround documented in docs/runbook-multi-catalog.md. Honours
# `MIP_DEFAULT_CATALOG` from .env.local; defaults to `mip`.
step "render SQL for target UC catalog (MIP_DEFAULT_CATALOG=${MIP_DEFAULT_CATALOG:-mip})"
run "$PYTHON" tools/render_sql.py --catalog "${MIP_DEFAULT_CATALOG:-mip}"

# -----------------------------------------------------------------------------
# Step 1: build the frontend
# -----------------------------------------------------------------------------
step "build frontend (frontend/dist/** is uploaded by the bundle sync.include)"
run npm --prefix frontend run build

# -----------------------------------------------------------------------------
# Step 2: validate bundle
# -----------------------------------------------------------------------------
step "validate bundle against -t ${TARGET}"
run "$PYTHON" tools/databricks/bundle_env.py validate -t "$TARGET"

# -----------------------------------------------------------------------------
# Step 3: deploy bundle
# -----------------------------------------------------------------------------
step "deploy bundle (app + warehouse + jobs + pipelines + Lakebase)"
run "$PYTHON" tools/databricks/bundle_env.py deploy -t "$TARGET"

# -----------------------------------------------------------------------------
# Step 4: silver refresh (FRED + Cotality share)
# -----------------------------------------------------------------------------
if [[ "$SKIP_SILVER" -eq 1 ]]; then
  step "silver refresh — SKIPPED (--skip-silver)"
else
  step "refresh silver — FRED MORTGAGE30US rates"
  run databricks bundle run mip_fred_rates_ingest -t "$TARGET"

  step "refresh silver — Cotality share (state-filtered to IL/CA/FL/TX/WA/CO)"
  run databricks bundle run mip_refresh_silver -t "$TARGET"
fi

# -----------------------------------------------------------------------------
# Step 5: Lakebase migration
# -----------------------------------------------------------------------------
step "migrate Lakebase — schema.sql + seed_campaigns.sql (idempotent)"
run databricks bundle run mip_lakebase_migrate -t "$TARGET"

# -----------------------------------------------------------------------------
# Step 6: gold refresh (CTAS chain, ends with refresh_semantics_views)
# -----------------------------------------------------------------------------
step "refresh gold — borrower_360, lead_scores, *_population, dossier, + mip.semantics.*"
run databricks bundle run mip_refresh_scores -t "$TARGET"

# -----------------------------------------------------------------------------
# Step 7: lifecycle sync + funnel snapshot (approval / outreach rates)
# -----------------------------------------------------------------------------
step "sync lifecycle state from Lakebase + record daily funnel snapshot"
run databricks bundle run mip_sync_lifecycle_state -t "$TARGET"

# -----------------------------------------------------------------------------
# Step 8: provision the Genie space
# -----------------------------------------------------------------------------
step "provision Genie space — bind trusted assets from genie/mortgage_lead_intelligence_space.yml"
run "$PYTHON" tools/databricks/provision_genie_space.py --no-smoke-test

# -----------------------------------------------------------------------------
# Step 9 (optional): live smoke test
# -----------------------------------------------------------------------------
if [[ "$SKIP_SMOKE" -eq 1 ]]; then
  step "live smoke — SKIPPED (--skip-smoke)"
else
  if [[ -x scripts/smoke_live.sh ]]; then
    step "live smoke — scripts/smoke_live.sh against the deployed app"
    run ./scripts/smoke_live.sh || {
      echo "${YLW}[deploy] smoke test failed — the deploy itself is green, but the app isn't responding yet.${RST}" >&2
      echo "${YLW}[deploy] this is often a cold warehouse / cold Lakebase. See docs/runbook.md §1.${RST}" >&2
    }
  else
    step "live smoke — scripts/smoke_live.sh not executable; skipping"
  fi
fi

# -----------------------------------------------------------------------------
# Done
# -----------------------------------------------------------------------------
echo
echo "${GRN}[deploy] complete.${RST}"
echo "${DIM}  App URL:     \$MIP_APP_URL (or check the Databricks workspace → Apps).${RST}"
echo "${DIM}  Genie space: genie/space_id.txt (written by step 8).${RST}"
echo "${DIM}  Re-run any time — every step is idempotent.${RST}"

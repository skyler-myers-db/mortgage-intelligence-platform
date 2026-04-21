#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# scripts/smoke_live.sh -- operator "is real UC actually reachable?" smoke
#
# Slice 9 of the real-data migration closes the self-contained loop: after
# a `databricks bundle deploy -t dev`, the operator should be able to run
# this script and see 200s from every canonical endpoint in under ~30s.
#
# Behaviour:
#   1. Boots uvicorn (backend) + vite (frontend) locally IF MIP_APP_URL is
#      unset, then waits for /api/health to return ok. If MIP_APP_URL is
#      set, targets that URL directly (no local boot).
#   2. Asserts /api/health is `status:"ok"` with every dependency `up`.
#   3. Plays through the 5 canonical API calls in user-flow order:
#        portfolio preview -> leads -> borrower dossier -> evidence -> genie
#   4. Tears down the local servers cleanly on exit (trap on SIGINT/SIGTERM
#      too).
#
# Exit codes:
#   0 -- every call returned 200 AND /api/health shows all dependencies up.
#   1 -- any probe failed (prints the failing call on stderr).
#   2 -- env prerequisites missing (curl / jq).
#
# Flags:
#   --no-genie  -- skip the /api/genie/ask probe (useful for cold-Genie
#                  laptops where the space takes 30s to warm).
#   --boot-timeout <s> -- override the boot wait (default 20s).
#
# Env:
#   MIP_APP_URL      Override target URL. Default: http://127.0.0.1:8000.
#   MIP_FRONTEND_URL Frontend URL. Default: http://127.0.0.1:5173.
# ---------------------------------------------------------------------------
set -euo pipefail

APP_URL="${MIP_APP_URL:-http://127.0.0.1:8000}"
FRONTEND_URL="${MIP_FRONTEND_URL:-http://127.0.0.1:5173}"
BOOT_TIMEOUT=20
SKIP_GENIE=0
BOOT_LOCAL=0
BACKEND_PID=""
FRONTEND_PID=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-genie) SKIP_GENIE=1; shift ;;
    --boot-timeout) BOOT_TIMEOUT="$2"; shift 2 ;;
    -h|--help)
      sed -n '3,30p' "$0"; exit 0 ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
  esac
done

# --- Prereqs --------------------------------------------------------------
command -v curl >/dev/null || { echo "curl required" >&2; exit 2; }
command -v jq   >/dev/null || { echo "jq required"   >&2; exit 2; }

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# --- Teardown -------------------------------------------------------------
cleanup() {
  local rc=$?
  if [[ -n "$BACKEND_PID" ]] && kill -0 "$BACKEND_PID" 2>/dev/null; then
    kill "$BACKEND_PID" 2>/dev/null || true
  fi
  if [[ -n "$FRONTEND_PID" ]] && kill -0 "$FRONTEND_PID" 2>/dev/null; then
    kill "$FRONTEND_PID" 2>/dev/null || true
  fi
  exit $rc
}
trap cleanup EXIT INT TERM

# --- Boot local servers if targeting 127.0.0.1 ---------------------------
if [[ "$APP_URL" == http://127.0.0.1:* ]] || [[ "$APP_URL" == http://localhost:* ]]; then
  BOOT_LOCAL=1
  echo "[smoke] booting local uvicorn + vite..."
  if [[ -d "$REPO_ROOT/.venv" ]]; then
    PYBIN="$REPO_ROOT/.venv/bin/python"
  else
    PYBIN="$(command -v python3)"
  fi
  "$PYBIN" -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 \
    > /tmp/mip-smoke-backend.log 2>&1 &
  BACKEND_PID=$!
  npm --prefix "$REPO_ROOT/frontend" run dev > /tmp/mip-smoke-frontend.log 2>&1 &
  FRONTEND_PID=$!

  # Poll /api/health until green or timeout.
  waited=0
  until curl -sf "$APP_URL/api/health" > /dev/null 2>&1; do
    sleep 1
    waited=$((waited + 1))
    if (( waited >= BOOT_TIMEOUT )); then
      echo "[smoke] backend never came up within ${BOOT_TIMEOUT}s" >&2
      echo "--- backend log (tail) ---" >&2
      tail -n 40 /tmp/mip-smoke-backend.log >&2 || true
      exit 1
    fi
  done
  echo "[smoke] local servers up after ${waited}s"
fi

# --- Health --------------------------------------------------------------
echo "[smoke] GET /api/health"
HEALTH="$(curl -sf "$APP_URL/api/health")" || {
  echo "[smoke] /api/health failed" >&2; exit 1;
}

STATUS=$(echo "$HEALTH" | jq -r '.status')
if [[ "$STATUS" != "ok" ]]; then
  echo "[smoke] /api/health returned status=$STATUS (expected ok):" >&2
  echo "$HEALTH" | jq . >&2
  exit 1
fi

for dep in warehouse lakebase genie; do
  state=$(echo "$HEALTH" | jq -r ".dependencies.$dep")
  if [[ "$state" != "up" ]]; then
    echo "[smoke] dependency $dep=$state (expected up)" >&2
    exit 1
  fi
done
echo "[smoke] health ok · warehouse/lakebase/genie all up"

# --- Five canonical API calls -------------------------------------------
probe() {
  local label="$1"; local path="$2"; local method="${3:-GET}"; local body="${4:-}"
  local code
  if [[ "$method" == "POST" ]]; then
    code=$(curl -s -o /tmp/mip-smoke-out.json -w '%{http_code}' \
      -X POST -H 'content-type: application/json' --data "$body" "$APP_URL$path")
  else
    code=$(curl -s -o /tmp/mip-smoke-out.json -w '%{http_code}' "$APP_URL$path")
  fi
  if [[ "$code" != "200" ]]; then
    echo "[smoke] $label ($path) returned $code" >&2
    cat /tmp/mip-smoke-out.json >&2 || true
    exit 1
  fi
  echo "[smoke] ok · $label"
}

probe "portfolio preview" "/api/portfolio/preview"
probe "ranked leads"      "/api/leads?limit=5"
probe "borrower dossier"  "/api/borrowers/B-48291"
probe "evidence timeline" "/api/borrowers/B-48291/evidence"

if [[ "$SKIP_GENIE" == "0" ]]; then
  probe "genie ask" "/api/genie/ask" POST \
    '{"question":"How many borrowers across the 6-state footprint are currently in-the-money?"}'
fi

echo "[smoke] PASS · $APP_URL"
exit 0

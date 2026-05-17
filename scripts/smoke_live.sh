#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# scripts/smoke_live.sh -- operator "is real UC actually reachable?" smoke
#
# Slice 9 of the real-data migration closes the self-contained loop: after
# `./scripts/deploy.sh -t dev` (or a resource deploy plus app promotion and
# refresh jobs), the operator should be able to run this script and see 200s
# from every canonical endpoint in under ~30s.
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
#   --no-genie  -- skip the /api/genie/message probe (useful for cold-Genie
#                  laptops where the space takes 30s to warm).
#   --boot-timeout <s> -- override the boot wait (default 20s).
#
# Env:
#   MIP_APP_URL      Override target URL. Default: http://127.0.0.1:8000.
#   MIP_FRONTEND_URL Frontend URL. Default: http://127.0.0.1:5173.
#   MIP_BEARER_TOKEN Optional Databricks Apps bearer token for deployed URLs.
# ---------------------------------------------------------------------------
set -euo pipefail

APP_URL="${MIP_APP_URL:-http://127.0.0.1:8000}"
FRONTEND_URL="${MIP_FRONTEND_URL:-http://127.0.0.1:5173}"
AUTH_TOKEN="${MIP_BEARER_TOKEN:-${DATABRICKS_TOKEN:-}}"
BOOT_TIMEOUT=20
SKIP_GENIE=0
BOOT_LOCAL=0
BACKEND_PID=""
FRONTEND_PID=""
CURL_AUTH_ARGS=()
if [[ -n "$AUTH_TOKEN" ]]; then
  CURL_AUTH_ARGS=(-H "Authorization: Bearer $AUTH_TOKEN")
fi

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

new_request_id() {
  if command -v uuidgen >/dev/null; then
    uuidgen | tr '[:upper:]' '[:lower:]'
  else
    python3 - <<'PY'
import uuid
print(uuid.uuid4())
PY
  fi
}

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
  until curl -sf "${CURL_AUTH_ARGS[@]}" "$APP_URL/api/health" > /dev/null 2>&1; do
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
HEALTH="$(curl -sf "${CURL_AUTH_ARGS[@]}" "$APP_URL/api/health")" || {
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
  breaker=$(echo "$HEALTH" | jq -r ".circuit_breakers.$dep // empty")
  if [[ ! "$breaker" =~ ^(closed|open|half_open)$ ]]; then
    echo "[smoke] circuit_breakers.$dep=$breaker (expected closed|open|half_open)" >&2
    echo "$HEALTH" | jq . >&2
    exit 1
  fi
done
echo "[smoke] health ok · warehouse/lakebase/genie all up · breaker states present"

# --- Five canonical API calls -------------------------------------------
probe() {
  local label="$1"; local path="$2"; local method="${3:-GET}"; local body="${4:-}"
  local code
  if [[ "$method" == "POST" ]]; then
    code=$(curl -s -o /tmp/mip-smoke-out.json -w '%{http_code}' \
      "${CURL_AUTH_ARGS[@]}" \
      -X POST -H 'content-type: application/json' --data "$body" "$APP_URL$path")
  else
    code=$(curl -s -o /tmp/mip-smoke-out.json -w '%{http_code}' \
      "${CURL_AUTH_ARGS[@]}" "$APP_URL$path")
  fi
  if [[ "$code" != "200" ]]; then
    echo "[smoke] $label ($path) returned $code" >&2
    cat /tmp/mip-smoke-out.json >&2 || true
    exit 1
  fi
  echo "[smoke] ok · $label"
}

probe "portfolio preview" "/api/portfolio/preview" POST '{}'
probe "ranked leads"      "/api/leads?limit=5"
if ! jq -e 'all(.[]; (.clip // "" | test("^(clip_ref_|clip_demo_|$)")))' /tmp/mip-smoke-out.json >/dev/null; then
  echo "[smoke] ranked leads exposed an unmasked property ref" >&2
  cat /tmp/mip-smoke-out.json >&2 || true
  exit 1
fi
BORROWER_ID="$(jq -r '.[0].borrower_id // empty' /tmp/mip-smoke-out.json)"
if [[ -z "$BORROWER_ID" ]]; then
  echo "[smoke] ranked leads returned no borrower_id" >&2
  cat /tmp/mip-smoke-out.json >&2 || true
  exit 1
fi
probe "borrower dossier"  "/api/borrowers/$BORROWER_ID"
if ! jq -e '(.clip_id // "" | test("^(clip_ref_|clip_demo_)")) and (.owner_link_id // "" | test("^(owner_link_ref_|ol_demo_|$)"))' /tmp/mip-smoke-out.json >/dev/null; then
  echo "[smoke] borrower dossier exposed an unmasked Cotality identifier" >&2
  cat /tmp/mip-smoke-out.json >&2 || true
  exit 1
fi
probe "evidence timeline" "/api/borrowers/$BORROWER_ID/evidence"
if ! jq -e 'length > 0 and all(.[]; (.source_table // "" | test("^mip\\.")) and (.source_product // "" | length > 0) and (.signal_type // "" | length > 0))' /tmp/mip-smoke-out.json >/dev/null; then
  echo "[smoke] evidence timeline did not return source-backed evidence rows" >&2
  cat /tmp/mip-smoke-out.json >&2 || true
  exit 1
fi

probe "data estate proof" "/api/data-estate"
if ! jq -e '.public_demo_masking == true and (.proof_assets | length > 0) and any(.lanes[]?.assets[]?; .synthetic_demo == true)' /tmp/mip-smoke-out.json >/dev/null; then
  echo "[smoke] data estate proof is missing masking/proof/synthetic-disclosure contract" >&2
  cat /tmp/mip-smoke-out.json >&2 || true
  exit 1
fi

probe "source readiness" "/api/admin/sources"
if ! jq -e '
  . as $rows
  |
  length > 0
  and (["Cotality Public Records","Voluntary Lien","MMA Mortgage Analytics","CLIP","Owner Link","AVM","FRED Market Rates","UC Gold Borrower 360","UC Gold Lead Scores","UC Gold Lead Population","UC Gold Segment Population","UC Gold Borrower Dossier"] as $core
    | all($core[]; . as $name
      | any($rows[]; .name == $name and .status == "live" and (.rows // 0) > 0 and (.last_updated // "") != "" and (.checked_at // "") != "")))
  and (["First-party LOS / Applications","First-party Servicing Portfolio","First-party CRM / Campaigns","First-party Customer Interactions","First-party Product Balances"] as $firstparty
    | all($firstparty[]; . as $name
      | any($rows[]; .name == $name and (.status == "live" or .status == "demo_synthetic") and (.rows // 0) > 0 and (.last_updated // "") != "" and (.checked_at // "") != "")))
  and all($rows[]; if (.name == "MLS" or .name == "Building Permits") then .status != "live" else true end)
  and all($rows[]; if .synthetic_demo == true then .status == "demo_synthetic" else true end)
' /tmp/mip-smoke-out.json >/dev/null; then
  echo "[smoke] source readiness failed core-live/synthetic-disclosure checks" >&2
  cat /tmp/mip-smoke-out.json >&2 || true
  exit 1
fi

probe "geo state rollups" "/api/geo/state-rollups?segment_codes=itm,equity&segment_mode=all"
if ! jq -e '.rollups | length > 0' /tmp/mip-smoke-out.json >/dev/null; then
  echo "[smoke] geo state rollups returned no filtered rows" >&2
  cat /tmp/mip-smoke-out.json >&2 || true
  exit 1
fi
SMOKE_STATE="$(jq -r '.rollups | map(select((.addressable // 0) > 0)) | .[0].state // empty' /tmp/mip-smoke-out.json)"
if [[ -z "$SMOKE_STATE" ]]; then
  echo "[smoke] geo state rollups did not include a populated state" >&2
  cat /tmp/mip-smoke-out.json >&2 || true
  exit 1
fi
probe "geo county rollups" "/api/geo/county-rollups?state=$SMOKE_STATE&segment_codes=itm,equity&segment_mode=all"
if ! jq -e '.rollups | length > 0' /tmp/mip-smoke-out.json >/dev/null; then
  echo "[smoke] geo county rollups returned no filtered rows for state=$SMOKE_STATE" >&2
  cat /tmp/mip-smoke-out.json >&2 || true
  exit 1
fi
SMOKE_FIPS="$(jq -r '.rollups | map(select((.addressable_borrowers // 0) > 0)) | .[0].fips_5 // empty' /tmp/mip-smoke-out.json)"
if [[ -z "$SMOKE_FIPS" ]]; then
  echo "[smoke] geo county rollups did not include fips_5" >&2
  cat /tmp/mip-smoke-out.json >&2 || true
  exit 1
fi
probe "geo zip rollups" "/api/geo/zip-rollups?county_fips=$SMOKE_FIPS&segment_codes=itm,equity&segment_mode=all"
if ! jq -e '.rollups | length > 0' /tmp/mip-smoke-out.json >/dev/null; then
  echo "[smoke] geo zip rollups returned no filtered rows for county_fips=$SMOKE_FIPS" >&2
  cat /tmp/mip-smoke-out.json >&2 || true
  exit 1
fi

SMOKE_REQUEST_ID="$(new_request_id)"
probe "outreach draft for approval" "/api/outreach/draft" POST \
  "{\"borrower_id\":\"$BORROWER_ID\",\"channel\":\"email\"}"
SMOKE_DRAFT_BODY="$(jq -r '.body // empty' /tmp/mip-smoke-out.json)"
SMOKE_OFFER_CODE="$(jq -r '.offer_code // empty' /tmp/mip-smoke-out.json)"
if [[ -z "$SMOKE_DRAFT_BODY" || -z "$SMOKE_OFFER_CODE" ]]; then
  echo "[smoke] outreach draft did not return body and offer_code for approval gate" >&2
  cat /tmp/mip-smoke-out.json >&2 || true
  exit 1
fi
SMOKE_APPROVE_PAYLOAD="$(jq -n \
  --arg borrower_id "$BORROWER_ID" \
  --arg offer_code "$SMOKE_OFFER_CODE" \
  --arg draft_body "$SMOKE_DRAFT_BODY" \
  --arg request_id "$SMOKE_REQUEST_ID" \
  '{borrower_id:$borrower_id, offer_code:$offer_code, evidence_ids:[], channel:"email", draft_body:$draft_body, request_id:$request_id}')"
probe "outreach approval audit write" "/api/outreach/approve" POST \
  "$SMOKE_APPROVE_PAYLOAD"
if ! jq -e '.approved == true and (.audit_event_id // "" | length > 0)' /tmp/mip-smoke-out.json >/dev/null; then
  echo "[smoke] outreach approval did not return an audit event id" >&2
  cat /tmp/mip-smoke-out.json >&2 || true
  exit 1
fi

if [[ "$SKIP_GENIE" == "0" ]]; then
  probe "genie message" "/api/genie/message" POST \
    '{"question":"How many borrowers across current refreshed coverage are currently in-the-money?"}'
  if ! jq -e '(.source == "genie" or .source == "trusted_sql") and (.proof.trusted == true) and ((.proof.source_assets // []) | length > 0) and ((.sql_query // "") | length > 0)' /tmp/mip-smoke-out.json >/dev/null; then
    echo "[smoke] Genie endpoint did not return a trusted governed answer with SQL/source proof" >&2
    cat /tmp/mip-smoke-out.json >&2 || true
    exit 1
  fi
fi

echo "[smoke] PASS · $APP_URL"
exit 0

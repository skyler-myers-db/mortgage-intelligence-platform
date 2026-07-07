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
#      unset, then waits for /api/v1/health to return ok. If MIP_APP_URL is
#      set, targets that URL directly (no local boot).
#   2. Asserts /api/v1/health is `status:"ok"` with every dependency `up`.
#   3. Plays through the 5 canonical API calls in user-flow order:
#        portfolio preview -> leads -> borrower dossier -> evidence -> genie
#   4. Tears down the local servers cleanly on exit (trap on SIGINT/SIGTERM
#      too).
#
# Exit codes:
#   0 -- every call returned 200 AND /api/v1/health shows all dependencies up.
#   1 -- any probe failed (prints the failing call on stderr).
#   2 -- env prerequisites missing (curl / jq).
#
# Flags:
#   --no-genie  -- skip the /api/v1/genie/message probe (useful for cold-Genie
#                  laptops where the space takes 30s to warm).
#   --no-capabilities -- skip the admin live-capability probe.
#   --boot-timeout <s> -- override the boot wait (default 20s).
#
# Env:
#   MIP_APP_URL      Override target URL. Default: http://127.0.0.1:8000.
#   MIP_API_PREFIX   API prefix. Default: /api/v1.
#   MIP_FRONTEND_URL Frontend URL. Default: http://127.0.0.1:5173.
#   MIP_BEARER_TOKEN Optional Databricks Apps OAuth bearer for deployed URLs.
#   MIP_ADMIN_BEARER_TOKEN Optional app-admin OAuth bearer for admin-only probes.
#   MIP_EXPECT_AGENTIC_CAPABILITIES When 1, require the deployed agentic GA
#      capability rows (Genie API, Agent Eval, Agent Orchestrator, Lakebase
#      Sync) to be claimable. AI Gateway is claimable only with a fresh,
#      ledger-verified exact inference-row proof after a live endpoint probe.
#   MIP_REQUIRE_AI_GATEWAY_CLAIMABLE When 1, fail unless AI Gateway is available
#      with ledger-verified exact inference-row proof.
# ---------------------------------------------------------------------------
set -euo pipefail

APP_URL="${MIP_APP_URL:-http://127.0.0.1:8000}"
API_PREFIX="${MIP_API_PREFIX:-/api/v1}"
API_PREFIX="/${API_PREFIX#/}"
API_PREFIX="${API_PREFIX%/}"
FRONTEND_URL="${MIP_FRONTEND_URL:-http://127.0.0.1:5173}"
AUTH_TOKEN="${MIP_BEARER_TOKEN:-}"
ADMIN_AUTH_TOKEN="${MIP_ADMIN_BEARER_TOKEN:-}"
BOOT_TIMEOUT=20
REMOTE_BOOT_TIMEOUT="${MIP_REMOTE_BOOT_TIMEOUT:-240}"
SKIP_GENIE=0
SKIP_CAPABILITIES=0
EXPECT_AGENTIC_CAPABILITIES="${MIP_EXPECT_AGENTIC_CAPABILITIES:-0}"
REQUIRE_AI_GATEWAY_CLAIMABLE="${MIP_REQUIRE_AI_GATEWAY_CLAIMABLE:-0}"
BOOT_LOCAL=0
BACKEND_PID=""
FRONTEND_PID=""
CURL_AUTH_ARGS=()
if [[ -n "$AUTH_TOKEN" ]]; then
  CURL_AUTH_ARGS=(-H "Authorization: Bearer $AUTH_TOKEN")
fi
CURL_ADMIN_AUTH_ARGS=()
if [[ -n "$ADMIN_AUTH_TOKEN" ]]; then
  CURL_ADMIN_AUTH_ARGS=(-H "Authorization: Bearer $ADMIN_AUTH_TOKEN")
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-genie) SKIP_GENIE=1; shift ;;
    --no-capabilities) SKIP_CAPABILITIES=1; shift ;;
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

health_ready() {
  jq -e '
    .status == "ok"
    and .dependencies.warehouse == "up"
    and .dependencies.lakebase == "up"
    and .dependencies.genie == "up"
  ' "$1" >/dev/null 2>&1
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
  exit "$rc"
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

  # Poll health until green or timeout.
  waited=0
  until curl -sf "${CURL_AUTH_ARGS[@]}" "$APP_URL$API_PREFIX/health" > /dev/null 2>&1; do
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
echo "[smoke] GET $API_PREFIX/health"
if [[ "$BOOT_LOCAL" == "0" ]]; then
  waited=0
  last_code="000"
  until last_code="$(curl -sS "${CURL_AUTH_ARGS[@]}" -o /tmp/mip-smoke-health.json -w '%{http_code}' "$APP_URL$API_PREFIX/health" 2>/tmp/mip-smoke-health.err)" \
    && [[ "$last_code" == "200" ]] \
    && health_ready /tmp/mip-smoke-health.json; do
    sleep 5
    waited=$((waited + 5))
    if (( waited >= REMOTE_BOOT_TIMEOUT )); then
      echo "[smoke] deployed app health was not ready within ${REMOTE_BOOT_TIMEOUT}s" >&2
      echo "[smoke] last health HTTP status: ${last_code:-unknown}" >&2
      if [[ -s /tmp/mip-smoke-health.json ]]; then
        cat /tmp/mip-smoke-health.json >&2 || true
      fi
      if [[ -s /tmp/mip-smoke-health.err ]]; then
        cat /tmp/mip-smoke-health.err >&2 || true
      fi
      exit 1
    fi
  done
  echo "[smoke] deployed app health ready after ${waited}s"
  HEALTH="$(cat /tmp/mip-smoke-health.json)"
else
  HEALTH="$(curl -sf "${CURL_AUTH_ARGS[@]}" "$APP_URL$API_PREFIX/health")" || {
    echo "[smoke] $API_PREFIX/health failed" >&2; exit 1;
  }
fi

STATUS=$(echo "$HEALTH" | jq -r '.status')
if [[ "$STATUS" != "ok" ]]; then
  echo "[smoke] $API_PREFIX/health returned status=$STATUS (expected ok):" >&2
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

probe_admin_or_forbidden() {
  local label="$1"; local path="$2"
  local code attempt

  # Cold-start grace (2026-07-07): the ?live=1 capability sweep runs real
  # probes (Genie turn, serving-endpoint query) and, seconds after an app
  # restart, can cross the Databricks Apps proxy 60s ceiling — observed as
  # a 504 in deploy step 22 that a warmed rerun passed cleanly. Only
  # infrastructure-timeout codes (503/504) are retried; every content
  # assertion stays strict.
  if [[ -z "$ADMIN_AUTH_TOKEN" ]]; then
    for attempt in 1 2 3; do
      code=$(curl -s -o /tmp/mip-smoke-out.json -w '%{http_code}' \
        "${CURL_AUTH_ARGS[@]}" "$APP_URL$path")
      if [[ "$code" != "503" && "$code" != "504" ]]; then
        break
      fi
      if [[ "$attempt" -lt 3 ]]; then
        echo "[smoke] $label returned $code (likely cold start) — retrying in 20s"
        sleep 20
      fi
    done
    # Posture-aware (2026-06-11): the default bearer's identity may or may
    # not be in the deployed MIP_ADMIN_EMAILS allowlist — both are valid
    # operator decisions. 403 proves the deny path; 200 means the deployer
    # is a configured admin, so fall through and let the caller run the
    # full governed-payload contract checks (stronger than skipping). Any
    # other status is a real failure. The deny path for non-admin
    # identities stays covered by the rbac unit suite.
    if [[ "$code" == "403" ]]; then
      echo "[smoke] ok · $label admin gate rejects non-admin bearer"
      return 10
    fi
    if [[ "$code" == "200" ]]; then
      echo "[smoke] ok · $label admin gate admits configured admin bearer"
      return 0
    fi
    echo "[smoke] $label admin gate returned $code (expected 403 for non-admin or 200 for configured admin)" >&2
    cat /tmp/mip-smoke-out.json >&2 || true
    exit 1
  fi

  for attempt in 1 2 3; do
    code=$(curl -s -o /tmp/mip-smoke-out.json -w '%{http_code}' \
      "${CURL_ADMIN_AUTH_ARGS[@]}" "$APP_URL$path")
    if [[ "$code" != "503" && "$code" != "504" ]]; then
      break
    fi
    if [[ "$attempt" -lt 3 ]]; then
      echo "[smoke] $label returned $code (likely cold start) — retrying in 20s"
      sleep 20
    fi
  done
  if [[ "$code" != "200" ]]; then
    echo "[smoke] $label ($path) returned $code with admin bearer" >&2
    cat /tmp/mip-smoke-out.json >&2 || true
    exit 1
  fi
  echo "[smoke] ok · $label"
}

probe "portfolio preview" "$API_PREFIX/portfolio/preview" POST '{}'
probe "ranked leads"      "$API_PREFIX/leads?limit=5"
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
probe "borrower dossier"  "$API_PREFIX/borrowers/$BORROWER_ID"
if ! jq -e '(.clip_id // "" | test("^(clip_ref_|clip_demo_)")) and (.owner_link_id // "" | test("^(owner_link_ref_|ol_demo_|$)"))' /tmp/mip-smoke-out.json >/dev/null; then
  echo "[smoke] borrower dossier exposed an unmasked Cotality identifier" >&2
  cat /tmp/mip-smoke-out.json >&2 || true
  exit 1
fi
probe "evidence timeline" "$API_PREFIX/borrowers/$BORROWER_ID/evidence"
if ! jq -e 'length > 0 and all(.[]; (.source_table // "" | test("^mip\\.")) and (.source_product // "" | length > 0) and (.signal_type // "" | length > 0))' /tmp/mip-smoke-out.json >/dev/null; then
  echo "[smoke] evidence timeline did not return source-backed evidence rows" >&2
  cat /tmp/mip-smoke-out.json >&2 || true
  exit 1
fi

probe "data estate proof" "$API_PREFIX/data-estate"
if ! jq -e '.public_demo_masking == true and (.proof_assets | length > 0) and any(.lanes[]?.assets[]?; .synthetic_demo == true)' /tmp/mip-smoke-out.json >/dev/null; then
  echo "[smoke] data estate proof is missing masking/proof/synthetic-disclosure contract" >&2
  cat /tmp/mip-smoke-out.json >&2 || true
  exit 1
fi

if probe_admin_or_forbidden "source readiness" "$API_PREFIX/admin/sources"; then
  if ! jq -e '
    . as $rows
    |
    length > 0
    and (["Cotality Public Records","Voluntary Lien","MMA Mortgage Analytics","CLIP","Owner Link","AVM","FRED Market Rates","MLS Listings","Cotality HELOC Propensity","Cotality Refi Propensity","UC Gold Borrower 360","UC Gold Lead Scores","UC Gold Lead Population","UC Gold Segment Population","UC Gold Borrower Dossier"] as $core
      | all($core[]; . as $name
        | any($rows[]; .name == $name and .status == "live" and (.rows // 0) > 0 and (.last_updated // "") != "" and (.checked_at // "") != "")))
    and (["First-party LOS / Applications","First-party Servicing Portfolio","First-party CRM / Campaigns","First-party Customer Interactions","First-party Product Balances"] as $firstparty
      | all($firstparty[]; . as $name
        | any($rows[]; .name == $name and (.status == "live" or .status == "demo_synthetic") and (.rows // 0) > 0 and (.last_updated // "") != "" and (.checked_at // "") != "")))
    and all($rows[]; if .name == "Building Permits" then .status != "live" else true end)
    and all($rows[]; if .synthetic_demo == true then .status == "demo_synthetic" else true end)
  ' /tmp/mip-smoke-out.json >/dev/null; then
    echo "[smoke] source readiness failed core-live/synthetic-disclosure checks" >&2
    cat /tmp/mip-smoke-out.json >&2 || true
    exit 1
  fi
fi

if [[ "$SKIP_CAPABILITIES" == "0" ]]; then
  if probe_admin_or_forbidden "live capability readiness" "$API_PREFIX/admin/capabilities?live=1"; then
    if ! jq -e '(.capabilities // .items // []) | length > 0' /tmp/mip-smoke-out.json >/dev/null; then
      echo "[smoke] capability readiness payload did not return capability rows" >&2
      cat /tmp/mip-smoke-out.json >&2 || true
      exit 1
    fi
    if [[ "$EXPECT_AGENTIC_CAPABILITIES" == "1" ]]; then
      if ! jq -e '
        (.capabilities // .items // []) as $rows
        | all(["genie_conversation_api","agent_eval","agent_orchestrator","lakebase_sync"][]; . as $key
            | any($rows[]; .key == $key and .status == "available" and .claimable == true))
      ' /tmp/mip-smoke-out.json >/dev/null; then
        echo "[smoke] agentic GA capability rows are not all live-claimable" >&2
        cat /tmp/mip-smoke-out.json >&2 || true
        exit 1
      fi
    fi
    if ! jq -e '
      (.capabilities // .items // []) as $rows
      | any($rows[]; .key == "ai_gateway")
      and all($rows[]; if .key == "ai_gateway" then
          (
            (.status == "available" and .claimable == true and ((.detail // "") | test("exact inference-row round-trip verified")))
            or
            (.status == "configured" and .claimable == false and ((.detail // "") | test("not claimable|not visible|unproven|Live probe did not pass")))
            or
            # Honest fully-unprovisioned state: the workspace rejects AI Gateway
            # config on the current endpoint type (platform eligibility change,
            # 2026-07-07), so provisioning skips and the flag stays off. More
            # conservative than "configured"; strict mode below still fails it.
            (.status == "not_provisioned" and .claimable == false and ((.detail // "") | test("Disabled|not provisioned|missing")))
          )
        else true end)
    ' /tmp/mip-smoke-out.json >/dev/null; then
      echo "[smoke] AI Gateway capability row overclaims or lacks inference-row proof detail" >&2
      cat /tmp/mip-smoke-out.json >&2 || true
      exit 1
    fi
    if [[ "$REQUIRE_AI_GATEWAY_CLAIMABLE" == "1" ]]; then
      if ! jq -e '
        (.capabilities // .items // [])
        | any(.[]; .key == "ai_gateway" and .status == "available" and .claimable == true and ((.detail // "") | test("exact inference-row round-trip verified")))
      ' /tmp/mip-smoke-out.json >/dev/null; then
        echo "[smoke] AI Gateway is configured but not claimable with inference-row proof" >&2
        cat /tmp/mip-smoke-out.json >&2 || true
        exit 1
      fi
    fi
    echo "[smoke] ok · live capability readiness"
  elif [[ "$EXPECT_AGENTIC_CAPABILITIES" == "1" || "$REQUIRE_AI_GATEWAY_CLAIMABLE" == "1" ]]; then
    echo "[smoke] live capability readiness requires an admin-readable payload when agentic capability proof is expected" >&2
    cat /tmp/mip-smoke-out.json >&2 || true
    exit 1
  fi
fi

probe "geo state rollups" "$API_PREFIX/geo/state-rollups?segment_codes=itm,equity&segment_mode=all"
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
probe "geo county rollups" "$API_PREFIX/geo/county-rollups?state=$SMOKE_STATE&segment_codes=itm,equity&segment_mode=all"
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
probe "geo zip rollups" "$API_PREFIX/geo/zip-rollups?county_fips=$SMOKE_FIPS&segment_codes=itm,equity&segment_mode=all"
if ! jq -e '.rollups | length > 0' /tmp/mip-smoke-out.json >/dev/null; then
  echo "[smoke] geo zip rollups returned no filtered rows for county_fips=$SMOKE_FIPS" >&2
  cat /tmp/mip-smoke-out.json >&2 || true
  exit 1
fi

SMOKE_REQUEST_ID="$(new_request_id)"
probe "outreach draft for approval" "$API_PREFIX/outreach/draft" POST \
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
probe "outreach approval audit write" "$API_PREFIX/outreach/approve" POST \
  "$SMOKE_APPROVE_PAYLOAD"
if ! jq -e '.approved == true and (.audit_event_id // "" | length > 0)' /tmp/mip-smoke-out.json >/dev/null; then
  echo "[smoke] outreach approval did not return an audit event id" >&2
  cat /tmp/mip-smoke-out.json >&2 || true
  exit 1
fi

if [[ "$SKIP_GENIE" == "0" ]]; then
  probe "genie message" "$API_PREFIX/genie/message" POST \
    '{"question":"How many borrowers are currently in-the-money?"}'
  if ! jq -e '(.source == "genie" or .source == "trusted_sql") and (.proof.trusted == true) and ((.proof.source_assets // []) | length > 0) and ((.sql_query // "") | length > 0)' /tmp/mip-smoke-out.json >/dev/null; then
    echo "[smoke] Genie endpoint did not return a trusted governed answer with SQL/source proof" >&2
    cat /tmp/mip-smoke-out.json >&2 || true
    exit 1
  fi
fi

echo "[smoke] PASS · $APP_URL"
exit 0

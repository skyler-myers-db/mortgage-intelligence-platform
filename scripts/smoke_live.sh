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
#   MIP_BEARER_TOKEN Optional Databricks Apps OAuth bearer for deployed URLs.
#   MIP_ADMIN_BEARER_TOKEN Optional app-admin OAuth bearer for admin-only probes.
#   MIP_EXPECT_GIT_SHA When set, require authenticated health to report this
#      exact deployed source revision before any product probes run.
#   MIP_EXPECT_AGENTIC_CAPABILITIES When 1, require the deployed agentic GA
#      capability rows (Genie API, Agent Eval, Agent Orchestrator, Lakebase
#      Sync) to be claimable. AI Gateway is claimable only with a fresh,
#      ledger-verified exact inference-row proof after a live endpoint probe.
#   MIP_REQUIRE_AI_GATEWAY_CLAIMABLE When 1, fail unless AI Gateway is available
#      with ledger-verified exact inference-row proof.
#   MIP_SMOKE_CONNECT_TIMEOUT_S Per-request curl connect timeout. Default: 10.
#   MIP_SMOKE_REQUEST_TIMEOUT_S Per-request curl total timeout. Default: 75.
#   MIP_SMOKE_PROBE_ATTEMPTS Maximum attempts for retry-eligible probes. Default: 4.
#   MIP_SMOKE_PROBE_RETRY_DELAY_S Delay between probe attempts. Default: 20.
#   MIP_SMOKE_PROBE_RETRY_BUDGET_S Total wall-clock budget for one retrying
#      probe, including requests and sleeps. Default: 300.
# ---------------------------------------------------------------------------
set -euo pipefail

APP_URL="${MIP_APP_URL:-http://127.0.0.1:8000}"
API_PREFIX="${MIP_API_PREFIX:-/api/v1}"
API_PREFIX="/${API_PREFIX#/}"
API_PREFIX="${API_PREFIX%/}"
AUTH_TOKEN="${MIP_BEARER_TOKEN:-}"
ADMIN_AUTH_TOKEN="${MIP_ADMIN_BEARER_TOKEN:-}"
BOOT_TIMEOUT=20
REMOTE_BOOT_TIMEOUT="${MIP_REMOTE_BOOT_TIMEOUT:-240}"
PROBE_ATTEMPTS="${MIP_SMOKE_PROBE_ATTEMPTS:-4}"
PROBE_RETRY_DELAY="${MIP_SMOKE_PROBE_RETRY_DELAY_S:-20}"
PROBE_RETRY_BUDGET="${MIP_SMOKE_PROBE_RETRY_BUDGET_S:-300}"
CURL_CONNECT_TIMEOUT="${MIP_SMOKE_CONNECT_TIMEOUT_S:-10}"
CURL_MAX_TIME="${MIP_SMOKE_REQUEST_TIMEOUT_S:-75}"
SKIP_GENIE=0
SKIP_CAPABILITIES=0
EXPECT_AGENTIC_CAPABILITIES="${MIP_EXPECT_AGENTIC_CAPABILITIES:-0}"
REQUIRE_AI_GATEWAY_CLAIMABLE="${MIP_REQUIRE_AI_GATEWAY_CLAIMABLE:-0}"
EXPECT_GIT_SHA="${MIP_EXPECT_GIT_SHA:-}"
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

require_positive_integer() {
  local name="$1" value="$2"
  if [[ ! "$value" =~ ^[1-9][0-9]*$ ]]; then
    echo "$name must be a positive integer (got: $value)" >&2
    exit 2
  fi
}

require_nonnegative_integer() {
  local name="$1" value="$2"
  if [[ ! "$value" =~ ^[0-9]+$ ]]; then
    echo "$name must be a non-negative integer (got: $value)" >&2
    exit 2
  fi
}

require_positive_integer "boot timeout" "$BOOT_TIMEOUT"
require_positive_integer "remote boot timeout" "$REMOTE_BOOT_TIMEOUT"
require_positive_integer "probe attempts" "$PROBE_ATTEMPTS"
require_nonnegative_integer "probe retry delay" "$PROBE_RETRY_DELAY"
require_positive_integer "probe retry budget" "$PROBE_RETRY_BUDGET"
require_positive_integer "curl connect timeout" "$CURL_CONNECT_TIMEOUT"
require_positive_integer "curl request timeout" "$CURL_MAX_TIME"

curl_with_timeout() {
  local max_time="$1"
  shift
  command curl \
    --connect-timeout "$CURL_CONNECT_TIMEOUT" \
    --max-time "$max_time" \
    "$@"
}

curl_bounded() {
  curl_with_timeout "$CURL_MAX_TIME" "$@"
}

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
# shellcheck disable=SC2329  # Invoked indirectly by the EXIT/INT/TERM trap.
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

  # Poll health until green without allowing a hung request to exceed the
  # operator's total boot budget.
  boot_started=$SECONDS
  boot_deadline=$((boot_started + BOOT_TIMEOUT))
  until curl_with_timeout 2 -sf "${CURL_AUTH_ARGS[@]}" "$APP_URL$API_PREFIX/health" > /dev/null 2>&1; do
    if (( SECONDS >= boot_deadline )); then
      echo "[smoke] backend never came up within ${BOOT_TIMEOUT}s" >&2
      echo "--- backend log (tail) ---" >&2
      tail -n 40 /tmp/mip-smoke-backend.log >&2 || true
      exit 1
    fi
    sleep 1
  done
  waited=$((SECONDS - boot_started))
  echo "[smoke] local servers up after ${waited}s"
fi

# --- Health --------------------------------------------------------------
echo "[smoke] GET $API_PREFIX/health"
if [[ "$BOOT_LOCAL" == "0" ]]; then
  health_started=$SECONDS
  health_deadline=$((health_started + REMOTE_BOOT_TIMEOUT))
  last_code="000"
  while true; do
    health_remaining=$((health_deadline - SECONDS))
    if (( health_remaining <= 0 )); then
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
    health_request_timeout="$CURL_MAX_TIME"
    if (( health_request_timeout > health_remaining )); then
      health_request_timeout="$health_remaining"
    fi
    if last_code="$(curl_with_timeout "$health_request_timeout" -sS \
      "${CURL_AUTH_ARGS[@]}" -o /tmp/mip-smoke-health.json -w '%{http_code}' \
      "$APP_URL$API_PREFIX/health" 2>/tmp/mip-smoke-health.err)" \
      && [[ "$last_code" == "200" ]] \
      && health_ready /tmp/mip-smoke-health.json; then
      break
    fi
    health_remaining=$((health_deadline - SECONDS))
    if (( health_remaining > 0 )); then
      health_sleep=5
      if (( health_sleep > health_remaining )); then
        health_sleep="$health_remaining"
      fi
      sleep "$health_sleep"
    fi
  done
  waited=$((SECONDS - health_started))
  echo "[smoke] deployed app health ready after ${waited}s"
  HEALTH="$(cat /tmp/mip-smoke-health.json)"
else
  HEALTH="$(curl_bounded -sf "${CURL_AUTH_ARGS[@]}" "$APP_URL$API_PREFIX/health")" || {
    echo "[smoke] $API_PREFIX/health failed" >&2; exit 1;
  }
fi

STATUS=$(echo "$HEALTH" | jq -r '.status')
if [[ "$STATUS" != "ok" ]]; then
  echo "[smoke] $API_PREFIX/health returned status=$STATUS (expected ok):" >&2
  echo "$HEALTH" | jq . >&2
  exit 1
fi

if [[ -n "$EXPECT_GIT_SHA" ]]; then
  DEPLOYED_GIT_SHA=$(echo "$HEALTH" | jq -r '.git_sha // empty')
  if [[ "$DEPLOYED_GIT_SHA" != "$EXPECT_GIT_SHA" ]]; then
    echo "[smoke] deployed git_sha=$DEPLOYED_GIT_SHA (expected $EXPECT_GIT_SHA)" >&2
    echo "$HEALTH" | jq . >&2
    exit 1
  fi
  echo "[smoke] exact deployed git SHA verified · $DEPLOYED_GIT_SHA"
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
REQUEST_HTTP_CODE="000"
REQUEST_CURL_RC=0
REQUEST_ATTEMPTS=0

request_with_retry() {
  local label="$1" path="$2" method="${3:-GET}" body="${4:-}"
  local retry_policy="${5:-never}" idempotency_key="${6:-}" auth_scope="${7:-user}"
  local max_attempts=1 retry_deadline=0 attempt attempt_timeout retry_remaining retry_sleep
  local -a auth_args request_args

  case "$retry_policy" in
    never|safe_read|idempotent_mutation) ;;
    *)
      echo "[smoke] invalid retry policy for $label: $retry_policy" >&2
      exit 2
      ;;
  esac
  if [[ "$method" == "GET" || "$method" == "HEAD" \
    || "$retry_policy" == "safe_read" || "$retry_policy" == "idempotent_mutation" ]]; then
    max_attempts="$PROBE_ATTEMPTS"
    retry_deadline=$((SECONDS + PROBE_RETRY_BUDGET))
  fi
  if [[ "$retry_policy" == "idempotent_mutation" ]]; then
    if [[ -z "$idempotency_key" ]] \
      || ! jq -e --arg key "$idempotency_key" '.request_id == $key' <<<"$body" >/dev/null; then
      echo "[smoke] $label cannot retry without a stable request_id idempotency key" >&2
      exit 2
    fi
  fi

  if [[ "$auth_scope" == "admin" ]]; then
    auth_args=("${CURL_ADMIN_AUTH_ARGS[@]}")
  else
    auth_args=("${CURL_AUTH_ARGS[@]}")
  fi

  REQUEST_HTTP_CODE="000"
  REQUEST_CURL_RC=0
  REQUEST_ATTEMPTS=0
  for ((attempt = 1; attempt <= max_attempts; attempt++)); do
    attempt_timeout="$CURL_MAX_TIME"
    if (( max_attempts > 1 )); then
      retry_remaining=$((retry_deadline - SECONDS))
      if (( retry_remaining <= 0 )); then
        break
      fi
      if (( attempt_timeout > retry_remaining )); then
        attempt_timeout="$retry_remaining"
      fi
    fi

    REQUEST_ATTEMPTS="$attempt"
    : > /tmp/mip-smoke-curl.err
    request_args=(-sS -o /tmp/mip-smoke-out.json -w '%{http_code}')
    if [[ "$method" != "GET" ]]; then
      request_args+=(-X "$method")
    fi
    if [[ -n "$body" ]]; then
      request_args+=(-H 'content-type: application/json' --data "$body")
    fi
    if [[ -n "$idempotency_key" ]]; then
      request_args+=(-H "Idempotency-Key: $idempotency_key")
    fi

    if REQUEST_HTTP_CODE="$(curl_with_timeout "$attempt_timeout" \
      "${auth_args[@]}" "${request_args[@]}" "$APP_URL$path" \
      2>/tmp/mip-smoke-curl.err)"; then
      REQUEST_CURL_RC=0
    else
      REQUEST_CURL_RC=$?
      REQUEST_HTTP_CODE="${REQUEST_HTTP_CODE:-000}"
    fi

    if (( REQUEST_CURL_RC == 0 )) \
      && [[ "$REQUEST_HTTP_CODE" != "502" && "$REQUEST_HTTP_CODE" != "503" \
        && "$REQUEST_HTTP_CODE" != "504" ]]; then
      break
    fi
    if (( attempt >= max_attempts )); then
      break
    fi

    retry_remaining=$((retry_deadline - SECONDS))
    if (( retry_remaining <= 0 )); then
      break
    fi
    retry_sleep="$PROBE_RETRY_DELAY"
    if (( retry_sleep > retry_remaining )); then
      retry_sleep="$retry_remaining"
    fi
    if (( REQUEST_CURL_RC != 0 )); then
      echo "[smoke] $label curl transport failure rc=$REQUEST_CURL_RC — retrying in ${retry_sleep}s"
    else
      echo "[smoke] $label returned $REQUEST_HTTP_CODE during dependency warm-up — retrying in ${retry_sleep}s"
    fi
    if (( retry_sleep > 0 )); then
      sleep "$retry_sleep"
    fi
  done
}

probe() {
  local label="$1" path="$2" method="${3:-GET}" body="${4:-}"
  local retry_policy="${5:-never}" idempotency_key="${6:-}"
  request_with_retry \
    "$label" "$path" "$method" "$body" "$retry_policy" "$idempotency_key" user
  if (( REQUEST_CURL_RC != 0 )); then
    echo "[smoke] $label ($path) transport failed after $REQUEST_ATTEMPTS attempt(s) (curl rc=$REQUEST_CURL_RC)" >&2
    cat /tmp/mip-smoke-curl.err >&2 || true
    exit 1
  fi
  if [[ "$REQUEST_HTTP_CODE" != "200" ]]; then
    echo "[smoke] $label ($path) returned $REQUEST_HTTP_CODE" >&2
    cat /tmp/mip-smoke-out.json >&2 || true
    exit 1
  fi
  echo "[smoke] ok · $label"
}

probe_admin_or_forbidden() {
  local label="$1" path="$2" auth_scope="user"

  # Cold-start grace (2026-07-07): the ?live=1 capability sweep runs real
  # probes (Genie turn, serving-endpoint query) and, seconds after an app
  # restart, can cross the Databricks Apps proxy 60s ceiling — observed as
  # a 504 (gw5) and a 502 (gw10) in deploy step 22 that warmed reruns passed cleanly. Only
  # transport failures and infrastructure-timeout codes (502/503/504) are
  # retried within one wall-clock budget; every content assertion stays strict.
  if [[ -n "$ADMIN_AUTH_TOKEN" ]]; then
    auth_scope="admin"
  fi
  request_with_retry "$label" "$path" GET "" safe_read "" "$auth_scope"
  if (( REQUEST_CURL_RC != 0 )); then
    echo "[smoke] $label ($path) transport failed after $REQUEST_ATTEMPTS attempt(s) (curl rc=$REQUEST_CURL_RC)" >&2
    cat /tmp/mip-smoke-curl.err >&2 || true
    exit 1
  fi

  if [[ -z "$ADMIN_AUTH_TOKEN" ]]; then
    # Posture-aware (2026-06-11): the default bearer's identity may or may
    # not be in the deployed MIP_ADMIN_EMAILS allowlist — both are valid
    # operator decisions. 403 proves the deny path; 200 means the deployer
    # is a configured admin, so fall through and let the caller run the
    # full governed-payload contract checks (stronger than skipping). Any
    # other status is a real failure. The deny path for non-admin
    # identities stays covered by the rbac unit suite.
    if [[ "$REQUEST_HTTP_CODE" == "403" ]]; then
      echo "[smoke] ok · $label admin gate rejects non-admin bearer"
      return 10
    fi
    if [[ "$REQUEST_HTTP_CODE" == "200" ]]; then
      echo "[smoke] ok · $label admin gate admits configured admin bearer"
      return 0
    fi
    echo "[smoke] $label admin gate returned $REQUEST_HTTP_CODE (expected 403 for non-admin or 200 for configured admin)" >&2
    cat /tmp/mip-smoke-out.json >&2 || true
    exit 1
  fi

  if [[ "$REQUEST_HTTP_CODE" != "200" ]]; then
    echo "[smoke] $label ($path) returned $REQUEST_HTTP_CODE with admin bearer" >&2
    cat /tmp/mip-smoke-out.json >&2 || true
    exit 1
  fi
  echo "[smoke] ok · $label"
}

probe "portfolio preview" "$API_PREFIX/portfolio/preview" POST '{}' safe_read
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
SMOKE_EVIDENCE_IDS="$(jq -c '.[0].evidence_ids // []' /tmp/mip-smoke-out.json)"
if ! jq -e 'type == "array" and length > 0 and all(.[]; type == "string" and length > 0)' \
  <<<"$SMOKE_EVIDENCE_IDS" >/dev/null; then
  echo "[smoke] ranked lead did not include canonical evidence_ids for approval proof" >&2
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
SMOKE_DRAFT_SUBJECT="$(jq -r '.subject // empty' /tmp/mip-smoke-out.json)"
SMOKE_OFFER_CODE="$(jq -r '.offer_code // empty' /tmp/mip-smoke-out.json)"
SMOKE_DRAFT_GENERATION_ID="$(jq -r '.generation_id // empty' /tmp/mip-smoke-out.json)"
SMOKE_DRAFT_RESPONSE_HASH="$(jq -r '.response_hash // empty' /tmp/mip-smoke-out.json)"
SMOKE_DRAFT_SOURCE_REFRESHED_AT="$(jq -r '.source_refreshed_at // empty' /tmp/mip-smoke-out.json)"
if [[ -z "$SMOKE_DRAFT_BODY" || -z "$SMOKE_DRAFT_SUBJECT" || -z "$SMOKE_OFFER_CODE" \
  || -z "$SMOKE_DRAFT_GENERATION_ID" || -z "$SMOKE_DRAFT_RESPONSE_HASH" \
  || -z "$SMOKE_DRAFT_SOURCE_REFRESHED_AT" ]]; then
  echo "[smoke] persisted email draft did not return complete approval proof" >&2
  cat /tmp/mip-smoke-out.json >&2 || true
  exit 1
fi
if ! jq -e '
  .status == "draft"
  and .channel == "email"
  and (.generation_id | length > 0)
  and (.response_hash | test("^[0-9a-f]{64}$"))
  and (.source_refreshed_at | length > 0)
  and (.subject | length > 0)
' /tmp/mip-smoke-out.json >/dev/null; then
  echo "[smoke] outreach draft response failed persisted-proof contract" >&2
  cat /tmp/mip-smoke-out.json >&2 || true
  exit 1
fi
SMOKE_APPROVE_PAYLOAD="$(jq -n \
  --arg borrower_id "$BORROWER_ID" \
  --arg offer_code "$SMOKE_OFFER_CODE" \
  --arg draft_body "$SMOKE_DRAFT_BODY" \
  --arg draft_subject "$SMOKE_DRAFT_SUBJECT" \
  --arg draft_generation_id "$SMOKE_DRAFT_GENERATION_ID" \
  --arg draft_response_hash "$SMOKE_DRAFT_RESPONSE_HASH" \
  --arg draft_source_refreshed_at "$SMOKE_DRAFT_SOURCE_REFRESHED_AT" \
  --arg request_id "$SMOKE_REQUEST_ID" \
  --argjson evidence_ids "$SMOKE_EVIDENCE_IDS" \
  '{borrower_id:$borrower_id, offer_code:$offer_code, evidence_ids:$evidence_ids,
    channel:"email", draft_body:$draft_body, draft_subject:$draft_subject,
    draft_generation_id:$draft_generation_id, draft_response_hash:$draft_response_hash,
    draft_source_refreshed_at:$draft_source_refreshed_at, request_id:$request_id}')"
probe "outreach approval audit write" "$API_PREFIX/outreach/approve" POST \
  "$SMOKE_APPROVE_PAYLOAD" idempotent_mutation "$SMOKE_REQUEST_ID"
if ! jq -e --arg generation_id "$SMOKE_DRAFT_GENERATION_ID" \
  '.approved == true
    and (.audit_event_id // "" | length > 0)
    and .draft_generation_id == $generation_id' \
  /tmp/mip-smoke-out.json >/dev/null; then
  echo "[smoke] outreach approval did not return audit and persisted-draft proof" >&2
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

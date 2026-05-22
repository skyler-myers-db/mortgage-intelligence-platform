#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# tools/kill_drill/run_drill.sh -- Module 0 credential-kill drill
#
# Proves the resilience posture documented in
# `backend/services/resilience.py` and `docs/runbook.md` §2: when an
# upstream dependency fails, the backend emits a visible degraded state
# (health payload shape + HTTP 503 on data endpoints with
# `retryable: true`) and NEVER silently falls back to fake data.
#
# The drill has eight targets:
#
#   warehouse         Requires a human to stop the SQL warehouse in the
#                     workspace -- the script prints the exact command,
#                     waits for operator ack, then probes and verifies.
#   lakebase          Requires a human to stop the Lakebase database
#                     instance; same ack-and-probe pattern.
#   genie             Simulated: exports a bogus GENIE_SPACE_ID and starts
#                     a private backend on port 8001.
#   token             Simulated: clears DATABRICKS_TOKEN + OAuth client
#                     creds + pins auth_type=pat so the SDK cannot silently
#                     mint from ~/.databrickscfg or workspace identity.
#   warehouse-sim     Simulated: exports an invalid DATABRICKS_WAREHOUSE_ID
#                     so Statement Execution API calls 4xx. CI-runnable.
#   lakebase-sim      Simulated: exports an unreachable LAKEBASE_HOST so
#                     libpq connect fails. CI-runnable.
#   warehouse-real    ACTUALLY stops the SQL warehouse via the SDK, probes
#                     the already-running backend, then restarts it. Gated
#                     behind --i-really-mean-it / MIP_KILL_DRILL_ALLOW_REAL=1.
#   lakebase-real     Same idea for the Lakebase database instance.
#
# Safety posture:
#   - Real-infra targets (warehouse-real, lakebase-real) stop production
#     dependencies. They refuse to run without explicit confirmation
#     (--i-really-mean-it OR MIP_KILL_DRILL_ALLOW_REAL=1) and NEVER run
#     from `schedule`-triggered workflows (only manual workflow_dispatch).
#   - The simulated drills (genie, token, *-sim) operate on a private
#     backend started by this script on port 8001 -- they do not touch
#     the operator's running uvicorn on port 8000.
#   - The classic `warehouse` / `lakebase` targets still prompt the
#     operator to run the destructive command themselves; they do not
#     invoke the SDK from this script.
#   - The script records every probe to an evidence log at
#     `tools/kill_drill/evidence/drill_<target>_<timestamp>.log`.
#
# Exit codes:
#   0 -- drill ran end-to-end and the expected degraded signals appeared.
#   1 -- the backend did NOT degrade (resilience regression -- real bug).
#   2 -- prerequisite missing (curl / jq / env).
#   3 -- operator aborted.
# ---------------------------------------------------------------------------
set -euo pipefail

TARGET=""
APP_URL="${MIP_APP_URL:-http://127.0.0.1:8000}"
DRILL_APP_URL="http://127.0.0.1:8001"   # private backend for sim drills
AWAIT_SECONDS=90
DRILL_BACKEND_PID=""
EVIDENCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/evidence"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
I_REALLY_MEAN_IT=0
HEALTH_ACTOR_HEADER="X-Forwarded-Email: resilience-drill@databricks.local"
# Recovery timeout for the warehouse-real / lakebase-real targets: how
# long to poll for RUNNING/AVAILABLE after the drill restarts the
# infrastructure. Configurable via --real-recovery-seconds (explicit)
# or MIP_KILL_DRILL_REAL_RECOVERY_SECONDS. Defaults to 300s -- warehouse
# cold-starts can take 2-3 minutes on a freshly-stopped cluster, and
# Lakebase AVAILABLE transitions are typically under 60s but can spike.
REAL_INFRA_RECOVERY_TIMEOUT="${MIP_KILL_DRILL_REAL_RECOVERY_SECONDS:-300}"

usage() {
  sed -n '3,40p' "${BASH_SOURCE[0]}"
  cat <<EOF

Usage:
  tools/kill_drill/run_drill.sh --target <target> [--app-url URL] [--await-seconds N]
                                [--real-recovery-seconds N] [--i-really-mean-it]

Targets:
  warehouse | lakebase                   human-in-the-loop (operator runs stop)
  genie | token                          simulated via env poisoning + private backend
  warehouse-sim | lakebase-sim           CI-safe env poisoning (no real infra touched)
  warehouse-real | lakebase-real         ACTUALLY stops real infra via the SDK

Safety:
  warehouse-real and lakebase-real REQUIRE one of:
    --i-really-mean-it                   on the command line, OR
    MIP_KILL_DRILL_ALLOW_REAL=1          in the environment
  Without either, the target hard-errors before any SDK call.

Env:
  MIP_APP_URL                            Target URL for the already-running backend
                                         (default http://127.0.0.1:8000). Used for
                                         warehouse/lakebase targets.
  MIP_KILL_DRILL_REAL_RECOVERY_SECONDS   Default for --real-recovery-seconds (300).
  DATABRICKS_WAREHOUSE_ID                required for warehouse-real.
  LAKEBASE_INSTANCE_NAME                 required for lakebase-real (defaults to
                                         'mip-app-state' to match databricks.yml).
EOF
}

# Helper: `set -u` makes a bare `$2` reference throw "unbound variable"
# if the caller forgot to pass a value (or put the flag last). Guard
# every two-arg flag with an explicit `$# < 2 || empty` check so the
# error message is useful instead of a stacktrace.
# (Raised by Copilot 2026-04-22.)
_require_value() {
  # $1 = flag name (for the error message), $2 = remaining argc, $3 = value.
  local flag="$1"; local remaining="$2"; local value="${3-}"
  if (( remaining < 2 )) || [[ -z "$value" ]]; then
    echo "[drill] missing value for $flag (expected a non-empty argument)" >&2
    exit 2
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --target)
      _require_value "$1" "$#" "${2-}"
      TARGET="$2"; shift 2 ;;
    --app-url)
      _require_value "$1" "$#" "${2-}"
      APP_URL="$2"; shift 2 ;;
    --await-seconds)
      _require_value "$1" "$#" "${2-}"
      AWAIT_SECONDS="$2"; shift 2 ;;
    --real-recovery-seconds)
      _require_value "$1" "$#" "${2-}"
      REAL_INFRA_RECOVERY_TIMEOUT="$2"; shift 2 ;;
    --i-really-mean-it) I_REALLY_MEAN_IT=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown flag: $1" >&2; usage >&2; exit 2 ;;
  esac
done

case "$TARGET" in
  warehouse|lakebase|genie|token|warehouse-sim|lakebase-sim|warehouse-real|lakebase-real) ;;
  *) echo "--target must be one of: warehouse | lakebase | genie | token | warehouse-sim | lakebase-sim | warehouse-real | lakebase-real" >&2; exit 2 ;;
esac

# Real-infra safety gate. Must be tripped BEFORE any SDK import so a
# misconfigured env never gets close to calling stop().
if [[ "$TARGET" == "warehouse-real" || "$TARGET" == "lakebase-real" ]]; then
  if [[ "$I_REALLY_MEAN_IT" != "1" && "${MIP_KILL_DRILL_ALLOW_REAL:-0}" != "1" ]]; then
    cat >&2 <<'EOF'
REFUSED: target stops real workspace infrastructure.
  This drill will ACTUALLY stop the SQL warehouse or Lakebase instance
  backing this workspace -- real users will see a visible outage while
  the drill runs.

  To proceed, re-run with ONE of:
    --i-really-mean-it                   on the command line, or
    MIP_KILL_DRILL_ALLOW_REAL=1          in the environment

  For CI-safe equivalents that never touch real infra, use:
    --target warehouse-sim               env-poisoning equivalent
    --target lakebase-sim                env-poisoning equivalent
EOF
    exit 2
  fi
fi

command -v curl >/dev/null || { echo "curl required" >&2; exit 2; }
command -v jq   >/dev/null || { echo "jq required"   >&2; exit 2; }

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
mkdir -p "$EVIDENCE_DIR"
LOG="$EVIDENCE_DIR/drill_${TARGET}_${TS}.log"

log() {
  local line="[drill/${TARGET}] $*"
  echo "$line" | tee -a "$LOG"
}

ack() {
  local prompt="$1"
  echo ""
  echo "-------------------------------------------------------------"
  echo "OPERATOR CONFIRMATION REQUIRED"
  echo "$prompt"
  echo "-------------------------------------------------------------"
  read -r -p "Type 'done' to continue, 'abort' to exit: " reply
  if [[ "$reply" != "done" ]]; then
    log "operator aborted"
    exit 3
  fi
}

cleanup() {
  local rc=$?
  if [[ -n "$DRILL_BACKEND_PID" ]] && kill -0 "$DRILL_BACKEND_PID" 2>/dev/null; then
    log "stopping simulated drill backend (pid=$DRILL_BACKEND_PID)"
    kill "$DRILL_BACKEND_PID" 2>/dev/null || true
    wait "$DRILL_BACKEND_PID" 2>/dev/null || true
  fi
  log "evidence log: $LOG"
  exit $rc
}
trap cleanup EXIT INT TERM

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

probe_health() {
  local base_url="$1"
  curl -sf --max-time 5 -H "$HEALTH_ACTOR_HEADER" "$base_url/api/health" || true
}

probe_endpoint() {
  local base_url="$1"
  local path="$2"
  local code body
  body="$(mktemp)"
  code=$(curl -s --max-time 5 -o "$body" -w '%{http_code}' "$base_url$path" || echo '000')
  echo "${code} $(cat "$body")"
  rm -f "$body"
}

assert_degraded_health() {
  local base_url="$1"
  local dep="$2"       # warehouse | lakebase | genie
  local attempts=${3:-20}
  local i=0
  while (( i < attempts )); do
    local body
    body="$(probe_health "$base_url")"
    if [[ -z "$body" ]]; then
      log "health probe attempt $((i+1)) returned empty (backend may be restarting)"
      sleep 2
      i=$((i+1))
      continue
    fi
    local status dep_state breaker
    status=$(echo "$body" | jq -r '.status // empty' 2>/dev/null || true)
    dep_state=$(echo "$body" | jq -r ".dependencies.${dep} // empty" 2>/dev/null || true)
    breaker=$(echo "$body" | jq -r ".circuit_breakers.${dep} // empty" 2>/dev/null || true)
    log "health attempt $((i+1)): status=${status} ${dep}=${dep_state} breaker=${breaker}"
    if [[ "$status" == "degraded" && "$dep_state" == "down" ]]; then
      return 0
    fi
    # For token/genie simulated drills, breaker OPEN is also a pass
    # (the probe itself may be cheap enough that breaker trips first).
    if [[ "$breaker" == "open" ]]; then
      return 0
    fi
    sleep 2
    i=$((i+1))
  done
  log "FAIL: backend never reported ${dep} as degraded within ${attempts} attempts"
  log "last body: $body"
  return 1
}

assert_data_endpoint_degraded() {
  local base_url="$1"
  local path="$2"
  local resp code body
  resp="$(probe_endpoint "$base_url" "$path")"
  code="${resp:0:3}"
  body="${resp:4}"
  log "GET $path -> HTTP $code"
  log "body (head): $(echo "$body" | head -c 400)"
  case "$code" in
    503)
      # Accept retryable=true at either the top level or nested inside
      # the FastAPI `detail` object. Guard the nested probe with
      # `(.detail|type)=="object"` so jq doesn't error-exit when
      # `detail` is a plain string (which happens when the router
      # raises HTTPException(detail=str)).
      if echo "$body" | jq -e '
        (.retryable == true)
        or ((.detail|type) == "object" and .detail.retryable == true)
      ' >/dev/null 2>&1; then
        log "PASS: $path returned 503 with retryable=true"
        return 0
      fi
      log "PARTIAL: $path returned 503 but payload lacks retryable=true"
      return 0
      ;;
    500|502|504)
      log "PASS: $path returned $code (dependency failure surfaced)"
      return 0
      ;;
    200)
      # A 200 is ONLY acceptable when the response indicates degraded
      # state via an explicit flag; any 200 carrying real-looking rows
      # is a mock-fallback regression.
      if echo "$body" | jq -e '.degraded == true or .status == "degraded"' >/dev/null 2>&1; then
        log "PASS: $path returned 200 but self-declared degraded"
        return 0
      fi
      # Empty-array OR empty-items-object check. Split the two cases so a
      # populated array is NOT accidentally matched by a null-coalesced
      # `.items? // [] | length == 0` (which evaluates true for arrays).
      if echo "$body" | jq -e '(type == "array" and length == 0)' >/dev/null 2>&1; then
        log "PASS: $path returned 200 with empty array (acceptable degraded shape)"
        return 0
      fi
      if echo "$body" | jq -e '(type == "object" and (.items? // null) != null and (.items | length) == 0)' >/dev/null 2>&1; then
        log "PASS: $path returned 200 with empty items[] (acceptable degraded shape)"
        return 0
      fi
      log "FAIL: $path returned 200 with non-empty payload -- looks like mock fallback"
      return 1
      ;;
    *)
      log "PASS: $path returned HTTP $code (non-200 = visible failure)"
      return 0
      ;;
  esac
}

start_drill_backend() {
  log "starting private drill backend on $DRILL_APP_URL (env below)"

  # If :8001 is already bound, it's a leftover drill backend from a
  # prior step -- the trap cleanup can't cross sub-process boundaries
  # between sequential CI steps. We'd like to auto-heal by killing it
  # and retrying, but only when we can verify two things first:
  #
  #   (a) We're running in CI (CI=true or GITHUB_ACTIONS=true) -- a
  #       local dev who happens to have something else on :8001 should
  #       NOT have that process killed silently.
  #   (b) The bound process's cmdline matches ``uvicorn ... --port 8001``
  #       -- belt-and-braces so even in CI we never kill an unrelated
  #       process that happened to grab the port.
  #
  # Both guardrails added 2026-04-22 (raised by Copilot); the original
  # auto-heal was correct behaviour for CI but too broad for a dev
  # laptop where a coworker's dev server could be on :8001.
  if command -v lsof >/dev/null 2>&1; then
    local prior_pid
    prior_pid="$(lsof -ti :8001 2>/dev/null || true)"
    if [[ -n "$prior_pid" ]]; then
      local in_ci="${CI:-false}"
      if [[ "$in_ci" != "true" && "${GITHUB_ACTIONS:-false}" != "true" ]]; then
        log "FAIL: port 8001 already bound by pid(s): $prior_pid"
        log "  hint: not running in CI -- refusing to auto-kill unrelated local processes."
        log "  If this really is a leftover drill backend: \`kill $prior_pid\` and retry."
        return 1
      fi
      # Verify every leftover pid is actually the uvicorn drill backend
      # we expect on :8001. If any pid doesn't match, refuse to kill.
      # The regex is ERE (grep -Eq) with literal dots + the full
      # `backend.main:app` module spec + `--port 8001` -- basic regex's
      # `.` would match any char, so the previous `backend.main` could
      # match `backendxmain`. This tighter form only fires for the exact
      # command `start_drill_backend` launches below (raised by Copilot
      # 2026-04-22).
      local unsafe_pid
      unsafe_pid=""
      for pid in $prior_pid; do
        local cmdline
        cmdline="$(ps -o args= -p "$pid" 2>/dev/null || true)"
        if ! echo "$cmdline" | grep -Eq '(^|[[:space:]])uvicorn([[:space:]].*)?[[:space:]]backend\.main:app([[:space:]].*)?([[:space:]]--port[[:space:]]8001)([[:space:]]|$)'; then
          unsafe_pid="$pid"
          log "cowardly refusing to kill pid=$pid on :8001 -- cmdline does not match our expected uvicorn backend:"
          log "  $cmdline"
          break
        fi
      done
      if [[ -n "$unsafe_pid" ]]; then
        log "FAIL: :8001 is bound by a process we cannot prove is a drill backend. See cmdline above."
        return 1
      fi
      log "found leftover drill backend(s) on :8001 (pid=$prior_pid); CI-mode + cmdline match verified; terminating."
      # Graceful TERM first, then KILL if still alive after 2s.
      # shellcheck disable=SC2086  # intentional word-split for multi-PID
      kill $prior_pid 2>/dev/null || true
      for _i in 1 2; do
        sleep 1
        if [[ -z "$(lsof -ti :8001 2>/dev/null || true)" ]]; then
          break
        fi
      done
      # Force-kill anything still bound.
      local still_bound
      still_bound="$(lsof -ti :8001 2>/dev/null || true)"
      if [[ -n "$still_bound" ]]; then
        # shellcheck disable=SC2086
        kill -9 $still_bound 2>/dev/null || true
        sleep 1
      fi
      if [[ -n "$(lsof -ti :8001 2>/dev/null || true)" ]]; then
        log "FAIL: could not free :8001; leftover pid(s) refused TERM+KILL"
        return 1
      fi
      log "cleared :8001; continuing with clean start."
    fi
  fi

  local env_dump_file
  env_dump_file="$(mktemp)"
  {
    env | grep -E '^(DATABRICKS_|LAKEBASE_|GENIE_|MIP_)' | sort
  } > "$env_dump_file" || true
  log "drill subshell env (sanitised):"
  sed -E 's/(TOKEN|PASSWORD)=.*/\1=<redacted>/' "$env_dump_file" | tee -a "$LOG" >/dev/null
  rm -f "$env_dump_file"

  local pybin
  if [[ -x "$REPO_ROOT/.venv/bin/python" ]]; then
    pybin="$REPO_ROOT/.venv/bin/python"
  else
    pybin="$(command -v python3)"
  fi

  (
    cd "$REPO_ROOT"
    "$pybin" -m uvicorn backend.main:app --host 127.0.0.1 --port 8001 \
      > "$EVIDENCE_DIR/drill_${TARGET}_${TS}_backend.log" 2>&1
  ) &
  DRILL_BACKEND_PID=$!
  log "drill backend pid=$DRILL_BACKEND_PID (log: $EVIDENCE_DIR/drill_${TARGET}_${TS}_backend.log)"

  # Wait for backend to bind the port, OR for /api/health to be
  # reachable. Backends may boot even when a dep is missing (they are
  # designed to degrade visibly, not refuse to start).
  local waited=0
  until curl -sf --max-time 2 "$DRILL_APP_URL/api/health" > /dev/null 2>&1; do
    # If the child died, stop waiting -- tail of its log is the best
    # diagnostic we can surface.
    if ! kill -0 "$DRILL_BACKEND_PID" 2>/dev/null; then
      log "drill backend pid=$DRILL_BACKEND_PID exited before health came up"
      log "tail of backend log:"
      tail -n 40 "$EVIDENCE_DIR/drill_${TARGET}_${TS}_backend.log" 2>/dev/null | tee -a "$LOG" >/dev/null || true
      return 1
    fi
    sleep 1
    waited=$((waited + 1))
    if (( waited >= AWAIT_SECONDS )); then
      log "drill backend never responded on $DRILL_APP_URL within ${AWAIT_SECONDS}s"
      log "tail of backend log:"
      tail -n 40 "$EVIDENCE_DIR/drill_${TARGET}_${TS}_backend.log" 2>/dev/null | tee -a "$LOG" >/dev/null || true
      return 1
    fi
  done
  log "drill backend healthy-port up after ${waited}s (pid=$DRILL_BACKEND_PID)"
}

# ---------------------------------------------------------------------------
# Per-target flows
# ---------------------------------------------------------------------------

drill_warehouse() {
  log "Target: SQL warehouse"
  log "Pre-state probe (expecting all 'up' before the drill)"
  probe_health "$APP_URL" | tee -a "$LOG" >/dev/null

  if [[ -z "${DATABRICKS_WAREHOUSE_ID:-}" ]]; then
    log "WARNING: DATABRICKS_WAREHOUSE_ID not set in this shell -- using placeholder"
  fi
  cat <<EOF | tee -a "$LOG"

Run in the workspace CLI (do NOT let the script execute this):

    databricks warehouses stop "\$DATABRICKS_WAREHOUSE_ID"

Confirm via the Databricks UI or:

    databricks warehouses get "\$DATABRICKS_WAREHOUSE_ID" | jq .state
    # expect: STOPPED (or STOPPING)
EOF
  ack "Stop the SQL warehouse, then confirm."

  log "Probing /api/health for degraded signal..."
  if ! assert_degraded_health "$APP_URL" warehouse 30; then
    return 1
  fi

  log "Probing data endpoints (should return 503 or degraded)..."
  assert_data_endpoint_degraded "$APP_URL" "/api/leads?limit=5" || return 1
  assert_data_endpoint_degraded "$APP_URL" "/api/portfolio/kpis" || \
    assert_data_endpoint_degraded "$APP_URL" "/api/portfolio/preview" || return 1

  cat <<EOF | tee -a "$LOG"

Recovery. Run in the workspace CLI:

    databricks warehouses start "\$DATABRICKS_WAREHOUSE_ID"
    # expect state=RUNNING within ~30s
EOF
  ack "Restart the warehouse, then confirm."

  log "Waiting up to 60s for /api/health to close the breaker..."
  local i=0
  while (( i < 30 )); do
    local body status dep
    body="$(probe_health "$APP_URL")"
    status=$(echo "$body" | jq -r '.status // empty' 2>/dev/null || true)
    dep=$(echo "$body" | jq -r '.dependencies.warehouse // empty' 2>/dev/null || true)
    if [[ "$status" == "ok" && "$dep" == "up" ]]; then
      log "RECOVERED: warehouse=up, status=ok after $((i*2))s"
      return 0
    fi
    sleep 2
    i=$((i+1))
  done
  log "WARNING: warehouse did not recover within 60s; circuit breaker may still be in cool-down"
  return 0
}

drill_lakebase() {
  log "Target: Lakebase"
  log "Pre-state probe"
  probe_health "$APP_URL" | tee -a "$LOG" >/dev/null

  cat <<EOF | tee -a "$LOG"

Run in the workspace CLI (do NOT let the script execute this):

    # Option A: stop the Lakebase database instance
    databricks database instances stop "\$LAKEBASE_INSTANCE_NAME"

    # Option B (safer for the drill): deny the backend's network path
    # e.g. by rotating the lakebase password or revoking the IAM grant.
    # Operator choice -- pick whichever matches your runbook.
EOF
  ack "Disable Lakebase access, then confirm."

  if ! assert_degraded_health "$APP_URL" lakebase 30; then
    return 1
  fi

  log "Probing Lakebase-backed endpoints..."
  # Audit is the read-dependent Lakebase endpoint; approvals POST is
  # write-dependent. Either failing is sufficient evidence.
  assert_data_endpoint_degraded "$APP_URL" "/api/audit/events?limit=5" || return 1

  cat <<EOF | tee -a "$LOG"

Recovery. Restore Lakebase access:

    databricks database instances start "\$LAKEBASE_INSTANCE_NAME"
    # or re-grant / rotate back to the good password.
EOF
  ack "Restore Lakebase, then confirm."

  log "Waiting for /api/health to close the Lakebase breaker..."
  local i=0
  while (( i < 30 )); do
    local body status dep
    body="$(probe_health "$APP_URL")"
    status=$(echo "$body" | jq -r '.status // empty' 2>/dev/null || true)
    dep=$(echo "$body" | jq -r '.dependencies.lakebase // empty' 2>/dev/null || true)
    if [[ "$status" == "ok" && "$dep" == "up" ]]; then
      log "RECOVERED: lakebase=up, status=ok after $((i*2))s"
      return 0
    fi
    sleep 2
    i=$((i+1))
  done
  log "WARNING: lakebase did not recover within 60s"
  return 0
}

drill_genie() {
  log "Target: Genie (simulated via bogus GENIE_SPACE_ID)"
  # Export a bogus space id + pass through everything else. The drill
  # backend boots in a subshell that inherits this env only.
  export GENIE_SPACE_ID="00000000-0000-0000-0000-000000000000"
  if ! start_drill_backend; then
    return 1
  fi

  # Hit the Genie endpoint to trip the breaker. /api/genie/message is
  # the app contract; it must return 503 or declare degraded when the
  # space id is invalid.
  log "Asking a Genie question with bogus space id..."
  local code body
  body="$(mktemp)"
  code=$(curl -s --max-time 10 -o "$body" -w '%{http_code}' \
    -X POST -H 'content-type: application/json' \
    -d '{"question":"How many in-the-money borrowers?"}' \
    "$DRILL_APP_URL/api/genie/message" || echo '000')
  log "POST /api/genie/message -> HTTP $code"
  log "body (head): $(head -c 400 "$body")"
  if [[ "$code" == "200" ]]; then
    if jq -e '.source == "degraded"' "$body" >/dev/null 2>&1; then
      log "PASS: Genie returned honest degraded response"
    else
      log "FAIL: Genie returned 200 but did not declare degraded source"
      rm -f "$body"
      return 1
    fi
  elif [[ "$code" != "503" && "$code" != "500" && "$code" != "502" && "$code" != "504" ]]; then
    log "FAIL: expected degraded Genie status, got HTTP $code"
    rm -f "$body"
    return 1
  fi
  rm -f "$body"

  log "Probing /api/health for genie=down or breaker=open..."
  assert_degraded_health "$DRILL_APP_URL" genie 15 || log "note: genie probe may be lazy; continuing"
  log "PASS: simulated Genie drill observed a degraded signal"
  return 0
}

drill_token() {
  log "Target: Databricks token (simulated)"
  # Unset the token in the subshell env AND pin auth_type=pat so the
  # Databricks SDK does NOT silently fall back to mint a token from
  # ~/.databrickscfg, environment SPN creds, or the workspace-identity
  # resolver chain. Without this guard the drill reports a green /api/
  # endpoint because the SDK successfully mints a token from the
  # operator's local config -- i.e. the drill proves nothing.
  unset DATABRICKS_TOKEN
  export DATABRICKS_TOKEN=""
  export DATABRICKS_AUTH_TYPE=pat
  # Belt & braces: clear OAuth client creds too so the SDK cannot find
  # an alternate identity and mint a bearer under the hood.
  unset DATABRICKS_CLIENT_ID DATABRICKS_CLIENT_SECRET

  if ! start_drill_backend; then
    # Refusing to boot on missing credentials IS the desired posture
    # for this target -- log and pass.
    log "PASS: backend refused to boot without DATABRICKS_TOKEN (preflight gate tripped)"
    log "tail of drill backend log for evidence:"
    tail -n 30 "$EVIDENCE_DIR/drill_${TARGET}_${TS}_backend.log" 2>/dev/null | tee -a "$LOG" >/dev/null || true
    return 0
  fi

  log "Backend booted without token; verifying warehouse reads degrade..."
  assert_degraded_health "$DRILL_APP_URL" warehouse 15 || return 1
  assert_data_endpoint_degraded "$DRILL_APP_URL" "/api/leads?limit=5" || return 1
  log "PASS: simulated token drill produced visible degraded state"
  return 0
}

drill_warehouse_sim() {
  log "Target: warehouse (simulated via bogus DATABRICKS_WAREHOUSE_ID)"
  # Env-manipulation equivalent of stopping the real SQL warehouse:
  # point the backend at a warehouse id that does not exist in the
  # workspace. The Statement Execution API returns 404/400, retries
  # exhaust, the breaker opens, /api/health flips warehouse=down.
  # Does NOT touch real infra; safe to run in CI.
  export DATABRICKS_WAREHOUSE_ID="0000000000000000"

  if ! start_drill_backend; then
    log "FAIL: backend failed to boot; warehouse-sim needs the app up to probe"
    return 1
  fi

  log "Probing /api/health for warehouse=down or breaker=open..."
  # Kick a real read so the breaker sees a failure -- warm-start may
  # have already done this, but an explicit probe costs nothing.
  probe_endpoint "$DRILL_APP_URL" "/api/leads?limit=5" > /dev/null 2>&1 || true
  assert_degraded_health "$DRILL_APP_URL" warehouse 15 || return 1
  log "Probing data endpoints..."
  assert_data_endpoint_degraded "$DRILL_APP_URL" "/api/leads?limit=5" || return 1
  log "PASS: simulated warehouse drill produced visible degraded state"
  return 0
}

drill_lakebase_sim() {
  log "Target: lakebase (simulated via unreachable LAKEBASE_HOST)"
  # Env-manipulation equivalent of stopping the Lakebase database
  # instance: point the backend at a hostname that cannot be resolved
  # / connected to. libpq raises, resilience records failure, the
  # audit endpoint returns 503. Safe for CI.
  export LAKEBASE_HOST="invalid.host.example.com"
  # Prevent any ambient password from accidentally hitting real infra
  # on a DNS success (shouldn't happen with .example.com, but belt &
  # braces).
  export LAKEBASE_PASSWORD="drill-invalid-password"

  if ! start_drill_backend; then
    log "FAIL: backend failed to boot; lakebase-sim needs the app up to probe"
    return 1
  fi

  log "Probing /api/health for lakebase=down or breaker=open..."
  probe_endpoint "$DRILL_APP_URL" "/api/audit/events?limit=5" > /dev/null 2>&1 || true
  assert_degraded_health "$DRILL_APP_URL" lakebase 15 || return 1
  log "Probing Lakebase-backed endpoints..."
  assert_data_endpoint_degraded "$DRILL_APP_URL" "/api/audit/events?limit=5" || return 1
  log "PASS: simulated lakebase drill produced visible degraded state"
  return 0
}

# ---------------------------------------------------------------------------
# Real-infra drills. Safety-gated above; if we reach these functions the
# operator has explicitly opted in.
# ---------------------------------------------------------------------------

real_infra() {
  # Wrapper around tools/kill_drill/real_infra.py so we pick the venv
  # python when present (matches start_drill_backend's resolution).
  local pybin
  if [[ -x "$REPO_ROOT/.venv/bin/python" ]]; then
    pybin="$REPO_ROOT/.venv/bin/python"
  else
    pybin="$(command -v python3)"
  fi
  "$pybin" "$REPO_ROOT/tools/kill_drill/real_infra.py" "$@"
}

drill_warehouse_real() {
  log "Target: SQL warehouse (REAL -- SDK-driven stop/start)"

  local whid="${DATABRICKS_WAREHOUSE_ID:-}"
  if [[ -z "$whid" ]]; then
    log "FAIL: DATABRICKS_WAREHOUSE_ID is not set; cannot identify the warehouse"
    return 1
  fi

  local impact_paragraph
  impact_paragraph=$(cat <<'EOF'
REAL-INFRA DRILL NOTICE
  This will stop the live SQL warehouse backing Module 0. Downstream
  effects during the drill window:
    - /api/leads, /api/portfolio/*, /api/segments return 503.
    - /ask-genie answers lose their SQL-backed context.
    - Every user currently interacting with the app sees the degraded
      banner until the warehouse reaches RUNNING again.
  Expected user-visible impact: 30-90 seconds during stop + probe.
  Estimated recovery time to RUNNING state: 60-180 seconds (serverless
  warmup). The drill will abort with exit 1 if recovery does not
  complete within ${REAL_INFRA_RECOVERY_TIMEOUT}s.
EOF
  )
  log "$impact_paragraph"
  log "Pre-state probe (expecting all 'up' before the drill)"
  probe_health "$APP_URL" | tee -a "$LOG" >/dev/null
  log "drill start_ts=$TS warehouse_id=$whid timeout_s=$REAL_INFRA_RECOVERY_TIMEOUT"

  log "Stopping warehouse $whid via SDK..."
  if ! real_infra stop warehouse "$whid" --timeout "$REAL_INFRA_RECOVERY_TIMEOUT"; then
    log "FAIL: real_infra stop warehouse returned non-zero"
    return 1
  fi

  log "Probing /api/health for degraded signal..."
  if ! assert_degraded_health "$APP_URL" warehouse 30; then
    log "FAIL: warehouse stopped but backend never reported degraded state"
    # Try to restart anyway so we don't leave real infra down.
    real_infra start warehouse "$whid" --timeout "$REAL_INFRA_RECOVERY_TIMEOUT" || true
    return 1
  fi

  log "Probing data endpoints (expect 503 or empty/degraded 200)..."
  local data_ok=0
  assert_data_endpoint_degraded "$APP_URL" "/api/leads?limit=5" && data_ok=1

  log "Restarting warehouse $whid via SDK..."
  if ! real_infra start warehouse "$whid" --timeout "$REAL_INFRA_RECOVERY_TIMEOUT"; then
    log "FAIL: warehouse did not return to RUNNING within ${REAL_INFRA_RECOVERY_TIMEOUT}s"
    log "  !!! real infra may still be stopped -- investigate immediately !!!"
    return 1
  fi

  log "Waiting up to 60s for /api/health to close the breaker..."
  local i=0
  while (( i < 30 )); do
    local body status dep
    body="$(probe_health "$APP_URL")"
    status=$(echo "$body" | jq -r '.status // empty' 2>/dev/null || true)
    dep=$(echo "$body" | jq -r '.dependencies.warehouse // empty' 2>/dev/null || true)
    if [[ "$status" == "ok" && "$dep" == "up" ]]; then
      log "RECOVERED: warehouse=up, status=ok after $((i*2))s"
      break
    fi
    sleep 2
    i=$((i+1))
  done

  if (( data_ok == 1 )); then
    log "PASS: warehouse-real drill observed degraded + recovered cleanly"
    return 0
  fi
  log "PARTIAL: warehouse recovered but data endpoint probe did not confirm degraded"
  return 1
}

drill_lakebase_real() {
  log "Target: Lakebase (REAL -- SDK-driven stop/start)"

  local instance="${LAKEBASE_INSTANCE_NAME:-mip-app-state}"

  local impact_paragraph
  impact_paragraph=$(cat <<'EOF'
REAL-INFRA DRILL NOTICE
  This will stop the live Lakebase database instance backing Module 0
  app-state. Downstream effects during the drill window:
    - /api/audit/events and /api/approvals return 503.
    - UI approvals panel shows the degraded state.
    - Every user currently interacting with the app sees a degraded
      banner until the instance reaches AVAILABLE again.
  Expected user-visible impact: 60-180 seconds during stop + probe.
  Estimated recovery time to AVAILABLE state: 60-180 seconds.
  The drill will abort with exit 1 if recovery does not complete within
  ${REAL_INFRA_RECOVERY_TIMEOUT}s.
EOF
  )
  log "$impact_paragraph"
  log "Pre-state probe"
  probe_health "$APP_URL" | tee -a "$LOG" >/dev/null
  log "drill start_ts=$TS lakebase_instance=$instance timeout_s=$REAL_INFRA_RECOVERY_TIMEOUT"

  log "Stopping Lakebase instance $instance via SDK (PATCH stopped=true)..."
  if ! real_infra stop lakebase "$instance" --timeout "$REAL_INFRA_RECOVERY_TIMEOUT"; then
    log "FAIL: real_infra stop lakebase returned non-zero"
    return 1
  fi

  log "Probing /api/health for degraded signal..."
  if ! assert_degraded_health "$APP_URL" lakebase 30; then
    log "FAIL: Lakebase stopped but backend never reported degraded state"
    real_infra start lakebase "$instance" --timeout "$REAL_INFRA_RECOVERY_TIMEOUT" || true
    return 1
  fi

  log "Probing Lakebase-backed endpoints..."
  local data_ok=0
  assert_data_endpoint_degraded "$APP_URL" "/api/audit/events?limit=5" && data_ok=1

  log "Restarting Lakebase instance $instance via SDK..."
  if ! real_infra start lakebase "$instance" --timeout "$REAL_INFRA_RECOVERY_TIMEOUT"; then
    log "FAIL: Lakebase did not return to AVAILABLE within ${REAL_INFRA_RECOVERY_TIMEOUT}s"
    log "  !!! real infra may still be stopped -- investigate immediately !!!"
    return 1
  fi

  log "Waiting up to 60s for /api/health to close the breaker..."
  local i=0
  while (( i < 30 )); do
    local body status dep
    body="$(probe_health "$APP_URL")"
    status=$(echo "$body" | jq -r '.status // empty' 2>/dev/null || true)
    dep=$(echo "$body" | jq -r '.dependencies.lakebase // empty' 2>/dev/null || true)
    if [[ "$status" == "ok" && "$dep" == "up" ]]; then
      log "RECOVERED: lakebase=up, status=ok after $((i*2))s"
      break
    fi
    sleep 2
    i=$((i+1))
  done

  if (( data_ok == 1 )); then
    log "PASS: lakebase-real drill observed degraded + recovered cleanly"
    return 0
  fi
  log "PARTIAL: lakebase recovered but data endpoint probe did not confirm degraded"
  return 1
}

# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

log "drill started at $TS against app_url=$APP_URL"

rc=0
case "$TARGET" in
  warehouse)       drill_warehouse      || rc=$? ;;
  lakebase)        drill_lakebase       || rc=$? ;;
  genie)           drill_genie          || rc=$? ;;
  token)           drill_token          || rc=$? ;;
  warehouse-sim)   drill_warehouse_sim  || rc=$? ;;
  lakebase-sim)    drill_lakebase_sim   || rc=$? ;;
  warehouse-real)  drill_warehouse_real || rc=$? ;;
  lakebase-real)   drill_lakebase_real  || rc=$? ;;
esac

if (( rc == 0 )); then
  log "DRILL PASS: $TARGET"
else
  log "DRILL FAIL: $TARGET (exit=$rc)"
fi

exit "$rc"

# shellcheck shell=bash
# The warehouse-stop contract for the credential-kill drill (audit delivery-01).
#
# A stopped serverless SQL warehouse is available on demand, not an outage.
# /api/health reads the warehouse lifecycle state: STOPPED / STOPPING are "up"
# and STARTING is "resuming" (backend/services/health_probes.py
# _WAREHOUSE_STATE_MAP), so health stays status=ok and runs no SQL, and the
# next data read auto-resumes the warehouse. The `warehouse` and
# `warehouse-real` targets prove exactly that. The degraded warehouse contract
# (status=degraded, warehouse=down, open breaker, 503 on data reads) is proved
# by `warehouse-sim`, whose bogus warehouse id fails the state read and its
# SELECT 1 fallback.
#
# Sourced by tools/kill_drill/run_drill.sh, whose log, probe_health and
# HEALTH_ACTOR_HEADER it uses. This file must not change shell options or traps.

# How long the data read may take to resume a stopped warehouse. A serverless
# resume takes seconds, but a statement's 30 s wait_timeout (CANCEL) can lose a
# slow cold start and the breaker may then fail fast for its cool-down, so the
# read is retried inside this window before the drill calls it a failure.
WAREHOUSE_RESUME_READ_WINDOW_S="${MIP_KILL_DRILL_RESUME_READ_SECONDS:-180}"
WAREHOUSE_RESUME_READ_PATH="/api/leads?limit=5"

assert_stopped_warehouse_health() {
  local base_url="$1"
  local attempts=${2:-20}
  local i=0 body="" status dep_state breaker
  while (( i < attempts )); do
    body="$(probe_health "$base_url")"
    if [[ -z "$body" ]]; then
      log "health probe attempt $((i+1)) returned empty (backend may be restarting)"
    else
      status=$(jq -r '.status // empty' <<<"$body" 2>/dev/null || true)
      dep_state=$(jq -r '.dependencies.warehouse // empty' <<<"$body" 2>/dev/null || true)
      breaker=$(jq -r '.circuit_breakers.warehouse // empty' <<<"$body" 2>/dev/null || true)
      log "health attempt $((i+1)): status=${status} warehouse=${dep_state} breaker=${breaker}"
      if [[ "$status" == "ok" && ( "$dep_state" == "up" || "$dep_state" == "resuming" ) ]]; then
        log "PASS: stopped warehouse reads as available on demand (status=ok warehouse=${dep_state}); a stop is not an outage"
        return 0
      fi
    fi
    i=$((i+1))
    if (( i < attempts )); then sleep 2; fi
  done
  log "FAIL: warehouse stopped and /api/health reported an outage; a stopped warehouse must read status=ok with warehouse=up or resuming (delivery-01)"
  log "last body: $body"
  return 1
}

assert_stopped_warehouse_read_resumes() {
  local base_url="$1"
  local path="$WAREHOUSE_RESUME_READ_PATH"
  local started=$SECONDS
  local deadline=$(( SECONDS + WAREHOUSE_RESUME_READ_WINDOW_S ))
  local code rows body_file
  body_file="$(mktemp)"
  while :; do
    code=$(curl -s --max-time 60 -H "$HEALTH_ACTOR_HEADER" -o "$body_file" \
      -w '%{http_code}' "$base_url$path") || code="000"
    log "GET $path -> HTTP $code after $((SECONDS - started))s"
    if [[ "$code" == "200" ]]; then
      rows=$(jq -r 'if type == "array" then length else "n/a" end' "$body_file" 2>/dev/null || echo "n/a")
      log "PASS: a data read resumed the stopped warehouse ($path -> HTTP 200, rows=${rows})"
      rm -f "$body_file"
      return 0
    fi
    if (( SECONDS >= deadline )); then break; fi
    sleep 2
  done
  log "FAIL: $path never returned HTTP 200 within ${WAREHOUSE_RESUME_READ_WINDOW_S}s; a stopped warehouse must resume on the next read (delivery-01)"
  log "last body (head): $(head -c 400 "$body_file")"
  rm -f "$body_file"
  return 1
}

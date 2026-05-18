#!/usr/bin/env bash
#
# Run the Module 0 load-test harness headlessly for 2 minutes at 20
# concurrent users. Prints p50/p95/p99 per endpoint to the terminal
# and writes a timestamped CSV (and HTML report) to
# tools/load_test/results/.
#
#   MIP_API_URL=http://localhost:8000 bash tools/load_test/run.sh
#
# Add MIP_LOAD_TEST_WRITE=1 only when you intentionally want to exercise
# governed write paths (outreach approval, portfolio create, Genie action
# confirm). Default runs are read-only.
#
# Locust is NOT a runtime dependency -- install it locally first:
#
#   pip install locust
#
# By default targets a local uvicorn boot on port 8000. Point
# MIP_API_URL at a deployed Databricks App URL to run against a
# staging environment; remember that every request hits a real SQL
# warehouse and that `wait_time` is tuned for a handful of users, not
# hundreds. DO NOT point this at production without coordinating with
# the Databricks workspace admin.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
RESULTS_DIR="${SCRIPT_DIR}/results"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT_PREFIX="${RESULTS_DIR}/${TIMESTAMP}"

mkdir -p "${RESULTS_DIR}"

# MIP_API_URL must be an absolute URL because Locust's --host takes a
# full origin, not a path.
MIP_API_URL="${MIP_API_URL:-http://localhost:8000}"
MIP_API_PREFIX="${MIP_API_PREFIX:-/api/v1}"
USERS="${MIP_USERS:-20}"
SPAWN_RATE="${MIP_SPAWN_RATE:-5}"
RUN_TIME="${MIP_RUN_TIME:-2m}"
WRITE_ENABLED="${MIP_LOAD_TEST_WRITE:-0}"
SKIP_WARMUP="${MIP_LOAD_TEST_SKIP_WARMUP:-0}"
WARM_BORROWERS="${MIP_LOAD_TEST_WARM_BORROWERS:-50}"
BORROWER_POOL_SIZE="${MIP_LOAD_TEST_BORROWER_POOL_SIZE:-50}"
BASELINE_PATH="${MIP_LOAD_TEST_BASELINE:-${SCRIPT_DIR}/baseline.json}"
WRITE_BASELINE="${MIP_LOAD_TEST_WRITE_BASELINE:-0}"
FAIL_ON_BASELINE_REGRESSION="${MIP_LOAD_TEST_FAIL_ON_BASELINE_REGRESSION:-0}"
PYTHON_BIN="${REPO_ROOT}/.venv/bin/python"
if [[ ! -x "${PYTHON_BIN}" ]]; then
    PYTHON_BIN="python3"
fi

# Pick the first `locust` on PATH, or fall back to the repo venv if
# present. Keeps the script working for contributors who source the
# repo venv and for CI environments that rely on a system pip.
LOCUST_BIN="$(command -v locust || true)"
if [[ -z "${LOCUST_BIN}" && -x "${REPO_ROOT}/.venv/bin/locust" ]]; then
    LOCUST_BIN="${REPO_ROOT}/.venv/bin/locust"
fi
if [[ -z "${LOCUST_BIN}" ]]; then
    echo "error: locust not installed. run 'pip install locust' first." >&2
    exit 127
fi

echo "load-test profile:"
echo "  target    : ${MIP_API_URL}"
echo "  api       : ${MIP_API_PREFIX}"
echo "  users     : ${USERS}"
echo "  spawn     : ${SPAWN_RATE}/s"
echo "  duration  : ${RUN_TIME}"
echo "  writes    : ${WRITE_ENABLED} (set MIP_LOAD_TEST_WRITE=1 to opt in)"
echo "  warmup    : $([[ "${SKIP_WARMUP}" =~ ^(1|true|TRUE|yes|YES|on|ON)$ ]] && echo "off" || echo "on, ${WARM_BORROWERS} borrowers per lead key")"
echo "  borrower pool: ${BORROWER_POOL_SIZE}"
echo "  results   : ${OUT_PREFIX}_*.csv (+ .html)"
echo "  baseline  : ${BASELINE_PATH}"
echo

if [[ ! "${SKIP_WARMUP}" =~ ^(1|true|TRUE|yes|YES|on|ON)$ ]]; then
    "${PYTHON_BIN}" - "${MIP_API_URL}" "${MIP_API_PREFIX}" "${WARM_BORROWERS}" <<'PY'
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

base_url, api_prefix, warm_borrowers_raw = sys.argv[1:4]
base_url = base_url.rstrip("/")
api_prefix = "/" + api_prefix.strip("/")
warm_borrowers = max(0, int(warm_borrowers_raw))
headers = {"Accept": "application/json"}
bearer = os.environ.get("MIP_BEARER_TOKEN", "").strip()
if bearer:
    headers["Authorization"] = f"Bearer {bearer}"


def request(method: str, path: str, *, payload: dict[str, object] | None = None) -> object:
    url = f"{base_url}{api_prefix}/{path.lstrip('/')}"
    body = None
    req_headers = dict(headers)
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        req_headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        snippet = exc.read(500).decode("utf-8", errors="replace")
        raise SystemExit(f"warmup failed: {method} {path} -> {exc.code}: {snippet}") from exc
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"warmup failed: {method} {path} returned non-JSON") from exc


segments = ["itm", "listed", "permit", "investor", "equity", "retention"]
print("warming read caches before measured load...")
request("GET", "health")
request("GET", "segments")
request("POST", "portfolio/preview", payload={})
borrower_ids: list[str] = []
for segment in [None, *segments]:
    suffix = "leads" if segment is None else f"leads?{urllib.parse.urlencode({'segment': segment})}"
    rows = request("GET", suffix)
    if not isinstance(rows, list):
        raise SystemExit(f"warmup failed: GET {suffix} returned {type(rows).__name__}")
    for row in rows[:warm_borrowers]:
        if isinstance(row, dict) and isinstance(row.get("borrower_id"), str):
            borrower_id = row["borrower_id"]
            if borrower_id not in borrower_ids:
                borrower_ids.append(borrower_id)
for borrower_id in borrower_ids:
    request("GET", f"borrowers/{urllib.parse.quote(borrower_id, safe='')}")
print(
    "warmup complete: "
    f"{len(borrower_ids)} borrower dossiers, "
    f"{len(segments) + 1} lead keys"
)
PY
    echo
fi

# --csv writes four files: _stats.csv, _stats_history.csv,
# _failures.csv, _exceptions.csv. --html writes a single-file visual
# report that's easy to attach to a PR.
"${LOCUST_BIN}" \
    -f "${SCRIPT_DIR}/locustfile.py" \
    --headless \
    --host "${MIP_API_URL}" \
    --users "${USERS}" \
    --spawn-rate "${SPAWN_RATE}" \
    --run-time "${RUN_TIME}" \
    --csv "${OUT_PREFIX}" \
    --html "${OUT_PREFIX}.html" \
    --only-summary \
    --loglevel WARNING

# Post-run: dump a compact per-endpoint latency table. The
# _stats.csv Locust writes has p50/p95/p99 columns already; just
# pretty-print them so operators don't need to open the file.
${PYTHON_BIN} - <<PY "${OUT_PREFIX}_stats.csv"
import csv, sys
path = sys.argv[1]
print()
print("endpoint latency summary (ms):")
print(f"  {'endpoint':<30} {'p50':>8} {'p95':>8} {'p99':>8} {'rps':>8} {'fail%':>8}")
with open(path, newline='') as fh:
    for row in csv.DictReader(fh):
        name = row.get('Name') or row.get('name') or ''
        if name == 'Aggregated':
            continue
        method = row.get('Type') or row.get('Method') or ''
        key = f"{method} {name}".strip()
        try:
            p50 = float(row.get('50%') or row.get('Median Response Time') or 0)
            p95 = float(row.get('95%') or 0)
            p99 = float(row.get('99%') or 0)
            rps = float(row.get('Requests/s') or 0)
            total = float(row.get('Request Count') or 0)
            fails = float(row.get('Failure Count') or 0)
            failp = (fails / total * 100.0) if total else 0.0
        except ValueError:
            continue
        print(f"  {key:<30} {p50:>8.0f} {p95:>8.0f} {p99:>8.0f} {rps:>8.1f} {failp:>7.1f}%")
PY

BASELINE_ARGS=(
    "${OUT_PREFIX}_stats.csv"
    "--baseline" "${BASELINE_PATH}"
    "--target" "${MIP_API_URL}${MIP_API_PREFIX}"
)
if [[ "${WRITE_ENABLED}" =~ ^(1|true|TRUE|yes|YES|on|ON)$ ]]; then
    BASELINE_ARGS+=("--write-enabled")
fi
if [[ "${WRITE_BASELINE}" =~ ^(1|true|TRUE|yes|YES|on|ON)$ ]]; then
    BASELINE_ARGS+=("--write-baseline")
fi
if [[ "${FAIL_ON_BASELINE_REGRESSION}" =~ ^(1|true|TRUE|yes|YES|on|ON)$ ]]; then
    BASELINE_ARGS+=("--fail-on-regression")
fi

"${PYTHON_BIN}" "${SCRIPT_DIR}/baseline.py" "${BASELINE_ARGS[@]}"

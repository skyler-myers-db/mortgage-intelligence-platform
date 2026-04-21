#!/usr/bin/env bash
#
# Run the Module 0 load-test harness headlessly for 2 minutes at 20
# concurrent users. Prints p50/p95/p99 per endpoint to the terminal
# and writes a timestamped CSV (and HTML report) to
# tools/load_test/results/.
#
#   MIP_API_URL=http://localhost:8000 bash tools/load_test/run.sh
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
USERS="${MIP_USERS:-20}"
SPAWN_RATE="${MIP_SPAWN_RATE:-5}"
RUN_TIME="${MIP_RUN_TIME:-2m}"

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
echo "  users     : ${USERS}"
echo "  spawn     : ${SPAWN_RATE}/s"
echo "  duration  : ${RUN_TIME}"
echo "  results   : ${OUT_PREFIX}_*.csv (+ .html)"
echo

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
python3 - <<PY "${OUT_PREFIX}_stats.csv"
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

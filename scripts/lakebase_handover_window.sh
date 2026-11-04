#!/usr/bin/env bash
# Owner-run WR5 (M2-PR8): the Lakebase ownership handover's announced window.
#
#   ./scripts/lakebase_handover_window.sh --journal <exhausted handover journal>
#   MIP_ACK_QUIESCE_WINDOW=<window sha256> ./scripts/lakebase_handover_window.sh --journal <path>
#
# The first run plans the window read-only, prints its sha256 and refuses with
# the App untouched. Announce the window, then re-run with the digest exported
# for that one run (never in .env.local). Everything that stops or starts the
# App is the window library's (scripts/lib/deploy_quiesce_window_lifecycle.sh,
# run_lakebase_handover_window, under tools/databricks/stop_primitive_map.py);
# this file only sets up the shell it needs and the EXIT trap that starts the
# App again when the window fails with it stopped. docs/deployment.md,
# "Ownership handover", has the whole sequence.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
PYTHON="${PYTHON:-python3}"
RED=$'\033[31m'
GRN=$'\033[32m'
RST=$'\033[0m'
step() { echo "==> $*"; }

WR5_JOURNAL=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --journal) WR5_JOURNAL="${2:?--journal needs a path}"; shift 2 ;;
    *) echo "[wr5] unknown argument: $1" >&2; exit 2 ;;
  esac
done
if [[ -z "$WR5_JOURNAL" ]]; then
  echo "[wr5] --journal <exhausted Lakebase handover journal> is required" >&2
  exit 2
fi

# shellcheck source=scripts/lib/deploy_quiesce_window_lifecycle.sh
. "$REPO_ROOT/scripts/lib/deploy_quiesce_window_lifecycle.sh"

# The App, rollback scope and instance names from the client profile.
WR5_CONTEXT="$("$PYTHON" -m tools.clients.lakebase_handover --window-context)"
eval "$WR5_CONTEXT"
export WR5_APP_NAME WR5_SCOPE WR5_LAKEBASE_INSTANCE WR5_JOURNAL
WR5_WINDOW_STOPPED=0

trap 'compensate_lakebase_handover_window' EXIT
rc=0
run_lakebase_handover_window || rc=$?
if [[ "$rc" -eq 0 ]]; then
  echo "${GRN}[wr5] the window closed: the handover's remainder applied and the App serves the deployment the start created.${RST}"
fi
exit "$rc"

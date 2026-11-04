"""``--phase data`` in shell, driven with stubs (M1-PR6, attack O11).

``run_data_phase`` (scripts/lib/deploy_governed_phases_lifecycle.sh) runs each
data job through the runner, then the reviewed-function EXECUTE reconcile and
its read-only verify after every job whose graph touches gold, then the data
smoke and the ledger close. A failure of the reconcile or of the verify refuses
success; a run still going at the deadline exits 4 (the next phase
re-attaches); a failed run exits 1 and reruns only that job next time. The real
reconcile function (deploy_reviewed_function_grants_lifecycle.sh) is the one
the release runs.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PHASES = REPO / "scripts" / "lib" / "deploy_governed_phases_lifecycle.sh"
GRANTS = REPO / "scripts" / "lib" / "deploy_reviewed_function_grants_lifecycle.sh"


def _drive(tmp_path: Path, **env: str) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    calls = tmp_path / "calls.log"
    script = f"""set -euo pipefail
DRY_RUN=0
SKIP_SILVER="${{SKIP_SILVER:-0}}"
PYTHON=py_stub
_GRANTS_APP_NAME=mip-app
_GRANTS_WAREHOUSE_ID=warehouse-1
MIP_APP_DEPLOYMENT_LEASE_ID=lease-PLACEHOLDER
SOURCE_GIT_SHA={"c" * 40}
DATA_PHASE_PLAN_JSON=/tmp/plan.json
DATABRICKS_AGENT_RUNTIME_CLIENT_ID=runtime
DATABRICKS_AGENT_PROXY_CLIENT_ID=proxy
RED="" YLW="" GRN="" RST=""
log() {{ printf '%s\\n' "$*" >> {str(calls)!r}; }}
step() {{ :; }}
tier_is_agentic() {{ [[ "${{TIER:-on}}" == on ]]; }}
run() {{ "$@"; }}
run_with_proof_signing_authority() {{ "$@"; }}
databricks() {{ log "databricks $*"; echo '{{"service_principal_client_id": "app-client"}}'; }}
py_stub() {{
  if [[ "$1" == "-c" ]]; then {sys.executable!r} "$@"; return; fi
  case "$2" in
    tools.databricks.job_phase_runner)
      local job="" out=""
      while [[ $# -gt 0 ]]; do
        case "$1" in --job-key) job="$2"; shift ;; --out-env) out="$2"; shift ;; esac
        shift
      done
      if [[ -n "$job" ]]; then
        log "runner run $job"
        [[ "${{NO_FLAG:-0}}" == 1 ]] || printf 'JOB_PHASE_TOUCHES_GOLD=%s\\n' "${{TOUCHES:-1}}" > "$out"
        return "${{RUN_RC:-0}}"
      fi
      log "runner other"
      ;;
    tools.databricks.verify_reviewed_function_execute_grants) log verify; return "${{VERIFY_RC:-0}}" ;;
    tools.databricks.verify_market_rate_alignment) log smoke ;;
    *) log "python $2" ;;
  esac
}}
. {str(GRANTS)!r}
# The statement itself is a warehouse call; the real reconcile around it runs as released.
apply_uc_grant() {{ log grant; return "${{GRANT_RC:-0}}"; }}
. {str(PHASES)!r}
run_data_phase
echo NOT-REACHED
"""
    result = subprocess.run(
        ["bash", "-c", script], text=True, capture_output=True, env={"PATH": os.environ["PATH"], "TMPDIR": str(tmp_path), **env}, check=False
    )
    return result, calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []


def _without_grants(calls: list[str]) -> list[str]:
    return [call for call in calls if call != "grant"]


def test_each_gold_touching_job_is_followed_by_the_reconcile_and_its_verify(tmp_path: Path) -> None:
    result, calls = _drive(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "NOT-REACHED" not in result.stdout and "the App was not touched" in result.stdout
    assert _without_grants(calls) == [
        "databricks apps get mip-app -o json",
        "runner other",
        "runner run mip_refresh_silver",
        "verify",
        "runner run mip_scheduled_refresh",
        "verify",
        "smoke",
        "runner other",
    ]
    assert calls.count("grant") == 2 * 3 * 3, "three functions to the App, the runtime and the proxy, twice"
    assert not any(call.startswith("databricks") and "apps get" not in call for call in calls)


@pytest.mark.parametrize(("failure", "env"), (("grant", {"GRANT_RC": "4"}), ("verify", {"VERIFY_RC": "1"})))
def test_a_failed_reconcile_or_verify_refuses_success(tmp_path: Path, failure: str, env: dict[str, str]) -> None:
    result, calls = _drive(tmp_path, **env)
    assert result.returncode == 4, result.stdout + result.stderr
    assert "the reviewed function EXECUTE grants are unproven; the data phase refuses success" in result.stderr
    assert "runner run mip_scheduled_refresh" not in calls, "nothing runs after the refusal"
    assert "smoke" not in calls and calls.count("runner other") == 1, "no smoke and no ledger close"


@pytest.mark.parametrize(("rc", "message"), (("4", "re-run --phase data to re-attach"), ("1", "rerun only the failed job")))
def test_a_job_still_running_or_failed_ends_the_phase_without_a_reconcile(tmp_path: Path, rc: str, message: str) -> None:
    result, calls = _drive(tmp_path, RUN_RC=rc)
    assert result.returncode == int(rc)
    assert message in result.stderr
    assert "verify" not in calls and "smoke" not in calls


def test_a_gold_free_job_skips_the_reconcile_and_a_missing_flag_fails_closed(tmp_path: Path) -> None:
    free, free_calls = _drive(tmp_path / "free", TOUCHES="0", SKIP_SILVER="1")
    assert free.returncode == 0 and "verify" not in free_calls
    unflagged, calls = _drive(tmp_path / "unflagged", NO_FLAG="1", SKIP_SILVER="1")
    assert unflagged.returncode == 0 and calls.count("verify") == 1


def test_the_core_tier_reconciles_the_apps_grants_only(tmp_path: Path) -> None:
    result, calls = _drive(tmp_path, TIER="off", SKIP_SILVER="1")
    assert result.returncode == 0 and calls.count("grant") == 3


def test_the_data_phase_runs_under_its_lease_and_ends_before_anything_arms_an_app_stop() -> None:
    from tests.fixtures.deploy_script import deploy_entrypoint_text

    text = deploy_entrypoint_text()
    steps = text.index('step "preflight — check .env.local, databricks CLI, venv"')
    plan = text.index("\n  plan_data_phase\n", steps)
    gate = text.index("\n  run_bundle_plan_gate pre-lease\n", steps)
    lease = text.index("tools.databricks.app_deployment_lease acquire", steps)
    heartbeat = text.index("tools.databricks.app_deployment_lease heartbeat", steps)
    run = text.index("\n    run_data_phase\n", steps)
    # Read-only before the lease, in place of the probes and the plan gate; run
    # under the lease right after its heartbeat; nothing after it arms a stop.
    assert plan < gate < lease < heartbeat < run
    assert text.index("APP_FAIL_CLOSED_ARMED=1", steps) > run
    for later in ("app_rollback_secret_scope ensure", "databricks apps list -o json", "converge_lakebase_oauth_role"):
        assert text.index(later, lease) > run, later
    body = PHASES.read_text(encoding="utf-8")
    start = body.index("run_data_phase() {")
    run_body = body[start : body.index("\n}\n", start) + 3]
    assert run_body.endswith("  exit 0\n}\n") and "APP_FAIL_CLOSED_ARMED" not in run_body

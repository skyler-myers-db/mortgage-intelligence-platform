"""``--phase data`` in shell, driven with stubs (M1-PR6, attack O11).

``run_data_phase`` (scripts/lib/deploy_governed_phases_lifecycle.sh) reads the
App's service principal pinned to its signed rollback record, runs each data
job through the runner, then the reviewed-function EXECUTE reconcile and its
read-only verify after every job whose graph touches gold, whatever the job's
outcome but a run still going, then the data smoke and the ledger close. A
failure of the reconcile or of the verify refuses success; a run still going
at the deadline exits 4 with no reconcile (the next phase re-attaches); a
failed or refused run is repaired, then exits with its own code and reruns only
that job next time. The real reconcile function
(deploy_reviewed_function_grants_lifecycle.sh) is the one the release runs.
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
APP_ROLLBACK_SECRET_SCOPE=mip-app-rollback
MIP_LAKEBASE_INSTANCE=mip-app-state
DATABRICKS_ACCOUNT_CLIENT_ID=account-client
DATABRICKS_AGENT_RUNTIME_CLIENT_ID=runtime
DATABRICKS_AGENT_PROXY_CLIENT_ID=proxy
RED="" YLW="" GRN="" RST=""
log() {{ printf '%s\\n' "$*" >> {str(calls)!r}; }}
step() {{ :; }}
tier_is_agentic() {{ [[ "${{TIER:-on}}" == on ]]; }}
run() {{ "$@"; }}
run_with_proof_signing_authority() {{ "$@"; }}
same_identity_casefold() {{ [[ -n "$1" && "${{1,,}}" == "${{2,,}}" ]]; }}
databricks() {{ log "databricks $*"; return 97; }}
py_stub() {{
  if [[ "$1" == "-c" ]]; then {sys.executable!r} "$@"; return; fi
  case "$2" in
    tools.databricks.job_phase_runner)
      local job="" out="" all="${{*:3}}"
      while [[ $# -gt 0 ]]; do
        case "$1" in --job-key) job="$2"; shift ;; --out-env) out="$2"; shift ;; esac
        shift
      done
      if [[ -n "$job" ]]; then
        log "runner run $job"
        [[ "${{NO_FLAG:-0}}" == 1 ]] || printf 'JOB_PHASE_TOUCHES_GOLD=%s\\n' "${{TOUCHES:-1}}" > "$out"
        return "${{RUN_RC:-0}}"
      fi
      log "runner other $all"
      ;;
    tools.databricks.data_phase_app_identity) log identity; echo "${{APP_IDENTITY-app-client}}"; return "${{IDENTITY_RC:-0}}" ;;
    tools.databricks.verify_reviewed_function_execute_grants) log verify; return "${{VERIFY_RC:-0}}" ;;
    tools.databricks.verify_market_rate_alignment) log smoke; return "${{SMOKE_RC:-0}}" ;;
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
    return [call.split()[0] if call.startswith("runner other") else call for call in calls if call != "grant"]


def _runner_modes(calls: list[str]) -> list[str]:
    return [call.split()[2] for call in calls if call.startswith("runner other")]


def test_each_gold_touching_job_is_followed_by_the_reconcile_and_its_verify(tmp_path: Path) -> None:
    result, calls = _drive(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "NOT-REACHED" not in result.stdout and "the App was not touched" in result.stdout
    assert _without_grants(calls) == [
        "identity",
        "runner",
        "runner run mip_refresh_silver",
        "verify",
        "runner run mip_scheduled_refresh",
        "verify",
        "smoke",
        "runner",
    ]
    assert _runner_modes(calls) == ["begin", "close"]
    assert calls.count("grant") == 2 * 3 * 3, "three functions to the App, the runtime and the proxy, twice"
    assert not any(call.startswith("databricks") for call in calls), "no CLI call: no unpinned apps get"


@pytest.mark.parametrize(("failure", "env"), (("grant", {"GRANT_RC": "4"}), ("verify", {"VERIFY_RC": "1"})))
def test_a_failed_reconcile_or_verify_refuses_success(tmp_path: Path, failure: str, env: dict[str, str]) -> None:
    result, calls = _drive(tmp_path, **env)
    assert result.returncode == 4, result.stdout + result.stderr
    assert "the reviewed function EXECUTE grants are unproven; the data phase refuses success" in result.stderr
    assert "runner run mip_scheduled_refresh" not in calls, "nothing runs after the refusal"
    assert "smoke" not in calls and _runner_modes(calls) == ["begin"], "no smoke and no ledger close"


def test_a_job_still_running_ends_the_phase_without_a_reconcile(tmp_path: Path) -> None:
    result, calls = _drive(tmp_path, RUN_RC="4")
    assert result.returncode == 4
    assert "re-run --phase data to re-attach" in result.stderr
    assert "grant" not in calls and "verify" not in calls and "smoke" not in calls


@pytest.mark.parametrize("rc", ("1", "2"))
def test_a_failed_or_refused_job_is_repaired_then_ends_the_phase_with_its_own_code(tmp_path: Path, rc: str) -> None:
    # As the release's governed job boundary does: the reconcile runs whatever the job did.
    result, calls = _drive(tmp_path, RUN_RC=rc)
    assert result.returncode == int(rc), result.stderr
    assert "rerun only the failed job" in result.stderr
    assert calls.count("grant") == 3 * 3, "three functions to the App, the runtime and the proxy"
    assert _without_grants(calls)[-2:] == ["runner run mip_refresh_silver", "verify"]
    assert "smoke" not in calls and _runner_modes(calls) == ["begin"]
    both, both_calls = _drive(tmp_path / "both", RUN_RC=rc, VERIFY_RC="1")
    assert both.returncode == int(rc), "the job's own code, not the repair's"
    assert "and the reviewed function grant repair both failed" in both.stderr
    free, free_calls = _drive(tmp_path / "free", RUN_RC=rc, TOUCHES="0", SKIP_SILVER="1")
    assert free.returncode == int(rc) and "verify" not in free_calls, "a gold-free job needs no repair"


def test_a_failed_smoke_keeps_the_generation_open_and_names_the_rerun_ack(tmp_path: Path) -> None:
    result, calls = _drive(tmp_path, SMOKE_RC="1")
    assert result.returncode == 1
    assert "MIP_ACK_DATA_LEDGER_RERUN" in result.stderr
    assert _runner_modes(calls) == ["begin"], "no close"


@pytest.mark.parametrize(
    ("env", "message"),
    (
        ({"IDENTITY_RC": "2"}, "not the one its signed rollback record pins"),
        ({"APP_IDENTITY": ""}, "not the one its signed rollback record pins"),
        ({"APP_IDENTITY": "ACCOUNT-CLIENT"}, "must be distinct from the App service principal"),
    ),
)
def test_the_app_identity_is_pinned_and_separated_before_anything_runs(tmp_path: Path, env: dict[str, str], message: str) -> None:
    result, calls = _drive(tmp_path, **env)
    assert result.returncode == 2 and message in result.stderr
    assert calls == ["identity"], "no ledger, no job and no grant"


def test_the_rerun_ack_reaches_begin_only_as_a_64_hex_digest(tmp_path: Path) -> None:
    digest = "a" * 64
    result, calls = _drive(tmp_path, MIP_ACK_DATA_LEDGER_RERUN=digest, MIP_ACK_DATA_LEDGER_SUPERSEDE="b" * 64)
    assert result.returncode == 0, result.stderr
    begin = next(call for call in calls if call.startswith("runner other begin"))
    assert f"--ack-supersede {'b' * 64}" in begin and f"--ack-rerun {digest}" in begin
    bad, bad_calls = _drive(tmp_path / "bad", MIP_ACK_DATA_LEDGER_RERUN="not-a-digest")
    assert bad.returncode == 2 and "must be the 64-hex sha256" in bad.stderr
    assert _runner_modes(bad_calls) == []


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


def test_a_release_or_a_run_with_no_phase_reads_the_ledger_right_after_its_lease() -> None:
    # A data phase that exited 4 released the lease with its job still running.
    from tests.fixtures.deploy_script import deploy_entrypoint_text

    text = deploy_entrypoint_text()
    steps = text.index('step "preflight — check .env.local, databricks CLI, venv"')
    heartbeat = text.index("tools.databricks.app_deployment_lease heartbeat", steps)
    run = text.index("\n    run_data_phase\n", steps)
    quiet = text.index("\n  refuse_while_a_data_job_runs\n", steps)
    assert heartbeat < run < quiet < text.index("app_rollback_secret_scope ensure", steps)
    assert text.count("\n  refuse_while_a_data_job_runs\n") == 1


@pytest.mark.parametrize(("dry_run", "quiet_rc", "rc", "called"), ((0, 0, 0, True), (0, 2, 2, True), (1, 2, 0, False)))
def test_refuse_while_a_data_job_runs(tmp_path: Path, dry_run: int, quiet_rc: int, rc: int, called: bool) -> None:
    calls = tmp_path / "calls.log"
    script = f"""set -euo pipefail
DRY_RUN={dry_run}
PYTHON=py_stub
_GRANTS_APP_NAME=mip-app
RED="" RST=""
py_stub() {{ printf '%s\\n' "$*" >> {str(calls)!r}; return {quiet_rc}; }}
. {str(PHASES)!r}
refuse_while_a_data_job_runs
echo CONTINUED
"""
    result = subprocess.run(["bash", "-c", script], text=True, capture_output=True, check=False)
    assert result.returncode == rc, result.stderr
    logged = calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []
    assert logged == (["-m tools.databricks.job_phase_runner quiet --app-name mip-app"] if called else [])
    assert ("CONTINUED" in result.stdout) is (rc == 0)
    if rc:
        assert "a data-phase job is still running" in result.stderr

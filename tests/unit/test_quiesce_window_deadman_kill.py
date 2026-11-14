"""A runner SIGKILLed inside the window leaves the App restarted by the dead-man (M0-PR9, T5).

The deploy shell is M0-PR8's harness (``tests/unit/test_quiesce_window_shell.py``):
the real window and EXIT-trap libraries, the EXIT trap installed. Its
``$PYTHON`` is ``tests/fixtures/quiesce_window_sandbox.py``, which runs the
REAL tools against a fake estate pickled between processes. The run opens a
WR3 window (budget admitted, release published, dead-man armed, journal
``window_open``, App stopped) and is then killed with SIGKILL, which no trap
sees. The fake Jobs service then runs the submitted dead-man from the bytes
the window published, at its deadline: the App returns to ACTIVE on the
deployment its start created and the marker reads ``deadman_restarted``. The
next run, under its own lease, re-pins that deployment with
``repin --from-deadman`` and closes the journal.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import time
from pathlib import Path

from tests.fixtures import quiesce_window_sandbox as sandbox
from tests.fixtures.quiesce_window_estate import (
    APP_NAME,
    BLUE_ID,
    CLIENT_KEY,
    DEPLOYER,
    GREEN_COMMIT,
    LEASE_ID,
    green_release,
    run_deadman,
    stored_record,
)
from tests.unit.test_app_deployment_rollback import SIGNING_KEY
from tests.unit.test_quiesce_window_shell import LIB, PRELUDE
from tests.unit.test_quiesce_window_start_repin import (  # noqa: F401 - the autouse env
    SCOPE,
    _env,
    _estate,
    _plan,
)
from tools.databricks import quiesce_window_deadman as deadman
from tools.databricks import quiesce_window_journal as journal

REPO = Path(__file__).resolve().parents[2]
NEXT_LEASE = "33333333-3333-4333-8333-333333333333"


def _shell(
    tmp_path: Path, body: str, env: dict[str, str], *, timeout: int = 180
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    from backend.services.ai_gateway_proof_attestation import derive_gateway_proof_verify_key

    calls = tmp_path / "calls.log"
    calls.write_text("", encoding="utf-8")
    python = sandbox.wrapper(tmp_path / "sandbox-python", REPO)
    script = tmp_path / f"harness-{len(list(tmp_path.glob('harness-*')))}.sh"
    script.write_text(
        PRELUDE
        + f"PYTHON={shlex.quote(str(python))}\n"
        + f". {shlex.quote(str(LIB / 'deploy_quiesce_window_lifecycle.sh'))}\n"
        + f". {shlex.quote(str(LIB / 'deploy_app_release_lifecycle.sh'))}\n"
        + 'restore_signed_blue_while_quiesced() { echo "legacy-restore" >> "$CALLS"; }\n'
        + 'stop_and_quiesce_unproven_app() { echo "legacy-stop" >> "$CALLS"; }\n'
        + body,
        encoding="utf-8",
    )
    result = subprocess.run(
        ["bash", str(script)],
        env={
            **{k: v for k, v in os.environ.items() if k in {"PATH", "HOME"}},
            "TMPDIR": str(tmp_path),
            "CALLS": str(calls),
            "MIP_SANDBOX_ESTATE": str(tmp_path / "estate.pickle"),
            "MIP_AI_GATEWAY_PROOF_SIGNING_KEY": SIGNING_KEY,
            "MIP_AI_GATEWAY_PROOF_VERIFY_KEY": derive_gateway_proof_verify_key(SIGNING_KEY),
            "MIP_LAKEBASE_INSTANCE": "mip-app-state",
            "LAKEBASE_INSTANCE_NAME": "mip-app-state",
            **env,
        },
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    return result, [line for line in calls.read_text(encoding="utf-8").splitlines() if line]


class Clock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def test_a_sigkilled_runner_leaves_the_app_restarted_and_the_next_run_re_pins_it(tmp_path: Path) -> None:
    estate = _estate()
    release = green_release(tmp_path / "release", extra={"requirements.txt": b"# deps-digest\n"})
    plan = _plan(estate)
    (tmp_path / "plan.json").write_text(json.dumps(plan.full()), encoding="utf-8")
    sandbox.save(estate, tmp_path / "estate.pickle")
    deadline = int(time.time()) + 3 * 3600
    window = f"""
QUIESCE_WINDOW_STATE=blue_serving; QUIESCE_WINDOW_REASONS=WR3; APP_UPGRADE_STATE=blue_quiesced
QUIESCE_WINDOW_PLAN_JSON={shlex.quote(str(tmp_path / 'plan.json'))}
MIP_JOB_DEADLINE_EPOCH={deadline}; MIP_APP_DEPLOYMENT_LEASE_ID={LEASE_ID}; SOURCE_GIT_SHA={GREEN_COMMIT}
APP_RELEASE_ID={release.manifest.release_id}; APP_RELEASE_STAGE_ROOT={shlex.quote(str(tmp_path / 'release'))}
APP_RELEASE_MANIFEST={shlex.quote(str(tmp_path / 'release' / 'manifest.json'))}
CLIENT_DEPLOYER={DEPLOYER}; CLIENT_KEY={CLIENT_KEY}
_EXISTING_APP_ID=app-object-id; _EXISTING_APP_SP_CLIENT_ID=app-client; _EXISTING_APP_SP_SCIM_ID=app-scim-id
trap stop_app_after_failed_deploy EXIT
ensure_quiesce_window_open_for WR3
echo "stopped-and-armed run=$QUIESCE_WINDOW_DEADMAN_RUN_ID"
kill -KILL $$
echo unreachable
"""
    killed, calls = _shell(tmp_path, window, {"MIP_ACK_QUIESCE_WINDOW": plan.sha256})

    assert killed.returncode == -9, killed.stdout + killed.stderr
    assert "stopped-and-armed run=9001" in killed.stdout and "unreachable" not in killed.stdout
    assert [call.split()[1:3] for call in calls] == [
        ["tools.databricks.quiesce_window", "open"],
        # The run's own deadline watchdog (a no-op in the sandbox): armed after the open.
        ["tools.databricks.quiesce_window_watchdog", "--check"],
        ["tools.databricks.quiesce_window_watchdog", "--run-pid"],
    ]
    after_kill = sandbox.load(tmp_path / "estate.pickle")
    assert (after_kill.apps.stops, after_kill.apps.starts, after_kill.apps.compute) == (1, 0, "STOPPED")
    assert journal.read(after_kill.workspace, app_name=APP_NAME)["state"] == "window_open"
    run = after_kill.jobs.only_run()
    assert after_kill.jobs.cancels == [], "no trap ran: the dead-man stays armed"
    assert run.tasks[0].spark_python_task.python_file == f"{release.release_path}/jobs/window_deadman.py"

    # The Jobs service runs the dead-man at its deadline, from the published file.
    clock = Clock(float(deadline - 60))
    assert run_deadman(after_kill, run.run_id, clock=clock, sleep=clock.sleep) == 0
    assert after_kill.apps.compute == "ACTIVE" and after_kill.apps.active_id == "deployment-start-1"
    token = deadman.window_token(plan.sha256, LEASE_ID)
    marker = json.loads(after_kill.files.data[deadman.marker_path(APP_NAME, token)])
    assert marker["state"] == "deadman_restarted" and marker["start_deployment_id"] == "deployment-start-1"
    assert clock.now <= deadline + deadman.DEADMAN_START_BUDGET_S, "restarted by deadline + start budget"
    sandbox.save(after_kill, tmp_path / "estate.pickle")

    # The next run, under its own lease: re-pin with the window's proofs.
    recovered, calls = _shell(
        tmp_path,
        f"MIP_APP_DEPLOYMENT_LEASE_ID={NEXT_LEASE}; SOURCE_GIT_SHA={GREEN_COMMIT}; APP_ROLLBACK_SECRET_SCOPE={SCOPE}\n"
        "MIP_LAKEBASE_INSTANCE=mip-app-state\n"
        "export MIP_BEARER_TOKEN=token\nrecover_deadman_quiesce_window\necho recovered\n",
        {},
    )
    assert recovered.returncode == 0, recovered.stdout + recovered.stderr
    assert "recovered" in recovered.stdout
    repin = next(call for call in calls if "app_deployment_rollback repin" in call)
    assert "repin --from-deadman" in repin and f"--deployment-lease-id {NEXT_LEASE}" in repin
    final = sandbox.load(tmp_path / "estate.pickle")
    record = stored_record(final)
    assert record["deployment_id"] == "deployment-start-1" and record["predecessor_deployment_id"] == BLUE_ID
    closed = journal.read(final.workspace, app_name=APP_NAME)
    assert closed["state"] == "closed" and closed["closed_by"] == "deadman_repin"
    assert (final.apps.stops, final.apps.starts, final.apps.deploys) == (1, 1, 0)


def test_a_contract_window_killed_mid_way_stays_stopped_and_is_never_re_pinned(tmp_path: Path) -> None:
    from tests.unit.test_quiesce_window_start_repin import MIGRATION_APPLY

    estate = _estate()
    release = green_release(tmp_path / "release", extra={"requirements.txt": b"# deps-digest\n"})
    contract_plan = {**MIGRATION_APPLY, "blue_restorable": False, "migrations": {"pending": ["0042"], "pending_contract": ["0042"]}}
    plan = _plan(estate, migration=contract_plan)
    assert plan.blue_restorable is False
    (tmp_path / "plan.json").write_text(json.dumps(plan.full()), encoding="utf-8")
    sandbox.save(estate, tmp_path / "estate.pickle")
    deadline = int(time.time()) + 3 * 3600
    killed, _calls = _shell(
        tmp_path,
        f"""
QUIESCE_WINDOW_STATE=blue_serving; QUIESCE_WINDOW_REASONS=WR3
QUIESCE_WINDOW_PLAN_JSON={shlex.quote(str(tmp_path / 'plan.json'))}
MIP_JOB_DEADLINE_EPOCH={deadline}; MIP_APP_DEPLOYMENT_LEASE_ID={LEASE_ID}; SOURCE_GIT_SHA={GREEN_COMMIT}
APP_RELEASE_ID={release.manifest.release_id}; APP_RELEASE_STAGE_ROOT={shlex.quote(str(tmp_path / 'release'))}
APP_RELEASE_MANIFEST={shlex.quote(str(tmp_path / 'release' / 'manifest.json'))}
CLIENT_DEPLOYER={DEPLOYER}; CLIENT_KEY={CLIENT_KEY}
_EXISTING_APP_ID=app-object-id; _EXISTING_APP_SP_CLIENT_ID=app-client; _EXISTING_APP_SP_SCIM_ID=app-scim-id
ensure_quiesce_window_open_for WR3
kill -KILL $$
""",
        {"MIP_ACK_QUIESCE_WINDOW": plan.sha256},
    )
    assert killed.returncode == -9, killed.stdout + killed.stderr
    after_kill = sandbox.load(tmp_path / "estate.pickle")
    run = after_kill.jobs.only_run()
    clock = Clock(float(deadline - 60))
    assert run_deadman(after_kill, run.run_id, clock=clock, sleep=clock.sleep) == 0
    assert (after_kill.apps.starts, after_kill.apps.compute) == (0, "STOPPED"), "a contract window never starts blue"
    token = deadman.window_token(plan.sha256, LEASE_ID)
    assert json.loads(after_kill.files.data[deadman.marker_path(APP_NAME, token)])["state"] == "deadman_left_stopped"
    sandbox.save(after_kill, tmp_path / "estate.pickle")

    untouched, calls = _shell(tmp_path, "recover_deadman_quiesce_window\necho untouched\n", {})
    assert untouched.returncode == 0 and "untouched" in untouched.stdout
    assert not any("app_deployment_rollback repin" in call for call in calls)
    assert stored_record(sandbox.load(tmp_path / "estate.pickle"))["deployment_id"] == BLUE_ID

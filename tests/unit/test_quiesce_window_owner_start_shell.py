"""An owner's break-glass start, through the real window shell and the real tools (wave 7c).

The harness is the kill test's (``tests/unit/test_quiesce_window_deadman_kill.py``):
the real window and EXIT-trap libraries, with a ``$PYTHON`` that runs the REAL
``quiesce_window`` and rollback tools against a fake estate pickled between
processes (``tests/fixtures/quiesce_window_sandbox.py``). The fake Apps API is
the one spike S7 measured: a start's deployment shows only reads later.

Two stories, each end to end:

* the runner is SIGKILLed after the window's stop, the owner starts the App by
  hand, and the next run's step-0a recovery re-pins that start only with the
  owner's exact ``owner_start_repin`` ack in its environment;
* the owner starts the App while the run is still alive, and that start is
  still in flight (compute STARTING, its deployment not yet listed) when the
  run reaches its own close: the close refuses ``window_start_baseline_moved``,
  its EXIT trap refuses again, and neither starts nor stops anything. The
  owner's start comes up, keeps serving and is re-pinned by the next run like
  any other.
"""

from __future__ import annotations

import json
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
    DEPLOYER_CALLER,
    GREEN_COMMIT,
    LEASE_ID,
    OWNER_CALLER,
    Estate,
    green_release,
    stored_record,
)
from tests.unit.test_quiesce_window_deadman_kill import NEXT_LEASE
from tests.unit.test_quiesce_window_deadman_kill import _shell as _kill_test_shell
from tests.unit.test_quiesce_window_start_repin import (  # noqa: F401 - the autouse env
    SCOPE,
    _env,
    _estate,
    _plan,
)
from tools.databricks import app_start_deployment as starts
from tools.databricks import quiesce_window_journal as journal
from tools.databricks import quiesce_window_recovery as recovery

REPO = Path(__file__).resolve().parents[2]
NEXT_RUN = (
    f"MIP_APP_DEPLOYMENT_LEASE_ID={NEXT_LEASE}; SOURCE_GIT_SHA={GREEN_COMMIT}; APP_ROLLBACK_SECRET_SCOPE={SCOPE}\n"
    "MIP_LAKEBASE_INSTANCE=mip-app-state\n"
    "export MIP_BEARER_TOKEN=token\nrecover_deadman_quiesce_window\necho recovered\n"
)
# The owner's raw ``apps start`` from another process, while the deploy shell is alive (the
# sandbox's own ``-c`` passthrough, on the pickled estate). It is left IN FLIGHT: the compute
# is STARTING and the new deployment's id shows only reads later, as spike S7 measured.
OWNER_STARTS_NOW = (
    "from pathlib import Path; import os; "
    "from tests.fixtures import quiesce_window_sandbox as sandbox; "
    "from tests.fixtures.quiesce_window_estate import APP_NAME, OWNER_CALLER; "
    "path = Path(os.environ['MIP_SANDBOX_ESTATE']); estate = sandbox.load(path); "
    "estate.apps.caller = OWNER_CALLER; estate.apps.start(APP_NAME); sandbox.save(estate, path)"
)


def _shell(tmp_path: Path, body: str, env: dict[str, str]) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    # One shell here runs up to five real tools, each a fresh interpreter: a longer bound
    # than the kill test's, so a loaded runner is not read as a hang.
    return _kill_test_shell(tmp_path, body, env, timeout=600)


def _land(estate: Estate, baseline: frozenset[str]) -> str:
    """Time passes: the start in flight shows its deployment, and that deployment ends."""

    created = starts.find_start_deployment(estate.workspace, app_name=APP_NAME, baseline=baseline)
    starts.wait_for_terminal(estate.workspace, app_name=APP_NAME, deployment_id=created)
    estate.apps.caller = DEPLOYER_CALLER
    return created


def owner_start(estate: Estate) -> str:
    """The owner's raw ``apps start``, then time passes until its deployment is terminal."""

    estate.apps.caller = OWNER_CALLER
    before = starts.deployment_ids(estate.workspace, app_name=APP_NAME)
    estate.apps.start(APP_NAME)
    return _land(estate, before)


def _window(tmp_path: Path, tail: str) -> tuple[str, dict[str, str]]:
    """A deploy shell that opens a WR3 window over the pickled estate, then runs ``tail``."""

    estate = _estate()
    release = green_release(tmp_path / "release", extra={"requirements.txt": b"# deps-digest\n"})
    plan = _plan(estate)
    (tmp_path / "plan.json").write_text(json.dumps(plan.full()), encoding="utf-8")
    sandbox.save(estate, tmp_path / "estate.pickle")
    deadline = int(time.time()) + 3 * 3600
    script = f"""
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
{tail}
"""
    return script, {"MIP_ACK_QUIESCE_WINDOW": plan.sha256}


def _load(tmp_path: Path) -> Estate:
    return sandbox.load(tmp_path / "estate.pickle")


def _digest(estate: Estate) -> str:
    found = recovery.classify(
        estate.workspace, app_name=APP_NAME, current=journal.read(estate.workspace, app_name=APP_NAME)
    )
    assert found.kind == "owner_start", found.owner_start
    return found.sha256


def _apps(estate: Estate) -> tuple[int, int, int, int, str, str]:
    apps = estate.apps
    return (apps.starts, apps.stops, apps.deploys, apps.updates, apps.compute, apps.active_id)


def test_a_sigkilled_runner_then_the_owners_start_is_re_pinned_by_the_next_run_only_with_its_ack(
    tmp_path: Path,
) -> None:
    script, env = _window(tmp_path, "kill -KILL $$\necho unreachable\n")
    killed, _calls = _shell(tmp_path, script, env)
    assert killed.returncode == -9 and "stopped-and-armed run=9001" in killed.stdout, killed.stdout + killed.stderr
    estate = _load(tmp_path)
    opened = journal.read(estate.workspace, app_name=APP_NAME)
    assert opened["state"] == "window_open" and opened["start_baseline_ids"] == [BLUE_ID]
    assert (estate.apps.stops, estate.apps.starts, estate.apps.compute) == (1, 0, "STOPPED")

    started = owner_start(estate)  # the outage could not wait for the dead-man: the break-glass
    digest = _digest(estate)
    sandbox.save(estate, tmp_path / "estate.pickle")
    serving = _apps(estate)
    assert serving == (1, 1, 0, 0, "ACTIVE", started)

    # The next run, under its own lease, without the ack, then with another plan's.
    for given in ("", "0" * 64):
        refused, calls = _shell(tmp_path, NEXT_RUN, {recovery.ACK_ENV: given} if given else {})
        assert refused.returncode == 2 and "recovered" not in refused.stdout, refused.stdout + refused.stderr
        status = next(call for call in calls if "quiesce_window status" in call)
        assert "status --recovery" in status
        repin = [call for call in calls if "app_deployment_rollback repin" in call]
        assert len(repin) == 1 and "repin --from-owner-start" in repin[0] and digest not in repin[0]
        assert f"REFUSED owner_start_unacked: {'ack_mismatch' if given else 'ack_missing'}" in refused.stderr
        assert f"this window's owner_start_repin plan is {digest}" in refused.stderr
        assert "does not name this plan; nothing else ran and the App is left as it was" in refused.stderr
        assert started not in refused.stdout + refused.stderr and BLUE_ID not in refused.stdout + refused.stderr
        after = _load(tmp_path)
        assert _apps(after) == serving, "the owner's start keeps serving: nothing started, stopped or deployed"
        assert stored_record(after)["deployment_id"] == BLUE_ID
        assert journal.read(after.workspace, app_name=APP_NAME) == opened

    recovered, calls = _shell(tmp_path, NEXT_RUN, {recovery.ACK_ENV: digest})
    assert recovered.returncode == 0 and "recovered" in recovered.stdout, recovered.stdout + recovered.stderr
    repin = next(call for call in calls if "app_deployment_rollback repin" in call)
    assert "repin --from-owner-start" in repin and f"--deployment-lease-id {NEXT_LEASE}" in repin
    final = _load(tmp_path)
    record = stored_record(final)
    assert record["deployment_id"] == started and record["predecessor_deployment_id"] == BLUE_ID
    closed = journal.read(final.workspace, app_name=APP_NAME)
    assert (closed["state"], closed["closed_by"], closed["repinned_deployment_id"]) == ("closed", "owner_start_repin", started)
    assert _apps(final) == serving

    # Closed: the same ack in a later run's environment is a stray one, and changes nothing.
    again, calls = _shell(tmp_path, NEXT_RUN, {recovery.ACK_ENV: digest})
    assert again.returncode == 0 and "recovered" in again.stdout
    assert not any("rollback repin" in call for call in calls)


def test_an_owner_start_while_the_run_is_alive_is_never_started_over_or_stopped_by_that_run(
    tmp_path: Path,
) -> None:
    tail = (
        f'"$PYTHON" -c {shlex.quote(OWNER_STARTS_NOW)}\n'
        'if close_quiesce_window; then echo "closed"; else echo "close-refused"; fi\n'
        "exit 1\n"
    )
    script, env = _window(tmp_path, tail)
    result, calls = _shell(tmp_path, script, env)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "close-refused" in result.stdout and "closed\n" not in result.stdout
    # The run's own close and then its EXIT trap: both starts refuse before any write, start or stop.
    start_calls = [call for call in calls if "quiesce_window start" in call]
    assert len(start_calls) == 2, calls
    assert result.stderr.count(f"RuntimeError: {starts.BASELINE_MOVED}:") == 2
    assert "the quiesce window stays open" in result.stderr
    assert not any(needle in call for call in calls for needle in ("quiesce_window await", "rollback repin", "legacy-stop"))

    estate = _load(tmp_path)
    assert (estate.apps.starts, estate.apps.stops) == (1, 1), "the owner's one start and the window's own stop"
    assert estate.apps.compute in {"STARTING", "ACTIVE"}, "never stopped again"
    started = _land(estate, frozenset({BLUE_ID}))  # the owner's start comes up, minutes later
    sandbox.save(estate, tmp_path / "estate.pickle")
    assert (estate.apps.compute, estate.apps.active_id) == ("ACTIVE", started) and started != BLUE_ID
    assert estate.apps.deployments[started].creator == OWNER_CALLER
    opened = journal.read(estate.workspace, app_name=APP_NAME)
    assert (opened["state"], opened["start_baseline_ids"], opened["start_deployment_id"]) == ("window_open", [BLUE_ID], "")
    assert estate.jobs.cancels == [], "the window stays open, so its dead-man stays armed (it will skip a running App)"

    # That is the break-glass case: the next run re-pins the owner's start with its exact ack.
    digest = _digest(estate)
    recovered, calls = _shell(tmp_path, NEXT_RUN, {recovery.ACK_ENV: digest})
    assert recovered.returncode == 0 and "recovered" in recovered.stdout, recovered.stdout + recovered.stderr
    final = _load(tmp_path)
    assert stored_record(final)["deployment_id"] == started
    assert journal.read(final.workspace, app_name=APP_NAME)["closed_by"] == "owner_start_repin"
    assert (final.apps.starts, final.apps.stops, final.apps.compute, final.apps.active_id) == (1, 1, "ACTIVE", started)

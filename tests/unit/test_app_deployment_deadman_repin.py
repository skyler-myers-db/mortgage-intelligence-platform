"""The next run re-pins the dead-man's restart with the window's own proofs (M0-PR9).

A runner dies inside a restorable window; at the deadline the dead-man, run
by the fake Jobs service from the bytes the window published, starts blue
(spike S7's worst case: a NEW deployment) and records ``deadman_restarted``.
``app_deployment_rollback repin --from-deadman`` then runs under the next
lease: the marker is a pointer, never proof, so the start deployment is
re-proved against the signed journal and the App before the record is
re-signed and the journal closes. Every claim the marker makes that the
journal or the App does not bear out refuses with nothing written. The
pre-lease plan lets exactly this case through to the recovery; the pre-apply
recheck never does.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from tests.fixtures.quiesce_window_estate import (
    APP_NAME,
    BLUE_ID,
    RELEASE,
    Estate,
    StartBehaviour,
    run_deadman,
    stored_record,
)
from tests.unit.test_quiesce_window_start_repin import (  # noqa: F401 - the autouse env
    GIT_SHA,
    MIGRATION_NOOP,
    SCOPE,
    _env,
    _estate,
    _open,
    _plan,
)
from tools.databricks import app_deployment_rollback_cli as cli
from tools.databricks import quiesce_window as window
from tools.databricks import quiesce_window_deadman as deadman
from tools.databricks import quiesce_window_journal as journal
from tools.databricks.app_deployment_deadman_repin import DeadmanRepinRefused, repin_deadman_start

NEXT_LEASE = "22222222-2222-4222-8222-222222222222"


class Clock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def _dead_runner(estate: Estate) -> dict[str, Any]:
    """Open the window (arm, journal, stop), then the runner dies: the dead-man runs."""

    opened = _open(estate)
    run = estate.jobs.only_run()
    parameters = list(run.tasks[0].spark_python_task.parameters)
    deadline = int(parameters[parameters.index("--deadline") + 1])
    clock = Clock(float(deadline - 5))
    assert run_deadman(estate, run.run_id, clock=clock, sleep=clock.sleep) == 0
    return opened


def _marker(estate: Estate, opened: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    token = deadman.window_token(opened["window_sha256"], opened["lease_id"])
    path = deadman.marker_path(APP_NAME, token)
    return path, json.loads(estate.files.data[path])


def _repin(estate: Estate, **extra: Any) -> dict[str, Any]:
    return repin_deadman_start(
        estate.workspace,
        app_name=APP_NAME,
        scope=SCOPE,
        base_url="https://mip.example",
        bearer_token="token",
        deployment_lease_id=NEXT_LEASE,
        deployment_source_git_sha=GIT_SHA,
        **extra,
    )


def test_the_next_run_re_pins_the_dead_mans_start_and_closes_the_window() -> None:
    estate = _estate()
    opened = _dead_runner(estate)
    _, marker = _marker(estate, opened)
    assert marker["state"] == "deadman_restarted" and estate.apps.compute == "ACTIVE"

    record = _repin(estate)
    assert record["deployment_id"] == marker["start_deployment_id"] == "deployment-start-1"
    assert record["predecessor_deployment_id"] == BLUE_ID and record["git_sha"] == GIT_SHA
    assert stored_record(estate)["deployment_id"] == "deployment-start-1"
    closed = journal.read(estate.workspace, app_name=APP_NAME)
    assert closed["state"] == "closed" and closed["closed_by"] == "deadman_repin"
    assert closed["repinned_deployment_id"] == "deployment-start-1"
    assert (estate.apps.stops, estate.apps.starts, estate.apps.deploys) == (1, 1, 0)
    with pytest.raises(DeadmanRepinRefused, match="no window is open"):
        _repin(estate)


def test_a_dead_runners_start_intent_is_re_pinned_and_closed_by_the_next_run() -> None:
    # The runner journaled window_starting and died before apps start: the
    # dead-man restarted it, and only the re-pin may close that intent.
    estate = _estate()
    opened = _open(estate)
    starting = journal.write(
        estate.workspace,
        app_name=APP_NAME,
        expected=opened,
        changes={"state": "window_starting"},
        assert_lease_held=lambda: None,
    )
    with pytest.raises(RuntimeError, match="transition is not allowed"):
        journal.write(
            estate.workspace,
            app_name=APP_NAME,
            expected=starting,
            changes={"state": "closed", "closed_by": "repin"},
            assert_lease_held=lambda: None,
        )
    run = estate.jobs.only_run()
    parameters = list(run.tasks[0].spark_python_task.parameters)
    clock = Clock(float(int(parameters[parameters.index("--deadline") + 1]) - 5))
    assert run_deadman(estate, run.run_id, clock=clock, sleep=clock.sleep) == 0
    record = _repin(estate)
    assert record["deployment_id"] == "deployment-start-1"
    closed = journal.read(estate.workspace, app_name=APP_NAME)
    assert closed["state"] == "closed" and closed["closed_by"] == "deadman_repin"


def test_the_cli_runs_it_as_repin_from_deadman(monkeypatch: pytest.MonkeyPatch) -> None:
    estate = _estate()
    _dead_runner(estate)
    monkeypatch.setattr(cli, "WorkspaceClient", lambda: estate.workspace)
    monkeypatch.setenv("MIP_BEARER_TOKEN", "token")
    argv = [
        "repin", "--from-deadman", "--app-name", APP_NAME, "--scope", SCOPE,
        "--base-url", "https://mip.example", "--token-env", "MIP_BEARER_TOKEN",
        "--deployment-lease-id", NEXT_LEASE, "--deployment-source-git-sha", GIT_SHA,
    ]  # fmt: skip
    assert cli.main(argv) == 0
    assert journal.read(estate.workspace, app_name=APP_NAME)["closed_by"] == "deadman_repin"
    with pytest.raises(SystemExit):
        cli.main([*argv, "--to", "deployment-start-1"])


def _rewrite_marker(estate: Estate, opened: dict[str, Any], **changes: Any) -> None:
    path, marker = _marker(estate, opened)
    estate.files.data[path] = json.dumps({**marker, **changes}).encode()


@pytest.mark.parametrize(
    ("break_it", "match"),
    (
        (lambda estate, opened: _rewrite_marker(estate, opened, state="deadman_start_failed"), "recorded no restart"),
        (lambda estate, opened: _rewrite_marker(estate, opened, window_sha256="e" * 64), "not this window's"),
        (lambda estate, opened: _rewrite_marker(estate, opened, blue_deployment_id="deployment-x"), "not this window's"),
        (lambda estate, opened: _rewrite_marker(estate, opened, start_deployment_id=BLUE_ID), "no new start"),
        (
            lambda estate, opened: estate.files.data.pop(_marker(estate, opened)[0]),
            "recorded no restart",
        ),
    ),
    ids=("start-failed", "another-window", "another-blue", "names-blue", "no-marker"),
)
def test_a_marker_the_signed_journal_does_not_bear_out_refuses_with_nothing_written(
    break_it: Any, match: str
) -> None:
    estate = _estate()
    opened = _dead_runner(estate)
    break_it(estate, opened)
    before = stored_record(estate)
    with pytest.raises(DeadmanRepinRefused, match=match):
        _repin(estate)
    assert stored_record(estate) == before
    assert journal.read(estate.workspace, app_name=APP_NAME)["state"] == "window_open"


def test_a_marker_pointing_at_a_deployment_the_app_does_not_serve_refuses() -> None:
    # The marker is a pointer: it may name any id. The App must serve exactly
    # that deployment before the re-pin proves it is blue's bytes.
    from types import SimpleNamespace

    estate = _estate()
    opened = _dead_runner(estate)
    forged = SimpleNamespace(**{**estate.apps.deployments["deployment-start-1"].__dict__})
    forged.deployment_id = "deployment-forged"
    estate.apps.deployments["deployment-forged"] = forged
    _rewrite_marker(estate, opened, start_deployment_id="deployment-forged")
    before = stored_record(estate)
    with pytest.raises(DeadmanRepinRefused, match="does not serve"):
        _repin(estate)
    assert stored_record(estate) == before
    assert journal.read(estate.workspace, app_name=APP_NAME)["state"] == "window_open"


def test_an_app_that_is_not_active_refuses_before_any_write() -> None:
    estate = _estate()
    _dead_runner(estate)
    estate.apps.compute = "STOPPED"
    before = stored_record(estate)
    with pytest.raises(DeadmanRepinRefused, match="not ACTIVE"):
        _repin(estate)
    assert stored_record(estate) == before


@pytest.mark.parametrize(
    ("behaviour", "code"),
    (
        (StartBehaviour(tamper_snapshot=True), "start_tree_digest"),
        (StartBehaviour(carry_env=False), "start_env_names"),
    ),
)
def test_a_dead_man_start_that_is_not_provably_blue_is_never_re_pinned(
    behaviour: StartBehaviour, code: str
) -> None:
    estate = _estate(behaviour)
    _dead_runner(estate)
    before = stored_record(estate)
    with pytest.raises(RuntimeError, match=code):
        _repin(estate)
    assert stored_record(estate) == before


def test_a_restore_folder_changed_since_the_window_opened_refuses() -> None:
    estate = _estate()
    _dead_runner(estate)
    estate.files.put_tree(RELEASE, {"late.py": b"written after the window\n"})
    before = stored_record(estate)
    with pytest.raises(DeadmanRepinRefused, match="restore source changed"):
        _repin(estate)
    assert stored_record(estate) == before


def test_a_contract_window_is_never_re_pinned_from_the_dead_man(monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.databricks.app_deployment_deadman_repin import proven_deadman_start

    estate = _estate()
    _open(estate)
    contract = {**journal.read(estate.workspace, app_name=APP_NAME), "blue_restorable": False}
    monkeypatch.setattr(journal, "read", lambda *_a, **_k: contract)
    with pytest.raises(DeadmanRepinRefused, match="contract window"):
        proven_deadman_start(estate.workspace, app_name=APP_NAME)


# --- status and the plan ------------------------------------------------------------


def test_status_reads_the_dead_mans_marker_as_codes_only(tmp_path: Any, capsys: pytest.CaptureFixture[str]) -> None:
    estate = _estate()
    _dead_runner(estate)
    out = tmp_path / "status.env"
    assert window.main(["status", "--app-name", APP_NAME, "--out-env", str(out)], workspace_factory=lambda: estate.workspace) == 0
    text = out.read_text(encoding="utf-8")
    assert "MIP_QUIESCE_WINDOW_DEADMAN_STATE=deadman_restarted\n" in text
    assert "MIP_QUIESCE_WINDOW_DEADMAN_START_DEPLOYMENT_ID=deployment-start-1\n" in text
    line = capsys.readouterr().out
    assert "status: journal window_open, blue restorable True, dead-man deadman_restarted" in line
    assert "deployment-" not in line, "the public line names no id"


@pytest.mark.parametrize("intent", (False, True), ids=("window_open", "window_starting"))
@pytest.mark.parametrize(("position", "refused"), (("pre-lease", False), ("pre-apply", True)))
def test_the_pre_lease_plan_lets_a_dead_man_restart_through_to_the_recovery(
    position: str, refused: bool, intent: bool
) -> None:
    estate = _estate()
    if intent:  # the runner journaled its start intent, then died before the start
        opened = _open(estate)
        journal.write(
            estate.workspace,
            app_name=APP_NAME,
            expected=opened,
            changes={"state": "window_starting"},
            assert_lease_held=lambda: None,
        )
        run = estate.jobs.only_run()
        parameters = list(run.tasks[0].spark_python_task.parameters)
        clock = Clock(float(int(parameters[parameters.index("--deadline") + 1]) - 5))
        assert run_deadman(estate, run.run_id, clock=clock, sleep=clock.sleep) == 0
    else:
        _dead_runner(estate)
    facts = window.collect_facts(
        estate.workspace,
        app_name=APP_NAME,
        scope=SCOPE,
        lakebase_instance="mip-app-state",
        gate_report={"verdict": "passed", "instance_update_sha256": ""},
        binding_payload={"name": APP_NAME, "description": "", "resources": estate.apps.resources},
        role_state="exact",
        verifier_role_state="exact",
        jobs_reader_role_state="exact",
        migration_plan=MIGRATION_NOOP,
        rebase_unverified_app=False,
    )
    state = "window_starting" if intent else "window_open"
    assert (facts.journal_state, facts.deadman_state) == (state, "deadman_restarted")
    plan = window.window_plan(facts, position=position, acknowledgement="")
    assert ("window_journal_open" in plan.refusals) is refused
    assert plan.public_summary()["deadman_recovery"] is (not refused)


def test_an_open_window_without_a_restart_still_refuses_before_the_lease() -> None:
    estate = _estate()
    _open(estate)  # the dead-man has not run yet
    plan = _plan(estate)
    assert plan.code == "refused" and "window_journal_open" in plan.refusals
    assert plan.public_summary()["deadman_recovery"] is False


def test_a_recovering_run_that_also_needs_a_window_keeps_its_pre_lease_ack() -> None:
    # The pre-lease plan's digest names the old blue; the re-pin moves blue to
    # the dead-man's start (its predecessor the old blue), so the pre-apply
    # recheck matches the ack through the re-pin and nothing else (round 2 of #334).
    from tests.unit.test_quiesce_window_start_repin import MIGRATION_APPLY

    estate = _estate()
    _dead_runner(estate)

    def plan(position: str, ack: str, migration: dict[str, Any] = MIGRATION_APPLY) -> Any:
        facts = window.collect_facts(
            estate.workspace,
            app_name=APP_NAME,
            scope=SCOPE,
            lakebase_instance="mip-app-state",
            gate_report={"verdict": "passed", "instance_update_sha256": ""},
            binding_payload={"name": APP_NAME, "description": "", "resources": estate.apps.resources},
            role_state="exact",
            verifier_role_state="exact",
            jobs_reader_role_state="exact",
            migration_plan=migration,
            rebase_unverified_app=False,
        )
        return window.window_plan(facts, position=position, acknowledgement=ack)

    digest = plan("pre-lease", "").sha256
    lease = plan("pre-lease", digest)
    assert lease.code == "window" and lease.public_summary()["deadman_recovery"] is True
    _repin(estate)
    recheck = plan("pre-apply", digest)
    assert recheck.code == "window" and recheck.ack == "matched", recheck.refusals
    assert recheck.canonical["blue_deployment_id"] == "deployment-start-1" and recheck.sha256 != digest
    another = {**MIGRATION_APPLY, "migrations": {"pending": ["0042", "0099"], "pending_contract": []}}
    moved = plan("pre-apply", digest, another)  # any other fact that moved still refuses
    assert moved.code == "refused" and "window_unacked" in moved.refusals

"""An owner's break-glass start over an open quiesce window is re-pinned by the next release (wave 7c).

A run ends inside a restorable window and the owner starts the App by hand.
The raw start serves but proves nothing, and before this change the journal
stayed open for good: every later release refused ``window_journal_open``.
Now the window's journal carries the App's deployment ids from ``window_open``
(the baseline), the read-only classifier names the one new deployment nobody
journaled or marked, and ``repin --from-owner-start`` re-pins it under the
next lease with the dead-man re-pin's own proofs, only when the owner's exact
``owner_start_repin`` digest ack names that plan.

The fake Apps API is the one spike S7 measured (tests/fixtures/measured_apps.py):
a start shows only reads later, a STOPPED App's GET has no active deployment,
and a deployment shows its env NAMES only. So the shape check cannot see a
changed value or ``value_from`` (the positive control below), which is why the
ack is always required. Every refusal leaves the App serving the owner's
start, the signed record and the journal byte-equal, and makes no ``apps``
start, stop, deploy or update call.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from tests.fixtures.quiesce_window_estate import (
    APP_NAME,
    BLUE_ID,
    DEPLOYER_CALLER,
    OTHER_SOURCE,
    OWNER_CALLER,
    RELEASE,
    SOURCE_FILES,
    Estate,
    StartBehaviour,
    run_deadman,
    stored_record,
)
from tests.unit.test_app_deployment_deadman_repin import Clock, _dead_runner
from tests.unit.test_app_deployment_deadman_repin import _repin as _deadman_repin
from tests.unit.test_quiesce_window_start_repin import (  # noqa: F401 - the autouse env
    EQUAL_BINDINGS,
    GIT_SHA,
    MIGRATION_CONTRACT,
    MIGRATION_NOOP,
    SCOPE,
    _await,
    _env,
    _estate,
    _immutable_payload,
    _open,
    _plan,
    _start,
    _stop,
)
from tools.databricks import app_deployment_rollback as rollback
from tools.databricks import app_deployment_rollback_cli as cli
from tools.databricks import app_start_deployment as starts
from tools.databricks import core_blue_repin
from tools.databricks import quiesce_window as window
from tools.databricks import quiesce_window_deadman as deadman
from tools.databricks import quiesce_window_journal as journal
from tools.databricks import quiesce_window_recovery as recovery
from tools.databricks.app_deployment_deadman_repin import DeadmanRepinRefused
from tools.databricks.app_deployment_owner_start_repin import (
    REFUSAL_CODES,
    OwnerStartRepinRefused,
    repin_owner_start,
)
from tools.databricks.operation_ack import ack_digest
from tools.databricks.quiesce_window_plan import OWNER_START_REFUSALS, window_plan

NEXT_LEASE = "22222222-2222-4222-8222-222222222222"
JOURNAL = journal.journal_path(APP_NAME)


# --- the estate: a dead run's window, then the owner's raw start -------------------------


def _owner_start(estate: Estate, behaviour: StartBehaviour | None = None) -> str:
    """The owner's raw ``apps start``, then time passes until its deployment is terminal."""

    estate.apps.behaviour = behaviour or StartBehaviour()
    estate.apps.caller = OWNER_CALLER
    before = starts.deployment_ids(estate.workspace, app_name=APP_NAME)
    estate.apps.start(APP_NAME)
    created = starts.find_start_deployment(estate.workspace, app_name=APP_NAME, baseline=before)
    starts.wait_for_terminal(estate.workspace, app_name=APP_NAME, deployment_id=created)
    estate.apps.caller = DEPLOYER_CALLER
    return created


def _down(estate: Estate) -> None:
    """The App is down again before the break-glass (whatever stopped it)."""

    estate.apps.compute = "STOPPED"


def _open_then_die(estate: Estate) -> str:
    _open(estate)  # journal window_open, the dead-man armed, the stop; then the runner dies
    return _owner_start(estate)


def _intent_then_die(estate: Estate) -> str:
    opened = _open(estate)
    journal.write(
        estate.workspace,
        app_name=APP_NAME,
        expected=opened,
        changes={"state": "window_starting"},
        assert_lease_held=lambda: None,
    )
    return _owner_start(estate)


def _refused_then_die(estate: Estate) -> str:
    estate.apps.behaviour = StartBehaviour(state="FAILED")
    _open(estate)
    _start(estate)
    with pytest.raises(RuntimeError, match="start_not_succeeded"):
        _await(estate)  # the run's own start FAILED: stopped again, journaled window_refused
    assert estate.apps.compute == "STOPPED"
    return _owner_start(estate)


def _started_failed_then_die(estate: Estate) -> str:
    estate.apps.behaviour = StartBehaviour(state="FAILED")
    _open(estate)
    journaled = _start(estate)  # window_started; the runner dies before its await
    starts.wait_for_terminal(estate.workspace, app_name=APP_NAME, deployment_id=journaled)
    _down(estate)
    return _owner_start(estate)


def _proven_then_die(estate: Estate) -> str:
    _open(estate)
    _start(estate)
    _await(estate)  # window_proven; the runner dies before the re-pin, and the App goes down
    _down(estate)
    return _owner_start(estate)


def _deadman_then_restart(estate: Estate) -> str:
    _dead_runner(estate)  # the dead-man restarted blue (deployment-start-1), then the App went down
    _down(estate)
    return _owner_start(estate)


WINDOWS = {
    "window_open": (_open_then_die, "window_open", "absent"),
    "window_starting": (_intent_then_die, "window_starting", "absent"),
    "window_refused": (_refused_then_die, "window_refused", "absent"),
    "window_started": (_started_failed_then_die, "window_started", "absent"),
    "window_proven": (_proven_then_die, "window_proven", "absent"),
    "deadman_superseded": (_deadman_then_restart, "window_open", "deadman_superseded"),
}


def _classify(estate: Estate) -> recovery.Recovery:
    current = journal.read(estate.workspace, app_name=APP_NAME)
    return recovery.classify(estate.workspace, app_name=APP_NAME, current=current)


def _repin(estate: Estate, ack: str = "", **extra: Any) -> dict[str, Any]:
    return repin_owner_start(
        estate.workspace,
        app_name=APP_NAME,
        scope=SCOPE,
        base_url="https://mip.example",
        bearer_token="token",
        deployment_lease_id=NEXT_LEASE,
        deployment_source_git_sha=GIT_SHA,
        acknowledgement=ack,
        **extra,
    )


def _frozen(estate: Estate) -> tuple[Any, ...]:
    """Everything a refusal must leave alone: the journal's bytes, the record, the App."""

    apps = estate.apps
    record = json.dumps(stored_record(estate), sort_keys=True)
    return (estate.files.data[JOURNAL], record, apps.starts, apps.stops, apps.deploys, apps.updates, apps.compute, apps.active_id)


def _refuses(estate: Estate, code: str, ack: str | None = None) -> OwnerStartRepinRefused:
    """``repin --from-owner-start`` refuses with ``code`` and changes nothing."""

    before = _frozen(estate)
    with pytest.raises(OwnerStartRepinRefused) as refused:
        _repin(estate, _classify(estate).sha256 if ack is None else ack)
    assert refused.value.code == code and code in REFUSAL_CODES
    assert _frozen(estate) == before, "a refusal writes nothing and calls no apps start, stop, deploy or update"
    return refused.value


def _rewrite_journal(estate: Estate, **changes: Any) -> None:
    """Re-sign the stored journal with ``changes`` (a field set to None is dropped)."""

    stored = json.loads(estate.files.data[JOURNAL])
    unsigned = {key: value for key, value in stored.items() if not key.startswith("attestation_")}
    unsigned.update(changes)
    unsigned = {key: value for key, value in unsigned.items() if value is not None}
    estate.files.data[JOURNAL] = json.dumps(journal._sign(unsigned), sort_keys=True).encode("utf-8")


def _marker_path(estate: Estate) -> str:
    current = journal.read(estate.workspace, app_name=APP_NAME)
    return deadman.marker_path(APP_NAME, deadman.window_token(current["window_sha256"], current["lease_id"]))


# --- the re-pin, from every open restorable state ----------------------------------------


@pytest.mark.parametrize("name", sorted(WINDOWS))
def test_an_owner_start_over_any_open_restorable_window_is_re_pinned_and_closes_it(name: str) -> None:
    build, state, marker = WINDOWS[name]
    estate = _estate()
    started = build(estate)
    opened = journal.read(estate.workspace, app_name=APP_NAME)
    assert opened["state"] == state and opened["start_baseline_ids"] == [BLUE_ID]
    found = _classify(estate)
    assert (found.kind, found.owner_start, found.deadman_state) == ("owner_start", "candidate", marker)
    assert found.creator_class == "other", "the owner's login made it, not the deployer"
    assert found.body is not None and found.body["start_deployment_id"] == started
    assert found.sha256 == ack_digest("owner_start_repin", found.body)
    calls = (estate.apps.starts, estate.apps.stops, estate.apps.deploys, estate.apps.updates)

    record = _repin(estate, found.sha256)

    assert record["deployment_id"] == started and record["predecessor_deployment_id"] == BLUE_ID
    assert stored_record(estate)["deployment_id"] == started and record["git_sha"] == GIT_SHA
    closed = journal.read(estate.workspace, app_name=APP_NAME)
    assert (closed["state"], closed["closed_by"]) == ("closed", "owner_start_repin")
    assert closed["repinned_deployment_id"] == started
    # The governed start a run journaled (and refused) keeps its place in the record.
    assert closed["start_deployment_id"] == opened["start_deployment_id"]
    assert (estate.apps.starts, estate.apps.stops, estate.apps.deploys, estate.apps.updates) == calls
    assert estate.apps.compute == "ACTIVE" and estate.apps.active_id == started, "nothing was stopped"
    _refuses(estate, "owner_start_no_window", ack=found.sha256)  # closed: nothing is left to re-pin


def test_an_owner_start_made_while_the_run_was_alive_is_left_serving_and_re_pinned_later() -> None:
    # The run's own start refuses over the owner's (window_start_baseline_moved): it starts
    # and stops nothing, the window stays window_open, and that is this same break-glass case.
    estate = _estate()
    _open(estate)
    started = _owner_start(estate)
    calls = (estate.apps.starts, estate.apps.stops)
    with pytest.raises(RuntimeError, match=starts.BASELINE_MOVED):
        _start(estate)
    assert (estate.apps.starts, estate.apps.stops) == calls and estate.apps.active_id == started
    found = _classify(estate)
    assert found.owner_start == "candidate" and found.journal_state == "window_open"
    assert _repin(estate, found.sha256)["deployment_id"] == started
    assert (estate.apps.starts, estate.apps.stops) == calls


def test_the_armed_dead_man_exits_at_its_next_poll_once_the_journal_is_closed() -> None:
    estate = _estate()
    _open_then_die(estate)
    run = estate.jobs.only_run()
    parameters = list(run.tasks[0].spark_python_task.parameters)
    deadline = int(parameters[parameters.index("--deadline") + 1])
    _repin(estate, _classify(estate).sha256)
    starts_before = estate.apps.starts
    clock = Clock(float(deadline - 600))

    assert run_deadman(estate, run.run_id, clock=clock, sleep=clock.sleep) == 0
    assert estate.apps.starts == starts_before and clock.now < deadline, "it left at its first poll"
    assert _marker_path(estate) not in estate.files.data, "and recorded nothing"


def test_the_cli_runs_it_as_repin_from_owner_start_and_reads_the_ack_from_its_environment(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    estate = _estate()
    started = _open_then_die(estate)
    digest = _classify(estate).sha256
    monkeypatch.setattr(cli, "WorkspaceClient", lambda: estate.workspace)
    monkeypatch.setenv("MIP_BEARER_TOKEN", "token")
    monkeypatch.delenv(recovery.ACK_ENV, raising=False)
    argv = [
        "repin", "--from-owner-start", "--app-name", APP_NAME, "--scope", SCOPE,
        "--base-url", "https://mip.example", "--token-env", "MIP_BEARER_TOKEN",
        "--deployment-lease-id", NEXT_LEASE, "--deployment-source-git-sha", GIT_SHA,
    ]  # fmt: skip
    before = _frozen(estate)

    assert cli.main(argv) == 2
    refused = capsys.readouterr().err
    assert f"REFUSED owner_start_unacked: ack_missing: this window's owner_start_repin plan is {digest}" in refused
    assert "deployment-" not in refused and RELEASE not in refused, "codes and the digest only"
    assert _frozen(estate) == before
    for flags in (["--to", started], ["--from-deadman"], ["--expected-tree-sha256", "a" * 64]):
        with pytest.raises(SystemExit):
            cli.main([*argv, *flags])
    monkeypatch.setenv(recovery.ACK_ENV, digest)
    assert cli.main(argv) == 0
    assert journal.read(estate.workspace, app_name=APP_NAME)["closed_by"] == "owner_start_repin"


# --- the ack ------------------------------------------------------------------------------


def test_the_ack_is_this_plans_exact_digest_and_nothing_else() -> None:
    estate = _estate()
    _open_then_die(estate)
    found = _classify(estate)
    assert found.body is not None

    missing = _refuses(estate, "owner_start_unacked", ack="")
    assert found.sha256 in str(missing), "the refusal prints the plan's digest"
    assert "ack_malformed" in str(_refuses(estate, "owner_start_unacked", ack="A" * 64))
    assert "ack_mismatch" in str(_refuses(estate, "owner_start_unacked", ack="0" * 64))
    # Another window's ack, and the same body acknowledged for another purpose.
    another = ack_digest("owner_start_repin", {**found.body, "window_sha256": "e" * 64})
    _refuses(estate, "owner_start_unacked", ack=another)
    _refuses(estate, "owner_start_unacked", ack=ack_digest("adopt_live", found.body))
    _refuses(estate, "owner_start_unacked", ack=ack_digest("owner_start_repin", {**found.body, "version": 2}))
    assert _repin(estate, found.sha256)["deployment_id"] == found.body["start_deployment_id"]


def test_an_ack_taken_before_a_second_owner_restart_never_re_pins_the_second() -> None:
    # The judge's double start: two deployments nobody journaled. No ack can name which
    # of them the owner means, so there is no governed exit (docs: never start twice).
    estate = _estate()
    _open_then_die(estate)
    first = _classify(estate).sha256
    _down(estate)
    _owner_start(estate)
    _refuses(estate, "owner_start_ambiguous", ack=first)


def test_the_digest_moves_when_the_dead_man_reaches_its_deadline() -> None:
    # Rule (d): plan right before the release. The marker's state is in the body.
    estate = _estate()
    _open_then_die(estate)
    early = _classify(estate)
    run = estate.jobs.only_run()
    parameters = list(run.tasks[0].spark_python_task.parameters)
    clock = Clock(float(int(parameters[parameters.index("--deadline") + 1]) - 5))
    starts_before = estate.apps.starts

    assert run_deadman(estate, run.run_id, clock=clock, sleep=clock.sleep) == 1
    late = _classify(estate)
    assert estate.apps.starts == starts_before, "the dead-man found the App running and started nothing"
    assert (early.deadman_state, late.deadman_state) == ("absent", "deadman_skipped")
    assert late.owner_start == "candidate" and late.sha256 != early.sha256
    _refuses(estate, "owner_start_unacked", ack=early.sha256)
    assert _repin(estate, late.sha256)["predecessor_deployment_id"] == BLUE_ID


def test_why_the_ack_exists_a_deploy_with_another_env_value_reads_exactly_like_a_start() -> None:
    """The positive control: get_deployment shows env names only (spike S7).

    The run's own start serves, journaled; then someone DEPLOYS blue's folder
    with blue's env names, one literal value changed and one ``value_from``
    re-pointed. Nothing the API shows differs from a start, so the shape check
    reads a candidate. Only the owner's exact ack admits it, never a read.
    """

    estate = _estate()
    _open(estate)
    journaled = _start(estate)
    starts.wait_for_terminal(estate.workspace, app_name=APP_NAME, deployment_id=journaled)
    blue = _immutable_payload()
    tampered = [
        {"name": item["name"], "value": "another-literal-value"}
        if index == 0
        else {"name": item["name"], "value_from": "another-resource-key"}
        if index == 1
        else dict(item)
        for index, item in enumerate(blue["env_vars"])
    ]
    assert tampered[0] != blue["env_vars"][0] and tampered[1] != blue["env_vars"][1]
    estate.apps.caller = OWNER_CALLER
    deployed = estate.apps.deploy_and_wait(
        APP_NAME, SimpleNamespace(as_dict=lambda: {"source_code_path": RELEASE, "env_vars": tampered}), timeout=None
    )
    estate.files.put_tree(deployed.deployment_artifacts.source_code_path, SOURCE_FILES)  # its snapshot of blue's folder
    shown = estate.apps.get_deployment(APP_NAME, deployed.deployment_id)
    assert {(item.value, item.value_from) for item in shown.env_vars} == {(None, None)}, "names only, as measured"

    found = _classify(estate)
    assert found.owner_start == "candidate", "the shape check cannot see a value or a value_from"
    assert found.body is not None and found.body["start_deployment_id"] == deployed.deployment_id
    _refuses(estate, "owner_start_unacked", ack="")
    assert _repin(estate, found.sha256)["deployment_id"] == deployed.deployment_id


# --- every refusal of the classifier -------------------------------------------------------


def test_a_contract_window_is_never_re_pinned_from_an_owner_start() -> None:
    estate = _estate()
    plan = _plan(estate, migration=MIGRATION_CONTRACT)
    assert plan.blue_restorable is False
    window.open_window(
        estate.workspace,
        app_name=APP_NAME,
        plan=plan.full(),
        acknowledgement=plan.sha256,
        lease_id=NEXT_LEASE,
        source_git_sha=GIT_SHA,
        assert_lease_held=lambda: None,
        stop=_stop(estate),
        admit=lambda _canonical: None,
        arm=lambda _canonical: "9001",
        cancel=lambda _run_id: None,
    )
    _owner_start(estate)  # against the rule: blue's code now serves the migrated schema
    _refuses(estate, "owner_start_contract_window")


@pytest.mark.parametrize(
    "changes",
    ({"start_baseline_ids": []}, {"start_baseline_ids": None, "version": 1}, {"start_baseline_ids": ["deployment-other"]}),
    ids=("pre-baseline-window-open", "version-1", "baseline-without-blue"),
)
def test_a_window_without_a_baseline_that_holds_blue_stays_refused(changes: dict[str, Any]) -> None:
    estate = _estate()
    _open_then_die(estate)
    _rewrite_journal(estate, **changes)
    assert journal.read(estate.workspace, app_name=APP_NAME)["state"] == "window_open", "it still verifies"
    _refuses(estate, "owner_start_no_baseline")


def _stopped(estate: Estate) -> None:
    _down(estate)


def _starting(estate: Estate) -> None:
    _down(estate)
    estate.apps.start(APP_NAME)  # in flight: STARTING, its id not shown yet (about 100 s, S7)


def _pending(estate: Estate) -> None:
    estate.apps.pending_id = BLUE_ID


@pytest.mark.parametrize("break_it", (_stopped, _starting, _pending), ids=("stopped", "starting", "pending"))
def test_an_app_that_is_not_serving_with_nothing_pending_is_not_classified(break_it: Any) -> None:
    estate = _estate()
    _open_then_die(estate)
    break_it(estate)
    assert _classify(estate).owner_start == "owner_start_not_running"
    with pytest.raises(OwnerStartRepinRefused) as refused:
        _repin(estate, "0" * 64)
    assert refused.value.code == "owner_start_not_running"


def test_a_window_whose_app_serves_the_runs_own_journaled_start_has_no_owner_start() -> None:
    estate = _estate()
    _open(estate)
    _start(estate)
    _await(estate)  # window_proven: the journaled start serves; nobody started anything since
    _refuses(estate, "owner_start_none")


def test_a_second_unexplained_deployment_is_ambiguous() -> None:
    # The dead-man's start that it could not read (two new ids) plus the owner's restart.
    estate = _estate(StartBehaviour(twin=True))
    _open(estate)
    run = estate.jobs.only_run()
    parameters = list(run.tasks[0].spark_python_task.parameters)
    clock = Clock(float(int(parameters[parameters.index("--deadline") + 1]) - 5))
    assert run_deadman(estate, run.run_id, clock=clock, sleep=clock.sleep) == 1
    assert json.loads(estate.files.data[_marker_path(estate)])["state"] == "deadman_start_unreadable"
    _down(estate)
    _owner_start(estate)
    _refuses(estate, "owner_start_ambiguous")


def test_an_unexplained_deployment_the_app_does_not_serve_is_ambiguous() -> None:
    estate = _estate()
    _open(estate)
    _start(estate)
    _await(estate)  # the journaled start serves
    stray = SimpleNamespace(**{**estate.apps.deployments[BLUE_ID].__dict__, "deployment_id": "deployment-stray"})
    estate.apps.deployments["deployment-stray"] = stray
    _refuses(estate, "owner_start_ambiguous")


def test_a_listing_with_an_unreadable_deployment_is_ambiguous() -> None:
    estate = _estate()
    _open_then_die(estate)
    estate.apps.deployments["nameless"] = SimpleNamespace(
        **{**estate.apps.deployments[BLUE_ID].__dict__, "deployment_id": ""}
    )
    _refuses(estate, "owner_start_ambiguous")


def _blue_is_newer(estate: Estate) -> None:
    estate.apps.deployments[BLUE_ID].create_time = "2026-12-01T00:00:00Z"


def _default_source_moved(estate: Estate) -> None:
    estate.apps.default_source = OTHER_SOURCE


def _default_source_unread(estate: Estate) -> None:
    # A GET without a default source: the start's own source still has to be blue's.
    estate.apps.default_source = ""


@pytest.mark.parametrize(
    ("behaviour", "after"),
    (
        (StartBehaviour(), _blue_is_newer),
        (StartBehaviour(state="FAILED"), None),
        (StartBehaviour(source=OTHER_SOURCE), None),
        (StartBehaviour(), _default_source_moved),
        (StartBehaviour(source=OTHER_SOURCE), _default_source_unread),
    ),
    ids=("not-newest", "not-succeeded", "another-source", "default-source-moved", "another-source-default-unread"),
)
def test_a_new_deployment_that_is_not_the_current_start_of_blues_folder_is_refused(
    behaviour: StartBehaviour, after: Any
) -> None:
    estate = _estate()
    estate.files.put_tree(OTHER_SOURCE, SOURCE_FILES)
    _open(estate)
    _owner_start(estate, behaviour)
    if after is not None:
        after(estate)
    _refuses(estate, "owner_start_not_current")


def _command(start: SimpleNamespace) -> None:
    start.command = ["python", "-m", "another.entrypoint"]


def _git_source(start: SimpleNamespace) -> None:
    start.git_source = SimpleNamespace(branch="main")


def _auto_sync(start: SimpleNamespace) -> None:
    start.mode = "AUTO_SYNC"


def _no_mode(start: SimpleNamespace) -> None:
    start.mode = None


def _another_env_name(start: SimpleNamespace) -> None:
    start.env_vars = [*start.env_vars, SimpleNamespace(name="MIP_EXTRA", value=None, value_from=None)]


@pytest.mark.parametrize(
    "deploy_shaped",
    (_command, _git_source, _auto_sync, _no_mode, _another_env_name),
    ids=("command", "git-source", "auto-sync", "no-mode", "another-env-name"),
)
def test_a_deployment_that_does_not_have_the_shape_of_a_start_is_refused(deploy_shaped: Any) -> None:
    estate = _estate()
    started = _open_then_die(estate)
    deploy_shaped(estate.apps.deployments[started])
    _refuses(estate, "owner_start_not_a_start")


def test_fake_only_a_value_from_the_api_never_shows_would_refuse_if_it_ever_did() -> None:
    """FAKE-ONLY, not a live defence: get_deployment returns env names only (spike S7).

    The comparison is of the rows as the API shows them, so should the API one
    day show a value or a ``value_from``, a differing row refuses. Today both
    are always absent on both sides, and the positive control above shows what
    that means: the ack, never this check, is what tells a deploy from a start.
    """

    estate = _estate()
    started = _open_then_die(estate)
    rows = estate.apps.deployments[started].env_vars
    assert recovery._env_rows(estate.apps.deployments[started]) == core_blue_repin._env_vars(
        estate.apps.deployments[started]
    ), "the same rows the core tier's stranded-start check reads"
    rows[0] = SimpleNamespace(name=rows[0].name, value=None, value_from="another-resource-key")
    _refuses(estate, "owner_start_not_a_start")


@pytest.mark.parametrize(
    "marker",
    (
        {"window_sha256": "e" * 64},
        {"token": "f" * 64},
        {"state": ""},
        {"state": "deadman_restarted by owner-caller"},
        None,
    ),
    ids=("another-window", "another-token", "no-state", "unreviewed-state", "not-json"),
)
def test_a_marker_that_is_not_this_windows_or_cannot_be_read_refuses(marker: dict[str, Any] | None) -> None:
    estate = _estate()
    opened = _dead_runner(estate)
    _down(estate)
    _owner_start(estate)
    path = _marker_path(estate)
    assert opened["window_sha256"] in estate.files.data[path].decode()
    stored = json.loads(estate.files.data[path])
    estate.files.data[path] = b"not json" if marker is None else json.dumps({**stored, **marker}).encode()
    found = _classify(estate)
    assert found.deadman_state in {"mismatch", "unreadable"} and "owner-caller" not in json.dumps(found.public())
    _refuses(estate, "owner_start_marker_unreadable")


def test_an_unverifiable_journal_refuses_before_anything_is_read_of_the_app() -> None:
    estate = _estate()
    _open_then_die(estate)
    stored = json.loads(estate.files.data[JOURNAL])
    estate.files.data[JOURNAL] = json.dumps({**stored, "blue_restorable": False}).encode()
    reads = estate.apps.get_calls
    with pytest.raises(RuntimeError, match="signature is invalid"):
        _repin(estate, "0" * 64)
    assert estate.apps.get_calls == reads and stored_record(estate)["deployment_id"] == BLUE_ID


def test_the_re_pin_runs_only_under_a_lease() -> None:
    from tools.databricks.app_deployment_owner_start_repin import repin_from_owner_start

    estate = _estate()
    _open_then_die(estate)
    with pytest.raises(OwnerStartRepinRefused) as refused:
        repin_from_owner_start(
            estate.workspace,
            app_name=APP_NAME,
            deployment_lease_id="",
            deployment_source_git_sha=GIT_SHA,
            repin=lambda **_proofs: pytest.fail("never re-pinned without a lease"),
            assert_lease_held=lambda: None,
            acknowledgement=_classify(estate).sha256,
        )
    assert refused.value.code == "owner_start_usage"


# --- every refusal of the proofs under the lease ------------------------------------------


def _unproven(estate: Estate, match: str, error: type[Exception] = RuntimeError) -> None:
    """The exact ack was given, and a proof still refuses: nothing is written."""

    found = _classify(estate)
    assert found.owner_start == "candidate", found.owner_start
    before = _frozen(estate)
    with pytest.raises(error, match=match):
        _repin(estate, found.sha256)
    assert _frozen(estate) == before


def test_an_owner_start_of_a_tampered_snapshot_is_never_re_pinned() -> None:
    estate = _estate()
    _open(estate)
    _owner_start(estate, StartBehaviour(tamper_snapshot=True))
    _unproven(estate, "start_tree_digest", starts.StartProofError)


@pytest.mark.parametrize("gone", (False, True), ids=("changed", "gone"))
def test_a_restore_folder_changed_since_the_window_opened_refuses(gone: bool) -> None:
    estate = _estate()
    _open(estate)
    estate.files.put_tree(RELEASE, {"late.py": b"written after the window opened\n"})
    _owner_start(estate)  # the start snapshotted the changed folder
    if gone:
        for path in [path for path in estate.files.data if path.startswith(RELEASE.removeprefix("/Workspace") + "/")]:
            del estate.files.data[path]
    refused = _refuses(estate, "owner_start_unproven")
    assert ("gone or unreadable" if gone else "changed since the window opened") in str(refused)


def test_an_owner_start_that_copied_a_refused_starts_other_env_names_is_never_re_pinned() -> None:
    # The run's own start dropped blue's env set and was refused; the owner's start copies
    # that newest deployment's names (S7), so it has the shape of a start and is not blue.
    estate = _estate(StartBehaviour(carry_env=False))
    _open(estate)
    _start(estate)
    with pytest.raises(RuntimeError, match="start_env_names"):
        _await(estate)
    _owner_start(estate)
    _unproven(estate, "start_env_names", starts.StartProofError)


def test_live_bindings_that_are_neither_the_records_nor_this_runs_refuse() -> None:
    estate = _estate()
    _open_then_die(estate)
    estate.apps.resources = [dict(item) for item in EQUAL_BINDINGS["resources"][:1]]
    _unproven(estate, "neither the signed record's nor")


def test_a_health_mismatch_refuses_before_any_write(monkeypatch: pytest.MonkeyPatch) -> None:
    estate = _estate()
    _open_then_die(estate)

    def unhealthy(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("health names another deployment")

    monkeypatch.setattr(rollback, "_health", unhealthy)
    _unproven(estate, "health names another deployment")


def test_a_record_that_no_longer_names_the_windows_blue_refuses() -> None:
    from tests.unit.test_quiesce_window_start_repin import _record_moved

    estate = _estate()
    _open_then_die(estate)
    _record_moved(estate)
    _unproven(estate, "no longer names the expected blue")


# --- disjoint from the dead-man's path -------------------------------------------------------


def test_a_dead_man_restart_the_app_serves_is_the_dead_mans_path_and_needs_no_ack() -> None:
    estate = _estate()
    _dead_runner(estate)
    found = _classify(estate)
    assert (found.kind, found.deadman_state, found.owner_start, found.sha256) == ("deadman", "deadman_restarted", "absent", "")
    _refuses(estate, "owner_start_deadman", ack="0" * 64)
    record = _deadman_repin(estate)  # unchanged: no ack anywhere
    assert record["deployment_id"] == "deployment-start-1"
    assert journal.read(estate.workspace, app_name=APP_NAME)["closed_by"] == "deadman_repin"


def test_a_dead_man_restart_the_app_no_longer_serves_leaves_the_dead_mans_path_for_good() -> None:
    estate = _estate()
    _dead_runner(estate)
    _down(estate)
    assert _classify(estate).deadman_state == "deadman_superseded"
    assert _classify(estate).owner_start == "owner_start_not_running"
    with pytest.raises(DeadmanRepinRefused, match="not ACTIVE"):
        _deadman_repin(estate)  # what every run met under its lease before, for good
    plan = window_plan(_facts(estate), position="pre-lease", acknowledgement="")
    assert "window_journal_open" in plan.refusals and plan.public_summary()["deadman_recovery"] is False
    assert plan.public_summary()["deadman_state"] == "deadman_superseded"


# --- one digest at every site, and the resume -------------------------------------------------


def _facts(estate: Estate) -> Any:
    return window.collect_facts(
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


def test_the_plan_the_status_and_the_re_pin_under_the_lease_compute_one_digest(
    tmp_path: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    estate = _estate()
    started = _open_then_die(estate)
    facts = _facts(estate)
    plan = window_plan(facts, position="pre-lease", acknowledgement="")
    assert (facts.owner_start_state, facts.owner_start_creator) == ("candidate", "other")
    assert plan.public_summary()["owner_start_sha256"] == facts.owner_start_sha256

    out = tmp_path / "status.env"
    argv = ["status", "--recovery", "--app-name", APP_NAME, "--out-env", str(out)]
    assert window.main(argv, workspace_factory=lambda: estate.workspace) == 0
    printed = capsys.readouterr().out
    assert "MIP_QUIESCE_WINDOW_RECOVERY=owner_start\n" in out.read_text(encoding="utf-8")
    assert f"recovery owner_start (owner start candidate, creator other, sha256 {facts.owner_start_sha256})" in printed
    assert started not in printed and RELEASE not in printed, "codes and the digest only"

    under_lease = _refuses(estate, "owner_start_unacked", ack="")
    assert facts.owner_start_sha256 in str(under_lease)
    assert _classify(estate).sha256 == facts.owner_start_sha256


def test_the_plan_command_reads_the_ack_from_its_environment_and_only_before_the_lease(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The CLI the deploy shell runs: the plan phase prints the digest, the release carries it."""

    estate = _estate()
    started = _open_then_die(estate)
    digest = _classify(estate).sha256
    files = {
        "--plan-gate-report": {"verdict": "passed", "instance_update_sha256": ""},
        "--binding-payload": {"name": APP_NAME, "description": "", "resources": estate.apps.resources},
        "--migration-plan": MIGRATION_NOOP,
    }
    argv = ["plan", "--app-name", APP_NAME, "--scope", SCOPE, "--lakebase-instance", "mip-app-state"]
    for flag in ("--role-check", "--verifier-role-check", "--jobs-reader-role-check"):
        argv += [flag, "exact"]
    for index, (flag, payload) in enumerate(files.items()):
        path = tmp_path / f"input-{index}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        argv += [flag, str(path)]

    def plan(position: str) -> tuple[int, str, dict[str, Any]]:
        out = tmp_path / f"{position}.json"
        code = window.main([*argv, "--position", position, "--out", str(out)], workspace_factory=lambda: estate.workspace)
        printed = capsys.readouterr()
        assert started not in printed.out + printed.err and RELEASE not in printed.out + printed.err
        return code, printed.out + printed.err, json.loads(out.read_text(encoding="utf-8"))

    before = _frozen(estate)
    monkeypatch.delenv(window.ACK_ENV, raising=False)
    monkeypatch.delenv(recovery.ACK_ENV, raising=False)
    code, printed, written = plan("pre-lease")
    assert code == 2 and written["refusals"] == ["window_journal_open"]
    assert f"re-pin it on a release with operation_acks owner_start_repin={digest}" in printed
    assert "REFUSED window_journal_open (remedy in the private plan)" in printed
    assert "owner_start_repin digest this plan prints (repin --from-owner-start;" in written["remedies"]["window_journal_open"]

    monkeypatch.setenv(recovery.ACK_ENV, "0" * 64)
    assert plan("pre-lease")[0] == 2, "another plan's digest"
    monkeypatch.setenv(window.ACK_ENV, digest)  # the window's own ack is another purpose
    monkeypatch.delenv(recovery.ACK_ENV)
    assert plan("pre-lease")[0] == 2
    monkeypatch.delenv(window.ACK_ENV)

    monkeypatch.setenv(recovery.ACK_ENV, f" {digest}\n")
    code, printed, written = plan("pre-lease")
    assert code == 0 and written["refusals"] == [] and written["code"] == "none"
    assert "this run re-pins it under the lease (repin --from-owner-start" in printed
    # Under the lease the recovery ran first: a journal still open at pre-apply is refused, acked or not.
    code, _printed, written = plan("pre-apply")
    assert code == 2 and written["refusals"] == ["window_journal_open"]
    assert _frozen(estate) == before, "a plan reads only"


def test_a_re_pin_signed_before_a_failed_journal_close_resumes_with_the_same_ack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    estate = _estate()
    started = _open_then_die(estate)
    digest = _classify(estate).sha256
    real_write = journal.write

    def lease_lost(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("the lease was lost before the journal close")

    monkeypatch.setattr(journal, "write", lease_lost)
    with pytest.raises(RuntimeError, match="lease was lost"):
        _repin(estate, digest)
    signed = estate.secrets.values.copy()
    assert stored_record(estate)["deployment_id"] == started, "the record was re-pinned"
    assert journal.read(estate.workspace, app_name=APP_NAME)["state"] == "window_open"

    monkeypatch.setattr(journal, "write", real_write)
    assert _classify(estate).sha256 == digest, "the record is not in the plan: the same ack still names it"
    record = _repin(estate, digest)
    assert record["deployment_id"] == started and record["predecessor_deployment_id"] == BLUE_ID
    assert estate.secrets.values == signed, "never signed a second time"
    assert journal.read(estate.workspace, app_name=APP_NAME)["closed_by"] == "owner_start_repin"


def test_a_recovering_run_that_also_needs_a_window_keeps_its_pre_lease_window_ack() -> None:
    # The predecessor rule, as for the dead-man's re-pin: the pre-lease window digest names
    # the old blue; the owner-start re-pin moves blue to the owner's start (its predecessor
    # the old blue), so the pre-apply recheck matches that ack through the re-pin alone.
    from tests.unit.test_quiesce_window_start_repin import MIGRATION_APPLY

    estate = _estate()
    started = _open_then_die(estate)
    owner_ack = _classify(estate).sha256

    def plan(position: str, ack: str, owner: str = owner_ack) -> Any:
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
            migration_plan=MIGRATION_APPLY,
            rebase_unverified_app=False,
        )
        return window_plan(facts, position=position, acknowledgement=ack, owner_start_acknowledgement=owner)

    unacked = plan("pre-lease", "", owner="")
    assert set(unacked.refusals) == {"window_journal_open", "window_unacked"}
    digest = unacked.sha256
    lease = plan("pre-lease", digest)
    assert lease.code == "window" and lease.public_summary()["owner_start_recovery"] is True
    assert lease.canonical["blue_deployment_id"] == BLUE_ID
    _repin(estate, owner_ack)
    recheck = plan("pre-apply", digest)
    assert recheck.code == "window" and recheck.ack == "matched", recheck.refusals
    assert recheck.canonical["blue_deployment_id"] == started and recheck.sha256 != digest
    assert recheck.owner_start_ack == "not_required", "the journal is closed: nothing is left to acknowledge"


def test_status_without_the_flag_is_unchanged_and_the_flag_needs_the_verified_env_file(
    tmp_path: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    estate = _estate()
    _open_then_die(estate)
    out = tmp_path / "status.env"
    reads = estate.apps.get_calls
    assert window.main(["status", "--app-name", APP_NAME, "--out-env", str(out)], workspace_factory=lambda: estate.workspace) == 0
    plain = capsys.readouterr().out
    assert "MIP_QUIESCE_WINDOW_RECOVERY" not in out.read_text(encoding="utf-8") and "recovery" not in plain
    assert estate.apps.get_calls == reads, "the EXIT trap's status reads the journal and the marker only"
    for argv in (["status", "--recovery", "--app-name", APP_NAME], ["status", "--recovery", "--unverified", "--app-name", APP_NAME]):
        with pytest.raises(SystemExit, match="status --recovery is the verified read, with --out-env"):
            window.main(argv, workspace_factory=lambda: estate.workspace)


@pytest.mark.parametrize("closed", (False, True), ids=("no-journal", "closed-journal"))
def test_status_recovery_reads_nothing_of_the_app_when_no_window_is_open(
    tmp_path: Any, capsys: pytest.CaptureFixture[str], closed: bool
) -> None:
    estate = _estate()
    if closed:
        _open_then_die(estate)
        _repin(estate, _classify(estate).sha256)
    reads = (estate.apps.get_calls, estate.apps.starts, estate.apps.stops)
    out = tmp_path / "status.env"
    argv = ["status", "--recovery", "--app-name", APP_NAME, "--out-env", str(out)]
    assert window.main(argv, workspace_factory=lambda: estate.workspace) == 0
    assert "MIP_QUIESCE_WINDOW_RECOVERY=none\n" in out.read_text(encoding="utf-8")
    assert "recovery none (owner start absent, creator unknown)" in capsys.readouterr().out
    assert (estate.apps.get_calls, estate.apps.starts, estate.apps.stops) == reads


def test_status_recovery_refines_a_dead_man_restart_the_app_no_longer_serves(tmp_path: Any) -> None:
    estate = _estate()
    _dead_runner(estate)
    out = tmp_path / "status.env"
    argv = ["status", "--recovery", "--app-name", APP_NAME, "--out-env", str(out)]
    assert window.main(argv, workspace_factory=lambda: estate.workspace) == 0
    served = out.read_text(encoding="utf-8")
    assert "MIP_QUIESCE_WINDOW_RECOVERY=deadman\n" in served and "MIP_QUIESCE_WINDOW_DEADMAN_STATE=deadman_restarted\n" in served
    _down(estate)
    assert window.main(argv, workspace_factory=lambda: estate.workspace) == 0
    superseded = out.read_text(encoding="utf-8")
    assert "MIP_QUIESCE_WINDOW_RECOVERY=none\n" in superseded
    assert "MIP_QUIESCE_WINDOW_DEADMAN_STATE=deadman_superseded\n" in superseded, "never the dead-man arm again"
    # Without the flag the EXIT trap still reads the marker's own state.
    assert window.main(["status", "--app-name", APP_NAME, "--out-env", str(out)], workspace_factory=lambda: estate.workspace) == 0
    assert "MIP_QUIESCE_WINDOW_DEADMAN_STATE=deadman_restarted\n" in out.read_text(encoding="utf-8")


# --- the codes are a closed, reviewed set ----------------------------------------------------


def test_every_refusal_code_is_reviewed_and_public_output_is_codes_only() -> None:
    assert recovery.REFUSALS == OWNER_START_REFUSALS == (
        "owner_start_contract_window",
        "owner_start_marker_unreadable",
        "owner_start_no_baseline",
        "owner_start_not_running",
        "owner_start_none",
        "owner_start_ambiguous",
        "owner_start_not_current",
        "owner_start_not_a_start",
    )
    # What the marker may read is exactly what the dead-man job may write, plus the three
    # reads of a missing, foreign or broken marker: a state added to the job is reviewed here.
    from pathlib import Path

    from tests.fixtures.quiesce_window_estate import load_deadman

    job = load_deadman((Path(__file__).resolve().parents[2] / "jobs" / "window_deadman.py").read_bytes(), "deadman_states")
    assert job["MARKER_STATES"] | {"absent", "mismatch", "unreadable"} == recovery.MARKER_READS
    assert recovery.KNOWN_START_MARKERS - {recovery.SUPERSEDED} < job["MARKER_STATES"]
    under_the_lease = {"owner_start_usage", "owner_start_no_window", "owner_start_deadman", "owner_start_unacked", "owner_start_unproven"}
    assert {*OWNER_START_REFUSALS, *under_the_lease} == REFUSAL_CODES
    with pytest.raises(ValueError):
        OwnerStartRepinRefused("owner_start_whatever", "an unreviewed code")
    with pytest.raises(ValueError):
        recovery.Recovery(kind="owner_start")  # a candidate always carries its code
    with pytest.raises(ValueError):
        recovery.Recovery(deadman_state="deadman_restarted by owner-caller")
    estate = _estate()
    started = _open_then_die(estate)
    public = json.dumps(_classify(estate).public(), sort_keys=True)
    for private in (started, BLUE_ID, RELEASE, OWNER_CALLER, DEPLOYER_CALLER, NEXT_LEASE):
        assert private not in public, private


def test_the_two_new_modules_call_no_apps_start_stop_deploy_or_update() -> None:
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "tools" / "databricks"
    for name in ("quiesce_window_recovery.py", "app_deployment_owner_start_repin.py"):
        tree = ast.parse((root / name).read_text(encoding="utf-8"))
        called = {node.func.attr for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
        assert not called & {"start", "stop", "deploy", "deploy_and_wait", "update", "create_update", "delete", "upload"}, name
        assert {"get", "list_deployments", "get_deployment"} & called or name != "quiesce_window_recovery.py"

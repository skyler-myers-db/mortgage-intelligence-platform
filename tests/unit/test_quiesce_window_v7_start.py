"""The quiesce window's start-and-re-pin over a v7 core blue (M0-PR8 for M2-PR7b, spike S7).

Client #1's case: blue was adopted as a v7 core record. Spike S7 recorded how a
start behaves, so the window no longer refuses a v7 core blue
(``window_over_v7_blue``): it stops, starts, proves the start's deployment
with the same proofs v5/v6 use (blue's source folder, the folder's and the
snapshot's full-tree digests against the record's ``source_tree_sha256``, the
record's own env-name set, health pinned to the new id) and re-signs the
record onto it (``core_blue_repin``: the next v7 version, same provenance,
``predecessor_deployment_id`` blue). Step 0a starts and re-pins a stopped
core blue the same way (tier a). The Apps API is the measured one
(tests/fixtures/measured_apps.py). A v7 agentic record stays refused.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from tests.fixtures import rollback_records as rr
from tests.fixtures.core_blue_world import CoreWorld, adopted_core_world
from tests.fixtures.measured_apps import virtual_start_clock
from tools.databricks import app_core_rollback as cli
from tools.databricks import app_rollback_record_contract as contract
from tools.databricks import core_blue, core_blue_repin, core_blue_restore
from tools.databricks import quiesce_window as window
from tools.databricks import quiesce_window_facts as facts
from tools.databricks import quiesce_window_journal as journal
from tools.databricks.app_deployment_lease import LEASE_ROOT
from tools.databricks.app_rollback_resource_contract import (
    app_resource_contract_digest,
    validated_app_resource_contract,
)
from tools.databricks.app_start_deployment import StartProofError, ensure_started, tree_digest
from tools.databricks.quiesce_window_plan import window_plan

APPLY = {
    "mode": "plan",
    "decision": "apply",
    "blue_restorable": True,
    "migrations": {"pending": ["0042"], "pending_contract": []},
    "jobs_reader_grants": "exact",
    "app_role_regrant": "exact",
}
BLUE = "deployment-blue"


@pytest.fixture(autouse=True)
def _keys(monkeypatch: pytest.MonkeyPatch) -> None:
    rr.signing_env(monkeypatch)
    monkeypatch.setattr(contract, "assert_owned_app_rollback_scope", lambda *_a, **_kw: None)
    monkeypatch.setattr(facts, "app_or_none", lambda workspace, *, app_name: workspace.apps.get(app_name))
    virtual_start_clock(monkeypatch)


@pytest.fixture
def world(tmp_path: Path) -> CoreWorld:
    adopted = adopted_core_world(tmp_path)
    adopted.tree.add_dir(LEASE_ROOT)  # the journal's folder, beside the deployment lease
    return adopted


def _plan(world: CoreWorld, **overrides: Any) -> Any:
    values: dict[str, Any] = {
        "app_name": rr.APP_NAME,
        "scope": rr.SCOPE,
        "lakebase_instance": rr.LAKEBASE,
        "gate_report": {"verdict": "passed", "instance_update_sha256": ""},
        "binding_payload": {"name": rr.APP_NAME, "resources": copy.deepcopy(rr.APP_RESOURCES)},
        "role_state": "exact",
        "verifier_role_state": "absent",
        "jobs_reader_role_state": "exact",
        "migration_plan": APPLY,
        "rebase_unverified_app": False,
        **overrides,
    }
    found = facts.collect_facts(world, **values)
    first = window_plan(found, position="pre-apply", acknowledgement="")
    return window_plan(found, position="pre-apply", acknowledgement=first.sha256)


def _stop(world: CoreWorld) -> Any:
    def stop() -> str:
        world.apps.mutations.append("stop")
        world.compute = "STOPPED"
        return "stopped"

    return stop


def _open(world: CoreWorld) -> dict[str, Any]:
    plan = _plan(world)
    assert plan.code == "window" and plan.reasons == ("WR3",), plan.refusals
    return window.open_window(
        world,
        app_name=rr.APP_NAME,
        plan=plan.full(),
        acknowledgement=plan.sha256,
        lease_id=rr.LEASE_ID,
        source_git_sha=rr.GIT_SHA,
        assert_lease_held=lambda: None,
        stop=_stop(world),
        admit=lambda _canonical: None,
        arm=lambda _canonical: "run-1",
        cancel=lambda _run_id: None,
    )


def _start_and_await(world: CoreWorld) -> str:
    window.start_window(world, app_name=rr.APP_NAME, assert_lease_held=lambda: None, stop=_stop(world))
    return window.await_window(
        world,
        app_name=rr.APP_NAME,
        scope=rr.SCOPE,
        lakebase_instance=rr.LAKEBASE,
        assert_lease_held=lambda: None,
        stop=_stop(world),
    )


def _repin(world: CoreWorld, start_id: str, tree: str | None = None) -> dict[str, Any]:
    return core_blue_repin.repin_core_start(
        world,
        app_name=rr.APP_NAME,
        scope=rr.SCOPE,
        lakebase_instance=rr.LAKEBASE,
        deployment_id=start_id,
        expected_rollback_deployment_id=BLUE,
        health=world.serves,
        lease=world.lease,
        expected_tree_sha256=tree,
    )


def test_a_core_blue_window_plans_against_the_records_own_tree(world: CoreWorld) -> None:
    record = world.record()
    assert record is not None
    plan = _plan(world)
    assert plan.code == "window" and plan.facts.blue_current
    assert plan.facts.artifact_tree_sha256 == record["source_tree_sha256"] == plan.facts.source_tree_sha256
    assert plan.facts.env_names_sha256 == record["env_names_sha256"]
    assert world.apps.mutations == [], "planning is read-only"


def test_a_core_blue_window_starts_proves_and_re_pins_the_v7_record(world: CoreWorld) -> None:
    before = world.record()
    assert before is not None
    opened = _open(world)
    proven = _start_and_await(world)
    record = _repin(world, proven, opened["source_tree_sha256"])
    window.close_window(
        world,
        app_name=rr.APP_NAME,
        scope=rr.SCOPE,
        lakebase_instance=rr.LAKEBASE,
        repinned=proven,
        superseded_by_activation=False,
        assert_lease_held=lambda: None,
    )

    assert proven != BLUE and world.active == proven, "the start created a new deployment"
    assert (record["version"], record["tier"], record["provenance"]) == (7, "core", "adopted")
    assert record["deployment_id"] == proven and record["predecessor_deployment_id"] == BLUE
    assert (record["ack_purpose"], record["ack_sha256"]) == (before["ack_purpose"], before["ack_sha256"])
    assert record["source_code_path"] == before["source_code_path"]
    assert record["env_names"] == before["env_names"] and record["restore_source_path"] == before["restore_source_path"]
    assert journal.read(world, app_name=rr.APP_NAME)["state"] == "closed"
    assert world.apps.mutations == ["stop", "start"] and world.health_calls[-1] == proven


@pytest.mark.parametrize(
    ("break_it", "code"),
    (
        (lambda world: setattr(world, "start_final", "FAILED"), "start_not_succeeded"),
        (lambda world: setattr(world, "env_names", [*world.env_names, "MIP_EXTRA"]), "start_env_names"),
    ),
    ids=("failed-start", "env-names-drift"),
)
def test_a_core_start_that_is_not_provably_blue_refuses_and_stays_stopped(
    world: CoreWorld, break_it: Any, code: str
) -> None:
    _open(world)
    break_it(world)
    with pytest.raises(RuntimeError, match=code):
        _start_and_await(world)
    entry = journal.read(world, app_name=rr.APP_NAME)
    assert entry["state"] == "window_refused" and entry["refusal"] == code
    assert world.compute == "STOPPED" and world.record()["deployment_id"] == BLUE


def test_a_changed_source_folder_refuses_the_start(world: CoreWorld) -> None:
    _open(world)
    world.tree.add_file(f"{world.folder(BLUE).removeprefix('/Workspace')}/backend/late.py", b"x\n")
    with pytest.raises(RuntimeError, match="start_folder_digest"):
        _start_and_await(world)
    assert world.compute == "STOPPED"


def test_the_core_repin_re_proves_on_its_own_and_is_idempotent(world: CoreWorld) -> None:
    opened = _open(world)
    proven = _start_and_await(world)
    world.env_sets[proven] = [*world.env_sets[proven], "MIP_EXTRA"]
    with pytest.raises(StartProofError, match="start_env_names"):
        _repin(world, proven, opened["source_tree_sha256"])
    world.env_sets[proven] = list(world.env_names)
    first = _repin(world, proven, opened["source_tree_sha256"])
    assert _repin(world, proven, opened["source_tree_sha256"]) == first, "a rerun re-proves, never re-signs"
    with pytest.raises(core_blue.CoreBlueRefused) as refused:
        _repin(world, "deployment-other")
    assert refused.value.code == "record_changed"


def _drift(world: CoreWorld) -> None:
    world.tree.add_file(f"{world.folder(BLUE).removeprefix('/Workspace')}/backend/late.py", b"x\n")


def test_a_folder_that_drifted_from_the_record_before_the_plan_refuses_the_window(world: CoreWorld) -> None:
    """The plan's check is against the record's SIGNED tree, never the folder against itself."""

    _drift(world)
    plan = _plan(world)
    assert plan.code == "refused" and "window_start_source_drift" in plan.refusals
    assert plan.facts.source_tree_sha256 != world.record()["source_tree_sha256"]
    assert plan.facts.artifact_tree_sha256 == world.record()["source_tree_sha256"]
    assert world.apps.mutations == [], "refused before any stop"


def test_a_repin_proves_only_against_the_records_own_tree(world: CoreWorld) -> None:
    """A caller's digest of other bytes never re-signs the record onto a start of them."""

    world.compute = "STOPPED"
    _drift(world)
    drifted = tree_digest(world, world.folder(BLUE))
    started = ensure_started(world, app_name=rr.APP_NAME)
    with pytest.raises(core_blue.CoreBlueRefused) as refused:
        _repin(world, started, drifted)
    assert refused.value.code == "repin_unproven"
    assert world.record()["deployment_id"] == BLUE, "never re-signed"
    with pytest.raises(StartProofError, match="start_tree_digest"):
        _repin(world, started, world.record()["source_tree_sha256"])  # the signed tree: not this start


def _bound(
    world: CoreWorld, live: list[dict[str, Any]], *, applied: list[dict[str, object]] | None
) -> dict[str, Any]:
    """Re-pin a window's proven start while the App is bound to ``live``."""

    opened = _open(world)
    proven = _start_and_await(world)
    world.resources = live
    return core_blue_repin.repin_core_start(
        world,
        app_name=rr.APP_NAME,
        scope=rr.SCOPE,
        lakebase_instance=rr.LAKEBASE,
        deployment_id=proven,
        expected_rollback_deployment_id=BLUE,
        health=world.serves,
        lease=world.lease,
        expected_tree_sha256=opened["source_tree_sha256"],
        applied_app_resources=applied,
    )


def _other_warehouse() -> list[dict[str, Any]]:
    other = copy.deepcopy(rr.APP_RESOURCES)
    other[1]["sql_warehouse"]["id"] = "warehouse-other"  # type: ignore[index]
    return other


def test_a_repin_refuses_live_bindings_no_window_applied(world: CoreWorld) -> None:
    with pytest.raises(core_blue.CoreBlueRefused) as refused:
        _bound(world, _other_warehouse(), applied=None)
    assert refused.value.code == "resource_drift" and world.record()["deployment_id"] == BLUE


def test_a_repin_refuses_an_applied_set_that_rebinds_the_genie_space(world: CoreWorld) -> None:
    rebind = copy.deepcopy(rr.APP_RESOURCES)
    rebind[0]["genie_space"]["space_id"] = "genie-space-other"  # type: ignore[index]
    with pytest.raises(core_blue.CoreBlueRefused) as refused:
        _bound(world, rebind, applied=validated_app_resource_contract(rebind))
    assert refused.value.code == "resource_drift" and "Genie space" in str(refused.value)
    assert world.record()["deployment_id"] == BLUE


def test_a_repin_records_the_exact_bindings_a_wr1_window_applied(world: CoreWorld) -> None:
    applied = validated_app_resource_contract(_other_warehouse())
    record = _bound(world, _other_warehouse(), applied=applied)
    assert record["app_resources"] == applied
    assert record["app_resources_sha256"] == app_resource_contract_digest(applied)
    assert world.record()["app_resources"] == applied


def test_an_agentic_v7_record_is_still_refused(world: CoreWorld) -> None:
    contract.save_versioned_record(world, scope=rr.SCOPE, record=rr.v7_agentic_record())
    with pytest.raises(RuntimeError, match=contract.V7_REFUSAL):
        _plan(world)


# --- step 0a over a stopped core blue (tier a) -------------------------------------------


def test_step_0a_starts_and_re_pins_a_stopped_core_blue(world: CoreWorld) -> None:
    world.compute = "STOPPED"
    record = core_blue_restore.ensure_core_blue(world, **world.kwargs())

    assert record["deployment_id"] == world.active != BLUE
    assert record["predecessor_deployment_id"] == BLUE and record["provenance"] == "adopted"
    assert world.apps.mutations == ["start"], "tier a: a start only, no redeploy"
    assert world.treatments == ["quiesce"], "step 0a leaves treatment quiesced"


def test_a_window_acked_over_a_stopped_blue_opens_after_step_0a_re_pins_it(world: CoreWorld) -> None:
    """Round 2 of #334: the pre-lease ack names the stopped blue; step 0a then re-pins its start."""

    world.compute = "STOPPED"  # client #1's shape: stopped, blue newest SUCCEEDED
    values = {
        "app_name": rr.APP_NAME,
        "scope": rr.SCOPE,
        "lakebase_instance": rr.LAKEBASE,
        "gate_report": {"verdict": "passed", "instance_update_sha256": ""},
        "binding_payload": {"name": rr.APP_NAME, "resources": copy.deepcopy(rr.APP_RESOURCES)},
        "role_state": "exact",
        "verifier_role_state": "absent",
        "jobs_reader_role_state": "exact",
        "migration_plan": APPLY,
        "rebase_unverified_app": False,
    }
    ack = window_plan(facts.collect_facts(world, **values), position="pre-lease", acknowledgement="").sha256
    pre_lease = window_plan(facts.collect_facts(world, **values), position="pre-lease", acknowledgement=ack)
    assert pre_lease.code == "window" and pre_lease.canonical["blue_deployment_id"] == BLUE

    record = core_blue_restore.ensure_core_blue(world, **world.kwargs())  # step 0a: tier a
    started = record["deployment_id"]
    assert started != BLUE and record["predecessor_deployment_id"] == BLUE

    pre_apply = window_plan(facts.collect_facts(world, **values), position="pre-apply", acknowledgement=ack)
    assert pre_apply.code == "window" and pre_apply.ack == "matched", pre_apply.refusals
    assert pre_apply.canonical["blue_deployment_id"] == started
    opened = window.open_window(
        world,
        app_name=rr.APP_NAME,
        plan=pre_apply.full(),
        acknowledgement=ack,
        lease_id=rr.LEASE_ID,
        source_git_sha=rr.GIT_SHA,
        assert_lease_held=lambda: None,
        stop=_stop(world),
        admit=lambda _canonical: None,
        arm=lambda _canonical: "run-1",
        cancel=lambda _run_id: None,
    )
    assert opened["state"] == "window_open" and opened["blue_deployment_id"] == started
    assert opened["window_sha256"] == pre_apply.sha256 and world.apps.mutations == ["start", "stop"]
    with pytest.raises(RuntimeError, match="exact window digest"):
        window._validated_plan(pre_apply.full(), acknowledgement="0" * 64)


def test_step_0a_restores_a_stopped_core_app_whose_newest_deployment_is_not_blue(world: CoreWorld) -> None:
    payload = world.prove_restore_payload()
    world.activate_green()
    world.compute = "STOPPED"
    record = core_blue_restore.ensure_core_blue(world, **world.kwargs())

    assert world.apps.mutations == ["start", "deploy_and_wait"] and world.apps.deployed == [payload]
    assert record["deployment_id"] == world.active and record["predecessor_deployment_id"] == BLUE


def test_the_cli_repin_mode_and_its_usage(world: CoreWorld, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MIP_BEARER_TOKEN", "token")
    monkeypatch.setattr(core_blue, "core_health", lambda *_a, **_kw: world.serves)
    monkeypatch.setattr(cli, "_lease", lambda *_a, **_kw: world.lease)
    opened = _open(world)
    proven = _start_and_await(world)
    base = [
        "repin", "--app-name", rr.APP_NAME, "--scope", rr.SCOPE, "--lakebase-instance", rr.LAKEBASE,
        "--agentic-tier", "off", "--base-url", "https://mip.example", "--deployment-lease-id", rr.LEASE_ID,
        "--deployment-source-git-sha", rr.GIT_SHA,
    ]  # fmt: skip
    assert cli.main([*base, "--to", proven], workspace_factory=lambda: world) == 2, "usage: the blue and tree"
    out = tmp_path / "binding.env"
    argv = [
        *base, "--to", proven, "--expected-rollback-deployment-id", BLUE,
        "--expected-tree-sha256", opened["source_tree_sha256"], "--out-env", str(out),
    ]  # fmt: skip
    assert cli.main(argv, workspace_factory=lambda: world) == 0
    assert f"MIP_APP_ROLLBACK_DEPLOYMENT_ID={proven}" in out.read_text(encoding="utf-8")


@pytest.mark.parametrize("tier", ("off", "on"))
def test_the_window_library_re_pins_with_the_tiers_own_tool(tmp_path: Path, tier: str) -> None:
    # The window's close and the dead-man recovery, in the real window library.
    from tests.unit.test_quiesce_window_shell import _run

    (tmp_path / "close").mkdir()
    (tmp_path / "recover").mkdir()
    closed, close_calls = _run(
        tmp_path / "close",
        "QUIESCE_WINDOW_STATE=window_open\nstop_app_after_failed_deploy\n",
        JOURNAL="window_open",
        CLIENT_AGENTIC_TIER=tier,
    )
    assert closed.returncode == 0, closed.stderr
    recovered, recover_calls = _run(
        tmp_path / "recover",
        "recover_deadman_quiesce_window\necho recovered\n",
        JOURNAL="window_open",
        DEADMAN_STATE="deadman_restarted",
        CLIENT_AGENTIC_TIER=tier,
    )
    assert recovered.returncode == 0, recovered.stderr
    core = "app_core_rollback repin --lakebase-instance instance --agentic-tier off"
    for calls, flag in ((close_calls, "--to deployment-start-1"), (recover_calls, "--from-deadman")):
        repin = [call for call in calls if "rollback repin" in call]
        assert len(repin) == 1 and flag in repin[0]
        assert (core in repin[0]) is (tier == "off"), "a v7 core blue is re-pinned by the core tool"
        assert ("app_deployment_rollback repin" in repin[0]) is (tier == "on")


def test_the_dead_man_restarts_a_stopped_core_blue_and_the_next_run_re_pins_it(
    world: CoreWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.fixtures.quiesce_window_estate import load_deadman
    from tools.databricks import quiesce_window_deadman as deadman

    opened = _open(world)
    token = deadman.window_token(opened["window_sha256"], opened["lease_id"])
    now = 1_800_000_000.0
    clock = {"now": now}

    def sleep(seconds: float) -> None:
        clock["now"] += seconds

    argv = [
        "--app", rr.APP_NAME, "--app-id", "app-object-id", "--journal", journal.journal_path(rr.APP_NAME),
        "--marker", deadman.marker_path(rr.APP_NAME, token), "--armed-marker", deadman.armed_path(rr.APP_NAME, token),
        "--token", token, "--window-sha256", opened["window_sha256"], "--blue-deployment-id", BLUE,
        "--restore-source", opened["restore_source"], "--deadline", str(int(now) + 600),
        "--blue-restorable", "true", "--start-timeout-seconds", "600", "--poll-seconds", "60",
    ]  # fmt: skip
    module = load_deadman((Path(__file__).resolve().parents[2] / "jobs/window_deadman.py").read_bytes(), "deadman_v7")
    assert module["main"](argv, workspace_factory=lambda: world, clock=lambda: clock["now"], sleep=sleep) == 0
    marker = deadman.read_marker(world, app_name=rr.APP_NAME, token=token)
    assert marker is not None and marker["state"] == "deadman_restarted"
    started = marker["start_deployment_id"]
    assert started == world.active != BLUE and world.compute == "ACTIVE"

    monkeypatch.setenv("MIP_BEARER_TOKEN", "token")
    monkeypatch.setattr(core_blue, "core_health", lambda *_a, **_kw: world.serves)
    monkeypatch.setattr(cli, "_lease", lambda *_a, **_kw: world.lease)
    argv = [
        "repin", "--from-deadman", "--app-name", rr.APP_NAME, "--scope", rr.SCOPE,
        "--lakebase-instance", rr.LAKEBASE, "--agentic-tier", "off", "--base-url", "https://mip.example",
        "--deployment-lease-id", rr.LEASE_ID, "--deployment-source-git-sha", rr.GIT_SHA,
    ]  # fmt: skip
    assert cli.main(argv, workspace_factory=lambda: world) == 0
    record = world.record()
    assert record["deployment_id"] == started and record["predecessor_deployment_id"] == BLUE
    closed = journal.read(world, app_name=rr.APP_NAME)
    assert closed["state"] == "closed" and closed["closed_by"] == "deadman_repin"
    assert world.apps.mutations == ["stop", "start"], "the dead-man's one start; nothing else"

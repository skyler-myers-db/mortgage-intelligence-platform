"""A failed tier-a start never strands the next release over a payload-less core blue (spike S7).

Client #1's first governed release is an adopted v7 core record with no
restore payload over a stopped App, so step 0a's tier a starts the real App
for the first time. Whatever ends that start before its re-pin (a FAILED
install, a refused proof or health check, a lost runner) leaves the start as
the App's newest deployment of any state: from blue's own folder, with blue's
env-name set, the default source still that folder. Before this fix the next
run's pre-stop probe passed it with a "restores the record's deployment" note
and step 0a then took tier b and refused ``no_restore_payload`` under the
lease after quiescing treatment, on every later run, so only an out-of-band
start plus an adopt supersede could recover it.

Now step 0a reads that stranded start (``core_blue_restore.stranded_start``):
a running App serving it SUCCEEDED has it proven against the signed tree and
re-pinned in place; otherwise it is stopped if running and blue's folder is
started again once it still hashes to the signed tree. Any other deployment
over a record with no restore payload is refused before the lease, exactly as
step 0a would refuse it. The pre-stop probe reads the same function, and the
last test sweeps every state for that parity.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from tests.fixtures import rollback_records as rr
from tests.fixtures.core_blue_world import CoreWorld, adopted_core_world
from tests.fixtures.measured_apps import virtual_start_clock
from tools.databricks import app_rollback_record_contract as contract
from tools.databricks import core_blue_restore
from tools.databricks import prestop_rollback_record as prestop
from tools.databricks import stop_app_fail_closed as stops
from tools.databricks.app_deployment_lease import LEASE_ROOT
from tools.databricks.app_start_deployment import StartProofError, ensure_started
from tools.databricks.core_blue import CoreBlueRefused

BLUE = "deployment-blue"
START = "deployment-started-2"  # the failed tier-a start: the world's third deployment
DRIFT = "backend/late.py"


@pytest.fixture(autouse=True)
def _keys(monkeypatch: pytest.MonkeyPatch) -> None:
    rr.signing_env(monkeypatch)
    monkeypatch.setattr(contract, "assert_owned_app_rollback_scope", lambda *_a, **_kw: None)
    virtual_start_clock(monkeypatch)  # a start's 5 s polls cost no real time
    real = stops.stop_app_fail_closed
    monkeypatch.setattr(stops, "stop_app_fail_closed", lambda **kw: real(**{**kw, "interval_s": 0}))


@pytest.fixture
def world(tmp_path: Path) -> CoreWorld:
    adopted = adopted_core_world(tmp_path)
    adopted.tree.add_dir(LEASE_ROOT)
    assert adopted.record()["restore_payload"] is None, "client #1's adopted blue: no payload"
    return adopted


def _verdict(world: CoreWorld) -> Any:
    return prestop.rollback_record_verdict(
        world,
        app_name=rr.APP_NAME,
        scope=rr.SCOPE,
        app_exists=True,
        rebase_unverified_app=False,
        journal_state=lambda: "absent",
        lakebase_instance=rr.LAKEBASE,
        agentic_tier="off",
    )


def _ensure(world: CoreWorld, **overrides: Any) -> dict[str, Any]:
    return core_blue_restore.ensure_core_blue(world, **world.kwargs(**overrides))


def _drift_path(world: CoreWorld) -> str:
    return f"{world.folder(BLUE).removeprefix('/Workspace')}/{DRIFT}"


def _drift(world: CoreWorld) -> None:
    world.tree.add_file(_drift_path(world), b"x = 1\n")


def _undrift(world: CoreWorld) -> None:
    del world.tree.nodes[_drift_path(world)]


def _new_run(world: CoreWorld) -> None:
    world.apps.mutations.clear()
    world.start_final = "SUCCEEDED"  # whatever failed went away


# --- the states a tier-a start can be left in ------------------------------------------------


def failed_install(world: CoreWorld) -> None:
    """Run 1's start fails its install: step 0a stops it, identity-pinned (round 1's fix)."""

    world.compute = "STOPPED"
    world.start_final = "FAILED"
    with pytest.raises(StartProofError) as refused:
        _ensure(world)
    assert refused.value.code == "start_not_succeeded"
    assert world.apps.mutations == ["start", "stop"] and world.compute == "STOPPED"
    assert world.deployments[-1] == START and world.record()["deployment_id"] == BLUE
    _new_run(world)


def failed_health(world: CoreWorld) -> None:
    """Run 1's start succeeds but its health fails: it is stopped too, never left serving."""

    world.compute = "STOPPED"
    real = world.serves

    def unhealthy(record: dict[str, Any], deployment_id: str) -> None:
        if deployment_id != BLUE:
            raise CoreBlueRefused("health_mismatch", "the started App's health fails")
        real(record, deployment_id)

    with pytest.raises(CoreBlueRefused) as refused:
        _ensure(world, health=unhealthy)
    assert refused.value.code == "health_mismatch"
    assert world.apps.mutations == ["start", "stop"] and world.compute == "STOPPED"
    _new_run(world)


def lost_runner(world: CoreWorld) -> None:
    """Run 1's runner dies after the start and before the re-pin: the start keeps serving."""

    world.compute = "STOPPED"
    assert ensure_started(world, app_name=rr.APP_NAME) == START
    assert world.compute == "ACTIVE" and world.active == START
    _new_run(world)


def lost_runner_after_a_failed_install(world: CoreWorld) -> None:
    """The runner dies between a FAILED install and its stop: a FAILED start is active."""

    world.compute = "STOPPED"
    world.start_final = "FAILED"
    ensure_started(world, app_name=rr.APP_NAME)
    assert world.compute == "ACTIVE" and world.active == START
    assert world.deployment(START).status.state == "FAILED"
    _new_run(world)


STRANDED: dict[str, Callable[[CoreWorld], None]] = {
    "failed-install": failed_install,
    "failed-health": failed_health,
    "lost-runner": lost_runner,
    "lost-runner-after-a-failed-install": lost_runner_after_a_failed_install,
}


# --- the next run recovers, with deploy.sh alone ----------------------------------------------


@pytest.mark.parametrize("strand", tuple(STRANDED.values()), ids=tuple(STRANDED))
def test_the_next_run_recovers_a_stranded_tier_a_start(world: CoreWorld, strand: Any) -> None:
    blue = world.record()
    strand(world)

    verdict = _verdict(world)
    assert verdict.refusals == (), "the pre-stop probe passes what step 0a recovers"
    assert "earlier" in verdict.notes[0] and "tier a" in verdict.notes[0]
    record = _ensure(world)

    assert record["deployment_id"] == world.active and world.compute == "ACTIVE"
    assert record["predecessor_deployment_id"] == BLUE, "re-pinned from blue, never from the start"
    assert record["source_tree_sha256"] == blue["source_tree_sha256"], "blue's signed tree, kept"
    assert world.record() == record and world.treatments[-1] == "quiesce"
    assert "deploy_and_wait" not in world.apps.mutations, "tier a, never a tier-b restore"


def test_a_lost_runner_s_succeeded_start_is_re_pinned_in_place(world: CoreWorld) -> None:
    lost_runner(world)

    assert "re-pins it" in _verdict(world).notes[0]
    record = _ensure(world)
    assert world.apps.mutations == [], "proven against the signed tree and re-pinned, no restart"
    assert record["deployment_id"] == START and world.health_calls[-1] == START


@pytest.mark.parametrize(
    ("strand", "mutations"),
    (
        (failed_install, ["start"]),
        (failed_health, ["start"]),
        (lost_runner_after_a_failed_install, ["stop", "start"]),
    ),
    ids=("failed-install", "failed-health", "lost-runner-after-a-failed-install"),
)
def test_an_unsucceeded_or_stopped_start_is_restarted_from_blues_folder(
    world: CoreWorld, strand: Any, mutations: list[str]
) -> None:
    strand(world)

    record = _ensure(world)
    assert world.apps.mutations == mutations
    assert record["deployment_id"] == world.deployments[-1] != START


def test_a_start_that_keeps_failing_is_stopped_again_and_never_strands(world: CoreWorld) -> None:
    failed_install(world)
    world.start_final = "FAILED"

    assert _verdict(world).refusals == ()
    with pytest.raises(StartProofError):
        _ensure(world)
    assert world.apps.mutations == ["start", "stop"] and world.compute == "STOPPED"
    _new_run(world)
    assert _verdict(world).refusals == () and _ensure(world)["predecessor_deployment_id"] == BLUE


def test_a_stranded_start_from_a_folder_edited_since_is_refused_before_the_lease(
    world: CoreWorld,
) -> None:
    """A start reruns the folder's CURRENT bytes: edited ones are never started, and say so."""

    failed_install(world)
    _drift(world)

    verdict = _verdict(world)
    assert [refusal.code for refusal in verdict.refusals] == ["rollback_record_drift"]
    assert "no_restore_payload" in verdict.refusals[0].message
    with pytest.raises(Exception) as refused:
        _ensure(world)
    assert getattr(refused.value, "code", None) == "no_restore_payload"
    assert world.apps.mutations == [] and world.compute == "STOPPED"

    _undrift(world)  # the owner puts blue's bytes back: the next run recovers
    assert _verdict(world).refusals == ()
    assert _ensure(world)["predecessor_deployment_id"] == BLUE


def test_a_lost_runner_s_start_of_edited_bytes_is_stopped_never_re_pinned(world: CoreWorld) -> None:
    world.compute = "STOPPED"
    real_start = world.apps.start

    def edit_then_start(name: str) -> Any:
        _drift(world)  # edited between the re-hash and the start's snapshot
        return real_start(name)

    world.apps.start = edit_then_start  # type: ignore[method-assign]
    ensure_started(world, app_name=rr.APP_NAME)
    world.apps.start = real_start  # type: ignore[method-assign]
    _new_run(world)

    assert _verdict(world).refusals == (), "step 0a must stop what it cannot prove"
    with pytest.raises(StartProofError) as refused:
        _ensure(world)
    assert refused.value.code == "start_tree_digest"
    assert world.apps.mutations == ["stop"] and world.compute == "STOPPED"
    assert world.record()["deployment_id"] == BLUE, "never re-signed onto unproven bytes"
    _new_run(world)
    assert "no_restore_payload" in _verdict(world).refusals[0].message


def test_a_deployment_of_blues_folder_with_other_env_values_is_never_taken_for_a_start(
    world: CoreWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A start copies blue's env vars; a peer's deploy of blue's folder may carry other values."""

    lost_runner(world)
    real = world.deployment

    def deployment(deployment_id: str) -> Any:
        found = real(deployment_id)
        if deployment_id == START:
            found.env_vars[0].value = "a-peer's-value"
        return found

    monkeypatch.setattr(world, "deployment", deployment)

    verdict = _verdict(world)
    assert "no_restore_payload" in verdict.refusals[0].message, verdict.notes
    with pytest.raises(Exception) as refused:
        _ensure(world)
    assert getattr(refused.value, "code", None) == "no_restore_payload"
    assert world.record()["deployment_id"] == BLUE, "never re-signed onto it"


# --- any other deployment is tier b's, and refused before the lease without a payload ---------


def test_another_newer_deployment_without_a_payload_is_refused_before_the_lease(
    world: CoreWorld,
) -> None:
    world.activate_green()  # another folder: not a start of blue's
    world.compute = "STOPPED"

    verdict = _verdict(world)
    assert [refusal.code for refusal in verdict.refusals] == ["rollback_record_drift"]
    assert "no_restore_payload" in verdict.refusals[0].message
    assert "MIP_ADOPT_LIVE_SUPERSEDE" in verdict.refusals[0].message
    with pytest.raises(Exception) as refused:
        _ensure(world)
    assert getattr(refused.value, "code", None) == "no_restore_payload"
    assert world.apps.mutations == []


def test_the_drift_remedy_names_the_dispatch_selector_as_a_digest(world: CoreWorld) -> None:
    """B2: through deploy-dev.yml the supersede is operation_acks supersedes_deployment=<sha256>."""

    world.activate_green()
    world.compute = "STOPPED"

    message = _verdict(world).refusals[0].message
    selector = hashlib.sha256(world.record()["deployment_id"].encode("utf-8")).hexdigest()
    assert f"operation_acks supersedes_deployment={selector}" in message
    assert world.record()["deployment_id"] not in message, "a digest, never the id"


def test_the_drift_remedy_names_no_supersede_selector_for_a_record_adopt_live_did_not_sign(
    world: CoreWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only an adopted record is superseded: adopt-live refuses any other as supersede_mismatch.

    A captured core record always carries its restore payload (the v7 record
    refuses one without it), so no signed record reaches this branch with
    another provenance today. The guard is pinned here through the probe's
    record seam, so a later record shape that does reach it is never told to
    pass a selector adopt-live would refuse.
    """

    world.activate_green()
    world.compute = "STOPPED"
    load = prestop.CORE_LOAD
    monkeypatch.setattr(prestop, "CORE_LOAD", lambda *a, **kw: {**load(*a, **kw), "provenance": "captured"})

    refusal = _verdict(world).refusals[0]
    assert refusal.code == "rollback_record_drift" and "no_restore_payload" in refusal.message
    assert "MIP_ADOPT_LIVE_SUPERSEDE" in refusal.message, "the remedy is still named"
    assert "supersedes_deployment" not in refusal.message


def test_another_newer_deployment_with_a_payload_is_still_tier_bs_note(world: CoreWorld) -> None:
    world.prove_restore_payload()
    world.activate_green()
    world.compute = "STOPPED"

    verdict = _verdict(world)
    assert verdict.refusals == () and "restores the record's deployment over it" in verdict.notes[0]
    assert _ensure(world)["source_code_path"] == world.record()["restore_source_path"]
    assert world.apps.mutations == ["start", "deploy_and_wait"]


# --- parity: the pre-stop probe refuses exactly what step 0a refuses ---------------------------


def _activate_green(world: CoreWorld) -> None:
    world.activate_green()


def _stop_on_green(world: CoreWorld) -> None:
    world.activate_green()
    world.compute = "STOPPED"


def _stopped(world: CoreWorld) -> None:
    world.compute = "STOPPED"


def _stopped_drifted(world: CoreWorld) -> None:
    world.compute = "STOPPED"
    _drift(world)


def _stopped_on_another_default_source(world: CoreWorld) -> None:
    world.compute = "STOPPED"
    world.active = "deployment-old"


# Every state with and without blue's restore payload, except the two strands whose
# own failed run assumes none (with a payload, tier b replaces the failed start).
_PAYLOADED = {key: value for key, value in STRANDED.items() if key.startswith("lost-runner")}
PARITY: dict[str, Callable[[CoreWorld], None]] = {
    "serving": lambda _world: None,
    "green-active": _activate_green,
    "stopped-on-green": _stop_on_green,
    "stopped": _stopped,
    "stopped-drifted": _stopped_drifted,
    "stopped-on-another-default-source": _stopped_on_another_default_source,
}
CASES = [
    *((f"{name}-no-payload", state, False) for name, state in {**PARITY, **STRANDED}.items()),
    *((f"{name}-payload", state, True) for name, state in {**PARITY, **_PAYLOADED}.items()),
]


@pytest.mark.parametrize(("state", "payload"), [case[1:] for case in CASES], ids=[case[0] for case in CASES])
def test_the_pre_stop_probe_refuses_exactly_what_step_0a_refuses(
    world: CoreWorld, state: Any, payload: bool
) -> None:
    if payload:
        world.prove_restore_payload()
    state(world)

    verdict = _verdict(world)
    try:
        _ensure(world)
        refused = False
    except Exception:  # noqa: BLE001 - any refusal is step 0a's
        refused = True
    assert bool(verdict.refusals) == refused, (verdict.refusals, verdict.notes)
    if refused:
        assert not {"start", "deploy_and_wait"} & set(world.apps.mutations), "refused before any start"

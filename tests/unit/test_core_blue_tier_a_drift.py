"""Step 0a's tier a over a stopped v7 core blue never leaves an unproven start serving (spike S7).

A start snapshots the CURRENT bytes of the folder blue's deployment names
(spike S7), and anyone who can write that folder changes what it runs. So
before any start step 0a re-hashes the folder against the record's signed
``source_tree_sha256`` and checks blue's env-name set
(``core_blue_restore.start_reruns_record``): a folder that is not blue's goes
to tier b, which restores blue from the immutable copy, or refuses before any
start when the record has no restore payload. A start that then fails its
proof (a FAILED install, or bytes edited after the check) is replaced by tier
b, or, with no restore payload, stopped, pinned to the App's id and the
record's identity. The pre-stop probe predicts the same outcomes before the
lease. Against the review's probe on the branch before this fix, every case
here started the drifted folder and left the App ACTIVE on it.
"""

from __future__ import annotations

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
from tools.databricks.app_start_deployment import StartProofError
from tools.databricks.blue_restore_proof import RestoreProofRefused

BLUE = "deployment-blue"


@pytest.fixture(autouse=True)
def _keys(monkeypatch: pytest.MonkeyPatch) -> None:
    rr.signing_env(monkeypatch)
    monkeypatch.setattr(contract, "assert_owned_app_rollback_scope", lambda *_a, **_kw: None)
    virtual_start_clock(monkeypatch)  # a start's 5 s polls cost no real time


@pytest.fixture
def world(tmp_path: Path) -> CoreWorld:
    adopted = adopted_core_world(tmp_path)
    adopted.tree.add_dir(LEASE_ROOT)
    return adopted


@pytest.fixture
def stop_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """The real fail-closed stop, recording the identity it was pinned to."""

    calls: list[dict[str, Any]] = []
    real = stops.stop_app_fail_closed

    def spy(**kwargs: Any) -> str:
        calls.append({key: value for key, value in kwargs.items() if key != "workspace"})
        return real(**{**kwargs, "interval_s": 0})

    monkeypatch.setattr(stops, "stop_app_fail_closed", spy)
    return calls


def _drift(world: CoreWorld) -> None:
    """Someone edits blue's own folder: the one a start snapshots."""

    world.tree.add_file(f"{world.folder(BLUE).removeprefix('/Workspace')}/backend/late.py", b"x = 1\n")


def _edit_after_the_check(world: CoreWorld) -> None:
    """The folder changes between step 0a's re-hash and the start's snapshot."""

    real_start = world.apps.start

    def start(name: str) -> Any:
        _drift(world)
        return real_start(name)

    world.apps.start = start  # type: ignore[method-assign]


def _ensure(world: CoreWorld) -> dict[str, Any]:
    return core_blue_restore.ensure_core_blue(world, **world.kwargs())


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


# --- a folder that is no longer blue's is never started ------------------------------------


def test_a_drifted_folder_without_a_restore_payload_refuses_before_any_start(world: CoreWorld) -> None:
    world.compute = "STOPPED"
    _drift(world)

    verdict = _verdict(world)
    assert [refusal.code for refusal in verdict.refusals] == ["rollback_record_drift"]
    assert "no_restore_payload" in verdict.refusals[0].message
    with pytest.raises(RestoreProofRefused) as refused:
        _ensure(world)
    assert refused.value.code == "no_restore_payload"
    assert world.apps.mutations == [] and world.compute == "STOPPED", "the App is left as it was"
    assert world.record()["deployment_id"] == BLUE and world.treatments[-1] == "quiesce"


def test_a_drifted_folder_with_a_restore_payload_is_restored_from_the_copy(world: CoreWorld) -> None:
    payload = world.prove_restore_payload()
    world.compute = "STOPPED"
    _drift(world)

    verdict = _verdict(world)
    assert verdict.refusals == () and "tier b" in verdict.notes[0]
    record = _ensure(world)

    assert world.apps.mutations == ["start", "deploy_and_wait"] and world.apps.deployed == [payload]
    assert world.active.startswith("deployment-restored-") and world.compute == "ACTIVE"
    assert record["deployment_id"] == world.active and record["predecessor_deployment_id"] == BLUE
    assert record["source_code_path"] == record["restore_source_path"], "blue's immutable copy serves"
    assert world.treatments[-1] == "quiesce", "step 0a leaves treatment quiesced"


def test_an_unchanged_folder_still_takes_tier_a_and_the_probe_says_so(world: CoreWorld) -> None:
    world.compute = "STOPPED"
    assert "tier a" in _verdict(world).notes[0]
    record = _ensure(world)
    assert world.apps.mutations == ["start"] and record["predecessor_deployment_id"] == BLUE


# --- a start that fails its proof never keeps serving ---------------------------------------


@pytest.mark.parametrize(
    ("break_it", "code"),
    (
        (lambda world: setattr(world, "start_final", "FAILED"), "start_not_succeeded"),
        (_edit_after_the_check, "start_tree_digest"),
    ),
    ids=("failed-install", "edited-after-the-check"),
)
def test_an_unproven_start_without_a_restore_payload_is_stopped_identity_pinned(
    world: CoreWorld, stop_calls: list[dict[str, Any]], break_it: Any, code: str
) -> None:
    world.compute = "STOPPED"
    break_it(world)

    with pytest.raises(StartProofError) as refused:
        _ensure(world)
    assert refused.value.code == code
    assert world.apps.mutations == ["start", "stop"] and world.compute == "STOPPED"
    assert stop_calls == [
        {
            "app_name": rr.APP_NAME,
            "expected_app_id": "app-object-id",
            "expected_client_id": "app-client",
            "expected_scim_id": "app-scim-id",
        }
    ]
    assert world.record()["deployment_id"] == BLUE, "never re-signed"
    assert world.treatments[-1] == "quiesce"


@pytest.mark.parametrize(
    "break_it",
    (lambda world: setattr(world, "start_final", "FAILED"), _edit_after_the_check),
    ids=("failed-install", "edited-after-the-check"),
)
def test_an_unproven_start_with_a_restore_payload_is_replaced_by_tier_b(
    world: CoreWorld, stop_calls: list[dict[str, Any]], break_it: Any
) -> None:
    payload = world.prove_restore_payload()
    world.compute = "STOPPED"
    break_it(world)

    record = _ensure(world)

    assert world.apps.mutations == ["start", "deploy_and_wait"] and world.apps.deployed == [payload]
    assert stop_calls == [], "tier b replaced the start; nothing to stop"
    assert record["deployment_id"] == world.active and world.active.startswith("deployment-restored-")
    assert record["predecessor_deployment_id"] == BLUE

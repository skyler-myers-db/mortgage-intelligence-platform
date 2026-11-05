"""The core tier's step-0a proof and tier-b restore over a v7 core blue (M2-PR7b).

Client #1's case throughout: blue was adopted as a v7 core record
(adopt-live, M2-PR7) and the release is core, so no tier transition is
needed. The core proofs never read a Gateway resource proof.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from tests.fixtures import rollback_records as rr
from tests.fixtures.core_blue_world import CoreWorld, adopted_core_world
from tools.databricks import app_rollback_record_contract as contract
from tools.databricks import core_blue, core_blue_restore
from tools.databricks.blue_restore_proof import RestoreProofRefused
from tools.databricks.blue_tier_gate import BlueTierRefused

SERVING = {"name": "agent", "serving_endpoint": {"name": "mip-gateway", "permission": "CAN_QUERY"}}


@pytest.fixture(autouse=True)
def _keys(monkeypatch: pytest.MonkeyPatch) -> None:
    rr.signing_env(monkeypatch)
    monkeypatch.setattr(contract, "assert_owned_app_rollback_scope", lambda *_a, **_kw: None)


@pytest.fixture
def world(tmp_path: Path) -> CoreWorld:
    return adopted_core_world(tmp_path)


def _never_gateway(*_args: Any, **_kwargs: Any) -> None:
    raise AssertionError("a core proof consulted the Gateway resource proof")


@pytest.fixture(autouse=True)
def _no_gateway_proof(monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.databricks import app_deployment_rollback, app_rollback_gateway_proof

    monkeypatch.setattr(app_rollback_gateway_proof, "resolve_stored_gateway_resource_proof", _never_gateway)
    monkeypatch.setattr(app_deployment_rollback, "resolve_exact_resource_proof", _never_gateway)


# --- step 0a ----------------------------------------------------------------------------


def test_the_paychex_case_needs_no_tier_transition_and_mutates_nothing(world: CoreWorld) -> None:
    record = core_blue_restore.ensure_core_blue(world, **world.kwargs())

    assert record["tier"] == "core" and record["provenance"] == "adopted"
    assert world.apps.mutations == [], "a serving, proven core blue is never stopped, deployed or updated"
    assert world.health_calls == ["deployment-blue"]
    assert world.treatments == ["quiesce"], "step 0a leaves treatment quiesced"
    values = core_blue.binding_values(record)
    assert values["MIP_APP_ROLLBACK_TIER"] == "core"
    assert values["MIP_APP_ROLLBACK_DEPLOYMENT_ID"] == "deployment-blue"


def test_step_0a_repairs_a_drifted_core_binding_and_refuses_a_serving_one_unrepaired(
    world: CoreWorld,
) -> None:
    job = {"name": "candidate_job", "job": {"id": "999", "permission": "CAN_MANAGE_RUN"}}
    world.resources = [*copy.deepcopy(rr.APP_RESOURCES), job]  # a killed run's candidate binding
    record = core_blue_restore.ensure_core_blue(world, **world.kwargs())
    assert world.apps.mutations == ["update"] and record["deployment_id"] == "deployment-blue"
    assert world.resources == rr.APP_RESOURCES, "the record's own bindings, restored"

    world.apps.mutations.clear()
    world.resources = [*copy.deepcopy(rr.APP_RESOURCES), SERVING]
    with pytest.raises(core_blue.CoreBlueRefused) as refused:
        core_blue_restore.ensure_core_blue(world, **world.kwargs())
    assert refused.value.code == "resource_drift" and "serving_endpoint" in str(refused.value)
    assert world.apps.mutations == [], "a serving binding is refused by name, never repaired"
    assert world.treatments[-1] == "quiesce"


def test_a_signed_record_that_binds_a_serving_resource_never_loads_as_a_core_blue(world: CoreWorld) -> None:
    record = world.record()
    assert record is not None
    forged = {**rr.v7_core_fields(), "version": 7, "app_resources": [*rr.APP_RESOURCES, SERVING]}
    from tools.databricks.app_rollback_record_v7 import build_v7_record

    with pytest.raises(Exception, match="serving_endpoint"):
        build_v7_record(expected_lakebase_instance=rr.LAKEBASE, **{k: v for k, v in forged.items() if k != "version"})
    # A signed one (written past the builder) refuses at load, before any proof.
    unsigned = {key: value for key, value in record.items() if not key.startswith("attestation_")}
    unsigned["app_resources"] = [*unsigned["app_resources"], SERVING]
    contract._write_record(world, scope=rr.SCOPE, record=unsigned, key=contract._record_key(rr.APP_NAME))
    with pytest.raises(Exception, match="serving_endpoint"):
        core_blue_restore.ensure_core_blue(world, **world.kwargs())
    assert world.apps.mutations == []


def test_a_stopped_core_blue_is_never_started_by_step_0a(world: CoreWorld) -> None:
    world.compute = "STOPPED"

    with pytest.raises(core_blue.CoreBlueRefused) as refused:
        core_blue_restore.ensure_core_blue(world, **world.kwargs())
    assert refused.value.code == "blue_stopped"
    assert "start" not in world.apps.mutations and world.treatments[-1] == "quiesce"



def test_an_empty_slot_or_a_tombstone_has_no_core_blue(world: CoreWorld) -> None:
    world.apply("unadopt")

    with pytest.raises(core_blue.CoreBlueRefused) as refused:
        core_blue_restore.ensure_core_blue(world, **world.kwargs())
    assert refused.value.code == "rollback_record_absent"
    assert core_blue.ABSENT in str(refused.value)


def test_a_killed_run_left_green_active_so_step_0a_restores_blue_from_the_copy(world: CoreWorld) -> None:
    payload = world.prove_restore_payload()
    world.activate_green()

    record = core_blue_restore.ensure_core_blue(world, **world.kwargs())

    assert world.apps.deployed == [payload]
    assert record["deployment_id"] == world.active != "deployment-green"
    assert record["predecessor_deployment_id"] == "deployment-blue"
    assert world.treatments[-1] == "quiesce", "step 0a's restore leaves treatment quiesced"


# --- tier b -----------------------------------------------------------------------------


def test_tier_b_redeploys_blues_restore_payload_and_re_pins_the_record(world: CoreWorld) -> None:
    payload = world.prove_restore_payload()
    before = world.record()
    assert before is not None
    world.activate_green()
    world.compute = "STOPPED"  # green was stopped before the rollback

    record = core_blue_restore.restore_core_blue(
        world, **world.kwargs(), expected_rollback_deployment_id="deployment-blue"
    )

    assert world.apps.mutations == ["start", "deploy_and_wait"]
    assert world.apps.deployed == [payload]
    assert record["deployment_id"] == world.active and world.active.startswith("deployment-restored-")
    assert record["source_code_path"] == record["restore_source_path"] == before["restore_source_path"]
    assert record["predecessor_deployment_id"] == "deployment-blue"
    assert (record["provenance"], record["ack_purpose"], record["ack_sha256"]) == (
        "adopted",
        "adopt_live",
        before["ack_sha256"],
    ), "a re-pin keeps the record's provenance and its authorising ack"
    assert world.treatments == ["quiesce", "runtime"], "runtime only after every proof"
    assert world.health_calls == [world.active]


def test_tier_b_refuses_a_tampered_copy_and_an_unproven_payload(world: CoreWorld) -> None:
    world.activate_green()
    with pytest.raises(RestoreProofRefused) as refused:
        core_blue_restore.restore_core_blue(world, **world.kwargs())
    assert refused.value.code == "no_restore_payload" and world.apps.deployed == []

    world.active = "deployment-blue"
    world.deployments.remove("deployment-green")
    world.prove_restore_payload()
    world.activate_green()
    record = world.record()
    assert record is not None
    world.tree.add_file(f"{record['restore_source_path'].removeprefix('/Workspace')}/backend/x.py", b"x\n")
    with pytest.raises(RestoreProofRefused) as refused:
        core_blue_restore.restore_core_blue(world, **world.kwargs())
    assert refused.value.code == "copy_tampered" and world.apps.deployed == []
    assert world.treatments[-1] == "quiesce"


def test_tier_b_refuses_a_record_that_changed_after_step_0a(world: CoreWorld) -> None:
    world.prove_restore_payload()
    world.activate_green()

    with pytest.raises(core_blue.CoreBlueRefused) as refused:
        core_blue_restore.restore_core_blue(
            world, **world.kwargs(), expected_rollback_deployment_id="deployment-other"
        )
    assert refused.value.code == "record_changed" and world.apps.deployed == []


def test_a_restore_that_is_not_blues_deployment_refuses_before_the_re_pin(
    world: CoreWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.prove_restore_payload()
    world.activate_green()
    real = world.apps.deploy_and_wait

    def deploy_elsewhere(name: str, deployment: Any, timeout: Any = None) -> Any:
        restored = real(name, deployment, timeout)
        world.sources[restored.deployment_id] = world.folder("deployment-green")
        return restored

    monkeypatch.setattr(world.apps, "deploy_and_wait", deploy_elsewhere)
    with pytest.raises(core_blue.CoreBlueRefused) as refused:
        core_blue_restore.restore_core_blue(world, **world.kwargs())
    assert refused.value.code == "restore_unproven"
    record = world.record()
    assert record is not None and record["deployment_id"] == "deployment-blue", "never re-pinned"


# --- the tier gate at the restore -------------------------------------------------------


def test_an_agentic_profile_stands_on_a_core_blue_only_with_the_exact_transition_ack(
    world: CoreWorld,
) -> None:
    with pytest.raises(BlueTierRefused) as refused:
        core_blue_restore.ensure_core_blue(world, **world.kwargs(agentic_tier="on"))
    assert refused.value.code == "blue_tier_mismatch"
    digest = str(refused.value).split("tier_transition ack ")[1].split(" ")[0]

    record = core_blue_restore.ensure_core_blue(
        world, **world.kwargs(agentic_tier="on", tier_transition_ack=digest)
    )
    assert record["tier"] == "core" and world.apps.mutations == []


def test_a_core_profile_never_stands_on_an_agentic_blue(world: CoreWorld) -> None:
    contract.save_versioned_record(world, scope=rr.SCOPE, record=rr.v6_record())

    with pytest.raises(BlueTierRefused) as refused:
        core_blue_restore.restore_core_blue(world, **world.kwargs(tier_transition_ack="0" * 64))
    assert refused.value.code == "blue_tier_mismatch" and "no ack lifts this" in str(refused.value)
    assert world.apps.mutations == [] and world.treatments == ["quiesce", "quiesce"]

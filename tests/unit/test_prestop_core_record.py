"""The core tier's pre-stop record clause judges blue as the core step 0a will (M2-PR7b)."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from tests.fixtures import rollback_records as rr
from tests.fixtures.core_blue_world import CoreWorld, adopted_core_world
from tools.databricks import app_rollback_record_contract as contract
from tools.databricks import prestop_refusals as codes
from tools.databricks import prestop_rollback_record as probe


@pytest.fixture(autouse=True)
def _keys(monkeypatch: pytest.MonkeyPatch) -> None:
    rr.signing_env(monkeypatch)
    monkeypatch.setattr(contract, "assert_owned_app_rollback_scope", lambda *_a, **_kw: None)


@pytest.fixture
def world(tmp_path: Path) -> CoreWorld:
    return adopted_core_world(tmp_path)


def _verdict(world: CoreWorld, monkeypatch: pytest.MonkeyPatch, tier: str = "off") -> probe.RollbackRecordVerdict:
    if tier == "off":
        monkeypatch.setattr(probe, "ASSERT_SIGNED", lambda *_a, **_kw: pytest.fail("the v5/v6 verifier is not asked"))
    return probe.rollback_record_verdict(
        world,
        app_name=rr.APP_NAME,
        scope=rr.SCOPE,
        app_exists=True,
        rebase_unverified_app=False,
        journal_state=lambda: "absent",
        lakebase_instance=rr.LAKEBASE,
        agentic_tier=tier,
    )


def test_the_paychex_case_an_adopted_core_blue_passes_the_core_clause(
    world: CoreWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    verdict = _verdict(world, monkeypatch)

    assert verdict.refusals == () and verdict.contract is None
    assert verdict.notes == (
        "signed core record verified (adopted, core resource proof); the active deployment is the record's",
    )
    assert world.apps.mutations == []


def test_a_green_left_active_is_a_note_the_core_step_0a_restores_over(
    world: CoreWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.activate_green()

    verdict = _verdict(world, monkeypatch)
    assert verdict.refusals == () and "restores the record's deployment over it" in verdict.notes[0]


def test_a_stopped_core_blue_is_a_note_step_0a_starts_and_re_pins(
    world: CoreWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.compute = "STOPPED"  # spike S7: its GET has no active deployment

    verdict = _verdict(world, monkeypatch)
    assert verdict.refusals == () and "starts it and re-pins the start (tier a)" in verdict.notes[0]
    assert world.apps.mutations == []


def test_a_stopped_core_app_whose_start_would_not_rerun_blue_refuses_before_the_lease(
    world: CoreWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review of #319: predicted here, nothing is quiesced or repaired under the lease."""

    world.compute = "STOPPED"
    world.active = "deployment-old"  # the default source follows the last active deployment

    verdict = _verdict(world, monkeypatch)
    assert [refusal.code for refusal in verdict.refusals] == [codes.ROLLBACK_RECORD_DRIFT]
    assert "tier_a_refused" in verdict.refusals[0].message and "owner break-glass" in verdict.refusals[0].message
    assert world.apps.mutations == []


def test_a_stopped_app_over_a_green_left_active_is_the_restores_note(
    world: CoreWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.activate_green()
    world.compute = "STOPPED"  # a killed run's candidate, stopped: step 0a's restore starts it

    verdict = _verdict(world, monkeypatch)
    assert verdict.refusals == () and "restores the record's deployment over it" in verdict.notes[0]


def test_an_agentic_record_refuses_the_core_clause_as_present(world: CoreWorld, monkeypatch: pytest.MonkeyPatch) -> None:
    contract.save_versioned_record(world, scope=rr.SCOPE, record=rr.v6_record())

    verdict = _verdict(world, monkeypatch)
    assert [refusal.code for refusal in verdict.refusals] == [codes.ROLLBACK_RECORD_PRESENT]
    assert "blue_tier_gate" in verdict.refusals[0].message or "agentic tier" in verdict.refusals[0].message


def test_no_core_record_refuses_as_absent(world: CoreWorld, monkeypatch: pytest.MonkeyPatch) -> None:
    world.apply("unadopt")

    verdict = _verdict(world, monkeypatch)
    assert [refusal.code for refusal in verdict.refusals] == [codes.ROLLBACK_RECORD_ABSENT]


def test_a_serving_binding_refuses_as_drift(world: CoreWorld, monkeypatch: pytest.MonkeyPatch) -> None:
    serving: dict[str, Any] = {"name": "agent", "serving_endpoint": {"name": "gw", "permission": "CAN_QUERY"}}
    world.resources = [*copy.deepcopy(rr.APP_RESOURCES), serving]

    verdict = _verdict(world, monkeypatch)
    assert [refusal.code for refusal in verdict.refusals] == [codes.ROLLBACK_RECORD_DRIFT]
    assert "serving_endpoint" in verdict.refusals[0].message


def test_the_agentic_clause_still_refuses_a_v7_record_by_name(world: CoreWorld, monkeypatch: pytest.MonkeyPatch) -> None:
    verdict = _verdict(world, monkeypatch, tier="on")

    assert [refusal.code for refusal in verdict.refusals] == [codes.ROLLBACK_RECORD_PRESENT]
    assert contract.V7_REFUSAL in verdict.refusals[0].message

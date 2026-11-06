"""The tier gate: a release stands on its own tier's blue, or on the exact tier_transition ack (M2-PR7b)."""

from __future__ import annotations

import copy

import pytest

from tests.fixtures import rollback_records as rr
from tools.databricks import blue_tier_gate as gate
from tools.databricks.adopt_live import record_digest
from tools.databricks.operation_ack import PURPOSES, ack_digest


def _signed(record: dict[str, object]) -> dict[str, object]:
    return {**record, "attestation_alg": "x", "attestation_verify_key": "k", "attestation_signature": "s"}


CORE = _signed(rr.v7_core_record())
V6 = _signed({**rr.v6_record(), "version": 6})
V7_AGENTIC = _signed(rr.v7_agentic_record())


def _plan(record: dict[str, object], tier: str = "on") -> dict[str, str]:
    return gate.tier_transition_plan(app_name=rr.APP_NAME, record=record, agentic_tier=tier)


def test_the_paychex_case_a_core_blue_under_a_core_profile_needs_no_ack() -> None:
    assert gate.assert_blue_tier(CORE, app_name=rr.APP_NAME, agentic_tier="off") == "same"
    for agentic in (V6, V7_AGENTIC):
        assert gate.assert_blue_tier(agentic, app_name=rr.APP_NAME, agentic_tier="on") == "same"


@pytest.mark.parametrize("record", [V6, V7_AGENTIC], ids=["v6", "v7-agentic"])
def test_an_agentic_blue_never_stands_under_a_core_profile_whatever_ack(record: dict[str, object]) -> None:
    for ack in (None, "0" * 64, ack_digest("tier_transition", _plan(record, "off"))):
        with pytest.raises(gate.BlueTierRefused) as refused:
            gate.assert_blue_tier(record, app_name=rr.APP_NAME, agentic_tier="off", tier_transition_ack=ack)
        assert refused.value.code == "blue_tier_mismatch"
        assert "no ack lifts this" in str(refused.value)


def test_a_core_blue_under_an_agentic_profile_needs_the_exact_tier_transition_ack() -> None:
    plan = _plan(CORE)
    digest = ack_digest("tier_transition", plan)
    with pytest.raises(gate.BlueTierRefused) as refused:
        gate.assert_blue_tier(CORE, app_name=rr.APP_NAME, agentic_tier="on")
    assert refused.value.code == "blue_tier_mismatch" and digest in str(refused.value)

    assert (
        gate.assert_blue_tier(CORE, app_name=rr.APP_NAME, agentic_tier="on", tier_transition_ack=digest)
        == "transition"
    )


@pytest.mark.parametrize("purpose", [p for p in PURPOSES if p != "tier_transition"])
def test_the_tier_transition_ack_is_purpose_bound(purpose: str) -> None:
    other = ack_digest(purpose, _plan(CORE))
    with pytest.raises(gate.BlueTierRefused) as refused:
        gate.assert_blue_tier(CORE, app_name=rr.APP_NAME, agentic_tier="on", tier_transition_ack=other)
    assert refused.value.code == "blue_tier_mismatch"


def test_the_ack_covers_one_app_one_blue_and_one_record_version() -> None:
    digest = ack_digest("tier_transition", _plan(CORE))
    assert set(_plan(CORE)) == {"app_name", "blue_deployment_sha256", "blue_record_sha256", "from_tier", "to_tier"}
    assert (_plan(CORE)["from_tier"], _plan(CORE)["to_tier"]) == ("core", "agentic")
    resigned = {**copy.deepcopy(CORE), "attestation_signature": "another-version"}
    other_blue = _signed(rr.v7_core_record(deployment_id="deployment-other"))
    for record, app in ((resigned, rr.APP_NAME), (other_blue, rr.APP_NAME), (CORE, "another-app")):
        with pytest.raises(gate.BlueTierRefused):
            gate.assert_blue_tier(record, app_name=app, agentic_tier="on", tier_transition_ack=digest)


def test_the_record_digest_is_adopt_lives_and_a_tombstone_is_no_blue() -> None:
    assert gate.signed_record_sha256(CORE) == record_digest(CORE)
    assert gate.current_blue(None) is None
    assert gate.current_blue({**CORE, "provenance": "retired"}) is None
    assert gate.current_blue(V6) is V6
    assert gate.record_tier(V6) == "agentic" and gate.record_tier(CORE) == "core"

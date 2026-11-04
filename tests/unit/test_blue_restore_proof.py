"""Blue's restore proofs after an adoption (M2-PR7): tier a, tier b and the pre-activation proof."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.fixtures import rollback_records as rr
from tests.fixtures.adopt_live_world import World, make_repo
from tools.databricks import app_rollback_record_contract as contract
from tools.databricks import blue_restore_proof as proof
from tools.databricks.operation_ack import ack_digest


@pytest.fixture(autouse=True)
def _keys(monkeypatch: pytest.MonkeyPatch) -> None:
    rr.signing_env(monkeypatch)
    monkeypatch.setattr(contract, "assert_owned_app_rollback_scope", lambda *_a, **_kw: None)


@pytest.fixture
def adopted(tmp_path: Path) -> World:
    repo, main, side = make_repo(tmp_path)
    world = World(repo=repo, main=main, side=side)
    world.apply()
    return world


def _restore_payload(world: World, **env: str) -> dict[str, object]:
    record = world.record()
    assert record is not None
    return rr.payload(record["restore_source_path"], env or None)


def _prepare(world: World, **kwargs: Any) -> str:
    return proof.prepare_activation(
        world,
        app_name=rr.APP_NAME,
        scope=rr.SCOPE,
        lakebase_instance=rr.LAKEBASE,
        lease=world.lease,
        **kwargs,
    )


def _tamper_copy(world: World) -> None:
    record = world.record()
    assert record is not None
    world.tree.add_file(
        f"{record['restore_source_path'].removeprefix('/Workspace')}/backend/main.py", b"tampered\n"
    )


# --- the pre-activation proof (attack O2) -----------------------------------------------


def test_activation_refuses_without_the_payload_or_the_exact_no_rollback_ack(
    adopted: World,
) -> None:
    record = adopted.record()
    assert record is not None
    plan = proof.no_rollback_plan(app_name=rr.APP_NAME, record=record)
    for ack in (None, "0" * 64, ack_digest("override_freeze", plan), ack_digest("supersede", plan)):
        with pytest.raises(proof.RestoreProofRefused) as refused:
            _prepare(adopted, no_rollback_ack=ack)
        assert refused.value.code == "no_rollback_proof"
        assert f"MIP_ACK_NO_ROLLBACK_FOR={ack_digest('no_rollback_for', plan)}" in str(
            refused.value
        )

    note = _prepare(adopted, no_rollback_ack=ack_digest("no_rollback_for", plan))
    assert "NO rollback" in note and adopted.apps.mutations == []


def test_the_restore_payload_is_buildable_and_recorded_before_activation(adopted: World) -> None:
    payload = _restore_payload(adopted)

    note = _prepare(adopted, restore_payload=payload)
    record = adopted.record()
    assert record is not None and "recorded before activation" in note
    assert record["restore_payload_sha256"] == contract._payload_digest(payload)
    assert record["provenance"] == "adopted" and record["ack_purpose"] == "adopt_live"
    assert "re-proven now" in _prepare(adopted), "the recorded payload is re-proven, no ack needed"
    assert adopted.apps.mutations == []


@pytest.mark.parametrize("change", ("env_names", "source"))
def test_a_restore_payload_that_is_not_blues_refuses(adopted: World, change: str) -> None:
    payload = (
        _restore_payload(adopted, MIP_EXTRA="x")
        if change == "env_names"
        else rr.payload(rr.SNAPSHOT)
    )

    with pytest.raises(proof.RestoreProofRefused) as refused:
        _prepare(adopted, restore_payload=payload)
    assert refused.value.code == "payload_invalid"
    record = adopted.record()
    assert record is not None and record["restore_payload_sha256"] is None


def test_a_tampered_copy_refuses_the_proof_and_the_restore(adopted: World) -> None:
    _prepare(adopted, restore_payload=_restore_payload(adopted))
    _tamper_copy(adopted)
    record = adopted.record()
    assert record is not None

    with pytest.raises(proof.RestoreProofRefused) as refused:
        _prepare(adopted)
    assert refused.value.code == "copy_tampered"
    with pytest.raises(proof.RestoreProofRefused) as refused:
        proof.tier_b_payload(adopted, app_name=rr.APP_NAME, record=record)
    assert refused.value.code == "copy_tampered"


def test_a_v5_or_v6_record_needs_nothing_new(adopted: World) -> None:
    contract.save_versioned_record(adopted, scope=rr.SCOPE, record=rr.v6_record())

    assert "own payload is blue's tier-b restore payload" in _prepare(adopted)


def test_no_record_or_a_tombstone_has_no_blue_to_protect(adopted: World) -> None:
    adopted.apply("unadopt")

    assert "no blue to protect" in _prepare(adopted)


def test_the_proof_must_precede_the_activation(adopted: World) -> None:
    adopted.redeploy("deployment-green")

    with pytest.raises(proof.RestoreProofRefused) as refused:
        _prepare(adopted, restore_payload=_restore_payload(adopted))
    assert refused.value.code == "blue_not_active"


# --- tier a and tier b ---------------------------------------------------------------


def test_tier_a_is_valid_while_blue_is_last_and_refuses_after_activation(adopted: World) -> None:
    record = adopted.record()
    assert record is not None
    proof.assert_tier_a(adopted, app_name=rr.APP_NAME, record=record)

    adopted.redeploy("deployment-green")
    with pytest.raises(proof.RestoreProofRefused) as refused:
        proof.assert_tier_a(adopted, app_name=rr.APP_NAME, record=record)
    assert refused.value.code == "tier_a_refused"


def test_tier_b_needs_the_recorded_payload(adopted: World) -> None:
    record = adopted.record()
    assert record is not None

    with pytest.raises(proof.RestoreProofRefused) as refused:
        proof.tier_b_payload(adopted, app_name=rr.APP_NAME, record=record)
    assert refused.value.code == "no_restore_payload"


def test_an_unverified_frontend_restore_needs_its_own_ack(adopted: World) -> None:
    payload = _restore_payload(adopted)
    _prepare(adopted, restore_payload=payload)
    record = adopted.record()
    assert record is not None and record["frontend_provenance"] == "unverified"
    plan = proof.unverified_frontend_plan(app_name=rr.APP_NAME, record=record)

    for ack in (None, ack_digest("no_rollback_for", plan), "0" * 64):
        with pytest.raises(proof.RestoreProofRefused) as refused:
            proof.tier_b_payload(
                adopted, app_name=rr.APP_NAME, record=record, unverified_frontend_ack=ack
            )
        assert refused.value.code in {"ack_missing", "ack_mismatch"}
    restored = proof.tier_b_payload(
        adopted,
        app_name=rr.APP_NAME,
        record=record,
        unverified_frontend_ack=ack_digest("restore_unverified_frontend", plan),
    )
    assert restored == payload and adopted.apps.mutations == []


def test_the_cli_refuses_with_exit_2_and_prints_the_ack_to_give(
    adopted: World, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from tools.databricks import app_deployment_lease

    monkeypatch.setattr(app_deployment_lease, "held_assertion", lambda *_a, **_kw: adopted.lease)
    argv = [
        "prepare-activation",
        "--app-name",
        rr.APP_NAME,
        "--scope",
        rr.SCOPE,
        "--lakebase-instance",
        rr.LAKEBASE,
        "--lease-id",
        "lease-1",
        "--source-git-sha",
        "0" * 40,
    ]

    assert proof.main(argv, workspace_factory=lambda: adopted) == 2
    assert "REFUSED no_rollback_proof" in capsys.readouterr().err

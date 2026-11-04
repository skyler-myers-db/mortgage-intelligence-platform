"""The pre-stop record clause in the adopt phases judges what adopt-live will (M2-PR7)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from tests.fixtures import rollback_records as rr
from tests.fixtures.deploy_script import deploy_entrypoint_text
from tools.databricks import app_rollback_record_contract as contract
from tools.databricks import prestop_refusals as codes
from tools.databricks import prestop_rollback_record as probe
from tools.databricks.app_rollback_secret_scope import expected_app_rollback_scope

SCOPE = expected_app_rollback_scope(rr.APP_NAME)


@pytest.fixture(autouse=True)
def _keys(monkeypatch: pytest.MonkeyPatch) -> None:
    rr.signing_env(monkeypatch)
    monkeypatch.setattr(contract, "assert_owned_app_rollback_scope", lambda *_a, **_kw: None)


def _verdict(
    monkeypatch: pytest.MonkeyPatch, workspace: Any, *, scope_exists: bool = True
) -> probe.RollbackRecordVerdict:
    monkeypatch.setattr(probe, "_scope_names", lambda _ws: {SCOPE} if scope_exists else set())
    monkeypatch.setattr(
        probe, "ASSERT_SIGNED", lambda *_a, **_kw: pytest.fail("the release verifier is not asked")
    )
    return probe.rollback_record_verdict(
        workspace,
        app_name=rr.APP_NAME,
        scope=SCOPE,
        app_exists=True,
        rebase_unverified_app=False,
        journal_state=lambda: "absent",
        adopt_phase=True,
        lakebase_instance=rr.LAKEBASE,
    )


def _workspace(record: dict[str, Any] | None = None) -> Any:
    workspace = SimpleNamespace(secrets=rr.Secrets())
    if record is not None:
        contract.save_versioned_record(workspace, scope=SCOPE, record=record)
    return workspace


@pytest.mark.parametrize("scope_exists", (False, True))
def test_no_record_passes_with_a_note(monkeypatch: pytest.MonkeyPatch, scope_exists: bool) -> None:
    verdict = _verdict(monkeypatch, _workspace(), scope_exists=scope_exists)

    assert verdict.refusals == () and verdict.notes[0].startswith("adopt phase: no rollback")


@pytest.mark.parametrize("provenance", ("adopted", "retired"))
def test_an_adopted_record_or_its_tombstone_is_adopt_lives_to_judge(
    monkeypatch: pytest.MonkeyPatch, provenance: str
) -> None:
    purpose = "unadopt" if provenance == "retired" else "adopt_live"
    verdict = _verdict(
        monkeypatch, _workspace(rr.v7_core_record(provenance=provenance, ack_purpose=purpose))
    )

    assert verdict.refusals == () and f"a v7 {provenance} record" in verdict.notes[0]


def test_a_captured_blue_refuses_as_present(monkeypatch: pytest.MonkeyPatch) -> None:
    verdict = _verdict(monkeypatch, _workspace(rr.v6_record()))

    assert [refusal.code for refusal in verdict.refusals] == [codes.ROLLBACK_RECORD_PRESENT]
    assert "a captured blue is already governed" in verdict.refusals[0].message


def test_a_record_that_does_not_verify_refuses_as_tampered(monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = _workspace(rr.v7_core_record())
    key = (SCOPE, contract._record_key(rr.APP_NAME))
    stored = json.loads(workspace.secrets.values[key])
    stored["treatment_mode"] = "runtime"
    workspace.secrets.values[key] = json.dumps(stored)

    verdict = _verdict(monkeypatch, workspace)
    assert [refusal.code for refusal in verdict.refusals] == [codes.ROLLBACK_RECORD_TAMPERED]


def test_the_deploy_passes_the_phase_only_in_the_adopt_phases() -> None:
    text = deploy_entrypoint_text()

    assert (
        "  _PRESTOP_PHASE_ARGS=()\n"
        '  [[ "${DEPLOY_PHASE:-}" != "adopt" && "${DEPLOY_PHASE:-}" != "unadopt" ]] || \\\n'
        '    _PRESTOP_PHASE_ARGS=(--phase "$DEPLOY_PHASE")\n'
    ) in text
    assert '"${_PRESTOP_TIER_ARGS[@]}" "${_PRESTOP_PHASE_ARGS[@]}" \\\n' in text


# --- the Genie pin reads the adopted record's space (M2-PR7) ------------------------------


def test_the_genie_pin_reads_a_v7_records_space_and_a_tombstone_names_none() -> None:
    from tools.databricks import genie_space_pin

    workspace = _workspace(rr.v7_core_record())
    workspace.secrets.list_scopes = lambda: [SimpleNamespace(name=SCOPE)]

    def space() -> str | None:
        return genie_space_pin.record_space_id(
            workspace, scope=SCOPE, app_name=rr.APP_NAME, lakebase_instance=rr.LAKEBASE
        )

    assert space() == rr.GENIE_SPACE_ID
    contract.save_versioned_record(
        workspace,
        scope=SCOPE,
        record=rr.v7_core_record(provenance="retired", ack_purpose="unadopt"),
    )
    assert space() is None


def test_outside_the_adopt_phases_a_v7_record_refuses_as_present_never_as_tampered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace(rr.v7_core_record())
    monkeypatch.setattr(probe, "_scope_names", lambda _ws: {SCOPE})

    def signed(ws: Any, *, app_name: str, scope: str) -> Any:
        return contract._load_record(
            ws, app_name=app_name, scope=scope, expected_lakebase_instance=rr.LAKEBASE
        )

    monkeypatch.setattr(probe, "ASSERT_SIGNED", signed)
    verdict = probe.rollback_record_verdict(
        workspace,
        app_name=rr.APP_NAME,
        scope=SCOPE,
        app_exists=True,
        rebase_unverified_app=False,
        journal_state=lambda: "absent",
    )

    assert [refusal.code for refusal in verdict.refusals] == [codes.ROLLBACK_RECORD_PRESENT]
    assert "tier-aware v7 record (an adopted blue)" in verdict.refusals[0].message

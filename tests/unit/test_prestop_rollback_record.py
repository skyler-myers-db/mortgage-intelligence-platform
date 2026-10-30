"""The pre-stop rollback-record probe: signed, absent, drifted, tampered or present (M0-PR5).

The record verifier and the bootstrap absence proof are the real step-0a
functions, pinned by identity in tests/unit/test_prestop_probe_identity.py and
tested on their own in tests/unit/test_app_deployment_rollback.py and
tests/unit/test_app_rollback_bootstrap_gate.py. Here they are scripted, so these
tests pin how the probe routes each install state to one of them and how it
classifies every failure: always a refusal, never a pass.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.fixtures.prestop_estate import (
    APP_NAME,
    FakeWorkspace,
    clean_estate,
    signed_contract,
)
from tools.databricks import prestop_refusals as codes
from tools.databricks import prestop_rollback_record as probe

SCOPE = "mip-app-rollback"


class _Script:
    """Scripted step-0a verifier and absence proof, recording each call."""

    def __init__(self, signed: Any = None, absent: str | None = None) -> None:
        self.calls: list[str] = []
        self._signed = signed
        self._absent = absent

    def signed(self, _workspace: Any, *, app_name: str, scope: str) -> Any:
        self.calls.append(f"signed {app_name} {scope}")
        if isinstance(self._signed, Exception):
            raise self._signed
        return self._signed

    def absent(self, _workspace: Any, *, app_name: str, scope: str) -> None:
        self.calls.append(f"absent {app_name} {scope}")
        if self._absent is not None:
            raise RuntimeError(self._absent)


def _verdict(
    monkeypatch: pytest.MonkeyPatch,
    script: _Script,
    *,
    estate: dict[str, Any] | None = None,
    app_exists: bool = True,
    rebase: bool = False,
    journal: Any = "absent",
) -> probe.RollbackRecordVerdict:
    state = estate or clean_estate()
    monkeypatch.setattr(probe, "ASSERT_SIGNED", script.signed)
    monkeypatch.setattr(probe, "ASSERT_ABSENT", script.absent)

    def journal_state() -> str:
        if isinstance(journal, Exception):
            raise journal
        return str(journal)

    return probe.rollback_record_verdict(
        FakeWorkspace(state),
        app_name=APP_NAME,
        scope=SCOPE,
        app_exists=app_exists,
        rebase_unverified_app=rebase,
        journal_state=journal_state,
    )


def _codes(verdict: probe.RollbackRecordVerdict) -> list[str]:
    return [refusal.code for refusal in verdict.refusals]


def test_a_signed_record_on_the_active_deployment_passes_and_pins_its_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contract = signed_contract(clean_estate()["record"])
    script = _Script(signed=contract)

    verdict = _verdict(monkeypatch, script)

    assert verdict.refusals == () and verdict.contract is contract
    assert script.calls == [f"signed {APP_NAME} {SCOPE}"]
    assert "signed last-good record verified" in verdict.notes[0]


@pytest.mark.parametrize(
    ("message", "code"),
    (
        (
            "no server-owned last-good App rollback contract exists; run the explicit bootstrap",
            codes.ROLLBACK_RECORD_ABSENT,
        ),
        ("App rollback secret scope is absent", codes.ROLLBACK_RECORD_ABSENT),
        ("App rollback contract signature is invalid", codes.ROLLBACK_RECORD_TAMPERED),
        ("App rollback payload digest does not match", codes.ROLLBACK_RECORD_TAMPERED),
        ("App rollback secret-scope ACL ownership proof failed", codes.ROLLBACK_RECORD_TAMPERED),
        (
            "App service-principal identity drifted from the signed rollback contract",
            codes.ROLLBACK_RECORD_DRIFT,
        ),
        (
            "App rollback payload does not match its live exact Gateway resource proof",
            codes.ROLLBACK_RECORD_DRIFT,
        ),
    ),
)
def test_every_verifier_failure_refuses_with_its_class(
    monkeypatch: pytest.MonkeyPatch, message: str, code: str
) -> None:
    verdict = _verdict(monkeypatch, _Script(signed=RuntimeError(message)))

    assert _codes(verdict) == [code]
    assert message in verdict.refusals[0].message
    assert "refuses after step 0a stops the App" in verdict.refusals[0].message
    assert verdict.contract is None


def test_an_active_deployment_other_than_the_record_is_left_to_the_step_0a_restore(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A run killed after green activation and before signed capture leaves this
    # state; step 0a's ensure restores the record's deployment, so a refusal
    # here would block the very re-run that recovers it.
    estate = clean_estate()
    estate["app"]["active_deployment"] = "deployment-emergency"
    contract = signed_contract(estate["record"])

    verdict = _verdict(monkeypatch, _Script(signed=contract), estate=estate)

    assert verdict.refusals == () and verdict.contract is contract
    assert verdict.notes == (
        "signed last-good record verified; the active deployment deployment-emergency is "
        "not the record's deployment-blue, so step 0a restores the record's deployment "
        "over it before any other mutation",
    )


def test_a_pending_deployment_is_drift(monkeypatch: pytest.MonkeyPatch) -> None:
    estate = clean_estate()
    estate["app"]["pending"] = True

    verdict = _verdict(monkeypatch, _Script(signed=signed_contract(estate["record"])), estate=estate)

    assert _codes(verdict) == [codes.ROLLBACK_RECORD_DRIFT]
    assert "pending deployment" in verdict.refusals[0].message


def test_no_app_has_no_blue_to_protect(monkeypatch: pytest.MonkeyPatch) -> None:
    script = _Script(signed=RuntimeError("never called"))

    verdict = _verdict(monkeypatch, script, app_exists=False, journal=RuntimeError("never read"))

    assert verdict.refusals == () and script.calls == []


@pytest.mark.parametrize("state", sorted(probe.JOURNAL_OWNED_STATES))
def test_a_journal_owned_app_is_left_to_the_recovery_under_the_lease(
    monkeypatch: pytest.MonkeyPatch, state: str
) -> None:
    script = _Script(signed=RuntimeError("never called"))

    verdict = _verdict(monkeypatch, script, journal=state, rebase=True)

    assert verdict.refusals == () and script.calls == []
    assert f"signed first-install journal ({state})" in verdict.notes[0]


def test_an_unreadable_journal_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    verdict = _verdict(
        monkeypatch, _Script(), journal=RuntimeError("first-install journal signature invalid")
    )

    assert _codes(verdict) == [codes.ROLLBACK_RECORD_TAMPERED]
    assert "first-install journal unreadable" in verdict.refusals[0].message


def test_the_rebase_absence_proof_runs_before_the_lease(monkeypatch: pytest.MonkeyPatch) -> None:
    script = _Script(signed=RuntimeError("the rebase never verifies a record"))

    verdict = _verdict(monkeypatch, script, rebase=True)

    assert verdict.refusals == ()
    assert script.calls == [f"absent {APP_NAME} {SCOPE}"]


def test_a_rebase_over_an_existing_record_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    script = _Script(
        absent=(
            "a server-owned last-good App rollback record already exists; "
            "refusing the one-time unsigned rebase"
        )
    )

    verdict = _verdict(monkeypatch, script, rebase=True)

    assert _codes(verdict) == [codes.ROLLBACK_RECORD_PRESENT]
    assert "unset MIP_REBASE_UNVERIFIED_APP" in verdict.refusals[0].message


def test_a_rebase_scope_that_fails_its_ownership_proof_is_tampered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verdict = _verdict(
        monkeypatch, _Script(absent="existing App rollback scope has no ownership marker"), rebase=True
    )

    assert _codes(verdict) == [codes.ROLLBACK_RECORD_TAMPERED]


def test_a_rebase_before_the_scope_exists_holds_no_record(monkeypatch: pytest.MonkeyPatch) -> None:
    estate = clean_estate()
    estate["secret_scopes"] = ["mip", "mip-runtime"]
    script = _Script(absent="never called: no scope, no record")

    verdict = _verdict(monkeypatch, script, estate=estate, rebase=True)

    assert verdict.refusals == () and script.calls == []
    assert "the rollback scope does not exist yet" in verdict.notes[0]


def test_a_rebase_on_a_signed_journal_takes_the_signed_blue_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = _Script(signed=signed_contract(clean_estate()["record"]))

    verdict = _verdict(monkeypatch, script, rebase=True, journal="signed")

    assert verdict.refusals == ()
    assert script.calls == [f"signed {APP_NAME} {SCOPE}"], "step 0a's own routing"

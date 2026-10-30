"""Pre-stop probe: the signed last-good App rollback record.

Step 0a reads the server-owned rollback record after the signed lease, with
the App fail-closed armed: ``app_deployment_rollback ensure`` on the
signed-blue path, ``app_rollback_bootstrap_gate`` on the one-time unsigned
rebase path. A record that is missing, tampered with or drifted from the live
App then stops the App. The same reads run here first, read-only, while the
App still serves:

* **signed** (existing App, no rebase): ``ASSERT_SIGNED`` is the verifier the
  ``ensure`` path calls (``verified_signed_last_good_contract``). A missing
  record or rollback scope refuses as ``rollback_record_absent``; a failed
  signature, digest or scope-ownership proof as ``rollback_record_tampered``;
  an App identity or Gateway resource that no longer matches the record as
  ``rollback_record_drift``, as does an active deployment that cannot be read
  as one stable id (a pending or in-progress deployment), which ``ensure``
  refuses too. An active deployment that is not the record's is no refusal:
  ``ensure`` restores the record's deployment over it (a run killed after
  green activation and before signed capture resumes that way), so the probe
  names it in a note and leaves the recovery to step 0a.
* **absent** (existing App with ``MIP_REBASE_UNVERIFIED_APP=1``, journal not
  signed): ``ASSERT_ABSENT`` is the bootstrap gate's proof, moved here from
  step 0a. A record that exists refuses as ``rollback_record_present``. A
  rollback scope that does not exist yet holds no record; the post-lease
  ``app_rollback_secret_scope ensure`` creates it.

No App, or an App a signed first-install journal owns (the recovery under the
lease deletes it), has no blue to protect, so nothing is read. Every call is a
GET or a list.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from tools.databricks.app_deployment_rollback import verified_signed_last_good_contract
from tools.databricks.app_deployment_state import active_deployment_id
from tools.databricks.app_rollback_bootstrap_gate import assert_rollback_record_absent
from tools.databricks.app_rollback_secret_scope import (
    _scope_names,
    expected_app_rollback_scope,
)
from tools.databricks.app_rollback_signed_contract import SignedLastGoodAppContract
from tools.databricks.prestop_refusals import (
    ROLLBACK_RECORD_ABSENT,
    ROLLBACK_RECORD_DRIFT,
    ROLLBACK_RECORD_PRESENT,
    ROLLBACK_RECORD_TAMPERED,
    PrestopRefusal,
)

# The very functions step 0a runs after the lease (identity, not copies).
ASSERT_SIGNED = verified_signed_last_good_contract
ASSERT_ABSENT = assert_rollback_record_absent
ACTIVE_DEPLOYMENT = active_deployment_id

# First-install journal states whose App the recovery under the lease deletes.
JOURNAL_OWNED_STATES = frozenset({"recover", "unclaimed"})
_ABSENT_MARKERS = (
    "no server-owned last-good App rollback contract exists",
    "App rollback secret scope is absent",
)
_DRIFT_MARKERS = (
    "drifted",
    "does not match its live exact Gateway resource proof",
    "could not resolve both service-principal identifiers",
)
_REMEDY_ABSENT = (
    "An existing App needs its signed last-good record before a governed upgrade. "
    "For the one-time adoption of an App deployed outside this script, re-run with "
    "MIP_REBASE_UNVERIFIED_APP=1 (that path stops and quarantines the App until signed "
    "capture); otherwise restore the record from the deploy that captured it. Then re-run."
)
_REMEDY_TAMPERED = (
    "The record or its scope failed verification: do not deploy over it. Compare the "
    "scope's keys and ACL with docs/deployment.md (App rollback record) and have the "
    "owner review the scope's audit log before any repair. Then re-run."
)
_REMEDY_DRIFT = (
    "The live App's identity, Gateway resources or deployment state no longer match its "
    "signed record, so step 0a refuses after it stops the App. Wait for a pending "
    "deployment to finish; otherwise have the owner compare the App with docs/deployment.md "
    "(App rollback record) before any repair. Then re-run."
)
_REMEDY_PRESENT = (
    "A signed last-good record exists, so the one-time unsigned rebase is refused: unset "
    "MIP_REBASE_UNVERIFIED_APP and let the signed-blue path verify that record. Then re-run."
)


@dataclass(frozen=True)
class RollbackRecordVerdict:
    """The probe's refusals and notes, plus the verified contract when signed."""

    refusals: tuple[PrestopRefusal, ...]
    notes: tuple[str, ...]
    contract: SignedLastGoodAppContract | None = None


def _classify(exc: Exception) -> str:
    message = str(exc)
    if any(marker in message for marker in _ABSENT_MARKERS):
        return ROLLBACK_RECORD_ABSENT
    if any(marker in message for marker in _DRIFT_MARKERS):
        return ROLLBACK_RECORD_DRIFT
    return ROLLBACK_RECORD_TAMPERED


def _refusal(code: str, detail: str) -> PrestopRefusal:
    remedy = {
        ROLLBACK_RECORD_ABSENT: _REMEDY_ABSENT,
        ROLLBACK_RECORD_TAMPERED: _REMEDY_TAMPERED,
        ROLLBACK_RECORD_DRIFT: _REMEDY_DRIFT,
        ROLLBACK_RECORD_PRESENT: _REMEDY_PRESENT,
    }[code]
    return PrestopRefusal(
        code,
        f"the signed last-good App rollback record refuses after step 0a stops the App: "
        f"{detail}. {remedy}",
    )


def _absence_verdict(workspace: Any, *, app_name: str, scope: str) -> RollbackRecordVerdict:
    try:
        if expected_app_rollback_scope(app_name) not in _scope_names(workspace):
            return RollbackRecordVerdict(
                (),
                ("unsigned rebase: the rollback scope does not exist yet, so no record does",),
            )
        ASSERT_ABSENT(workspace, app_name=app_name, scope=scope)
    except Exception as exc:  # noqa: BLE001 - every failure refuses; none reads as absence
        code = ROLLBACK_RECORD_PRESENT if "already exists" in str(exc) else _classify(exc)
        if code == ROLLBACK_RECORD_ABSENT:
            code = ROLLBACK_RECORD_TAMPERED
        return RollbackRecordVerdict((_refusal(code, str(exc)),), ())
    return RollbackRecordVerdict((), ("unsigned rebase: no last-good rollback record exists",))


def _signed_verdict(workspace: Any, *, app_name: str, scope: str) -> RollbackRecordVerdict:
    try:
        contract = ASSERT_SIGNED(workspace, app_name=app_name, scope=scope)
    except Exception as exc:  # noqa: BLE001 - every failure refuses with its class
        return RollbackRecordVerdict((_refusal(_classify(exc), str(exc)),), ())
    try:
        active = ACTIVE_DEPLOYMENT(workspace, app_name=app_name)
    except Exception as exc:  # noqa: BLE001 - ensure reads it the same way and refuses
        return RollbackRecordVerdict((_refusal(ROLLBACK_RECORD_DRIFT, str(exc)),), (), contract)
    if active != contract.deployment_id:
        return RollbackRecordVerdict(
            (),
            (
                f"signed last-good record verified; the active deployment {active} is not "
                f"the record's {contract.deployment_id}, so step 0a restores the record's "
                "deployment over it before any other mutation",
            ),
            contract,
        )
    return RollbackRecordVerdict(
        (),
        (f"signed last-good record verified; active deployment {active} is the record's",),
        contract,
    )


def rollback_record_verdict(
    workspace: Any,
    *,
    app_name: str,
    scope: str,
    app_exists: bool,
    rebase_unverified_app: bool,
    journal_state: Callable[[], str],
) -> RollbackRecordVerdict:
    """Judge the record the way step 0a will, before the lease and any stop."""

    if not app_exists:
        return RollbackRecordVerdict((), ("no App exists yet; there is no blue to protect",))
    try:
        state = journal_state()
    except Exception as exc:  # noqa: BLE001 - an unreadable journal never reads as absent
        return RollbackRecordVerdict(
            (_refusal(ROLLBACK_RECORD_TAMPERED, f"first-install journal unreadable: {exc}"),),
            (),
        )
    if state in JOURNAL_OWNED_STATES:
        return RollbackRecordVerdict(
            (),
            (
                f"the App is owned by its signed first-install journal ({state}); the "
                "recovery under the lease deletes it, so no blue is protected",
            ),
        )
    if rebase_unverified_app and state != "signed":
        return _absence_verdict(workspace, app_name=app_name, scope=scope)
    return _signed_verdict(workspace, app_name=app_name, scope=scope)

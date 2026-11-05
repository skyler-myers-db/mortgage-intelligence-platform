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

* **core tier** (``--agentic-tier off``, an existing App, no rebase; M2-PR7b):
  the record is judged the way the core step 0a (``app_core_rollback
  ensure``) judges it: ``CORE_LOAD`` reads it through the tier gate and
  ``CORE_VERIFY`` proves the App identity and the core resource proof, never
  the Gateway. No record or a retired tombstone refuses as
  ``rollback_record_absent``; a record of the other tier (a v5, v6 or agentic
  v7 record) as ``rollback_record_present``; a failed signature as
  ``rollback_record_tampered``; a drifted identity or binding as
  ``rollback_record_drift``. An active deployment that is not the record's is
  a note, as above: the core step 0a restores the record's over it. A stopped
  App over the record's own deployment refuses as ``rollback_record_drift``
  (``blue_stopped``): the core step 0a never starts a core blue, so it would
  refuse under the lease after quiescing blue's treatment; here nothing is.

* **adopt phase** (``--phase adopt|unadopt``, M2-PR7): the record is judged
  the way adopt-live will judge it, with the tier-aware reader. No record (or
  no scope yet), a v7 adopted record or its retired tombstone passes with a
  note: adopt-live itself proves the record precondition of its mode under
  the lease. Any other record refuses as ``rollback_record_present`` (an
  install with a captured blue is not adopted), and a record that does not
  verify as ``rollback_record_tampered``.

No App, or an App a signed first-install journal owns (the recovery under the
lease deletes it), has no blue to protect, so nothing is read. Every call is a
GET or a list.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from tools.databricks.app_deployment_rollback import verified_signed_last_good_contract
from tools.databricks.app_deployment_state import active_deployment_id, latest_succeeded
from tools.databricks.app_rollback_bootstrap_gate import assert_rollback_record_absent
from tools.databricks.app_rollback_record_contract import (
    V7_RECORD_VERSION,
    V7_REFUSAL,
    load_signed_record,
)
from tools.databricks.app_rollback_secret_scope import (
    _scope_names,
    expected_app_rollback_scope,
)
from tools.databricks.app_rollback_signed_contract import SignedLastGoodAppContract
from tools.databricks.app_start_deployment import is_stopped
from tools.databricks.blue_tier_gate import BlueTierRefused
from tools.databricks.core_blue import CoreBlueRefused, load_core_blue, verify_core_blue
from tools.databricks.core_blue_restore import BLUE_STOPPED
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
# The core tier's step-0a reads (app_core_rollback ensure, M2-PR7b).
CORE_LOAD = load_core_blue
CORE_VERIFY = verify_core_blue
CORE_LATEST = latest_succeeded
CORE_STOPPED = is_stopped

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
    "capture), or, over a retired adopt-live tombstone (where that rebase refuses), "
    "re-adopt with --phase adopt from a core-tier profile (MIP_AGENTIC_TIER off: adopt-live "
    "refuses the agentic tier as tier_agentic); otherwise restore the record from the "
    "deploy that captured it. Then re-run."
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


_REMEDY_ADOPT_PRESENT = (
    "adopt-live signs only an install with no record, its own adopted record or a "
    "retired tombstone; a captured blue is already governed, so run the release instead."
)


def _adopt_phase_verdict(
    workspace: Any, *, app_name: str, scope: str, lakebase_instance: str
) -> RollbackRecordVerdict:
    try:
        if expected_app_rollback_scope(app_name) not in _scope_names(workspace):
            return RollbackRecordVerdict((), ("adopt phase: no rollback scope yet; adopt-live creates it",))
        record = load_signed_record(
            workspace, app_name=app_name, scope=scope, expected_lakebase_instance=lakebase_instance
        )
    except Exception as exc:  # noqa: BLE001 - a record that does not verify refuses
        return RollbackRecordVerdict((_refusal(ROLLBACK_RECORD_TAMPERED, str(exc)),), ())
    if record is None:
        return RollbackRecordVerdict((), ("adopt phase: no rollback record; adopt-live judges and signs one",))
    if record["version"] == V7_RECORD_VERSION and record["provenance"] in {"adopted", "retired"}:
        return RollbackRecordVerdict(
            (), (f"adopt phase: a v7 {record['provenance']} record; adopt-live judges it under the lease",)
        )
    detail = f"the slot holds a v{record['version']} {record.get('provenance', 'captured')} record"
    return RollbackRecordVerdict(
        (PrestopRefusal(ROLLBACK_RECORD_PRESENT, f"{detail}. {_REMEDY_ADOPT_PRESENT}"),), ()
    )


_REMEDY_V7 = (
    "The slot holds a verified tier-aware v7 record (an adopted blue), which this v5/v6 "
    "release path can neither verify nor restore; the core release path (M2-PR7b) reads it. "
    "Nothing needs repair: release this install in the core tier (MIP_AGENTIC_TIER=off), or "
    "run the adopt phases; an agentic release over it is M4-PR1's."
)


def _signed_verdict(workspace: Any, *, app_name: str, scope: str) -> RollbackRecordVerdict:
    try:
        contract = ASSERT_SIGNED(workspace, app_name=app_name, scope=scope)
    except Exception as exc:  # noqa: BLE001 - every failure refuses with its class
        if V7_REFUSAL in str(exc):
            return RollbackRecordVerdict(
                (PrestopRefusal(ROLLBACK_RECORD_PRESENT, f"{exc}. {_REMEDY_V7}"),), ()
            )
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


_REMEDY_CORE_TIER = (
    "A core release stands on a core blue only (tools/databricks/blue_tier_gate.py): an "
    "agentic blue is released in the agentic tier. Nothing needs repair."
)


def _core_signed_verdict(
    workspace: Any, *, app_name: str, scope: str, lakebase_instance: str
) -> RollbackRecordVerdict:
    try:
        record = CORE_LOAD(
            workspace,
            app_name=app_name,
            scope=scope,
            lakebase_instance=lakebase_instance,
            agentic_tier="off",
        )
    except (BlueTierRefused, CoreBlueRefused) as exc:
        return RollbackRecordVerdict(
            (PrestopRefusal(ROLLBACK_RECORD_PRESENT, f"{exc}. {_REMEDY_CORE_TIER}"),), ()
        )
    except Exception as exc:  # noqa: BLE001 - every failure refuses with its class
        return RollbackRecordVerdict((_refusal(_classify(exc), str(exc)),), ())
    if record is None:
        return RollbackRecordVerdict(
            (_refusal(ROLLBACK_RECORD_ABSENT, "no core record (an empty slot or a tombstone)"),), ()
        )
    try:
        CORE_VERIFY(workspace, record, app_name=app_name)
        active = ACTIVE_DEPLOYMENT(workspace, app_name=app_name)
        latest = str(getattr(CORE_LATEST(workspace, app_name=app_name), "deployment_id", "") or "")
        # The core step 0a's own branch: over the record's deployment it proves
        # blue in place, and refuses a stopped one before any repair.
        in_place = active == record["deployment_id"] and latest == active
        stopped = in_place and CORE_STOPPED(workspace, app_name=app_name)
    except Exception as exc:  # noqa: BLE001 - the core step 0a reads it the same way
        return RollbackRecordVerdict((_refusal(ROLLBACK_RECORD_DRIFT, str(exc)),), ())
    if stopped:
        return RollbackRecordVerdict(
            (
                PrestopRefusal(
                    ROLLBACK_RECORD_DRIFT,
                    "the signed core blue is stopped, so the core step 0a would refuse it "
                    f"(blue_stopped) under the lease after quiescing its treatment: {BLUE_STOPPED}",
                ),
            ),
            (),
        )
    where = "is the record's" if active == record["deployment_id"] else (
        "is not the record's, so the core step 0a restores the record's deployment over it"
    )
    return RollbackRecordVerdict(
        (),
        (
            f"signed core record verified ({record['provenance']}, core resource proof); "
            f"the active deployment {where}",
        ),
    )


def rollback_record_verdict(
    workspace: Any,
    *,
    app_name: str,
    scope: str,
    app_exists: bool,
    rebase_unverified_app: bool,
    journal_state: Callable[[], str],
    adopt_phase: bool = False,
    lakebase_instance: str = "",
    agentic_tier: str = "on",
) -> RollbackRecordVerdict:
    """Judge the record the way step 0a (or, in an adopt phase, adopt-live) will."""

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
    if adopt_phase:
        return _adopt_phase_verdict(
            workspace, app_name=app_name, scope=scope, lakebase_instance=lakebase_instance
        )
    if rebase_unverified_app and state != "signed":
        return _absence_verdict(workspace, app_name=app_name, scope=scope)
    if agentic_tier == "off":
        return _core_signed_verdict(
            workspace, app_name=app_name, scope=scope, lakebase_instance=lakebase_instance
        )
    return _signed_verdict(workspace, app_name=app_name, scope=scope)

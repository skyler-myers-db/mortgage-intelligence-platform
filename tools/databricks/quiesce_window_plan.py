"""The read-only plan of the one bounded quiesce window a release may need (M0-PR8).

A release keeps blue serving. It stops the App only for a window reason:

* **WR1** binding diff: the App's live resource bindings differ from the
  bundle's, compared as a set keyed by binding name (an order-only difference
  is equal) on their access contract (a binding's own description is display
  text, ``app_resource_bindings.BINDING_DISPLAY_FIELDS``, never a reason), or
  a binding id is unresolved. Spike S2 measured that every
  update that carries resources, an identical array included, grants CREATE
  ON DATABASE to the App's Lakebase role again with no restart, and only the
  migration job's revoke takes it back: a WR1 window runs that job after its
  binding update whatever the migration plan says (``app_role_regrant``
  ``binding_send`` in the body, and its budget). A description-only
  difference is no reason: the release applies it online with a
  create-update masked to ``description`` (no restart, no role change; S2,
  S2b), and the public summary names it as an ``online_fixups`` entry when
  no window is open at the binding step (a WR1 update carries it, a WR4
  window leaves it to a later release);
* **WR2** Lakebase role rotation: ``converge_lakebase_oauth_role --check``
  drifts for the App, verifier or jobs-reader role, or a ``noop`` migration
  plan reads the jobs reader's grants as drift (M2-PR5; absent included) or
  the App role's CREATE ON DATABASE as re-granted (``app_role_regrant``
  ``owed``: an earlier binding send outside a window, spike S2). A rotation
  drops the role's grants, which only the migration job re-applies, and that
  job runs only inside a window. Blue never reads as the jobs reader, and the
  App role's re-grant changes no schema, so either keeps blue restorable. The
  grants alone are no reason under an ``apply`` plan, whose WR3 window runs
  that job anyway (a pending table reads as grant drift), nor under a
  ``refuse`` plan, which refuses the window;
* **WR3** pending migration: the Lakebase migration plan decides ``apply``;
* **WR4** acked instance update: the bundle plan updates the Lakebase
  instance under an acknowledged digest (``bundle_plan_instance_ack``).

The plan is computed twice from live, read-only facts: before the lease
(``pre-lease``, which the plan phase prints) and again under it immediately
before the non-App apply (``pre-apply``, the TOCTOU recheck). Its canonical
body names the reasons and every fact the window relies on (blue's deployment,
its restore source and that folder's full-tree digest, the env-name set, the
migration's compatibility), and its sha256 is the window's identity.

**Exact digest ack.** The owner hardware-key operation attestation is deferred
(the review mode is single-maintainer audited). In its place a window opens
only when ``MIP_ACK_QUIESCE_WINDOW`` equals this sha256 at both positions, so
the opened reasons are exactly the predicted ones. No reason needs no ack.
One re-derivation is accepted, at pre-apply only: step 0a's tier a starts a
stopped blue and re-pins the record onto the start between the two gates, so
the pre-apply body names the start where the acked pre-lease body named blue.
The body with ``blue_deployment_id`` set back to the record's signed
``predecessor_deployment_id`` is then the acked window
(``acknowledged_digests``); every other fact must be identical, so a re-pin
from another folder or tree (a tier-b restore names the immutable copy)
never matches.

**Tier a.** The window closes with a start-only restore, which is valid only
while blue is the App's last deployment and a start would snapshot blue's own
bytes: the App's default source is blue's source, and that folder's tree
digest equals blue's ``/src/`` snapshot's. Otherwise the plan refuses. Spike
S7 recorded what that rests on: a start snapshots the CURRENT bytes of the
default source with the newest deployment's env names, and the newest
deployment of ANY state (a FAILED one becomes active and moves the default
source) decides it, on a stopped App too (``last_deployment_is``).

**Contract migrations.** A pending migration that is not expand-only leaves
blue unable to run on the migrated schema: ``blue_restorable`` is false, the
window stays open until green activation, and the dead-man (M0-PR9) leaves the
App stopped. That outage is part of the acknowledged digest.

**Budget** (M0-PR9). A window's canonical body also names its budget
inputs: the predicted window (``predicted_seconds``), the predicted restore
(``predicted_restore_seconds``) and the 15-minute margin, so the exact digest
ack covers them. The admission itself is time-dependent and never in the
digest: ``tools/databricks/quiesce_window_budget.py`` refuses a window unless
the job's remaining budget (``MIP_JOB_DEADLINE_EPOCH`` minus now) covers the
sum. A plan without a reason has no budget, so its body and digest are the
ones M0-PR8 computed.

**An earlier window still open.** A run that ended inside its window leaves
the journal open, and the next plan refuses it (``window_journal_open``)
unless the pre-lease position can hand it to a recovery that runs under the
lease before step 0a: the dead-man's restart the App serves
(``deadman_recovery``, no ack), or an owner's break-glass start
(``owner_start_recovery``) with its exact ``owner_start_repin`` digest ack
(``MIP_ACK_OWNER_START_REPIN``; tools/databricks/quiesce_window_recovery.py
classifies it and says why that ack is never inferred). Neither is part of
the canonical body, so no window digest moves.

``public_summary()`` carries codes, counts, booleans and digests only.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any

ACK_ENV = "MIP_ACK_QUIESCE_WINDOW"
# The job's hard deadline in epoch seconds (M0-PR9): the workflow sets it at job
# start from timeout-minutes, a local run from --deadline. Shell only, one run.
DEADLINE_ENV = "MIP_JOB_DEADLINE_EPOCH"
PUBLIC_SCHEMA = "mip.quiesce-window-plan.public.v1"
FULL_SCHEMA = "mip.quiesce-window-plan.full.v1"
CANONICAL_SCHEMA = "mip.quiesce-window-plan.canonical.v1"
BUDGET_SCHEMA = "mip.quiesce-window-budget.v1"
POSITIONS = frozenset({"pre-lease", "pre-apply"})
REASONS = ("WR1", "WR2", "WR3", "WR4")
REASON_NAMES = {
    "WR1": "binding_diff",
    "WR2": "lakebase_role_rotation",
    "WR3": "pending_migration",
    "WR4": "acked_instance_update",
}
# WR5 (M2-PR8) is the Lakebase ownership handover's announced window, owner-run
# for an install no signed blue governs yet (tools/databricks/quiesce_window_wr5.py).
# A release never plans it, so it is not in REASONS: WindowFacts.reasons() and
# every release digest are unchanged.
HANDOVER_REASON = "WR5"
CODES = frozenset({"none", "window", "not_applicable", "refused"})
REFUSAL_CODES = frozenset(
    {
        "window_unacked",
        "window_blue_not_current",
        "window_start_source_drift",
        "window_migration_refused",
        "window_journal_open",
        "window_facts_incomplete",
        "window_budget_refused",
    }
)
REMEDIES = {
    "window_unacked": (
        "review the window's reasons and predicted length in the private plan, then re-run "
        f"with {ACK_ENV} set to the window sha256"
    ),
    "window_blue_not_current": (
        "blue is not the App's last deployment, so a start would not restore it (tier a); "
        "re-run once step 0a has restored and re-signed blue"
    ),
    "window_start_source_drift": (
        "a start would snapshot other bytes than blue runs: the App's default source or that "
        "folder's tree differs from blue's snapshot"
    ),
    "window_migration_refused": "the Lakebase migration plan refused; see its report",
    "window_journal_open": (
        "an earlier window's journal is still open, and neither a dead-man restart the App "
        "serves nor an acknowledged owner start is recorded for it: wait for that window's "
        "dead-man (M0-PR9), whose restart the next run re-pins (repin --from-deadman); or, "
        "after the owner's break-glass start, dispatch the release with the exact "
        "owner_start_repin digest this plan prints (repin --from-owner-start; "
        "docs/deployment.md, Owner break-glass)"
    ),
    "window_facts_incomplete": "a fact the window needs could not be read; see the private plan",
    "window_budget_refused": (
        f"the job budget does not admit the window: {DEADLINE_ENV} (CI sets it at job start "
        "from timeout-minutes; locally --deadline <epoch>) must leave the predicted window, "
        "the predicted restore and 15 minutes; see the plan's admission"
    ),
}
ACK_STATES = frozenset({"not_required", "matched", "absent", "mismatch"})
# An open window's dead-man state as a plan may print it: the marker's own
# states (jobs/window_deadman.py), a marker that is not the window's or cannot
# be read, and ``deadman_superseded``, a restart the App no longer serves
# (tools/databricks/quiesce_window_recovery.py refines it; never a marker state).
DEADMAN_STATES = frozenset(
    {
        "absent",
        "mismatch",
        "unreadable",
        "deadman_restarted",
        "deadman_superseded",
        "deadman_start_failed",
        "deadman_start_timeout",
        "deadman_start_unreadable",
        "deadman_left_stopped",
        "deadman_skipped",
    }
)
# An owner's raw start over an open window (the break-glass), as the recovery
# classifies it: ``candidate`` is the one deployment a release may re-pin with
# its exact owner_start_repin ack; every other code is why there is none.
OWNER_START_REFUSALS = (
    "owner_start_contract_window",
    "owner_start_marker_unreadable",
    "owner_start_no_baseline",
    "owner_start_not_running",
    "owner_start_none",
    "owner_start_ambiguous",
    "owner_start_not_current",
    "owner_start_not_a_start",
)
OWNER_START_STATES = frozenset({"absent", "candidate", *OWNER_START_REFUSALS})
OWNER_START_CREATORS = frozenset({"deployer", "other", "unknown"})
_SHA256 = re.compile(r"[0-9a-f]{64}")
DESCRIPTION_STATES = frozenset({"equal", "differs"})
# Conservative per-step budgets in seconds, for the admission (M0-PR9). They
# are not measurements, and none is shrunk by one: spike S7 measured a trivial
# App's start at 96-108 s to compute ACTIVE before its deployment even begins,
# and its stop at about 40-45 s; the real App's start deployment installs its
# whole tree on top, so "start" keeps its 600 s until a governed run measures it.
# "green_activation" is a contract window's tail: it stays open (the App
# stopped) through step 5's publish, the green deploy and its capture.
STEP_BUDGET_S = {
    "stop": 120,
    "WR1": 120,
    "WR2": 180,
    "WR3": 600,
    "WR4": 900,
    "start": 600,
    "repin": 300,
    # Every window runs the migration job (step 4b). A WR3 budgets it; any
    # other window gets this: the job re-grants a rotated role (WR2) and
    # revokes the CREATE ON DATABASE a binding send re-granted (WR1, spike S2).
    "migrate": 600,
    "green_activation": 1800,
}
# The admission margin on top of the predicted window and restore (M0-PR9).
ADMISSION_MARGIN_S = 15 * 60


@dataclass(frozen=True)
class WindowFacts:
    """Live, read-only facts one plan is computed from."""

    applicable: bool
    not_applicable_reason: str = ""
    journal_state: str = "absent"
    # The open window's dead-man marker state (M0-PR9; a pointer, never proof).
    deadman_state: str = "absent"
    # An owner start over the open window (quiesce_window_recovery): its code,
    # a candidate's owner_start_repin digest, and who made it (information only).
    owner_start_state: str = "absent"
    owner_start_sha256: str = ""
    owner_start_creator: str = "unknown"
    binding_state: str = "equal"  # "equal", "diff" or "unresolved"
    binding_lines: tuple[str, ...] = ()
    # The bundle's App description against the live one: a difference is the
    # release's online fix-up (create-update masked to description), never a window.
    description_state: str = "equal"  # "equal" or "differs"
    role_state: str = "exact"  # the App role: "exact" or "drift"
    verifier_role_state: str = "exact"  # the AI Gateway verifier role; "absent" in the core tier
    jobs_reader_role_state: str = "exact"  # the jobs identity's Lakebase role (M2-PR5)
    jobs_reader_grants: str = "exact"  # its grants, from the plan ("unchecked" if it refused early)
    # The App role's CREATE ON DATABASE, from the plan (spike S2): "owed" after
    # a binding send re-granted it ("unchecked" if the plan refused early).
    app_role_regrant: str = "exact"
    migration_decision: str = "noop"  # "noop", "apply" or "refuse"
    migration_blue_restorable: bool = True
    migration_pending: tuple[str, ...] = ()
    migration_pending_contract: tuple[str, ...] = ()
    instance_update_sha256: str = ""
    blue_deployment_id: str = ""
    # The signed record's predecessor_deployment_id: the blue a re-pin replaced.
    blue_predecessor_deployment_id: str = ""
    blue_current: bool = False
    restore_source: str = ""
    default_source_code_path: str = ""
    env_names_sha256: str = ""
    source_tree_sha256: str = ""
    artifact_tree_sha256: str = ""

    def reasons(self) -> tuple[str, ...]:
        # A core tier's absent verifier role has nothing to rotate; any other
        # value than exact is a drift (WR2), never a silent skip.
        verifier = "exact" if self.verifier_role_state == "absent" else self.verifier_role_state
        roles = (self.role_state, verifier, self.jobs_reader_role_state)
        # Only a skipped migration job leaves the jobs reader's grants, or the
        # App role's CREATE ON DATABASE (S2), as they are: apply re-grants and
        # revokes inside its WR3 window, refuse runs nothing.
        grants = self.jobs_reader_grants != "exact" or self.app_role_regrant != "exact"
        regrant = grants and self.migration_decision not in {"apply", "refuse"}
        present = {
            "WR1": self.binding_state != "equal",
            "WR2": any(state != "exact" for state in roles) or regrant,
            "WR3": self.migration_decision == "apply",
            "WR4": bool(self.instance_update_sha256),
        }
        return tuple(reason for reason in REASONS if present[reason])

    def as_dict(self) -> dict[str, Any]:
        return {
            name: list(value) if isinstance(value, tuple) else value
            for name, value in self.__dict__.items()
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> WindowFacts:
        known = set(cls.__dataclass_fields__)
        if set(value) - known:
            raise ValueError("window facts carry an unknown field")
        return cls(
            **{
                name: tuple(item) if isinstance(item, list) else item
                for name, item in value.items()
            }
        )


@dataclass(frozen=True)
class WindowPlan:
    position: str
    code: str
    reasons: tuple[str, ...]
    blue_restorable: bool
    predicted_seconds: int
    sha256: str
    ack: str
    canonical: Mapping[str, Any]
    facts: WindowFacts
    refusals: tuple[str, ...] = ()
    tier: str = ""
    closes_at: str = ""
    remedies: Mapping[str, str] = field(default_factory=dict)
    # The job-budget admission (M0-PR9, quiesce_window_budget), when evaluated.
    admission: Mapping[str, Any] = field(default_factory=dict)
    # The owner_start_repin ack against a candidate's digest (an ACK_STATES value).
    owner_start_ack: str = "not_required"

    @property
    def refused(self) -> bool:
        return bool(self.refusals)

    def public_summary(self) -> dict[str, Any]:
        if self.position not in POSITIONS or self.code not in CODES or self.ack not in ACK_STATES:
            raise ValueError("window plan carries an unreviewed position, code or ack state")
        if any(code not in REFUSAL_CODES for code in self.refusals):
            raise ValueError("window plan carries an unreviewed refusal code")
        if any(reason not in REASONS for reason in self.reasons):
            raise ValueError("window plan carries an unreviewed reason")
        if self.facts.description_state not in DESCRIPTION_STATES:
            raise ValueError("window plan carries an unreviewed description state")
        if (
            self.facts.deadman_state not in DEADMAN_STATES
            or self.facts.owner_start_state not in OWNER_START_STATES
            or self.facts.owner_start_creator not in OWNER_START_CREATORS
            or self.owner_start_ack not in ACK_STATES
        ):
            raise ValueError("window plan carries an unreviewed recovery code")
        candidate = self.facts.owner_start_state == "candidate"
        return {
            "schema": PUBLIC_SCHEMA,
            "position": self.position,
            "code": self.code,
            "reasons": list(self.reasons),
            "tier": self.tier,
            "blue_restorable": self.blue_restorable,
            "closes_at": self.closes_at,
            "predicted_seconds": int(self.predicted_seconds),
            "window_sha256": self.sha256,
            "ack": self.ack,
            "refusals": list(self.refusals),
            "admission": dict(self.admission) or {"state": "not_evaluated"},
            "deadman_recovery": deadman_recovery(self.facts, position=self.position),
            # An owner start over an open window: codes, and a candidate's digest.
            "journal_state": self.facts.journal_state,
            "deadman_state": self.facts.deadman_state,
            "owner_start": self.facts.owner_start_state,
            "owner_start_sha256": self.facts.owner_start_sha256 if candidate else "",
            "owner_start_creator": self.facts.owner_start_creator,
            "owner_start_ack": self.owner_start_ack,
            "owner_start_recovery": self.position == "pre-lease" and self.owner_start_ack == "matched",
            # What the binding step applies online (spike S2): never a reason.
            "online_fixups": online_fixups(self.facts, self.reasons),
            "app_role_regrant": list(self.canonical.get("app_role_regrant") or ()),
        }

    def full(self) -> dict[str, Any]:
        report = self.public_summary()
        report["schema"] = FULL_SCHEMA
        report["canonical"] = dict(self.canonical)
        report["facts"] = self.facts.as_dict()
        report["remedies"] = {code: REMEDIES[code] for code in self.refusals}
        return report


def canonical_body(facts: WindowFacts, *, blue_restorable: bool) -> dict[str, Any]:
    """Everything the window relies on; never the position, so both gates digest alike.

    A window's body also names its budget inputs (M0-PR9); a plan with no
    reason has none, so its body is exactly M0-PR8's.
    """

    reasons = facts.reasons()
    body: dict[str, Any] = {
        "schema": CANONICAL_SCHEMA,
        "reasons": list(reasons),
        "blue_deployment_id": facts.blue_deployment_id,
        "restore_source": facts.restore_source,
        "source_tree_sha256": facts.source_tree_sha256,
        "env_names_sha256": facts.env_names_sha256,
        "blue_restorable": blue_restorable,
        "binding_state": facts.binding_state,
        "binding_lines": sorted(facts.binding_lines),
        "role_state": facts.role_state,
        "verifier_role_state": facts.verifier_role_state,
        "jobs_reader_role_state": facts.jobs_reader_role_state,
        "jobs_reader_grants": facts.jobs_reader_grants,
        "migration_pending": sorted(facts.migration_pending),
        "migration_pending_contract": sorted(facts.migration_pending_contract),
        "instance_update_sha256": facts.instance_update_sha256,
    }
    if reasons:
        body["budget"] = budget_body(reasons, blue_restorable=blue_restorable)
        regrant = role_regrant(facts, reasons)
        if regrant:
            body["app_role_regrant"] = regrant
    return body


def role_regrant(facts: WindowFacts, reasons: tuple[str, ...]) -> list[str]:
    """Why the App role holds CREATE ON DATABASE in this window, which its migration job revokes.

    Spike S2: every update that carries resources grants that privilege to the
    App's Lakebase role again. ``binding_send``: this window's own binding
    update (WR1). ``observed``: an earlier send outside a window, which the
    migration plan read (``app_role_regrant`` ``owed``, a WR2).
    """

    sources = ["binding_send"] if "WR1" in reasons else []
    if facts.app_role_regrant == "owed":
        sources.append("observed")
    return sources


def body_sha256(body: Mapping[str, Any]) -> str:
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(b"mip-quiesce-window-plan\0" + canonical).hexdigest()


def acknowledged_digests(body: Mapping[str, Any], *, position: str, predecessor: str) -> tuple[str, ...]:
    """The digests an exact ack may equal: the body's own, and at pre-apply its re-pin's.

    Step 0a's tier a re-pins a stopped blue onto its start between the two
    gates, and the start is proven to be blue's signed tree from blue's own
    folder with blue's env names, so only ``blue_deployment_id`` moves: the
    body with it set back to the record's signed predecessor is the window
    the operator acked before the lease.
    """

    digests = [body_sha256(body)]
    if position == "pre-apply" and predecessor and predecessor != body.get("blue_deployment_id"):
        digests.append(body_sha256({**body, "blue_deployment_id": predecessor}))
    return tuple(digests)


def predicted_seconds(reasons: tuple[str, ...], *, blue_restorable: bool) -> int:
    """The window's predicted length: from the stop to the step that closes it.

    A restorable window closes on blue's start and re-pin. A contract window
    closes only at green activation, so its tail (``green_activation``) is
    part of the outage the admission must fit before the job's deadline.
    Every window runs the migration job: a window without WR3 adds its
    ``migrate`` budget (spike S2: the revoke after a binding send).
    """

    if not reasons:
        return 0
    total = STEP_BUDGET_S["stop"] + sum(STEP_BUDGET_S[reason] for reason in reasons)
    if "WR3" not in reasons:
        total += STEP_BUDGET_S["migrate"]
    if blue_restorable:
        total += STEP_BUDGET_S["start"] + STEP_BUDGET_S["repin"]
    else:
        total += STEP_BUDGET_S["green_activation"]
    return total


def predicted_restore_seconds(*, blue_restorable: bool) -> int:
    """The restore after a failure at the window's last mutation (M0-PR9).

    The EXIT-trap compensation then starts blue and re-pins it (tier a): one
    start and one re-pin budget. A contract window restores nothing: blue
    cannot run on the migrated schema, so the App stays stopped until green
    activation and the dead-man leaves it stopped.
    """

    return STEP_BUDGET_S["start"] + STEP_BUDGET_S["repin"] if blue_restorable else 0


def budget_body(reasons: tuple[str, ...], *, blue_restorable: bool) -> dict[str, Any]:
    """The budget inputs a window's digest covers: window, restore, margin and their sum."""

    window = predicted_seconds(reasons, blue_restorable=blue_restorable)
    restore = predicted_restore_seconds(blue_restorable=blue_restorable)
    return {
        "schema": BUDGET_SCHEMA,
        "predicted_window_seconds": window,
        "predicted_restore_seconds": restore,
        "margin_seconds": ADMISSION_MARGIN_S,
        "required_seconds": window + restore + ADMISSION_MARGIN_S,
    }


def with_admission(plan: WindowPlan, admission: Mapping[str, Any], *, admitted: bool) -> WindowPlan:
    """The plan with its job-budget admission; a window it does not admit refuses."""

    if not plan.reasons:
        return replace(plan, admission=dict(admission))
    refusals = plan.refusals if admitted else (*plan.refusals, "window_budget_refused")
    return replace(
        plan,
        admission=dict(admission),
        refusals=tuple(dict.fromkeys(refusals)),
        code="refused" if refusals else plan.code,
    )


# The reasons whose window is open at the binding step: WR4 opens before the
# non-App apply, WR1 at the binding step itself (deploy_step_bundle_apply.sh).
BINDING_STEP_WINDOW_REASONS = frozenset({"WR1", "WR4"})


def online_fixups(facts: WindowFacts, reasons: tuple[str, ...] = ()) -> list[str]:
    """What the binding step applies online while blue serves: a differing description (S2, S2b).

    Only with no window open at that step. A WR1 window's binding update
    carries the bundle's description itself. A WR4 window has already
    stopped the App, and every create-update the spikes measured ran on a
    serving App, so the step leaves the description to a later release.
    """

    if facts.description_state != "differs" or BINDING_STEP_WINDOW_REASONS & set(reasons):
        return []
    return ["description"]


def deadman_recovery(facts: WindowFacts, *, position: str) -> bool:
    """An earlier window's dead-man restarted blue: the run re-pins it under its lease.

    Only before the lease: the recovery (``repin --from-deadman``) runs under
    the lease before step 0a and closes the journal, so the pre-apply recheck
    reads it closed or refuses it as still open.
    """

    return (
        position == "pre-lease"
        and facts.journal_state in {"window_open", "window_starting"}
        and facts.deadman_state == "deadman_restarted"
    )


def owner_start_ack_state(facts: WindowFacts, acknowledgement: str) -> str:
    """The owner_start_repin ack against an open window's candidate: an ``ACK_STATES`` value."""

    if facts.journal_state in {"absent", "closed"} or facts.owner_start_state != "candidate":
        return "not_required"
    given, digest = acknowledgement.strip(), facts.owner_start_sha256
    if not given:
        return "absent"
    exact = _SHA256.fullmatch(given) and _SHA256.fullmatch(digest) and hmac.compare_digest(given, digest)
    return "matched" if exact else "mismatch"


def owner_start_recovery(facts: WindowFacts, *, position: str, acknowledgement: str) -> bool:
    """An owner's raw start serves over an earlier open window, and its exact ack was given.

    Only before the lease, like the dead-man's: the re-pin (``repin
    --from-owner-start``) runs under the lease before step 0a, recomputes the
    plan there, requires the same digest and closes the journal, so the
    pre-apply recheck reads it closed or refuses it as still open.
    """

    return position == "pre-lease" and owner_start_ack_state(facts, acknowledgement) == "matched"


def window_plan(
    facts: WindowFacts, *, position: str, acknowledgement: str, owner_start_acknowledgement: str = ""
) -> WindowPlan:
    """Decide the window from facts; refuse anything a start-and-re-pin cannot close."""

    if position not in POSITIONS:
        raise ValueError("unknown window plan position")
    reasons = facts.reasons()
    blue_restorable = facts.migration_blue_restorable if "WR3" in reasons else True
    body = canonical_body(facts, blue_restorable=blue_restorable)
    digest = body_sha256(body)
    common: dict[str, Any] = {
        "position": position,
        "reasons": reasons,
        "blue_restorable": blue_restorable,
        "predicted_seconds": predicted_seconds(reasons, blue_restorable=blue_restorable),
        "sha256": digest,
        "canonical": body,
        "facts": facts,
        "owner_start_ack": owner_start_ack_state(facts, owner_start_acknowledgement),
    }
    if not facts.applicable:
        return WindowPlan(
            **{**common, "reasons": (), "predicted_seconds": 0},
            code="not_applicable",
            ack="not_required",
        )
    refusals: list[str] = []
    recovering = deadman_recovery(facts, position=position) or owner_start_recovery(
        facts, position=position, acknowledgement=owner_start_acknowledgement
    )
    if facts.journal_state not in {"absent", "closed"} and not recovering:
        refusals.append("window_journal_open")
    if facts.migration_decision == "refuse":
        refusals.append("window_migration_refused")
    if not reasons and not refusals:
        return WindowPlan(**common, code="none", ack="not_required")
    acked = acknowledged_digests(
        body, position=position, predecessor=facts.blue_predecessor_deployment_id
    )
    ack = (
        "matched" if acknowledgement in acked else "absent" if not acknowledgement else "mismatch"
    )
    if reasons:
        if not (facts.blue_deployment_id and facts.restore_source and facts.env_names_sha256):
            refusals.append("window_facts_incomplete")
        if position == "pre-apply" and not facts.blue_current:
            refusals.append("window_blue_not_current")
        tree = facts.source_tree_sha256
        if (
            not tree
            or tree != facts.artifact_tree_sha256
            or (
                facts.default_source_code_path
                and facts.default_source_code_path != facts.restore_source
            )
        ):
            refusals.append("window_start_source_drift")
        if ack != "matched":
            refusals.append("window_unacked")
    else:
        ack = "not_required"
    return WindowPlan(
        **common,
        code="refused" if refusals else "window",
        ack=ack,
        refusals=tuple(dict.fromkeys(refusals)),
        tier="a" if reasons else "",
        closes_at=("start_repin" if blue_restorable else "green_activation") if reasons else "",
    )

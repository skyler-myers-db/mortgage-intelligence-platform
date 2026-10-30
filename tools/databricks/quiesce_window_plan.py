"""The read-only plan of the one bounded quiesce window a release may need (M0-PR8).

A release keeps blue serving. It stops the App only for a window reason:

* **WR1** binding diff: the App's live resource bindings differ from the
  bundle's (any diff, until spike S2 proves an unchanged database binding
  leaves a running App and its role alone), or a binding id is unresolved;
* **WR2** App role rotation: ``converge_lakebase_oauth_role --check`` drifts;
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

**Tier a.** The window closes with a start-only restore, which is valid only
while blue is the App's last deployment and a start would snapshot blue's own
bytes: the App's default source is blue's source, and that folder's tree
digest equals blue's ``/src/`` snapshot's. Otherwise the plan refuses.

**Contract migrations.** A pending migration that is not expand-only leaves
blue unable to run on the migrated schema: ``blue_restorable`` is false, the
window stays open until green activation, and the dead-man (M0-PR9) leaves the
App stopped. That outage is part of the acknowledged digest.

``public_summary()`` carries codes, counts, booleans and digests only.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

ACK_ENV = "MIP_ACK_QUIESCE_WINDOW"
PUBLIC_SCHEMA = "mip.quiesce-window-plan.public.v1"
FULL_SCHEMA = "mip.quiesce-window-plan.full.v1"
CANONICAL_SCHEMA = "mip.quiesce-window-plan.canonical.v1"
POSITIONS = frozenset({"pre-lease", "pre-apply"})
REASONS = ("WR1", "WR2", "WR3", "WR4")
REASON_NAMES = {
    "WR1": "binding_diff",
    "WR2": "app_role_rotation",
    "WR3": "pending_migration",
    "WR4": "acked_instance_update",
}
CODES = frozenset({"none", "window", "not_applicable", "refused"})
REFUSAL_CODES = frozenset(
    {
        "window_unacked",
        "window_blue_not_current",
        "window_start_source_drift",
        "window_migration_refused",
        "window_journal_open",
        "window_facts_incomplete",
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
        "an earlier window's journal is still open; the dead-man recovery (M0-PR9) or the "
        "owner break-glass must close it first"
    ),
    "window_facts_incomplete": "a fact the window needs could not be read; see the private plan",
}
ACK_STATES = frozenset({"not_required", "matched", "absent", "mismatch"})
# Conservative per-step budgets in seconds, for the admission (M0-PR9). They
# are not measurements: spikes S2 and S7 owe the live timings.
STEP_BUDGET_S = {
    "stop": 120,
    "WR1": 120,
    "WR2": 180,
    "WR3": 600,
    "WR4": 900,
    "start": 600,
    "repin": 300,
}


@dataclass(frozen=True)
class WindowFacts:
    """Live, read-only facts one plan is computed from."""

    applicable: bool
    not_applicable_reason: str = ""
    journal_state: str = "absent"
    binding_state: str = "equal"  # "equal", "diff" or "unresolved"
    binding_lines: tuple[str, ...] = ()
    role_state: str = "exact"  # "exact" or "drift"
    migration_decision: str = "noop"  # "noop", "apply" or "refuse"
    migration_blue_restorable: bool = True
    migration_pending: tuple[str, ...] = ()
    migration_pending_contract: tuple[str, ...] = ()
    instance_update_sha256: str = ""
    blue_deployment_id: str = ""
    blue_current: bool = False
    restore_source: str = ""
    default_source_code_path: str = ""
    env_names_sha256: str = ""
    source_tree_sha256: str = ""
    artifact_tree_sha256: str = ""

    def reasons(self) -> tuple[str, ...]:
        present = {
            "WR1": self.binding_state != "equal",
            "WR2": self.role_state != "exact",
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
        }

    def full(self) -> dict[str, Any]:
        report = self.public_summary()
        report["schema"] = FULL_SCHEMA
        report["canonical"] = dict(self.canonical)
        report["facts"] = self.facts.as_dict()
        report["remedies"] = {code: REMEDIES[code] for code in self.refusals}
        return report


def canonical_body(facts: WindowFacts, *, blue_restorable: bool) -> dict[str, Any]:
    """Everything the window relies on; never the position, so both gates digest alike."""

    return {
        "schema": CANONICAL_SCHEMA,
        "reasons": list(facts.reasons()),
        "blue_deployment_id": facts.blue_deployment_id,
        "restore_source": facts.restore_source,
        "source_tree_sha256": facts.source_tree_sha256,
        "env_names_sha256": facts.env_names_sha256,
        "blue_restorable": blue_restorable,
        "binding_state": facts.binding_state,
        "binding_lines": sorted(facts.binding_lines),
        "role_state": facts.role_state,
        "migration_pending": sorted(facts.migration_pending),
        "migration_pending_contract": sorted(facts.migration_pending_contract),
        "instance_update_sha256": facts.instance_update_sha256,
    }


def body_sha256(body: Mapping[str, Any]) -> str:
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(b"mip-quiesce-window-plan\0" + canonical).hexdigest()


def predicted_seconds(reasons: tuple[str, ...], *, blue_restorable: bool) -> int:
    if not reasons:
        return 0
    total = STEP_BUDGET_S["stop"] + sum(STEP_BUDGET_S[reason] for reason in reasons)
    if blue_restorable:
        total += STEP_BUDGET_S["start"] + STEP_BUDGET_S["repin"]
    return total


def window_plan(facts: WindowFacts, *, position: str, acknowledgement: str) -> WindowPlan:
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
    }
    if not facts.applicable:
        return WindowPlan(
            **{**common, "reasons": (), "predicted_seconds": 0},
            code="not_applicable",
            ack="not_required",
        )
    refusals: list[str] = []
    if facts.journal_state not in {"absent", "closed"}:
        refusals.append("window_journal_open")
    if facts.migration_decision == "refuse":
        refusals.append("window_migration_refused")
    if not reasons and not refusals:
        return WindowPlan(**common, code="none", ack="not_required")
    ack = (
        "matched" if acknowledgement == digest else "absent" if not acknowledgement else "mismatch"
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

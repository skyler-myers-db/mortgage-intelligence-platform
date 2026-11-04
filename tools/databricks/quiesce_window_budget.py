#!/usr/bin/env python3
"""The job-budget admission of the one bounded quiesce window (M0-PR9, threat T5).

A runner that dies inside the window leaves the App stopped. The window is
therefore admitted only while the job's remaining budget covers it:

    MIP_JOB_DEADLINE_EPOCH - now >= predicted window + predicted restore + 15 min

The three inputs are the budget body the window plan's canonical digest
covers (``quiesce_window_plan.budget_body``), so the exact digest ack names
them; only ``now`` and the deadline are read here. The deadline is the job's
hard kill time: ``deploy-dev.yml`` sets it at job start from its
``timeout-minutes``, a local run passes ``./scripts/deploy.sh --deadline
<epoch>`` (or exports it for that one run; never a ``.env.local`` line). It is
also the dead-man's deadline (``jobs/window_deadman.py``): a dead runner
leaves the App restarted by the deadline plus the start budget.

Fail closed: a window with a missing, unparseable, past or implausibly far
deadline (more than six hours, the hosted job cap) is refused, and so is a
budget that does not cover the sum. A plan with no reason needs no deadline.
``admission`` decides; the plan prints it before the lease and again under it,
and ``quiesce_window open`` re-decides it right before its dead-man submit and
again before the stop, with the then-current time.

  python -m tools.databricks.quiesce_window_budget describe --deadline-epoch E

``describe`` (the dry run's line) makes no Databricks call.
"""

from __future__ import annotations

import argparse
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from tools.databricks.quiesce_window_plan import (
    ADMISSION_MARGIN_S,
    BUDGET_SCHEMA,
    DEADLINE_ENV,
    STEP_BUDGET_S,
    budget_body,
)

# The run never starts blue this close to the job deadline: the dead-man does
# (quiesce_window start_window). It covers the start's own wait and the re-pin
# (plus a slack), so a start the run begins can finish before the deadline,
# never be cut off by the job's kill half-way and left window_starting or
# window_started for the owner. The admitted restore and margin cover it.
DEADMAN_GUARD_SLACK_S = 120
DEADMAN_GUARD_S = STEP_BUDGET_S["start"] + STEP_BUDGET_S["repin"] + DEADMAN_GUARD_SLACK_S
# The GitHub-hosted job cap: a deadline further out is a mistake, and a
# dead-man that long would outlive the lease (four hours) by hours.
MAX_HORIZON_S = 6 * 60 * 60
ADMISSION_STATES = frozenset(
    {
        "not_required",
        "admitted",
        "insufficient",
        "deadline_missing",
        "deadline_unparseable",
        "deadline_past",
        "deadline_too_far",
    }
)
_EPOCH = re.compile(r"[0-9]{9,11}")
# A governed phase's own budget (M1-PR6, tools/databricks/phase_budget.py) is
# admitted by the same rule against the same deadline, under its own schema.
PHASE_BUDGET_SCHEMA = "mip.phase-budget.v1"
REVIEWED_BUDGET_SCHEMAS = frozenset({BUDGET_SCHEMA, PHASE_BUDGET_SCHEMA})


class WindowAdmissionError(RuntimeError):
    """The job budget does not admit the window; nothing was stopped or submitted."""

    def __init__(self, admission: Admission) -> None:
        super().__init__(
            f"window_budget_refused: {admission.state} (remaining "
            f"{admission.remaining_seconds} s, required {admission.required_seconds} s); "
            f"set {DEADLINE_ENV} so the job budget covers the window"
        )
        self.admission = admission


@dataclass(frozen=True)
class Admission:
    """One admission decision: its state and the two numbers it compared."""

    state: str
    required_seconds: int = 0
    remaining_seconds: int | None = None
    deadline_epoch: int | None = None

    def __post_init__(self) -> None:
        if self.state not in ADMISSION_STATES:
            raise ValueError("window admission state is not reviewed")

    @property
    def admitted(self) -> bool:
        return self.state in {"admitted", "not_required"}

    def public(self) -> dict[str, Any]:
        """Codes and counts only: the state, the required and the remaining seconds."""

        return {
            "state": self.state,
            "required_seconds": self.required_seconds,
            "remaining_seconds": self.remaining_seconds,
        }


def parse_deadline(raw: object) -> int | None:
    """An epoch-seconds deadline: digits only (no sign, fraction or exponent), else None."""

    text = str(raw or "").strip()
    return int(text) if _EPOCH.fullmatch(text) else None


def admission(budget: Mapping[str, Any] | None, deadline: object, *, now: float) -> Admission:
    """Decide the admission of ``budget`` (None: no window, nothing to admit)."""

    if budget is None:
        return Admission("not_required")
    required = int(budget.get("required_seconds") or 0)
    if budget.get("schema") not in REVIEWED_BUDGET_SCHEMAS or required <= ADMISSION_MARGIN_S:
        raise ValueError("the window budget is not a reviewed budget body")
    if not str(deadline or "").strip():
        return Admission("deadline_missing", required)
    epoch = parse_deadline(deadline)
    if epoch is None:
        return Admission("deadline_unparseable", required)
    remaining = epoch - int(now)
    if remaining <= 0:
        return Admission("deadline_past", required, remaining, epoch)
    if remaining > MAX_HORIZON_S:
        return Admission("deadline_too_far", required, remaining, epoch)
    state = "admitted" if remaining >= required else "insufficient"
    return Admission(state, required, remaining, epoch)


def require_admitted(canonical: Mapping[str, Any], deadline: object, *, now: float) -> Admission:
    """The open's check: the canonical body's own budget, recomputed, and admitted now."""

    reasons = tuple(str(reason) for reason in canonical.get("reasons") or ())
    budget = canonical.get("budget")
    if not reasons or not isinstance(budget, Mapping):
        raise RuntimeError("the window plan carries no budget; re-plan before opening a window")
    expected = budget_body(reasons, blue_restorable=bool(canonical.get("blue_restorable")))
    if dict(budget) != expected:
        raise RuntimeError("the window plan's budget is not the reviewed budget of its reasons")
    decided = admission(budget, deadline, now=now)
    if not decided.admitted:
        raise WindowAdmissionError(decided)
    return decided


def describe(deadline: object, *, now: float) -> str:
    """The dry run's admission line: the deadline's state and the rule, no plan."""

    epoch = parse_deadline(deadline)
    if not str(deadline or "").strip():
        state = f"{DEADLINE_ENV} unset: a window would refuse (a run with no window needs none)"
    elif epoch is None:
        state = f"{DEADLINE_ENV} unparseable: a window would refuse"
    else:
        state = f"{DEADLINE_ENV} leaves {epoch - int(now)} s"
    return (
        f"window admission: {state}; a window opens only while the remaining job budget "
        f"covers its predicted length, its predicted restore and {ADMISSION_MARGIN_S} s"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("describe").add_argument("--deadline-epoch", default="")
    args = parser.parse_args(argv)
    print(f"  {describe(args.deadline_epoch, now=time.time())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

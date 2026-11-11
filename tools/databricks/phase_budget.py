#!/usr/bin/env python3
"""Each governed phase's predicted duration, admitted against the job deadline (M1-PR6).

``deploy-dev.yml`` runs one phase per job, with a hard deadline it exports as
``MIP_JOB_DEADLINE_EPOCH`` (plan 30, release 330 and data 330 minutes, M1-PR7).
A phase that cannot finish before that deadline is refused before its lease,
with nothing changed: a runner the job kills half-way leaves a release with
blue governed by the EXIT trap it never reaches, or a data job the next data
phase must re-attach to.

The prediction is a reviewed sum of per-step budgets (``STEP_BUDGET_S``),
never a measurement: the first governed run owes the live timings. Spike S7
measured only a trivial App's window steps (start to compute ACTIVE 96-108 s
before the start deployment even begins, stop about 40-45 s), which the real
App's install time exceeds, so no budget was shrunk. They are calibrated to
the envelope the parked workflow held for a whole first install (240
minutes, two one-use Lakebase role bootstraps of a 65-minute captured OAuth
lease each, data included):

* **plan**: the read-only preflight, the bundle plan gate and the window plan.
* **release**: today's run minus the data jobs. Its base depends on the tier
  (the agentic tier adds its provisioning, evaluation and a second
  activation). The window plan's code decides the rest: ``not_applicable``
  (no App, or one with no signed record: a first install, or one resumed
  after the owner bootstrap) adds one role bootstrap per tier role (the App's,
  and the verifier's in the agentic tier); ``window`` adds the window's own
  predicted length and restore (``quiesce_window_plan.budget_body``). The two
  never meet: a window needs signed blue. An existing signed blue (``none``
  or ``window``) also adds step 0a's tier a (``step0a_start``: the window's
  own stop, start and re-pin budgets): a release over a STOPPED blue starts
  and re-pins it before any other mutation, compute ACTIVE alone 96-108 s on
  the trivial App (S7) before the real App's install, and one over an
  earlier failed start a lost runner left active stops it first. The plan
  cannot tell a stopped blue from a serving one, so it is always predicted.
  No dead-man races that start: one is armed only by a window's open, after
  step 0a.
* **data**: the silver refresh (unless ``--skip-silver``), then
  ``mip_scheduled_refresh`` (its own ``timeout_seconds`` bounds it), each
  followed by the reviewed-function grant reconcile and verify, then the
  data smoke.

Each phase must predict at most ``PHASE_LIMIT_SECONDS`` (330 minutes), and
``MIP_JOB_DEADLINE_EPOCH - now`` must cover the prediction plus the 15-minute
margin, decided by the window's own admission rule
(``quiesce_window_budget.admission``) under the phase schema. A run with no
``--phase`` keeps today's behaviour: nothing here gates it.

  python -m tools.databricks.phase_budget describe --phase release ...
  python -m tools.databricks.phase_budget admit --phase data ...

``describe`` (the dry run's and the plan phase's line) never refuses and makes
no call. ``admit`` exits 0 admitted and 2 refused; neither reads anything but
its arguments and the clock.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from tools.databricks.quiesce_window_budget import (
    PHASE_BUDGET_SCHEMA,
    Admission,
    admission,
    parse_deadline,
)
from tools.databricks.quiesce_window_plan import (
    ADMISSION_MARGIN_S,
    DEADLINE_ENV,
    budget_body,
)
from tools.databricks.quiesce_window_plan import STEP_BUDGET_S as WINDOW_STEP_BUDGET_S

PHASES = ("plan", "release", "data")
PHASE_LIMIT_SECONDS = 330 * 60
WINDOW_CODES = frozenset({"none", "window", "not_applicable", "unknown"})
# Reviewed per-step budgets in seconds; not measurements (see the docstring).
STEP_BUDGET_S = {
    "plan": 20 * 60,
    "preflight": 10 * 60,
    "build": 10 * 60,
    "bundle_apply": 15 * 60,
    "grants_and_migration": 15 * 60,
    "publish": 5 * 60,
    "activation": 15 * 60,
    "smoke_and_capture": 10 * 60,
    "agentic_provisioning": 20 * 60,
    "agent_evaluation": 15 * 60,
    # One one-use Lakebase role bootstrap: the 65-minute captured OAuth lease
    # (lakebase_oauth_role_bootstrap_admission) plus skew.
    "role_bootstrap": 70 * 60,
    "mip_refresh_silver": 60 * 60,
    # databricks.yml's timeout_seconds for mip_scheduled_refresh.
    "mip_scheduled_refresh": 90 * 60,
    "grant_reconcile": 5 * 60,
    "data_smoke": 5 * 60,
}
# The data phase's jobs, in order; each touches gold (job_phase_runner).
DATA_JOBS = ("mip_refresh_silver", "mip_scheduled_refresh")


class PhaseBudgetRefused(RuntimeError):
    """The phase does not fit its limit or the job budget; nothing was changed."""


def data_jobs(*, skip_silver: bool) -> tuple[str, ...]:
    return DATA_JOBS[1:] if skip_silver else DATA_JOBS


def _release_components(*, agentic: bool, window_code: str, window: Mapping[str, Any] | None) -> dict[str, int]:
    names = ("preflight", "build", "bundle_apply", "grants_and_migration", "publish", "smoke_and_capture")
    components = {name: STEP_BUDGET_S[name] for name in names}
    # The core tier activates once; the agentic tier again after its evaluation.
    components["activation"] = STEP_BUDGET_S["activation"] * (2 if agentic else 1)
    if agentic:
        components["agentic_provisioning"] = STEP_BUDGET_S["agentic_provisioning"]
        components["agent_evaluation"] = STEP_BUDGET_S["agent_evaluation"]
    if window_code in {"not_applicable", "unknown"}:
        # A first install bootstraps the App's role, and the agentic tier the verifier's.
        components["role_bootstrap"] = STEP_BUDGET_S["role_bootstrap"] * (2 if agentic else 1)
    else:
        # Signed blue: step 0a may start and re-pin a stopped one (tier a, spike S7),
        # stopping first an earlier failed start a lost runner left active.
        components["step0a_start"] = sum(WINDOW_STEP_BUDGET_S[step] for step in ("stop", "start", "repin"))
    if window_code == "window":
        if not isinstance(window, Mapping):
            raise PhaseBudgetRefused("phase_budget_refused: a planned window carries no budget")
        components["window"] = int(window["predicted_window_seconds"]) + int(window["predicted_restore_seconds"])
    return components


def predict(
    phase: str,
    *,
    agentic: bool = True,
    window_code: str = "unknown",
    window_budget: Mapping[str, Any] | None = None,
    skip_silver: bool = False,
) -> dict[str, Any]:
    """The phase's budget body: its components, the predicted sum, the margin and the required sum.

    ``window_code`` is the pre-lease window plan's code; ``unknown`` (a dry
    run, which reads nothing) predicts the first-install worst case.
    """

    if phase not in PHASES:
        raise ValueError(f"unreviewed phase {phase!r}")
    if window_code not in WINDOW_CODES:
        raise ValueError(f"unreviewed window plan code {window_code!r}")
    if phase == "plan":
        components = {"plan": STEP_BUDGET_S["plan"]}
    elif phase == "release":
        components = _release_components(agentic=agentic, window_code=window_code, window=window_budget)
    else:
        components = {}
        for job in data_jobs(skip_silver=skip_silver):
            components[job] = STEP_BUDGET_S[job]
            components[f"{job}:grant_reconcile"] = STEP_BUDGET_S["grant_reconcile"]
        components["data_smoke"] = STEP_BUDGET_S["data_smoke"]
    predicted = sum(components.values())
    return {
        "schema": PHASE_BUDGET_SCHEMA,
        "phase": phase,
        "components": components,
        "predicted_seconds": predicted,
        "margin_seconds": ADMISSION_MARGIN_S,
        "required_seconds": predicted + ADMISSION_MARGIN_S,
        "limit_seconds": PHASE_LIMIT_SECONDS,
    }


def decide(body: Mapping[str, Any], deadline: object, *, now: float) -> Admission:
    """The window's admission rule, applied to a phase body that fits its limit."""

    if int(body["predicted_seconds"]) > PHASE_LIMIT_SECONDS:
        raise PhaseBudgetRefused(
            f"phase_budget_refused: {body['phase']} predicts {int(body['predicted_seconds']) // 60} min, "
            f"over the {PHASE_LIMIT_SECONDS // 60}-minute phase limit"
        )
    return admission(body, deadline, now=now)


def require_admitted(body: Mapping[str, Any], deadline: object, *, now: float) -> Admission:
    decided = decide(body, deadline, now=now)
    if not decided.admitted:
        raise PhaseBudgetRefused(
            f"phase_budget_refused: {body['phase']} {decided.state} (remaining "
            f"{decided.remaining_seconds} s, required {decided.required_seconds} s); "
            f"{DEADLINE_ENV} must leave the phase's prediction and {ADMISSION_MARGIN_S} s"
        )
    return decided


def describe(body: Mapping[str, Any], deadline: object, *, now: float) -> str:
    """One line: the prediction and how the current deadline would decide it; never refuses."""

    predicted_min = int(body["predicted_seconds"]) // 60
    if predicted_min * 60 > PHASE_LIMIT_SECONDS:
        verdict = f"over the {PHASE_LIMIT_SECONDS // 60}-minute limit: it would refuse"
    elif parse_deadline(deadline) is None:
        verdict = f"{DEADLINE_ENV} unset or unparseable: a live phase would refuse"
    else:
        verdict = f"{decide(body, deadline, now=now).state} by {DEADLINE_ENV}"
    return (
        f"phase budget: {body['phase']} predicts {predicted_min} min "
        f"(limit {PHASE_LIMIT_SECONDS // 60}, margin {ADMISSION_MARGIN_S // 60}); {verdict}"
    )


def _window_budget(path: str) -> tuple[str, Mapping[str, Any] | None]:
    """The window plan's code and, for a window, its reviewed budget body (recomputed)."""

    if not path:
        return "unknown", None
    plan = json.loads(Path(path).read_text(encoding="utf-8"))
    code = str(plan.get("code") or "")
    if code not in WINDOW_CODES - {"unknown"}:
        raise PhaseBudgetRefused("phase_budget_refused: the window plan has no reviewed code")
    if code != "window":
        return code, None
    canonical = plan.get("canonical") or {}
    reasons = tuple(str(reason) for reason in canonical.get("reasons") or plan.get("reasons") or ())
    expected = budget_body(reasons, blue_restorable=bool(canonical.get("blue_restorable", True)))
    if dict(canonical.get("budget") or {}) != expected:
        raise PhaseBudgetRefused("phase_budget_refused: the window plan's budget is not its reasons' reviewed budget")
    return code, expected


def _body(args: argparse.Namespace) -> dict[str, Any]:
    code, window = _window_budget(args.window_plan)
    return predict(
        args.phase,
        agentic=args.agentic_tier == "on",
        window_code=code,
        window_budget=window,
        skip_silver=args.skip_silver == "1",
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("describe", "admit"):
        mode = sub.add_parser(name)
        mode.add_argument("--phase", required=True, choices=PHASES)
        mode.add_argument("--agentic-tier", choices=("on", "off"), default="on")
        mode.add_argument("--skip-silver", choices=("0", "1"), default="0")
        mode.add_argument("--window-plan", default="", help="the pre-lease quiesce window plan (release)")
        mode.add_argument("--deadline-epoch", default="")
        mode.add_argument("--out", type=Path, help="write the budget body here (a local file)")
    args = parser.parse_args(argv)
    try:
        body = _body(args)
        line = describe(body, args.deadline_epoch, now=time.time())
        if args.command == "admit":
            require_admitted(body, args.deadline_epoch, now=time.time())
    except PhaseBudgetRefused as exc:
        print(f"[phase-budget] {exc}; nothing was changed", file=sys.stderr)
        return 2
    except (OSError, ValueError, KeyError) as exc:
        print(f"[phase-budget] the phase budget could not be computed: {type(exc).__name__}", file=sys.stderr)
        return 2
    if args.out is not None:
        args.out.write_text(json.dumps(body, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(f"  {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

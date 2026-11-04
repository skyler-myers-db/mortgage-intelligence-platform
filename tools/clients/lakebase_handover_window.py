"""The Lakebase handover's WR5 window half: the remainder, its digest, the in-window pass (M2-PR8).

An apply that exhausted its lock budget leaves a journal whose unfinished
objects are its *remainder*. ``remainder_sha256`` is what the WR5 window plan
pins (tools/databricks/quiesce_window_wr5.py). Neither window mode takes a
caller's word for the window: both read the private WR5 plan ``plan-wr5``
wrote and require it to be a passed, intact plan whose sha256 is the one the
owner acknowledged for this run (``MIP_ACK_QUIESCE_WINDOW``) and whose
remainder digest is this journal's.

* ``window_open`` records that window in the journal before the window
  library stops the App;
* ``in_window`` checks the ack again, that the App is the planned one and is
  STOPPED (read-only), and the plan's PostgreSQL preconditions recomputed,
  then settles any object whose change landed and applies the rest in one
  bounded pass (``WINDOW_BUDGET``, 300 s);
* ``window_close`` ends it.

None of these stops or starts the App.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable, Mapping
from typing import Any

from tools.clients import handover_journal as journals
from tools.clients.lakebase_handover import (
    _PREFIX,
    EXHAUSTED_EXIT,
    KIND,
    SYSTEM_CLOCK,
    Clock,
    ReadSession,
    compute,
    live_owners,
    run_entries,
)
from tools.clients.lakebase_handover_sql import LakebaseHandoverRefused, LockBudget

REMAINDER_DOMAIN = "mip-lakebase-ownership-handover-remainder"
# The WR5 window applies the remainder against a stopped App: one bounded pass.
WINDOW_BUDGET = LockBudget(attempts=3, total_s=300.0, backoff_s=(1.0, 2.0))


def remainder(journal: journals.Journal) -> list[dict[str, Any]]:
    return [entry for entry in journal.entries if entry["state"] in {"pending", "applying"}]


def remainder_sha256(journal: journals.Journal) -> str:
    """What a WR5 window would apply: the journal's plan and its unfinished objects."""

    rows = [{"key": entry["key"], "state": entry["state"]} for entry in remainder(journal)]
    return journals.plan_sha256({"plan_sha256": journal.plan_sha256, "remainder": rows}, domain=REMAINDER_DOMAIN)


def verify_window(plan: Mapping[str, Any], *, window_sha256: str, acknowledgement: str, handover_sha256: str) -> Mapping[str, Any]:
    """The plan's canonical body when it is the passed, intact, acknowledged WR5 plan for this remainder."""

    from tools.databricks.quiesce_window_plan import ACK_ENV, body_sha256

    public = plan.get("public")
    canonical = plan.get("canonical")
    if not isinstance(public, Mapping) or not isinstance(canonical, Mapping):
        raise LakebaseHandoverRefused("the WR5 plan is not a window plan")
    digest = body_sha256(canonical)
    if public.get("code") != "window" or public.get("window_sha256") != digest:
        raise LakebaseHandoverRefused("the WR5 plan is not a passed, intact window plan")
    if not (digest == window_sha256 == acknowledgement):
        raise LakebaseHandoverRefused(f"the WR5 window is not the one acknowledged for this run ({ACK_ENV})")
    if canonical.get("handover_sha256") != handover_sha256:
        raise LakebaseHandoverRefused("the WR5 plan pins another remainder than this journal's")
    return canonical


def window_open(
    journal: journals.Journal,
    *,
    window_sha256: str,
    handover_sha256: str,
    plan: Mapping[str, Any],
    acknowledgement: str,
) -> None:
    """Record the planned, acknowledged WR5 window before the window library stops the App."""

    if journal.status != "exhausted" or remainder_sha256(journal) != handover_sha256:
        raise LakebaseHandoverRefused("the journal is not the exhausted handover the window was planned for")
    verify_window(plan, window_sha256=window_sha256, acknowledgement=acknowledgement, handover_sha256=handover_sha256)
    journal.set_status("window_open", window_sha256=window_sha256, window_handover_sha256=handover_sha256)


def _state(app: object) -> str:
    raw = getattr(getattr(app, "compute_status", None), "state", None)
    return str(getattr(raw, "value", raw) or "").split(".")[-1].strip().upper()


def in_window(
    read_session: ReadSession,
    connect: Callable[[], Any],
    journal: journals.Journal,
    *,
    window_sha256: str,
    plan: Mapping[str, Any],
    acknowledgement: str,
    app: object,
    clock: Clock = SYSTEM_CLOCK,
) -> bool:
    """Inside the open WR5 window: re-check it, settle landed objects, apply the rest in one bounded pass."""

    if journal.status != "window_open" or journal.document.get("window_sha256") != window_sha256:
        raise LakebaseHandoverRefused("no WR5 window is open for this journal")
    handover_sha256 = str(journal.document.get("window_handover_sha256") or "")
    if remainder_sha256(journal) != handover_sha256:
        raise LakebaseHandoverRefused("the journal changed after the WR5 window opened")
    canonical = verify_window(plan, window_sha256=window_sha256, acknowledgement=acknowledgement, handover_sha256=handover_sha256)
    if str(getattr(app, "id", "") or "").strip() != canonical.get("app_id") or _state(app) != "STOPPED":
        raise LakebaseHandoverRefused("the planned App is not stopped: the window applies nothing while it serves")
    targets = {entry["new_owner"] for entry in journal.entries}
    if len(targets) != 1:
        raise LakebaseHandoverRefused("the journal names more than one target role")
    with read_session() as cursor:
        preconditions = compute(cursor, target=targets.pop())
        owners = live_owners(cursor)
    if preconditions.refusals:
        raise LakebaseHandoverRefused(f"the recomputed preconditions refuse ({len(preconditions.refusals)}); nothing was changed")
    pending = []
    for entry in remainder(journal):
        owner = owners.get(entry["key"])
        if owner == entry["new_owner"]:
            journal.mark(entry["key"], "applied", settled="live")
        elif owner == entry["prior_owner"]:
            if entry["state"] == "applying":
                journal.mark(entry["key"], "pending", settled="live")
            pending.append(journal.entry(entry["key"]))
        else:
            raise LakebaseHandoverRefused("an object changed owner outside the handover")
    connection = connect()
    try:
        finished = run_entries(connection, journal, pending, budget=WINDOW_BUDGET, clock=clock)
    finally:
        connection.close()
    journal.set_status("window_applied" if finished else "window_open")
    return finished


def window_close(journal: journals.Journal) -> None:
    if journal.status not in {"window_open", "window_applied"}:
        raise LakebaseHandoverRefused("no WR5 window is open for this journal")
    finished = not remainder(journal)
    journal.set_status("applied" if finished else "window_closed")


def run_window_mode(args: Any, current: Any, *, clock: Clock) -> int:
    """The CLI's ``--remainder``, ``--window-open``, ``--in-window`` and ``--window-close``."""

    from tools.databricks.quiesce_window_plan import ACK_ENV
    from tools.databricks.quiesce_window_wr5 import load_plan

    journal = journals.load(args.journal, kind=KIND)
    if args.remainder:
        print(f"{_PREFIX} remainder: {len(remainder(journal))} object(s), handover sha256 {remainder_sha256(journal)}")
        return 0
    if args.window_close:
        window_close(journal)
        print(f"{_PREFIX} window-close: {journal.status}")
        return 0
    plan = load_plan(args.window_plan)
    acknowledgement = os.environ.get(ACK_ENV, "").strip()
    if args.window_open:
        window_open(journal, window_sha256=args.window_sha256, handover_sha256=args.handover_sha256, plan=plan, acknowledgement=acknowledgement)
        print(f"{_PREFIX} window-open: journaled; the window library stops the App next")
        return 0
    app = current.workspace().apps.get(current.app_name)
    finished = in_window(
        current.read_session,
        current.connect,
        journal,
        window_sha256=args.window_sha256,
        plan=plan,
        acknowledgement=acknowledgement,
        app=app,
        clock=clock,
    )
    print(f"{_PREFIX} in-window: {json.dumps(journal.counts(), sort_keys=True)}")
    if not finished:
        print(f"{_PREFIX} the window's lock budget ran out; the window library starts the App, then --revert", file=sys.stderr)
    return 0 if finished else EXHAUSTED_EXIT


__all__ = [
    "REMAINDER_DOMAIN",
    "WINDOW_BUDGET",
    "in_window",
    "remainder",
    "remainder_sha256",
    "run_window_mode",
    "verify_window",
    "window_close",
    "window_open",
]

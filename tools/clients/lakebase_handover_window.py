"""The Lakebase handover's WR5 window half: the remainder, its digest, the in-window pass (M2-PR8).

An apply that exhausted its lock budget leaves a journal whose unfinished
objects are its *remainder*. ``remainder_sha256`` is what the WR5 window plan
pins (tools/databricks/quiesce_window_wr5.py); ``window_open`` records the
acknowledged window in the journal before the window library stops the App;
``in_window`` settles any object whose change landed, then applies the rest
in one bounded pass (``WINDOW_BUDGET``, 300 s); ``window_close`` ends it. None
of these stops or starts the App.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from tools.clients import handover_journal as journals
from tools.clients.lakebase_handover import (
    SYSTEM_CLOCK,
    Clock,
    ReadSession,
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


def window_open(journal: journals.Journal, *, window_sha256: str, handover_sha256: str) -> None:
    """Record the acknowledged WR5 window before the window library stops the App."""

    if journal.status != "exhausted" or remainder_sha256(journal) != handover_sha256:
        raise LakebaseHandoverRefused("the journal is not the exhausted handover the window was planned for")
    journal.set_status("window_open", window_sha256=window_sha256, window_handover_sha256=handover_sha256)


def in_window(
    read_session: ReadSession,
    connect: Callable[[], Any],
    journal: journals.Journal,
    *,
    window_sha256: str,
    clock: Clock = SYSTEM_CLOCK,
) -> bool:
    """Inside the open WR5 window: settle landed objects, apply the rest in one bounded pass."""

    if journal.status != "window_open" or journal.document.get("window_sha256") != window_sha256:
        raise LakebaseHandoverRefused("no WR5 window is open for this journal")
    with read_session() as cursor:
        owners = live_owners(cursor)
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


__all__ = ["REMAINDER_DOMAIN", "WINDOW_BUDGET", "in_window", "remainder", "remainder_sha256", "window_close", "window_open"]

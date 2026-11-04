"""Spike S3's rehearsal of the Lakebase handover on a throwaway instance (M2-PR8).

Owner-run only, and never against the install's own instance: the run
refuses when ``--rehearse-instance`` is the profile's ``MIP_LAKEBASE_INSTANCE``.
On the throwaway it creates one table it owns (``mip_handover_rehearsal``;
the owner deletes the instance afterwards, nothing here drops anything) and
then measures exactly what the real apply relies on:

1. a **concurrent long-running transaction** holds ACCESS SHARE on that table
   for ``--hold-seconds`` (longer than ``lock_timeout``), the way a long App
   query would;
2. a probe session keeps querying the table and records how long each query
   waited;
3. the handover's own lock-bounded change (``run_bounded``, the production
   ``lock_timeout='3s'``, ``statement_timeout='30s'`` and retry budget) moves
   the table to the target role, then the exact inverse moves it back.

It passes when the change needed a retry (the timeout path ran), succeeded
once the long transaction ended, the inverse restored the prior owner, and no
probe query waited longer than ``lock_timeout`` plus a scheduling margin. It
first reads the same PostgreSQL preconditions the plan does (``AUTHORITY_SQL``
on the rehearsal schema): a missing one fails the rehearsal and names the gap
the chosen mechanism must close, before any change. The
L-B mechanism (``delete_database_instance_role`` with ``reassign_owned_to``)
deletes a role, which no tool in this PR does; it is not rehearsed here.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any, Protocol

from tools.clients import handover_journal as journals
from tools.clients.lakebase_handover_sql import (
    AUTHORITY_SQL,
    DEFAULT_BUDGET,
    LOCK_TIMEOUT,
    LockBudget,
    quote_ident,
    run_bounded,
)

REHEARSAL_SCHEMA = "mip_handover_rehearsal"
REHEARSAL_TABLE = "rehearsal_target"
QUALIFIED = f"{quote_ident(REHEARSAL_SCHEMA)}.{quote_ident(REHEARSAL_TABLE)}"
SETUP = (
    f"CREATE SCHEMA IF NOT EXISTS {quote_ident(REHEARSAL_SCHEMA)}",
    f"CREATE TABLE IF NOT EXISTS {QUALIFIED} (id integer PRIMARY KEY)",
)
OWNER_SQL = (
    "SELECT pg_get_userbyid(relowner)::text FROM pg_class "
    f"WHERE oid = to_regclass('{REHEARSAL_SCHEMA}.{REHEARSAL_TABLE}')"
)
LOCK_TIMEOUT_S = float(LOCK_TIMEOUT.removesuffix("s"))
SCHEDULING_MARGIN_S = 1.0
_PREFIX = "[mip-lakebase-rehearsal]"


class Concurrent(Protocol):
    """The long transaction and the probe that run beside the handover's change."""

    def start(self) -> None: ...

    def join(self) -> list[float]: ...


@dataclass(frozen=True)
class RehearsalReport:
    lock_timeout_s: float
    hold_s: float
    change_attempts: int
    revert_attempts: int
    owner_after_change: str
    owner_after_revert: str
    max_probe_wait_s: float
    passed: bool
    missing_preconditions: tuple[str, ...] = ()


class ThreadedConcurrent:
    """Real sessions: one holds ACCESS SHARE for ``hold_s``, one probes the table."""

    def __init__(self, connect: Callable[[], Any], hold_s: float, interval_s: float = 0.25) -> None:
        self._connect = connect
        self._hold_s = hold_s
        self._interval_s = interval_s
        self._held = threading.Event()
        self._stop = threading.Event()
        self._waits: list[float] = []
        self._threads = [threading.Thread(target=self._hold), threading.Thread(target=self._probe)]

    def _hold(self) -> None:
        connection = self._connect()
        try:
            with connection.transaction(), connection.cursor() as cursor:
                cursor.execute(f"LOCK TABLE {QUALIFIED} IN ACCESS SHARE MODE")
                self._held.set()
                time.sleep(self._hold_s)
        finally:
            connection.close()

    def _probe(self) -> None:
        self._held.wait(timeout=30)
        connection = self._connect()
        try:
            while not self._stop.is_set():
                started = time.monotonic()
                with connection.transaction(), connection.cursor() as cursor:
                    cursor.execute(f"SELECT count(*) FROM {QUALIFIED}")
                    cursor.fetchone()
                self._waits.append(time.monotonic() - started)
                time.sleep(self._interval_s)
        finally:
            connection.close()

    def start(self) -> None:
        for thread in self._threads:
            thread.start()
        self._held.wait(timeout=30)

    def join(self) -> list[float]:
        self._threads[0].join()
        self._stop.set()
        self._threads[1].join()
        return list(self._waits)


def _owner(connection: Any) -> str:
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(OWNER_SQL)
        row = cursor.fetchone()
    return str(row[0]) if row else ""


def rehearse(
    connect: Callable[[], Any],
    *,
    legacy: str,
    target: str,
    hold_s: float,
    concurrent: Callable[[Callable[[], Any], float], Concurrent] = ThreadedConcurrent,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    budget: LockBudget = DEFAULT_BUDGET,
) -> RehearsalReport:
    if hold_s <= LOCK_TIMEOUT_S:
        raise ValueError("the long transaction must outlast lock_timeout to rehearse the retry")
    connection = connect()
    try:
        with connection.transaction(), connection.cursor() as cursor:
            for statement in SETUP:
                cursor.execute(statement)
            cursor.execute(AUTHORITY_SQL, {"target": target, "schema": REHEARSAL_SCHEMA})
            row = cursor.fetchone() or ("", False, False, False)
        missing = tuple(
            code
            for code, held in (("target_role_not_settable", row[2]), ("target_needs_schema_create", row[3]))
            if not held
        )
        if missing:
            owner = _owner(connection)
            return RehearsalReport(LOCK_TIMEOUT_S, hold_s, 0, 0, owner, owner, 0.0, False, missing)
        sessions = concurrent(connect, hold_s)
        sessions.start()
        deadline = clock() + budget.total_s
        change = f"ALTER TABLE {QUALIFIED} OWNER TO {quote_ident(target)}"
        change_attempts = run_bounded(connection, change, budget=budget, deadline=deadline, clock=clock, sleep=sleep)
        waits = sessions.join()
        after_change = _owner(connection)
        inverse = f"ALTER TABLE {QUALIFIED} OWNER TO {quote_ident(legacy)}"
        revert_attempts = run_bounded(connection, inverse, budget=budget, deadline=clock() + budget.total_s, clock=clock, sleep=sleep)
        after_revert = _owner(connection)
    finally:
        connection.close()
    worst = max(waits, default=0.0)
    passed = (
        change_attempts >= 2
        and after_change == target
        and after_revert == legacy
        and worst <= LOCK_TIMEOUT_S + SCHEDULING_MARGIN_S
    )
    return RehearsalReport(LOCK_TIMEOUT_S, hold_s, change_attempts, revert_attempts, after_change, after_revert, worst, passed)


def run_rehearsal(args: argparse.Namespace, *, session: Callable[..., Any], clock: Any) -> int:
    """The CLI's ``--rehearse``: refuse the install's own instance, then rehearse and report."""

    if not args.rehearse_instance or not args.rehearse_database:
        print(f"{_PREFIX} REFUSED: --rehearse needs --rehearse-instance and --rehearse-database (a throwaway)", file=sys.stderr)
        return 2
    current = session(args.rehearse_instance, args.rehearse_database)
    if args.rehearse_instance == current.live_instance:
        print(f"{_PREFIX} REFUSED rehearse_live_instance: the rehearsal never runs on the install's instance", file=sys.stderr)
        return 2
    workspace = current.workspace()
    legacy = str(getattr(workspace.current_user.me(), "user_name", "") or "")
    report = rehearse(current.connect, legacy=legacy, target=current.target, hold_s=8.0, clock=clock.now, sleep=clock.sleep)
    summary = {key: value for key, value in asdict(report).items() if not key.startswith("owner_")}
    if args.report_out is not None:
        journals.write_private(args.report_out, asdict(report))
    print(f"{_PREFIX} {'PASS' if report.passed else 'FAIL'}: {json.dumps(summary, sort_keys=True)}")
    return 0 if report.passed else 2


__all__ = ["REHEARSAL_SCHEMA", "RehearsalReport", "ThreadedConcurrent", "rehearse", "run_rehearsal"]

"""Spike S3's rehearsal of the Lakebase handover on a throwaway instance (M2-PR8).

Owner-run only, and never against the install's own instance: the run
refuses when ``--rehearse-instance`` is the profile's ``MIP_LAKEBASE_INSTANCE``,
when a ``LAKEBASE_HOST``/``USER``/``PASSWORD``/``PORT`` override would point the
connection elsewhere (``lakebase_handover.CONNECTION_OVERRIDES``), and, read
before anything is created, when the database already holds a ``mip_app``
schema (an install's database, not a throwaway).
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
once the long transaction ended, the inverse restored the prior owner, at
least one probe query arrived while the change waited, and no probe query
waited longer than ``lock_timeout`` plus a scheduling margin; a session that
dies voids the run (``RehearsalSessionFailed``) instead of passing it. It
first reads the same PostgreSQL preconditions the plan does (``AUTHORITY_SQL``
on the rehearsal schema): a missing one fails the rehearsal and names the gap
the chosen mechanism must close, before any change. The rehearsal creates and
owns its schema, so the target's CREATE there (``target_needs_schema_create``)
is the rehearsal's own to give: when the target lacks it, the rehearsal grants
it after creating the schema and revokes exactly that grant at the end, also
after a failure, and a pass needs the revoke to have landed (spike S3, answer
6: the first run otherwise refused until the owner granted it by hand). When a
failure's take-back fails too, it says so on stderr with the REVOKE to run,
because a later rehearsal that finds CREATE held leaves it alone. The
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
from dataclasses import asdict, dataclass, replace
from typing import Any, Protocol

from tools.clients import handover_journal as journals
from tools.clients.lakebase_handover_acl import grant_statement, revoke_statement
from tools.clients.lakebase_handover_sql import (
    AUTHORITY_SQL,
    DEFAULT_BUDGET,
    LOCK_TIMEOUT,
    SCHEMA_NAME,
    LakebaseHandoverRefused,
    LockBudget,
    PgObject,
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
# Read before anything is created: a database holding mip_app is an install's.
INSTALL_SCHEMA_SQL = "SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = %(schema)s)"
OWNER_SQL = (
    "SELECT pg_get_userbyid(relowner)::text FROM pg_class "
    f"WHERE oid = to_regclass('{REHEARSAL_SCHEMA}.{REHEARSAL_TABLE}')"
)
# The rehearsal schema's CREATE, which the rehearsal grants the target and takes back.
_SCHEMA_OBJECT = PgObject("SCHEMA", "", REHEARSAL_SCHEMA, "", "")
# Printed in place of the deployer's role name, which stays out of stderr.
DEPLOYER_PLACEHOLDER = "<the deployer's role>"
LOCK_TIMEOUT_S = float(LOCK_TIMEOUT.removesuffix("s"))
SCHEDULING_MARGIN_S = 1.0
_PREFIX = "[mip-lakebase-rehearsal]"


class Concurrent(Protocol):
    """The long transaction and the probe that run beside the handover's change.

    ``join`` returns each probe query's ``(started, ended)`` on the rehearsal's
    clock, so the rehearsal can tell which queries arrived while the change
    waited; it raises when a session died, because then nothing was measured.
    """

    def start(self) -> None: ...

    def join(self) -> list[tuple[float, float]]: ...


class RehearsalSessionFailed(LakebaseHandoverRefused):
    """A rehearsal session died, so the measurement is void (the class only, never a driver message)."""


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
    probe_samples: int = 0
    # Probe queries that overlapped the change's lock waits: the evidence itself.
    probe_samples_during_change: int = 0
    # The rehearsal granted the target CREATE on its own schema; and took it back.
    granted_schema_create: bool = False
    schema_create_revoked: bool = False


class ThreadedConcurrent:
    """Real sessions: one holds ACCESS SHARE for ``hold_s``, one probes the table.

    A session that raises is recorded and stops the other; ``start`` raises
    when the hold never took its lock, ``join`` when any session died.
    """

    def __init__(
        self,
        connect: Callable[[], Any],
        hold_s: float,
        interval_s: float = 0.25,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._connect = connect
        self._hold_s = hold_s
        self._interval_s = interval_s
        self._clock = clock
        self._held = threading.Event()
        self._ready = threading.Event()  # the lock is held, or the hold ended
        self._stop = threading.Event()
        self._samples: list[tuple[float, float]] = []
        self._errors: list[str] = []
        self._threads = [
            threading.Thread(target=self._guard, args=("hold", self._hold), daemon=True),
            threading.Thread(target=self._guard, args=("probe", self._probe), daemon=True),
        ]

    def _guard(self, role: str, body: Callable[[], None]) -> None:
        try:
            body()
        except BaseException as exc:  # noqa: BLE001 - recorded by class, re-raised by start/join
            self._errors.append(f"{role}: {type(exc).__name__}")
            self._stop.set()
        finally:
            if role == "hold":
                self._ready.set()

    def _hold(self) -> None:
        connection = self._connect()
        try:
            with connection.transaction(), connection.cursor() as cursor:
                cursor.execute(f"LOCK TABLE {QUALIFIED} IN ACCESS SHARE MODE")
                self._held.set()
                self._ready.set()
                time.sleep(self._hold_s)
        finally:
            connection.close()

    def _probe(self) -> None:
        self._ready.wait(timeout=30)
        if not self._held.is_set():
            return
        connection = self._connect()
        try:
            while not self._stop.is_set():
                started = self._clock()
                with connection.transaction(), connection.cursor() as cursor:
                    cursor.execute(f"SELECT count(*) FROM {QUALIFIED}")
                    cursor.fetchone()
                self._samples.append((started, self._clock()))
                time.sleep(self._interval_s)
        finally:
            connection.close()

    def _failed(self) -> RehearsalSessionFailed:
        return RehearsalSessionFailed(f"a rehearsal session died ({', '.join(self._errors)}); nothing was measured")

    def start(self) -> None:
        for thread in self._threads:
            thread.start()
        self._ready.wait(timeout=30)
        if self._errors or not self._held.is_set():
            self._stop.set()
            for thread in self._threads:
                thread.join()
            if not self._errors:
                self._errors.append("hold: lock not taken within 30 s")
            raise self._failed()

    def join(self) -> list[tuple[float, float]]:
        self._threads[0].join()
        self._stop.set()
        self._threads[1].join()
        if self._errors:
            raise self._failed()
        return list(self._samples)


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
    concurrent: Callable[[Callable[[], Any], float], Concurrent] | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    budget: LockBudget = DEFAULT_BUDGET,
) -> RehearsalReport:
    if hold_s <= LOCK_TIMEOUT_S:
        raise ValueError("the long transaction must outlast lock_timeout to rehearse the retry")
    connection = connect()
    granted: list[str] = []
    try:
        report = _rehearse_on(
            connection, connect, granted, legacy=legacy, target=target, hold_s=hold_s, concurrent=concurrent, clock=clock, sleep=sleep, budget=budget
        )
    except BaseException as failure:
        if not _take_back(connection, granted, target, quiet=True):
            # The grant may remain, and a later rehearsal, finding CREATE held, neither grants nor revokes it.
            note = (
                f"{_PREFIX} WARNING: the rehearsal could not take back the CREATE it granted the deployer on its "
                f"schema; on the throwaway, as the legacy owner, run: "
                f"{revoke_statement(_SCHEMA_OBJECT, ('CREATE',), DEPLOYER_PLACEHOLDER)};"
            )
            print(note, file=sys.stderr)
            failure.add_note(note)
        connection.close()
        raise
    revoked = _take_back(connection, granted, target, quiet=False)
    connection.close()
    if not granted:
        return report
    return replace(report, granted_schema_create=True, schema_create_revoked=revoked, passed=report.passed and revoked)


def _take_back(connection: Any, granted: list[str], target: str, *, quiet: bool) -> bool:
    """Revoke the rehearsal's own CREATE grant; True once the target no longer holds it."""

    if not granted:
        return True
    try:
        with connection.transaction(), connection.cursor() as cursor:
            cursor.execute(revoke_statement(_SCHEMA_OBJECT, ("CREATE",), target))
            cursor.execute(AUTHORITY_SQL, {"target": target, "schema": REHEARSAL_SCHEMA})
            row = cursor.fetchone()
        return row is not None and not row[3]
    except Exception:
        if quiet:
            return False  # the failure in flight is the one to report
        raise


def _rehearse_on(
    connection: Any,
    connect: Callable[[], Any],
    granted: list[str],
    *,
    legacy: str,
    target: str,
    hold_s: float,
    concurrent: Callable[[Callable[[], Any], float], Concurrent] | None,
    clock: Callable[[], float],
    sleep: Callable[[float], None],
    budget: LockBudget,
) -> RehearsalReport:
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(INSTALL_SCHEMA_SQL, {"schema": SCHEMA_NAME})
        holds_install = cursor.fetchone()
        if holds_install is None or holds_install[0]:
            raise LakebaseHandoverRefused("the rehearsal database holds mip_app: an install's database, not a throwaway")
        for statement in SETUP:
            cursor.execute(statement)
        cursor.execute(AUTHORITY_SQL, {"target": target, "schema": REHEARSAL_SCHEMA})
        row = cursor.fetchone() or ("", False, False, False, False, False, False)
        if row[1] and not row[3]:
            # The rehearsal owns its schema: CREATE there is its own to give (S3, answer 6).
            cursor.execute(grant_statement(_SCHEMA_OBJECT, ("CREATE",), target))
            granted.append(target)
            cursor.execute(AUTHORITY_SQL, {"target": target, "schema": REHEARSAL_SCHEMA})
            row = cursor.fetchone() or row
    missing = tuple(
        code
        for code, held in (
            ("target_role_not_settable", row[2]),
            ("target_needs_schema_create", row[3]),
            ("caller_cannot_revert", row[6]),
        )
        if not held
    )
    if missing:
        owner = _owner(connection)
        return RehearsalReport(LOCK_TIMEOUT_S, hold_s, 0, 0, owner, owner, 0.0, False, missing)
    factory = concurrent or (lambda opened, held: ThreadedConcurrent(opened, held, clock=clock))
    sessions = factory(connect, hold_s)
    sessions.start()
    try:
        change_started = clock()
        deadline = change_started + budget.total_s
        change = f"ALTER TABLE {QUALIFIED} OWNER TO {quote_ident(target)}"
        change_attempts = run_bounded(connection, change, budget=budget, deadline=deadline, clock=clock, sleep=sleep)
        change_ended = clock()
    finally:
        # Always ends both sessions; a session that died raises here.
        samples = sessions.join()
    after_change = _owner(connection)
    inverse = f"ALTER TABLE {QUALIFIED} OWNER TO {quote_ident(legacy)}"
    revert_attempts = run_bounded(connection, inverse, budget=budget, deadline=clock() + budget.total_s, clock=clock, sleep=sleep)
    after_revert = _owner(connection)
    worst = max((ended - started for started, ended in samples), default=0.0)
    # Only a probe query that arrived while the change waited measures what an
    # App query suffers; none (a probe that recorded nothing) is no evidence.
    during = sum(1 for started, ended in samples if started < change_ended and ended > change_started)
    passed = (
        change_attempts >= 2
        and after_change == target
        and after_revert == legacy
        and during >= 1
        and worst <= LOCK_TIMEOUT_S + SCHEDULING_MARGIN_S
    )
    return RehearsalReport(
        LOCK_TIMEOUT_S,
        hold_s,
        change_attempts,
        revert_attempts,
        after_change,
        after_revert,
        worst,
        passed,
        probe_samples=len(samples),
        probe_samples_during_change=during,
    )


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


__all__ = ["INSTALL_SCHEMA_SQL", "REHEARSAL_SCHEMA", "RehearsalReport", "RehearsalSessionFailed", "ThreadedConcurrent", "rehearse", "run_rehearsal"]

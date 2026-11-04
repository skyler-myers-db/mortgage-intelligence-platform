"""A fake psycopg server for the Lakebase handover, on a virtual clock.

``FakeServer`` holds the ``mip_app`` objects and their owners, the roles, and
any long App transaction that holds a lock on a table until ``held_until``.
An ``ALTER ... OWNER TO`` on a held table waits like PostgreSQL does: until
the holder ends, or until the transaction's ``lock_timeout``, whichever is
first, and then raises ``LockNotAvailable`` (SQLSTATE 55P03). Without a
``SET LOCAL lock_timeout`` it waits as long as the holder runs. Every wait is
recorded in ``exclusive_waits``: an App query arriving during one queues
behind it, so the longest wait is the longest any App query was blocked.

Time is virtual (``clock``/``sleep``), owner changes land only on commit, and
every executed statement is recorded. Reads go through the migration's
read-only guard, as ``read_only_session`` does.
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from jobs.lakebase_migration_read_only import assert_read_statement
from tools.clients.lakebase_handover_rehearse import (
    INSTALL_SCHEMA_SQL,
    OWNER_SQL,
    REHEARSAL_SCHEMA,
    SETUP,
)
from tools.clients.lakebase_handover_sql import AUTHORITY_SQL, INVENTORY_SQL, PgObject

LEGACY = "legacy-owner@example.com"
DEPLOYER = "00000000-0000-4000-8000-000000000001"
APP_ROLE = "00000000-0000-4000-8000-000000000099"
_ALTER = re.compile(r'(?P<head>ALTER [A-Z ]+? .+) OWNER TO "(?P<owner>(?:[^"]|"")+)"')
_BOUND = re.compile(r"SET LOCAL (?P<name>lock_timeout|statement_timeout) = '(?P<seconds>[0-9]+)s'")


REHEARSAL_KEY = f"TABLE:{REHEARSAL_SCHEMA}.rehearsal_target"


class LockNotAvailable(Exception):
    sqlstate = "55P03"


class SyntaxProblem(Exception):
    sqlstate = "42601"


def throwaway() -> FakeServer:
    """A rehearsal's throwaway database: no mip_app, so only the rehearsal's own table appears."""

    return FakeServer(objects=[])


def objects() -> list[PgObject]:
    """The App database and a small mip_app: tables, a view, a function, a type, one foreign-owned."""

    return [
        PgObject("DATABASE", "", "mip_app_state", "", LEGACY),
        PgObject("SCHEMA", "", "mip_app", "", LEGACY),
        PgObject("TABLE", "mip_app", "campaigns", "", LEGACY),
        PgObject("TABLE", "mip_app", "approvals", "", LEGACY),
        PgObject("TABLE", "mip_app", "action_audit", "", LEGACY),
        PgObject("VIEW", "mip_app", "campaign_summary", "", LEGACY),
        PgObject("SEQUENCE", "mip_app", "standalone_seq", "", LEGACY),
        PgObject("FUNCTION", "mip_app", "audit_guard", "", LEGACY),
        PgObject("FUNCTION", "mip_app", "touch_row", "row_id bigint, reason text", LEGACY),
        PgObject("TYPE", "mip_app", "approval_state", "", LEGACY),
        PgObject("TABLE", "mip_app", "app_owned_scratch", "", APP_ROLE),
    ]


_TAGS = {
    "TABLE": "relkind:r",
    "VIEW": "relkind:v",
    "MATERIALIZED VIEW": "relkind:m",
    "SEQUENCE": "relkind:S",
    "FOREIGN TABLE": "relkind:f",
    "FUNCTION": "prokind:f",
    "PROCEDURE": "prokind:p",
    "TYPE": "typtype:e",
    "DOMAIN": "typtype:d",
}


@dataclass
class FakeServer:
    caller: str = LEGACY
    roles: set[str] = field(default_factory=lambda: {LEGACY, DEPLOYER, APP_ROLE})
    settable: bool = True
    # The other PostgreSQL ALTER OWNER preconditions (AUTHORITY_SQL), all held by default.
    authority: dict[str, bool] = field(default_factory=lambda: {"schema_create": True, "database_create": True, "createdb": True})
    objects: list[PgObject] = field(default_factory=objects)
    owners: dict[str, str] = field(default_factory=dict)
    held_until: dict[str, float] = field(default_factory=dict)  # object key -> release time
    now: float = 0.0
    statements: list[str] = field(default_factory=list)
    transactions: list[list[str]] = field(default_factory=list)
    exclusive_waits: list[tuple[float, float]] = field(default_factory=list)
    connections: int = 0
    fail_on: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        self.owners = {item.key: item.owner for item in self.objects} | self.owners

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds

    def hold(self, key: str, seconds: float) -> None:
        """A long App transaction holds a conflicting lock on ``key`` for ``seconds``."""

        self.held_until[key] = self.now + seconds

    def max_app_block(self) -> float:
        return max((end - start for start, end in self.exclusive_waits), default=0.0)

    def connect(self) -> FakeConnection:
        self.connections += 1
        return FakeConnection(self)

    @contextmanager
    def read_session(self) -> Any:
        yield FakeCursor(self, None, read_only=True)

    def object_for(self, head: str) -> PgObject:
        for item in self.objects:
            if f"ALTER {item.kind} {item.target()}" == head:
                return item
        raise SyntaxProblem("no such object")


class FakeConnection:
    def __init__(self, server: FakeServer) -> None:
        self.server = server
        self._txn: dict[str, Any] | None = None
        self.closed = False

    @contextmanager
    def transaction(self) -> Any:
        self._txn = {"statements": [], "bounds": {}, "pending": {}}
        try:
            yield self
        except BaseException:
            self.server.transactions.append(self._txn["statements"] + ["ROLLBACK"])
            self._txn = None
            raise
        self.server.owners.update(self._txn["pending"])
        self.server.transactions.append(self._txn["statements"] + ["COMMIT"])
        self._txn = None

    @contextmanager
    def cursor(self) -> Any:
        yield FakeCursor(self.server, self._txn)

    def close(self) -> None:
        self.closed = True


class FakeCursor:
    def __init__(self, server: FakeServer, txn: dict[str, Any] | None, *, read_only: bool = False) -> None:
        self.server = server
        self.txn = txn
        self.read_only = read_only
        self._rows: list[tuple[Any, ...]] = []

    def execute(self, statement: str, params: Any = None) -> None:
        self.server.statements.append(statement)
        if self.read_only:
            assert_read_statement(statement)
        if statement == INVENTORY_SQL:
            self._rows = [
                (_TAGS.get(item.kind, item.kind), item.schema, item.name, item.signature, self.server.owners[item.key])
                for item in self.server.objects
            ]
            return
        if statement == AUTHORITY_SQL:
            target = params["target"]
            exists = target in self.server.roles
            grants = self.server.authority
            self._rows = [
                (self.server.caller, exists, exists and self.server.settable, exists and grants["schema_create"], grants["database_create"], grants["createdb"])
            ]
            return
        if statement == INSTALL_SCHEMA_SQL:
            schema = params["schema"]
            self._rows = [(any(item.schema == schema or (item.kind == "SCHEMA" and item.name == schema) for item in self.server.objects),)]
            return
        if statement == OWNER_SQL:
            self._rows = [(self.server.owners[REHEARSAL_KEY],)]
            return
        assert self.txn is not None, "a write outside a transaction"
        self.txn["statements"].append(statement)
        if statement in SETUP:
            if statement == SETUP[-1] and REHEARSAL_KEY not in self.server.owners:
                rehearsal = PgObject("TABLE", REHEARSAL_SCHEMA, "rehearsal_target", "", self.server.caller)
                self.server.objects.append(rehearsal)
                self.server.owners[rehearsal.key] = rehearsal.owner
            return
        bound = _BOUND.fullmatch(statement)
        if bound:
            self.txn["bounds"][bound.group("name")] = float(bound.group("seconds"))
            return
        match = _ALTER.fullmatch(statement)
        if match is None:
            raise SyntaxProblem("the fake accepts only lock bounds and owner changes")
        item = self.server.object_for(match.group("head"))
        if item.key in self.server.fail_on:
            raise SyntaxProblem("injected failure")
        release = self.server.held_until.get(item.key, 0.0)
        if release > self.server.now:
            limit = self.txn["bounds"].get("lock_timeout")
            wait = release - self.server.now if limit is None else min(limit, release - self.server.now)
            self.server.exclusive_waits.append((self.server.now, self.server.now + wait))
            self.server.now += wait
            if self.server.now < release:
                raise LockNotAvailable("canceling statement due to lock timeout")
        self.txn["pending"][item.key] = match.group("owner").replace('""', '"')

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self._rows)

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._rows[0] if self._rows else None


class VirtualConcurrent:
    """The rehearsal's long transaction and probe, on the server's virtual clock."""

    def __init__(self, server: FakeServer, hold_s: float) -> None:
        self.server = server
        self.hold_s = hold_s

    def start(self) -> None:
        self.server.hold(REHEARSAL_KEY, self.hold_s)

    def join(self) -> list[float]:
        return [end - start for start, end in self.server.exclusive_waits]

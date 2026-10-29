"""Server-enforced read-only Lakebase session with a client-side statement guard.

``--mode plan`` and ``--mode verify`` run every query through this session.
Two independent layers keep them from mutating the database: psycopg starts
each transaction ``READ ONLY`` (PostgreSQL then rejects DDL and DML), and the
cursor proxy refuses any statement that is not a single read before the
driver sees it. The session always rolls back.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from jobs.lakebase_migration_contracts import _SQL_STRING_LITERAL_RE


class ReadOnlyStatementRefused(RuntimeError):
    """A plan or verify code path tried to send a statement that is not a read."""


_COMMENT_RE = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)
_READ_LEAD_RE = re.compile(r"(?:SELECT|SHOW|WITH)\b", re.IGNORECASE)
# A WITH clause can wrap data-modifying statements, and a SELECT can create a
# table (INTO), lock rows, or call functions with side effects. The server's
# read-only transaction rejects most of these too; the guard refuses them
# before they are sent.
_WRITE_TOKEN_RE = re.compile(
    r"\b(?:INSERT|UPDATE|DELETE|MERGE|INTO|TRUNCATE|COPY|CALL|LOCK|NOTIFY)\b",
    re.IGNORECASE,
)
_SIDE_EFFECT_CALL_RE = re.compile(
    r"\b(?:nextval|setval|set_config|pg_terminate_backend|pg_cancel_backend|pg_notify"
    r"|pg_reload_conf|pg_(?:try_)?advisory_\w*|lo_\w+|dblink\w*)\s*\(",
    re.IGNORECASE,
)


def assert_read_statement(statement: object) -> None:
    """Refuse anything but one SELECT, SHOW or read-only WITH statement."""

    if not isinstance(statement, str):
        raise ReadOnlyStatementRefused("read-only session accepts literal SQL text only")
    code = _SQL_STRING_LITERAL_RE.sub("''", _COMMENT_RE.sub(" ", statement)).strip()
    code = code.removesuffix(";").rstrip()
    if ";" in code:
        raise ReadOnlyStatementRefused("read-only session refuses multiple statements")
    if not _READ_LEAD_RE.match(code):
        raise ReadOnlyStatementRefused("read-only session refuses a non-read statement")
    if _WRITE_TOKEN_RE.search(code) or _SIDE_EFFECT_CALL_RE.search(code):
        raise ReadOnlyStatementRefused("read-only session refuses a writing read statement")


class ReadOnlyCursor:
    """Cursor proxy that checks every statement before the driver sees it."""

    def __init__(self, cursor: Any) -> None:
        self._cursor = cursor

    def execute(self, statement: object, params: Any = None) -> None:
        assert_read_statement(statement)
        if params is None:
            self._cursor.execute(statement)
        else:
            self._cursor.execute(statement, params)

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self._cursor.fetchall())

    def fetchone(self) -> tuple[Any, ...] | None:
        row = self._cursor.fetchone()
        return None if row is None else tuple(row)


@contextmanager
def read_only_session(conn_kwargs: dict) -> Iterator[ReadOnlyCursor]:
    """Yield a guarded cursor inside a server-enforced read-only transaction."""

    import psycopg  # local import so `--help` still works without the wheel

    conn = psycopg.connect(**conn_kwargs, autocommit=False)
    try:
        # Set before the first statement: psycopg then opens every transaction
        # on this connection with BEGIN READ ONLY.
        conn.read_only = True
        with conn.cursor() as raw_cursor:
            cursor = ReadOnlyCursor(raw_cursor)
            cursor.execute("SHOW transaction_read_only")
            row = cursor.fetchone()
            if row is None or str(row[0]).lower() != "on":
                raise ReadOnlyStatementRefused("Lakebase session is not read-only")
            yield cursor
    finally:
        conn.rollback()
        conn.close()

"""Server-enforced read-only Lakebase session with a client-side statement guard.

``--mode plan`` and ``--mode verify`` run every query through this session.
Two independent layers keep them from mutating the database:

* the server: psycopg starts each transaction ``READ ONLY`` (PostgreSQL then
  rejects DDL and DML), and every statement is sent as a prepared statement,
  which PostgreSQL parses as exactly one command, so no text can end the
  read-only transaction and start another;
* the client: the cursor proxy refuses, before the driver sees it, any
  statement that is not a single read.

The session always rolls back.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any


class ReadOnlyStatementRefused(RuntimeError):
    """A plan or verify code path tried to send a statement that is not a read."""


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
# A dollar quote opens with $tag$ (the tag may be empty) and cannot start
# inside an identifier, where `$` is an ordinary character.
_DOLLAR_TAG_RE = re.compile(r"\$(?:[A-Za-z_][A-Za-z_0-9]*)?\$")
_IDENTIFIER_CHAR_RE = re.compile(r"[A-Za-z_0-9$]")


def _skip_block_comment(statement: str, index: int) -> int:
    depth = 0
    while index < len(statement):
        if statement.startswith("/*", index):
            depth, index = depth + 1, index + 2
        elif statement.startswith("*/", index):
            depth, index = depth - 1, index + 2
            if depth == 0:
                return index
        else:
            index += 1
    raise ReadOnlyStatementRefused("read-only session refuses an unterminated comment")


def _quoted_end(statement: str, index: int, quote: str) -> tuple[int, str]:
    """Return the index past a quoted run opening at ``index`` and its content."""

    content: list[str] = []
    index += 1
    while index < len(statement):
        char = statement[index]
        if char == quote:
            if statement.startswith(quote * 2, index):
                content.append(quote)
                index += 2
                continue
            return index + 1, "".join(content)
        if quote == "'" and char == "\\":
            # Its meaning depends on an E prefix and standard_conforming_strings.
            raise ReadOnlyStatementRefused("read-only session refuses a backslash in a literal")
        content.append(char)
        index += 1
    raise ReadOnlyStatementRefused("read-only session refuses an unterminated quote")


def statement_code(statement: str) -> str:
    """Return the statement with every literal and comment blanked out.

    One left-to-right scan, like PostgreSQL's lexer, so a comment marker
    inside a literal cannot hide code and a quote inside a comment cannot
    hide a literal's end. Quoted identifiers are kept without their quotes,
    so ``"nextval"(`` still reads as a call.
    """

    code: list[str] = []
    index = 0
    while index < len(statement):
        char = statement[index]
        if statement.startswith("--", index):
            newline = statement.find("\n", index)
            index = len(statement) if newline < 0 else newline
            code.append(" ")
        elif statement.startswith("/*", index):
            index = _skip_block_comment(statement, index)
            code.append(" ")
        elif char == "'":
            index, _content = _quoted_end(statement, index, "'")
            code.append("''")
        elif char == '"':
            index, identifier = _quoted_end(statement, index, '"')
            code.append(identifier)
        elif (
            char == "$"
            and (dollar := _DOLLAR_TAG_RE.match(statement, index)) is not None
            and not (index and _IDENTIFIER_CHAR_RE.match(statement[index - 1]))
        ):
            close = statement.find(dollar.group(0), dollar.end())
            if close < 0:
                raise ReadOnlyStatementRefused("read-only session refuses an unterminated quote")
            index = close + len(dollar.group(0))
            code.append("''")
        else:
            code.append(char)
            index += 1
    return "".join(code)


def assert_read_statement(statement: object) -> None:
    """Refuse anything but one SELECT, SHOW or read-only WITH statement."""

    if not isinstance(statement, str):
        raise ReadOnlyStatementRefused("read-only session accepts literal SQL text only")
    code = statement_code(statement).strip()
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
        # prepare=True sends Parse/Bind/Execute even without parameters; the
        # simple-query protocol would run every command in the text.
        self._cursor.execute(statement, params, prepare=True)

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

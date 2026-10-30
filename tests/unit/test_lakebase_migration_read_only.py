"""The read-only session's statement guard: one lexer-order scan, then one read.

The guard is the client half of the read-only proof for ``--mode plan`` and
``--mode verify`` (the server half is the READ ONLY transaction plus the
prepared-statement protocol, proven in the DSN suite). Each refusal case here
is text that would end the read-only transaction or write if it reached the
server's simple-query protocol.
"""

from __future__ import annotations

from typing import Any

import pytest

from jobs.lakebase_migration_read_only import (
    ReadOnlyCursor,
    ReadOnlyStatementRefused,
    assert_read_statement,
    statement_code,
)

# Each hides a second command behind a comment marker inside a literal, or
# behind a quote, or behind a lexical form the scanner must not misread.
_SMUGGLED = (
    "SELECT '--'; COMMIT; CREATE TABLE public.guard_probe (v int)",
    "SELECT '/*'; DELETE FROM mip_app.campaigns; SELECT '*/'",
    "SELECT 1 -- it's a comment\n; DELETE FROM mip_app.campaigns",
    "SELECT $q$ text $q$; COMMIT; DROP TABLE mip_app.campaigns",
    "SELECT $$ ' $$; COMMIT; CREATE TABLE public.guard_probe (v int)",
    "SELECT 1 /* outer /* inner */ still a comment */; COMMIT; DELETE FROM x",
    'SELECT "a;b"',
)
_WRITING_READS = (
    "SELECT \"nextval\"('mip_app.action_audit_audit_sequence_seq')",
    "SELECT pg_catalog.setval('mip_app.action_audit_audit_sequence_seq', 1)",
    "WITH gone AS (DELETE FROM mip_app.campaigns RETURNING 1) SELECT count(*) FROM gone",
    "SELECT * INTO public.copy FROM mip_app.campaigns",
)
_UNSCANNABLE = (
    "SELECT E'\\''; COMMIT; CREATE TABLE public.guard_probe (v int); SELECT '",
    "SELECT 'unterminated",
    "SELECT $tag$ unterminated",
    'SELECT "unterminated',
    "SELECT 1 /* unterminated",
)
_READS = (
    "SELECT 1",
    "SHOW transaction_read_only",
    "SELECT version FROM mip_app.schema_migrations ORDER BY version;",
    "SELECT ';' AS semicolon, '--' AS dashes, '/*' AS slash_star, $$;$$ AS dollar",
    "SELECT 1 /* outer /* inner */ ; still a comment */",
    "SELECT input_digest FROM mip_app.deployment_schema_digest WHERE scope = %s",
    "WITH applied AS (SELECT version FROM mip_app.schema_migrations) SELECT count(*) FROM applied",
    "SELECT 'it''s' -- a trailing comment; DELETE",
    "SELECT a$b$ FROM (SELECT 1 AS a$b$) AS identifier_with_dollars",
)


@pytest.mark.parametrize("statement", _SMUGGLED)
def test_a_second_command_hidden_by_a_literal_or_comment_refuses(statement: str) -> None:
    with pytest.raises(ReadOnlyStatementRefused, match="multiple statements"):
        assert_read_statement(statement)


@pytest.mark.parametrize("statement", _WRITING_READS)
def test_a_read_shaped_statement_that_writes_refuses(statement: str) -> None:
    with pytest.raises(ReadOnlyStatementRefused, match="writing read statement"):
        assert_read_statement(statement)


@pytest.mark.parametrize("statement", _UNSCANNABLE)
def test_text_the_scanner_cannot_read_unambiguously_refuses(statement: str) -> None:
    with pytest.raises(ReadOnlyStatementRefused, match="backslash|unterminated"):
        assert_read_statement(statement)


@pytest.mark.parametrize("statement", _READS)
def test_single_reads_are_admitted(statement: str) -> None:
    assert_read_statement(statement)


def test_the_scan_blanks_literals_and_comments_in_lexer_order() -> None:
    assert statement_code("SELECT '--'; COMMIT") == "SELECT ''; COMMIT"
    assert statement_code("SELECT 1 -- 'quote\n, 2") == "SELECT 1  \n, 2"
    assert statement_code('SELECT $a$ $b$ \' $a$, "Col""x"') == "SELECT '', Col\"x"


def test_non_text_statements_refuse() -> None:
    with pytest.raises(ReadOnlyStatementRefused, match="literal SQL text only"):
        assert_read_statement(b"SELECT 1")


class _RawCursor:
    def __init__(self) -> None:
        self.calls: list[tuple[object, Any, dict[str, Any]]] = []

    def execute(self, statement: object, params: Any = None, **kwargs: Any) -> None:
        self.calls.append((statement, params, kwargs))

    def fetchall(self) -> list[tuple[Any, ...]]:
        return []

    def fetchone(self) -> None:
        return None


def test_every_admitted_statement_is_sent_as_a_prepared_statement() -> None:
    raw = _RawCursor()
    cursor = ReadOnlyCursor(raw)

    cursor.execute("SELECT 1")
    cursor.execute("SELECT %s::text", ("value",))
    with pytest.raises(ReadOnlyStatementRefused):
        cursor.execute("SELECT '--'; COMMIT; CREATE TABLE public.guard_probe (v int)")

    assert raw.calls == [
        ("SELECT 1", None, {"prepare": True}),
        ("SELECT %s::text", ("value",), {"prepare": True}),
    ]

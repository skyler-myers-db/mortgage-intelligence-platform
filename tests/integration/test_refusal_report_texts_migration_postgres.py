"""Real-PostgreSQL contract of the W5c refusal-capture migrations.

``2026_10_07_genie_refusal_report_texts`` (D-audit-reads-d) adds the
consented, purge-only question-text table, and
``2026_10_07_disposition_notes_retired`` (D-shell-deviations-g2) re-comments
the retired disposition-note column. The suite starts from the schema as it
stood before the first block, then applies the full governed migration TWICE
through ``lakebase_migrate._run_transaction`` (each wrapped in
``contract_as_of``): the second run is the one whose executable-hook
preflight inventories the new CHECKs and whose trigger postflight checks the
two new triggers. It then proves the purge-only trigger, the no-remove
trigger and the CHECKs on real rows, and that the notes COMMENT applies
idempotently. Skipped unless ``MIP_TEST_POSTGRES_DSN`` names a disposable
database.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from backend.services import genie_refusal_report as refusal_report
from backend.services import genie_refusal_report_reads as reads
from backend.services.genie_refusal_reason import refusal_report_hash
from jobs import lakebase_migrate
from tests.fixtures.lakebase_contract_prefix import contract_as_of

pytestmark = pytest.mark.integration

_SCHEMA = Path("lakebase/schema.sql").read_text(encoding="utf-8")
_SEED = Path("lakebase/seed_campaigns.sql").read_text(encoding="utf-8")
_TEXTS_MARKER = "-- Genie refusal report texts (consented, 90 days, purge-only) ---"
_TEXTS_VERSION = "2026_10_07_genie_refusal_report_texts"
_NOTES_VERSION = "2026_10_07_disposition_notes_retired"
_HASH = "a" * 64


def _apply(conn_kwargs: dict[str, str], schema_sql: str) -> None:
    pre_seed, post_seed = lakebase_migrate._split_schema_sql(schema_sql)
    with contract_as_of(schema_sql):
        lakebase_migrate._run_transaction(
            (pre_seed, _SEED, post_seed),
            conn_kwargs,
            app_role="lakebase-schema-upgrade-test-role",
            verify_outreach_integrity=True,
            allow_absent_managed_event_triggers=True,
            allow_absent_provider_schema=True,
        )


@pytest.fixture(scope="module")
def conn_kwargs() -> Iterator[dict[str, str]]:
    dsn = os.environ.get("MIP_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("MIP_TEST_POSTGRES_DSN is not configured")
    kwargs = {"conninfo": dsn}
    with psycopg.connect(**kwargs, autocommit=True) as conn:
        conn.execute("DROP SCHEMA IF EXISTS mip_app CASCADE")
    _apply(kwargs, _SCHEMA[: _SCHEMA.index(_TEXTS_MARKER)])
    _apply(kwargs, _SCHEMA)
    # The re-run: the executable-hook preflight now inventories the new
    # CHECKs and the trigger postflight the two new triggers.
    _apply(kwargs, _SCHEMA)
    try:
        yield kwargs
    finally:
        with psycopg.connect(**kwargs, autocommit=True) as conn:
            conn.execute("DROP SCHEMA IF EXISTS mip_app CASCADE")


def _report(conn: psycopg.Connection[Any]) -> str:
    row = conn.execute(
        "INSERT INTO mip_app.genie_refusal_reports (actor_email, question_hash, refusal_reason) "
        "VALUES (%s, %s, 'unreviewed_criterion') RETURNING report_id",
        (f"lo-{uuid4().hex[:8]}@summit.example", _HASH),
    ).fetchone()
    assert row is not None
    return str(row[0])


def _text_row(conn_kwargs: dict[str, str], question: str = "Which zyrplax borrowers qualify?") -> str:
    with psycopg.connect(**conn_kwargs) as conn:
        report_id = _report(conn)
        conn.execute(
            "INSERT INTO mip_app.genie_refusal_report_texts "
            "(report_id, question_text, redacted, expires_at) "
            "VALUES (%s, %s, false, now() + interval '90 days')",
            (report_id, question),
        )
    return report_id


def _refused(conn_kwargs: dict[str, str], statement: str, params: tuple[Any, ...], code: str) -> None:
    with pytest.raises(psycopg.Error) as info, psycopg.connect(**conn_kwargs) as conn:
        conn.execute(statement, params)  # type: ignore[arg-type]
    assert info.value.sqlstate == code, info.value


def test_both_versions_are_recorded_once_after_the_re_run(conn_kwargs: dict[str, str]) -> None:
    with psycopg.connect(**conn_kwargs) as conn:
        rows = conn.execute(
            "SELECT version, count(*) FROM mip_app.schema_migrations "
            "WHERE version IN (%s, %s) GROUP BY version",
            (_TEXTS_VERSION, _NOTES_VERSION),
        ).fetchall()
        comment = conn.execute(
            "SELECT col_description('mip_app.call_dispositions'::regclass, attnum) "
            "FROM pg_attribute WHERE attrelid = 'mip_app.call_dispositions'::regclass "
            "AND attname = 'notes'"
        ).fetchone()
    assert dict(rows) == {_TEXTS_VERSION: 1, _NOTES_VERSION: 1}
    assert comment is not None and str(comment[0]).startswith("Retired 2026-10: no longer written")


def test_the_triggers_and_checks_are_in_the_catalog(conn_kwargs: dict[str, str]) -> None:
    with psycopg.connect(**conn_kwargs) as conn:
        triggers = dict(
            conn.execute(
                "SELECT tgname, tgtype FROM pg_trigger "
                "WHERE tgrelid = 'mip_app.genie_refusal_report_texts'::regclass AND NOT tgisinternal"
            ).fetchall()
        )
        checks = {
            str(name)
            for (name,) in conn.execute(
                "SELECT conname FROM pg_constraint "
                "WHERE conrelid = 'mip_app.genie_refusal_report_texts'::regclass AND contype = 'c'"
            ).fetchall()
        }
        default = conn.execute(
            "SELECT pg_get_expr(adbin, adrelid) FROM pg_attrdef d "
            "JOIN pg_attribute a ON a.attrelid = d.adrelid AND a.attnum = d.adnum "
            "WHERE d.adrelid = 'mip_app.genie_refusal_report_texts'::regclass "
            "AND a.attname = 'expires_at'"
        ).fetchone()
    assert triggers == {
        "trg_genie_refusal_report_texts_purge_only": 19,
        "trg_genie_refusal_report_texts_no_remove": 42,
    }
    assert {
        "genie_refusal_report_texts_purge_shape_chk",
        "genie_refusal_report_texts_expiry_chk",
    } <= checks
    assert default is None


def test_only_the_one_time_purge_update_is_allowed(conn_kwargs: dict[str, str]) -> None:
    report_id = _text_row(conn_kwargs)
    _refused(
        conn_kwargs,
        "UPDATE mip_app.genie_refusal_report_texts SET question_text = 'other text' WHERE report_id = %s",
        (report_id,),
        "42501",
    )
    _refused(
        conn_kwargs,
        "UPDATE mip_app.genie_refusal_report_texts SET expires_at = expires_at + interval '1 day' "
        "WHERE report_id = %s",
        (report_id,),
        "42501",
    )
    _refused(
        conn_kwargs,
        "UPDATE mip_app.genie_refusal_report_texts SET redacted = true WHERE report_id = %s",
        (report_id,),
        "42501",
    )
    with psycopg.connect(**conn_kwargs) as conn:
        purged = conn.execute(
            "UPDATE mip_app.genie_refusal_report_texts SET question_text = NULL, purged_at = now() "
            "WHERE report_id = %s RETURNING purged_at",
            (report_id,),
        ).fetchone()
    assert purged is not None and purged[0] is not None
    # A purged row is final: no second purge, no restore.
    _refused(
        conn_kwargs,
        "UPDATE mip_app.genie_refusal_report_texts SET purged_at = now() WHERE report_id = %s",
        (report_id,),
        "42501",
    )
    _refused(
        conn_kwargs,
        "UPDATE mip_app.genie_refusal_report_texts SET question_text = 'back', purged_at = NULL "
        "WHERE report_id = %s",
        (report_id,),
        "42501",
    )


def test_rows_are_never_deleted_or_truncated(conn_kwargs: dict[str, str]) -> None:
    report_id = _text_row(conn_kwargs)
    _refused(
        conn_kwargs,
        "DELETE FROM mip_app.genie_refusal_report_texts WHERE report_id = %s",
        (report_id,),
        "42501",
    )
    _refused(conn_kwargs, "TRUNCATE mip_app.genie_refusal_report_texts CASCADE", (), "42501")


def test_the_checks_refuse_oversized_text_and_a_backwards_expiry(conn_kwargs: dict[str, str]) -> None:
    with psycopg.connect(**conn_kwargs) as conn:
        report_id = _report(conn)
    _refused(
        conn_kwargs,
        "INSERT INTO mip_app.genie_refusal_report_texts (report_id, question_text, redacted, expires_at) "
        "VALUES (%s, %s, false, now() + interval '90 days')",
        (report_id, "x" * 16001),
        "23514",
    )
    _refused(
        conn_kwargs,
        "INSERT INTO mip_app.genie_refusal_report_texts (report_id, question_text, redacted, expires_at) "
        "VALUES (%s, 'q', false, now())",
        (report_id,),
        "23514",
    )
    _refused(
        conn_kwargs,
        "INSERT INTO mip_app.genie_refusal_report_texts (report_id, question_text, redacted, expires_at) "
        "VALUES (%s, 'q', false, now() - interval '1 day')",
        (report_id,),
        "23514",
    )
    # A NULL text must come with purged_at (the purge-shape CHECK).
    _refused(
        conn_kwargs,
        "INSERT INTO mip_app.genie_refusal_report_texts (report_id, question_text, redacted, expires_at) "
        "VALUES (%s, NULL, false, now() + interval '90 days')",
        (report_id,),
        "23514",
    )
    # And no text row without its report (the FK).
    _refused(
        conn_kwargs,
        "INSERT INTO mip_app.genie_refusal_report_texts (report_id, question_text, redacted, expires_at) "
        "VALUES (%s, 'q', false, now() + interval '90 days')",
        (str(uuid4()),),
        "23503",
    )


# -- the service SQL against the real tables -----------------------------------


class _Pg:
    """The LakebaseClient surface the refusal services use, over psycopg."""

    def __init__(self, conn_kwargs: dict[str, str]) -> None:
        self.conn_kwargs = conn_kwargs

    @contextmanager
    def transaction(self) -> Iterator[psycopg.Connection[dict[str, Any]]]:
        with psycopg.connect(**self.conn_kwargs, row_factory=dict_row) as conn:
            yield conn

    def fetchone(self, sql: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        with self.transaction() as conn:
            return conn.execute(sql, params).fetchone()  # type: ignore[arg-type]

    def fetchall(self, sql: str, params: dict[str, Any] | None = None, limit: int = 100) -> list[dict[str, Any]]:
        with self.transaction() as conn:
            return conn.execute(sql, params).fetchmany(limit)  # type: ignore[arg-type]


def _ledger_refusal(conn_kwargs: dict[str, str], actor: str, question: str) -> None:
    with psycopg.connect(**conn_kwargs) as conn:
        conn.execute(
            "INSERT INTO mip_app.action_audit (event_type, actor_email, entity_type, entity_id, metadata) "
            "VALUES ('RUN_GENIE', %s, 'genie_message', 'genie', %s::jsonb)",
            (
                actor,
                json.dumps(
                    {"question_hash": refusal_report_hash(question)[:16], "action_type": "refused_prompt"}
                ),
            ),
        )


def test_the_report_probe_insert_page_question_and_sweep_sql_run_on_postgres(
    conn_kwargs: dict[str, str],
) -> None:
    pg = _Pg(conn_kwargs)
    actor = f"lo-{uuid4().hex[:8]}@example.com"
    question = "Which zyrplax borrowers are eligible for a HELOC?"
    _ledger_refusal(conn_kwargs, actor, question)
    report = refusal_report.record_genie_refusal_report(
        pg,  # type: ignore[arg-type]
        actor=actor,
        question_hash=refusal_report_hash(question),
        refusal_reason="unreviewed_criterion",
        conversation_id=None,
        message_id=None,
        offered_text=refusal_report.OfferedRefusalText(scrubbed=question, redacted=False),
    )
    assert report.question_captured is True and report.report_id is not None
    replay = refusal_report.record_genie_refusal_report(
        pg,  # type: ignore[arg-type]
        actor=actor,
        question_hash=refusal_report_hash(question),
        refusal_reason="unreviewed_criterion",
        conversation_id=None,
        message_id=None,
        offered_text=refusal_report.OfferedRefusalText(scrubbed=question, redacted=False),
    )
    assert (replay.duplicate, replay.report_id, replay.question_captured) == (True, report.report_id, True)
    assert replay.audit_event_id == report.audit_event_id
    unbound = refusal_report.record_genie_refusal_report(
        pg,  # type: ignore[arg-type]
        actor=f"other-{actor}",
        question_hash=refusal_report_hash(question),
        refusal_reason="unreviewed_criterion",
        conversation_id=None,
        message_id=None,
        offered_text=refusal_report.OfferedRefusalText(scrubbed=question, redacted=False),
    )
    assert (unbound.question_captured, unbound.declined) == (False, "no_matching_refusal")

    page = reads.read_refusal_report_page(
        pg, since=datetime.now(UTC) - timedelta(days=90), family=None, limit=1, cursor=None,
        filter_fingerprint="f",
    )
    assert len(page.rows) == 1 and page.next_cursor is not None
    second = reads.read_refusal_report_page(
        pg, since=datetime.now(UTC), family=None, limit=1, cursor=page.next_cursor, filter_fingerprint="f",
    )
    assert {page.rows[0]["report_id"], second.rows[0]["report_id"]} == {report.report_id, unbound.report_id}
    by_id = {row["report_id"]: row for row in (page.rows[0], second.rows[0])}
    assert by_id[report.report_id]["has_text"] is True and by_id[unbound.report_id]["has_text"] is False
    family = reads.read_refusal_report_page(
        pg, since=datetime.now(UTC) - timedelta(days=90), family="out_of_scope", limit=5, cursor=None,
        filter_fingerprint="g",
    )
    assert family.rows == [] and {row["refusal_reason"] for row in family.family_counts} >= {"unreviewed_criterion"}
    held = reads.read_refusal_question(pg, report.report_id)
    assert held is not None and held["question_text"] == question

    # Expire it in place (as the clock would), then the sweep nulls it.
    with psycopg.connect(**conn_kwargs, autocommit=True) as conn:
        conn.execute("ALTER TABLE mip_app.genie_refusal_report_texts DISABLE TRIGGER trg_genie_refusal_report_texts_purge_only")
        conn.execute(
            "UPDATE mip_app.genie_refusal_report_texts SET captured_at = now() - interval '91 days', "
            "expires_at = now() - interval '1 day' WHERE report_id = %s",
            (report.report_id,),
        )
        conn.execute("ALTER TABLE mip_app.genie_refusal_report_texts ENABLE TRIGGER trg_genie_refusal_report_texts_purge_only")
    assert reads.read_refusal_question(pg, report.report_id) is None
    with pg.transaction() as conn:
        assert refusal_report.sweep_expired_refusal_texts(conn) >= 1
    with psycopg.connect(**conn_kwargs) as conn:
        row = conn.execute(
            "SELECT question_text, purged_at FROM mip_app.genie_refusal_report_texts WHERE report_id = %s",
            (report.report_id,),
        ).fetchone()
    assert row is not None and row[0] is None and row[1] is not None

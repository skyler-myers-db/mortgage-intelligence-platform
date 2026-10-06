"""Real-PostgreSQL contract of the W5c refusal-capture migrations.

``2026_10_01_genie_refusal_report_texts`` (D-audit-reads-d) adds the
consented, purge-only question-text table, and
``2026_10_01_disposition_notes_retired`` (D-shell-deviations-g2) re-comments
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

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
import pytest

from jobs import lakebase_migrate
from tests.fixtures.lakebase_contract_prefix import contract_as_of

pytestmark = pytest.mark.integration

_SCHEMA = Path("lakebase/schema.sql").read_text(encoding="utf-8")
_SEED = Path("lakebase/seed_campaigns.sql").read_text(encoding="utf-8")
_TEXTS_MARKER = "-- Genie refusal report texts (consented, 90 days, purge-only) ---"
_TEXTS_VERSION = "2026_10_01_genie_refusal_report_texts"
_NOTES_VERSION = "2026_10_01_disposition_notes_retired"
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

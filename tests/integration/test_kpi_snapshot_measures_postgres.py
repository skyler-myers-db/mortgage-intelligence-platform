"""Real-Postgres re-run contract of the KPI snapshot event-measures block.

Audit 2026-09-21 ``flow-05``. The block adds two nullable columns to
``mip_app.kpi_snapshots`` (``listed_for_sale``, ``competitor_lien``) with
named non-negative CHECKs, so Home's WHY NOW can cite listing and
competitor-lien movement since the last visit. This suite starts from the
schema just before the block with an old-shape snapshot row, applies the
governed migration TWICE through ``lakebase_migrate._run_transaction`` (the
executable-hook preflight inventories the catalog, so only a re-run can fail
on a new expression), and pins: the old row keeps NULL (never 0), the job's
own ``_UPSERT_SQL`` writes and refreshes both columns, and the CHECK refuses
a negative count. Skipped unless ``MIP_TEST_POSTGRES_DSN`` names a
disposable database.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest

from jobs import kpi_snapshot, lakebase_migrate
from tests.fixtures.lakebase_contract_prefix import contract_as_of

pytestmark = pytest.mark.integration

_SCHEMA = Path("lakebase/schema.sql").read_text(encoding="utf-8")
_SEED = Path("lakebase/seed_campaigns.sql").read_text(encoding="utf-8")
_MARKER = "-- KPI snapshot event measures ---"


@pytest.fixture
def conn_kwargs() -> Iterator[dict[str, str]]:
    dsn = os.environ.get("MIP_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("MIP_TEST_POSTGRES_DSN is not configured")
    kwargs = {"conninfo": dsn}
    with psycopg.connect(**kwargs, autocommit=True) as conn:
        conn.execute("DROP SCHEMA IF EXISTS mip_app CASCADE")
    try:
        yield kwargs
    finally:
        with psycopg.connect(**kwargs, autocommit=True) as conn:
            conn.execute("DROP SCHEMA IF EXISTS mip_app CASCADE")


def _apply(conn_kwargs: dict[str, str], schema_sql: str) -> None:
    pre_seed, post_seed = lakebase_migrate._split_schema_sql(schema_sql)
    # A prefix replay runs against the contract of its own era: the
    # approval-request block appended later adds triggers and routines.
    with contract_as_of(schema_sql):
        lakebase_migrate._run_transaction(
            (pre_seed, _SEED, post_seed),
            conn_kwargs,
            app_role="lakebase-schema-upgrade-test-role",
            verify_outreach_integrity=True,
            allow_absent_managed_event_triggers=True,
            allow_absent_provider_schema=True,
        )


def _columns(conn: psycopg.Connection[Any]) -> dict[str, tuple[str, str]]:
    rows = conn.execute(
        "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
        "WHERE table_schema = 'mip_app' AND table_name = 'kpi_snapshots'"
    ).fetchall()
    return {str(name): (str(kind), str(nullable)) for name, kind, nullable in rows}


def _upgraded_with_an_old_row(conn_kwargs: dict[str, str]) -> None:
    _apply(conn_kwargs, _SCHEMA[: _SCHEMA.index(_MARKER)])
    with psycopg.connect(**conn_kwargs) as conn:
        conn.execute(
            "INSERT INTO mip_app.kpi_snapshots (snapshot_date, snapshot_at, marketable_population, "
            "refi_economics_screen, high_opportunity, offers_available, offers_recommended, "
            "avg_opportunity_score) VALUES ('2026-09-01', '2026-09-01T06:00:00Z', 100, 10, 5, 8, 6, 61.5)"
        )
    _apply(conn_kwargs, _SCHEMA)
    # The executable-hook preflight inventories the upgraded catalog on this run.
    _apply(conn_kwargs, _SCHEMA)


def test_the_block_applies_twice_and_an_old_row_keeps_null(conn_kwargs: dict[str, str]) -> None:
    _upgraded_with_an_old_row(conn_kwargs)

    with psycopg.connect(**conn_kwargs) as conn:
        columns = _columns(conn)
        assert columns["listed_for_sale"] == ("bigint", "YES")
        assert columns["competitor_lien"] == ("bigint", "YES")
        old = conn.execute(
            "SELECT listed_for_sale, competitor_lien FROM mip_app.kpi_snapshots "
            "WHERE snapshot_date = '2026-09-01'"
        ).fetchone()
        assert old == (None, None)
        checks = conn.execute(
            "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid = 'mip_app.kpi_snapshots'::regclass AND conname LIKE 'kpi_snapshots_%_chk' "
            "ORDER BY conname"
        ).fetchall()
        assert [name for name, _ in checks] == [
            "kpi_snapshots_competitor_lien_chk",
            "kpi_snapshots_listed_for_sale_chk",
        ]
        ledger = conn.execute(
            "SELECT count(*) FROM mip_app.schema_migrations WHERE version LIKE '%_kpi_snapshot_event_measures'"
        ).fetchone()
        assert ledger is not None and ledger[0] == 1


def test_the_job_upsert_writes_both_columns_and_refreshes_them(conn_kwargs: dict[str, str]) -> None:
    _upgraded_with_an_old_row(conn_kwargs)
    aggregates = {
        "marketable_population": 200,
        "refi_economics_screen": 20,
        "high_opportunity": 9,
        "offers_available": 15,
        "offers_recommended": 12,
        "avg_opportunity_score": 62.0,
        "listed_for_sale": 7,
        "competitor_lien": 31,
    }
    with psycopg.connect(**conn_kwargs, autocommit=True) as conn:
        conn.execute(kpi_snapshot._UPSERT_SQL, aggregates)
        conn.execute(kpi_snapshot._UPSERT_SQL, {**aggregates, "listed_for_sale": 8, "competitor_lien": 30})
        rows = conn.execute(
            "SELECT listed_for_sale, competitor_lien FROM mip_app.kpi_snapshots "
            "WHERE snapshot_date <> '2026-09-01'"
        ).fetchall()
    # One row per day: the re-run refreshed it.
    assert rows == [(8, 30)]


def test_the_check_refuses_a_negative_count(conn_kwargs: dict[str, str]) -> None:
    _upgraded_with_an_old_row(conn_kwargs)
    with psycopg.connect(**conn_kwargs) as conn, pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "UPDATE mip_app.kpi_snapshots SET listed_for_sale = -1 WHERE snapshot_date = '2026-09-01'"
        )
    with psycopg.connect(**conn_kwargs) as conn, pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "UPDATE mip_app.kpi_snapshots SET competitor_lien = -1 WHERE snapshot_date = '2026-09-01'"
        )

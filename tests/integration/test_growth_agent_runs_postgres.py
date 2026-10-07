"""Real-Postgres re-run contract of the 2026_10_01 Growth Agent watchlist series block.

Audit 2026-09-21 ``wow-ai-4`` / ``genie-09``. The block adds a nullable
``growth_agent_runs.monitor_id`` (set at INSERT only), its partial index, and
``growth_agent_monitors.seed_run_id`` with a one-statement backfill. Runs stay
append-only: ``trg_growth_agent_runs_finalize_only`` still refuses every UPDATE
but the one-time ``audit_event_id`` attach, so the series can only be written
when a run is born. This suite starts from the schema just before the block
with a run and a monitor saved from it, applies the governed migration TWICE
through ``lakebase_migrate._run_transaction`` (the executable-hook preflight
inventories the catalog, so only a re-run can fail on a new expression), and
pins the columns, foreign keys, index, backfill and ledger row. Skipped unless
``MIP_TEST_POSTGRES_DSN`` names a disposable database.

W5c (w5-zcta-watchlist, the 12.3 plan fix): ``WATCHLIST_SUMMARY_SQL`` reads
its series through three index-backed arms instead of one OR join. The
32e5d382 statement is kept verbatim below as the result oracle, and the plan
pin proves each arm's index scan under a generic plan with sequential scans
disabled. On PG16 with statistics the OR join itself plans a BitmapOr (no Seq
Scan), so the pin is on the arms' own index scans, as the brief allows.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from backend.services.growth_agent_ledger_sql import (
    MONITOR_ID_BY_KEY_SQL,
    MONITOR_SELECT_BY_KEY_SQL,
    RUN_SELECT_FOR_SAVE_SQL,
    WATCHLIST_SUMMARY_SQL,
)
from backend.services.growth_agent_watchlist_summary import briefings_from_rows
from jobs import lakebase_migrate
from tests.fixtures.lakebase_contract_prefix import contract_as_of

pytestmark = pytest.mark.integration

# The 32e5d382 statement, verbatim: the result oracle for the three-arm rewrite.
_BASE_WATCHLIST_SUMMARY_SQL = """
WITH watchlists AS (
  SELECT monitor_id, workflow_id, name, cadence, status, seed_run_id, last_run_id, updated_at
  FROM mip_app.growth_agent_monitors
  WHERE actor_email = %(actor_email)s
  ORDER BY updated_at DESC, monitor_id
  LIMIT %(limit)s
),
series AS (
  SELECT w.monitor_id, r.created_at AS run_at, r.actionable_total, r.actionable_avg_score,
         LAG(r.created_at) OVER ordered AS previous_run_at,
         LAG(r.actionable_total) OVER ordered AS previous_actionable_total,
         LAG(r.actionable_avg_score) OVER ordered AS previous_actionable_avg_score,
         ROW_NUMBER() OVER (PARTITION BY w.monitor_id ORDER BY r.created_at DESC, r.run_id DESC) AS recency,
         COUNT(*) OVER (PARTITION BY w.monitor_id) AS run_count
  FROM watchlists AS w
  JOIN mip_app.growth_agent_runs AS r
    ON r.actor_email = %(actor_email)s
   AND r.status = 'completed'
   AND (r.monitor_id = w.monitor_id OR r.run_id = w.seed_run_id OR r.run_id = w.last_run_id)
  WINDOW ordered AS (PARTITION BY w.monitor_id ORDER BY r.created_at, r.run_id)
)
SELECT w.monitor_id, w.workflow_id, w.name, w.cadence, w.status,
       s.recency, s.run_count, s.run_at, s.actionable_total, s.actionable_avg_score,
       s.previous_run_at, s.previous_actionable_total, s.previous_actionable_avg_score
FROM watchlists AS w
LEFT JOIN series AS s
  ON s.monitor_id = w.monitor_id
 AND s.recency <= %(points)s
ORDER BY w.updated_at DESC, w.monitor_id, s.recency ASC
"""

_SCHEMA = Path("lakebase/schema.sql").read_text(encoding="utf-8")
_SEED = Path("lakebase/seed_campaigns.sql").read_text(encoding="utf-8")
_SERIES_MARKER = "-- Growth Agent watchlist series ---"
_ACTOR = "lo@example.com"


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
    with contract_as_of(schema_sql):
        lakebase_migrate._run_transaction(
            (pre_seed, _SEED, post_seed),
            conn_kwargs,
            app_role="lakebase-schema-upgrade-test-role",
            verify_outreach_integrity=True,
            allow_absent_managed_event_triggers=True,
            allow_absent_provider_schema=True,
        )


def _schema_before_series() -> str:
    return _SCHEMA[: _SCHEMA.index(_SERIES_MARKER)]


def insert_run(
    conn: psycopg.Connection[Any],
    *,
    actionable_total: int,
    avg_score: float | None,
    created_at: str,
    status: str = "completed",
    monitor_id: str | None = None,
    actor: str = _ACTOR,
) -> str:
    """Insert one run row the way RUN_INSERT_SQL does (a subset of columns)."""

    row = conn.execute(
        "INSERT INTO mip_app.growth_agent_runs (actor_email, workflow_id, workflow_title, status, "
        "actionable_total, actionable_avg_score, route, created_at"
        + (", monitor_id" if monitor_id else "")
        + ") VALUES (%s, 'daily_refi_brief', 'Daily refi brief', %s, %s, %s, '/lead-queue', %s::timestamptz"
        + (", %s" if monitor_id else "")
        + ") RETURNING run_id",
        (actor, status, actionable_total, avg_score, created_at, *([monitor_id] if monitor_id else [])),
    ).fetchone()
    assert row is not None
    return str(row[0] if not isinstance(row, dict) else row["run_id"])


def _insert_monitor(conn: psycopg.Connection[Any], *, last_run_id: str) -> str:
    row = conn.execute(
        "INSERT INTO mip_app.growth_agent_monitors (actor_email, workflow_id, name, cadence, route, "
        "actionable_total, last_run_id) VALUES (%s, 'daily_refi_brief', 'Daily refi brief - IL', 'daily', "
        "'/lead-queue', 40, %s) RETURNING monitor_id",
        (_ACTOR, last_run_id),
    ).fetchone()
    assert row is not None
    return str(row[0])


def _columns(conn: psycopg.Connection[Any], table: str) -> dict[str, tuple[str, str]]:
    rows = conn.execute(
        "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
        "WHERE table_schema = 'mip_app' AND table_name = %s",
        (table,),
    ).fetchall()
    return {str(name): (str(kind), str(nullable)) for name, kind, nullable in rows}


def _foreign_keys(conn: psycopg.Connection[Any], table: str) -> dict[str, str]:
    rows = conn.execute(
        "SELECT a.attname, pg_get_constraintdef(c.oid) FROM pg_constraint c "
        "JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1] "
        "WHERE c.conrelid = %s::regclass AND c.contype = 'f'",
        (f"mip_app.{table}",),
    ).fetchall()
    return {str(column): str(definition) for column, definition in rows}


def _upgraded_with_a_saved_watchlist(conn_kwargs: dict[str, str]) -> tuple[str, str]:
    _apply(conn_kwargs, _schema_before_series())
    with psycopg.connect(**conn_kwargs) as conn:
        seed_run = insert_run(conn, actionable_total=40, avg_score=71.0, created_at="2026-09-20T09:00:00Z")
        monitor_id = _insert_monitor(conn, last_run_id=seed_run)
    _apply(conn_kwargs, _SCHEMA)
    # The executable-hook preflight inventories the upgraded catalog on this run.
    _apply(conn_kwargs, _SCHEMA)
    return seed_run, monitor_id


def test_the_series_block_applies_twice_with_columns_keys_index_and_backfill(
    conn_kwargs: dict[str, str],
) -> None:
    seed_run, monitor_id = _upgraded_with_a_saved_watchlist(conn_kwargs)

    with psycopg.connect(**conn_kwargs) as conn:
        assert _columns(conn, "growth_agent_runs")["monitor_id"] == ("uuid", "YES")
        assert _columns(conn, "growth_agent_monitors")["seed_run_id"] == ("uuid", "YES")
        run_keys = _foreign_keys(conn, "growth_agent_runs")
        monitor_keys = _foreign_keys(conn, "growth_agent_monitors")
        assert run_keys["monitor_id"] == "FOREIGN KEY (monitor_id) REFERENCES mip_app.growth_agent_monitors(monitor_id)"
        assert monitor_keys["seed_run_id"] == "FOREIGN KEY (seed_run_id) REFERENCES mip_app.growth_agent_runs(run_id)"
        index = conn.execute(
            "SELECT indexdef FROM pg_indexes WHERE schemaname = 'mip_app' "
            "AND indexname = 'idx_growth_agent_runs_monitor_created'"
        ).fetchone()
        assert index is not None
        assert "(monitor_id, created_at DESC)" in str(index[0])
        assert "WHERE (monitor_id IS NOT NULL)" in str(index[0])
        backfilled = conn.execute(
            "SELECT seed_run_id::text FROM mip_app.growth_agent_monitors WHERE monitor_id = %s",
            (monitor_id,),
        ).fetchone()
        assert backfilled is not None and backfilled[0] == seed_run
        ledger = conn.execute(
            "SELECT count(*) FROM mip_app.schema_migrations "
            "WHERE version = '2026_10_01_growth_agent_watchlist_series'"
        ).fetchone()
        assert ledger is not None and ledger[0] == 1


def test_runs_stay_append_only_and_monitor_id_is_written_only_at_insert(
    conn_kwargs: dict[str, str],
) -> None:
    seed_run, monitor_id = _upgraded_with_a_saved_watchlist(conn_kwargs)

    with psycopg.connect(**conn_kwargs) as conn, pytest.raises(psycopg.Error) as refused:
        conn.execute(
            "UPDATE mip_app.growth_agent_runs SET monitor_id = %s WHERE run_id = %s",
            (monitor_id, seed_run),
        )
    assert refused.value.sqlstate == "42501"

    with psycopg.connect(**conn_kwargs) as conn:
        tagged = insert_run(
            conn,
            actionable_total=44,
            avg_score=72.5,
            created_at="2026-09-21T09:00:00Z",
            monitor_id=monitor_id,
        )
        stored = conn.execute(
            "SELECT monitor_id::text FROM mip_app.growth_agent_runs WHERE run_id = %s", (tagged,)
        ).fetchone()
        assert stored is not None and stored[0] == monitor_id

    with psycopg.connect(**conn_kwargs) as conn, pytest.raises(psycopg.Error) as orphan:
        insert_run(
            conn,
            actionable_total=1,
            avg_score=None,
            created_at="2026-09-22T09:00:00Z",
            monitor_id=str(uuid4()),
        )
    assert orphan.value.sqlstate == "23503"


def test_the_watchlist_summary_sql_reads_the_series_with_lag_deltas(conn_kwargs: dict[str, str]) -> None:
    seed_run, monitor_id = _upgraded_with_a_saved_watchlist(conn_kwargs)
    with psycopg.connect(**conn_kwargs) as conn:
        insert_run(conn, actionable_total=44, avg_score=72.0, created_at="2026-09-21T09:00:00Z", monitor_id=monitor_id)
        # A failed refresh is not part of the series.
        insert_run(
            conn,
            actionable_total=0,
            avg_score=None,
            created_at="2026-09-21T12:00:00Z",
            status="failed",
            monitor_id=monitor_id,
        )
        latest = insert_run(
            conn, actionable_total=52, avg_score=74.26, created_at="2026-09-22T09:00:00Z", monitor_id=monitor_id
        )
        # A one-off run and another actor's run never join the series.
        insert_run(conn, actionable_total=999, avg_score=99.0, created_at="2026-09-23T09:00:00Z")
        insert_run(
            conn,
            actionable_total=7,
            avg_score=60.0,
            created_at="2026-09-23T10:00:00Z",
            actor="someone.else@example.com",
        )
        conn.execute(
            "UPDATE mip_app.growth_agent_monitors SET last_run_id = %s WHERE monitor_id = %s",
            (latest, monitor_id),
        )
        empty = _insert_monitor_named(conn, "Daily refi brief - TX")

    with psycopg.connect(**conn_kwargs, row_factory=dict_row) as conn:
        rows = conn.execute(
            WATCHLIST_SUMMARY_SQL, {"actor_email": _ACTOR, "limit": 20, "points": 8}
        ).fetchall()
    briefings = {briefing.monitor_id: briefing for briefing in briefings_from_rows(rows)}

    series = briefings[monitor_id]
    assert series.run_count == 3
    assert (series.actionable_total, series.previous_actionable_total, series.actionable_delta) == (52, 44, 8)
    assert series.avg_score_delta == 2.3
    assert series.recent_actionable_totals == [40, 44, 52]
    assert series.previous_run_at == datetime(2026, 9, 21, 9, 0, tzinfo=UTC)
    assert series.last_run_at == datetime(2026, 9, 22, 9, 0, tzinfo=UTC)
    assert briefings[empty].run_count == 0 and briefings[empty].recent_actionable_totals == []
    assert seed_run  # the seed (untagged, pre-series) run is the first point


def test_the_save_and_series_lookups_never_read_another_actors_rows(conn_kwargs: dict[str, str]) -> None:
    # genie-09 part 1 / wow-ai-4: Save as watchlist reads the run and the
    # watchlist key, and a save_monitor run resolves its series, ONLY within
    # the caller's own rows; another actor's run is the same miss as an
    # unknown one (the route's 404). Each statement runs here as shipped.
    seed_run, monitor_id = _upgraded_with_a_saved_watchlist(conn_kwargs)
    other = "someone.else@example.com"
    key = {"workflow_id": "daily_refi_brief", "name": "Daily refi brief - IL"}
    with psycopg.connect(**conn_kwargs, row_factory=dict_row) as conn:
        assert conn.execute(RUN_SELECT_FOR_SAVE_SQL, {"actor_email": other, "run_id": seed_run}).fetchone() is None
        own = conn.execute(RUN_SELECT_FOR_SAVE_SQL, {"actor_email": _ACTOR, "run_id": seed_run}).fetchone()
        assert own is not None
        assert (str(own["run_id"]), own["status"], own["workflow_id"]) == (seed_run, "completed", "daily_refi_brief")
        for sql in (MONITOR_SELECT_BY_KEY_SQL, MONITOR_ID_BY_KEY_SQL):
            assert conn.execute(sql, {**key, "actor_email": other}).fetchone() is None
            mine = conn.execute(sql, {**key, "actor_email": _ACTOR}).fetchone()
            assert mine is not None and str(mine["monitor_id"]) == monitor_id


def _insert_monitor_named(conn: psycopg.Connection[Any], name: str) -> str:
    row = conn.execute(
        "INSERT INTO mip_app.growth_agent_monitors (actor_email, workflow_id, name, cadence, route) "
        "VALUES (%s, 'daily_refi_brief', %s, 'weekly', '/lead-queue') RETURNING monitor_id",
        (_ACTOR, name),
    ).fetchone()
    assert row is not None
    return str(row[0])


def test_the_runs_trigger_contract_is_unchanged(conn_kwargs: dict[str, str]) -> None:
    _upgraded_with_a_saved_watchlist(conn_kwargs)
    with psycopg.connect(**conn_kwargs, row_factory=dict_row) as conn:
        triggers = conn.execute(
            "SELECT tgname, pg_get_triggerdef(oid) AS definition FROM pg_trigger "
            "WHERE tgrelid = 'mip_app.growth_agent_runs'::regclass AND NOT tgisinternal ORDER BY tgname"
        ).fetchall()
    assert [row["tgname"] for row in triggers] == [
        "trg_growth_agent_runs_finalize_only",
        "trg_growth_agent_runs_no_remove",
    ]
    assert "BEFORE UPDATE" in triggers[0]["definition"]
    assert "enforce_audit_event_finalize_only" in triggers[0]["definition"]


def _series_corpus(conn: psycopg.Connection[Any], seed_run: str, monitor_id: str) -> None:
    """Seed / tagged / failed / one-off / other-actor runs, plus runs that match two and three arms."""
    for day in range(10, 22):
        insert_run(
            conn,
            actionable_total=day,
            avg_score=60.0 + day / 10,
            created_at=f"2026-09-{day}T09:00:00Z",
            monitor_id=monitor_id,
        )
    insert_run(
        conn, actionable_total=0, avg_score=None, created_at="2026-09-22T12:00:00Z", status="failed", monitor_id=monitor_id
    )
    # Tagged AND the last run: two arms.
    latest = insert_run(
        conn, actionable_total=52, avg_score=74.26, created_at="2026-09-23T09:00:00Z", monitor_id=monitor_id
    )
    conn.execute(
        "UPDATE mip_app.growth_agent_monitors SET last_run_id = %s WHERE monitor_id = %s", (latest, monitor_id)
    )
    insert_run(conn, actionable_total=999, avg_score=99.0, created_at="2026-09-24T09:00:00Z")
    insert_run(
        conn,
        actionable_total=7,
        avg_score=60.0,
        created_at="2026-09-24T10:00:00Z",
        actor="someone.else@example.com",
        monitor_id=monitor_id,
    )
    # A watchlist whose one tagged run is also its seed and its last run: three arms.
    triple = _insert_monitor_named(conn, "Daily refi brief - TX")
    run = insert_run(conn, actionable_total=12, avg_score=65.0, created_at="2026-09-25T09:00:00Z", monitor_id=triple)
    conn.execute(
        "UPDATE mip_app.growth_agent_monitors SET seed_run_id = %s, last_run_id = %s WHERE monitor_id = %s",
        (run, run, triple),
    )
    _insert_monitor_named(conn, "Daily refi brief - CA")
    assert seed_run


def test_the_three_arm_series_returns_exactly_the_or_join_rows(conn_kwargs: dict[str, str]) -> None:
    seed_run, monitor_id = _upgraded_with_a_saved_watchlist(conn_kwargs)
    with psycopg.connect(**conn_kwargs) as conn:
        _series_corpus(conn, seed_run, monitor_id)
    with psycopg.connect(**conn_kwargs, row_factory=dict_row) as conn:
        for params in (
            {"actor_email": _ACTOR, "limit": 20, "points": 8},
            {"actor_email": _ACTOR, "limit": 20, "points": 30},
            {"actor_email": _ACTOR, "limit": 1, "points": 8},
            {"actor_email": "someone.else@example.com", "limit": 20, "points": 8},
        ):
            rewritten = conn.execute(WATCHLIST_SUMMARY_SQL, params).fetchall()
            oracle = conn.execute(_BASE_WATCHLIST_SUMMARY_SQL, params).fetchall()
            assert rewritten == oracle, params
            assert [list(row) for row in rewritten] == [list(row) for row in oracle]
    assert rewritten == []  # the last params: the other actor has no watchlist of its own
    with psycopg.connect(**conn_kwargs, row_factory=dict_row) as conn:
        rows = conn.execute(WATCHLIST_SUMMARY_SQL, {"actor_email": _ACTOR, "limit": 20, "points": 30}).fetchall()
    counts = {str(row["monitor_id"]): row["run_count"] for row in rows}
    # The seed, twelve tagged runs and the latest (tagged and last): the
    # failed, one-off and other-actor runs never join, and nothing counts twice.
    assert counts[monitor_id] == 14
    assert sorted(counts.values(), key=lambda count: -1 if count is None else count) == [None, 1, 14]


def _positional(sql: str) -> str:
    return sql.replace("%(actor_email)s", "$1").replace("%(limit)s", "$2").replace("%(points)s", "$3")


def _generic_plan(conn: psycopg.Connection[Any], sql: str) -> str:
    conn.execute("SET plan_cache_mode = force_generic_plan")
    conn.execute("SET enable_seqscan = off")
    conn.execute("DEALLOCATE ALL")
    conn.execute(f"PREPARE watchlist_summary(text, int, int) AS {_positional(sql)}")
    plan = conn.execute("EXPLAIN EXECUTE watchlist_summary('lo@example.com', 20, 8)").fetchall()
    return "\n".join(str(row[0]) for row in plan)


def test_each_series_arm_is_an_index_scan_and_none_scans_the_runs_table(conn_kwargs: dict[str, str]) -> None:
    seed_run, monitor_id = _upgraded_with_a_saved_watchlist(conn_kwargs)
    with psycopg.connect(**conn_kwargs, autocommit=True) as conn:
        _series_corpus(conn, seed_run, monitor_id)
        # A ledger-sized table with statistics, three actors, a tenth failed.
        conn.execute(
            "INSERT INTO mip_app.growth_agent_runs (actor_email, workflow_id, workflow_title, status, "
            "actionable_total, actionable_avg_score, route, created_at) "
            "SELECT (ARRAY['lo@example.com', 'a@example.com', 'b@example.com'])[1 + g % 3], 'daily_refi_brief', "
            "'Daily refi brief', CASE WHEN g % 10 = 0 THEN 'failed' ELSE 'completed' END, g % 90, 50 + g % 40, "
            "'/lead-queue', TIMESTAMPTZ '2026-09-01' + g * INTERVAL '1 minute' FROM generate_series(1, 6000) AS g"
        )
        conn.execute("ANALYZE mip_app.growth_agent_runs")
        conn.execute("ANALYZE mip_app.growth_agent_monitors")
        plan = _generic_plan(conn, WATCHLIST_SUMMARY_SQL)
        base_plan = _generic_plan(conn, _BASE_WATCHLIST_SUMMARY_SQL)

    assert "Seq Scan on growth_agent_runs" not in plan, plan
    assert "Index Scan using idx_growth_agent_runs_monitor_created on growth_agent_runs" in plan, plan
    assert plan.count("Index Scan using growth_agent_runs_pkey on growth_agent_runs") == 2, plan
    for arm in (r"\(monitor_id = w_\d+\.monitor_id\)", r"\(run_id = w_\d+\.seed_run_id\)", r"\(run_id = w_\d+\.last_run_id\)"):
        assert re.search(rf"Index Cond: {arm}", plan), (arm, plan)
    # Non-vacuity: the 32e5d382 OR join never plans these three index scans
    # (on PG16 it is one BitmapOr filtered row by row).
    assert "Index Scan using growth_agent_runs_pkey on growth_agent_runs" not in base_plan, base_plan

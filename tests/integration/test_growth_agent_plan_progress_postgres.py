"""Real-PostgreSQL contract of the W5d Growth Agent watchlists block (wow-ai-2, genie-09 part 4).

The schema before the ``-- Growth Agent watchlists (W5d) ---`` block is
applied with a legacy run and watchlist, then the full schema, then a
``rate_trigger_watch`` run and watchlist are inserted, then the full schema
AGAIN through ``lakebase_migrate._run_transaction`` (each apply wrapped in
``contract_as_of``). The second full apply is the one that matters: the
pre-seed DO blocks drop and re-add the workflow_id CHECKs on every apply, so
the CHECKs must be widened in place for the re-add to validate the existing
rate watch rows (a post-seed widening would be re-narrowed first and abort).

Then: an unknown workflow id is still refused; the progress table's CHECKs
refuse a 9-step plan, a 65-character tool, an unknown phase and a step past
the total; the real recorder and reader round-trip running -> gate ->
recording for the caller only; the app role gets SELECT/INSERT/UPDATE and no
DELETE; and a quiet rate watch is due by cadence only under the real DUE SQL.
Skipped unless ``MIP_TEST_POSTGRES_DSN`` names a disposable database.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql as psql
from psycopg.rows import dict_row

from backend.services.growth_agent_ledger_sql import DUE_MONITOR_LIST_ALL_SQL, DUE_MONITOR_LIST_SQL
from backend.services.growth_agent_plan_progress import (
    LakebasePlanProgressRecorder,
    read_plan_progress,
)
from jobs import lakebase_migrate
from tests.fixtures.lakebase_contract_prefix import contract_as_of

pytestmark = pytest.mark.integration

#: PLACEHOLDER: the integrator dates the block at the W5d merge.
GROWTH_WATCHLISTS_VERSION = "2026_10_0X_growth_watchlists"
_MARKER = "-- Growth Agent watchlists (W5d) ---"
_SCHEMA = Path("lakebase/schema.sql").read_text(encoding="utf-8")
_SEED = Path("lakebase/seed_campaigns.sql").read_text(encoding="utf-8")
_ACTOR = "lo@summit.example"
_OTHER = "other@summit.example"
_RATE_CRITERIA = (
    '{"states": [], "rate_trigger": {"series_id": "MORTGAGE30US", "step_bps": -50, "level_pct": 5.8, '
    '"direction": "at_or_below", "base_market_rate_pct": 6.3}, "workflow_id": "rate_trigger_watch"}'
)


class _PgLakebase:
    """The LakebaseClient surface the progress service uses, one real connection per call."""

    def __init__(self, conn_kwargs: dict[str, str]) -> None:
        self._kwargs = conn_kwargs
        self.in_transaction = False

    def execute(self, statement: str, params: dict[str, Any] | None = None) -> None:
        assert not self.in_transaction, "progress is never written inside another transaction"
        with psycopg.connect(**self._kwargs) as conn:
            conn.execute(statement, params)

    def fetchone(self, statement: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        with psycopg.connect(**self._kwargs, row_factory=dict_row) as conn:
            return conn.execute(statement, params).fetchone()

    @contextmanager
    def transaction(self) -> Iterator[psycopg.Connection[dict[str, Any]]]:
        self.in_transaction = True
        try:
            with psycopg.connect(**self._kwargs, row_factory=dict_row) as conn:
                yield conn
        finally:
            self.in_transaction = False


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


def _insert_run(conn: psycopg.Connection[Any], workflow_id: str, *, actor: str = _ACTOR) -> str:
    row = conn.execute(
        "INSERT INTO mip_app.growth_agent_runs (actor_email, workflow_id, workflow_title, actionable_total, route) "
        "VALUES (%s, %s, 'title', 3, '/') RETURNING run_id",
        (actor, workflow_id),
    ).fetchone()
    assert row is not None
    return str(row[0])


def _insert_monitor(
    conn: psycopg.Connection[Any], workflow_id: str, *, last_run_id: str | None, name: str, criteria: str = "{}"
) -> str:
    row = conn.execute(
        "INSERT INTO mip_app.growth_agent_monitors (actor_email, workflow_id, name, cadence, criteria, route, "
        "last_run_id) VALUES (%s, %s, %s, 'daily', %s::jsonb, '/', %s) RETURNING monitor_id",
        (_ACTOR, workflow_id, name, criteria, last_run_id),
    ).fetchone()
    assert row is not None
    return str(row[0])


def _migrated_with_rate_watch_rows(conn_kwargs: dict[str, str]) -> tuple[str, str]:
    _apply(conn_kwargs, _SCHEMA[: _SCHEMA.index(_MARKER)])
    with psycopg.connect(**conn_kwargs) as conn:
        legacy_run = _insert_run(conn, "daily_refi_brief")
        _insert_monitor(conn, "daily_refi_brief", last_run_id=legacy_run, name="Daily Refi Opportunity Brief")
    _apply(conn_kwargs, _SCHEMA)
    with psycopg.connect(**conn_kwargs) as conn:
        rate_run = _insert_run(conn, "rate_trigger_watch")
        rate_monitor = _insert_monitor(
            conn, "rate_trigger_watch", last_run_id=rate_run,
            name="Rate watch - 30-year par at or below 5.80 percent", criteria=_RATE_CRITERIA,
        )
    # The re-apply: the pre-seed DO blocks re-add both workflow_id CHECKs over
    # these rows, and the executable-hook preflight inventories the new CHECKs.
    _apply(conn_kwargs, _SCHEMA)
    return rate_run, rate_monitor


def test_the_block_reapplies_over_rate_watch_rows_and_is_recorded_once(conn_kwargs: dict[str, str]) -> None:
    _migrated_with_rate_watch_rows(conn_kwargs)
    with psycopg.connect(**conn_kwargs) as conn:
        versions = conn.execute(
            "SELECT count(*) FROM mip_app.schema_migrations WHERE version = %s", (GROWTH_WATCHLISTS_VERSION,)
        ).fetchone()
        checks = conn.execute(
            "SELECT count(*) FROM pg_constraint "
            "WHERE conrelid = 'mip_app.growth_agent_plan_progress'::regclass AND contype = 'c'"
        ).fetchone()
        for table in ("growth_agent_runs", "growth_agent_monitors"):
            definition = conn.execute(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = %s::regclass "
                "AND conname = %s",
                (f"mip_app.{table}", f"ck_{table}_workflow_id"),
            ).fetchone()
            assert definition is not None and "rate_trigger_watch" in definition[0], table
    assert versions == (1,)
    assert checks == (5,), "applied twice, still one CHECK each"


@pytest.mark.parametrize("table", ["growth_agent_runs", "growth_agent_monitors"])
def test_an_unknown_workflow_id_is_still_refused(conn_kwargs: dict[str, str], table: str) -> None:
    _migrated_with_rate_watch_rows(conn_kwargs)
    with pytest.raises(psycopg.errors.CheckViolation), psycopg.connect(**conn_kwargs) as conn:
        if table == "growth_agent_runs":
            _insert_run(conn, "rate_forecast_watch")
        else:
            _insert_monitor(conn, "rate_forecast_watch", last_run_id=None, name="x")


@pytest.mark.parametrize(
    "overrides",
    [
        {"step_total": 9, "step_index": 1},
        {"step_tool": "t" * 65},
        {"phase": "done"},
        {"step_index": 4, "step_total": 3},
        {"step_index": -1},
        {"request_id": "r" * 129},
    ],
    ids=["nine-steps", "long-tool", "unknown-phase", "past-total", "negative", "long-request-id"],
)
def test_the_progress_checks_refuse_malformed_rows(conn_kwargs: dict[str, str], overrides: dict[str, Any]) -> None:
    _migrated_with_rate_watch_rows(conn_kwargs)
    row: dict[str, Any] = {
        "actor_email": _ACTOR, "request_id": "r-1", "step_index": 1, "step_total": 3,
        "step_tool": "fn_build_cohort", "phase": "running", **overrides,
    }
    with pytest.raises(psycopg.errors.CheckViolation), psycopg.connect(**conn_kwargs) as conn:
        conn.execute(
            "INSERT INTO mip_app.growth_agent_plan_progress (actor_email, request_id, step_index, step_total, "
            "step_tool, phase) VALUES (%(actor_email)s, %(request_id)s, %(step_index)s, %(step_total)s, "
            "%(step_tool)s, %(phase)s)",
            row,
        )


def test_the_recorder_and_reader_round_trip_for_the_caller_only(conn_kwargs: dict[str, str]) -> None:
    _migrated_with_rate_watch_rows(conn_kwargs)
    lakebase = _PgLakebase(conn_kwargs)
    request_id = str(uuid4())
    recorder = LakebasePlanProgressRecorder(lakebase, actor=_ACTOR, request_id=request_id)  # type: ignore[arg-type]
    seen = []
    for step_index, phase in ((1, "running"), (2, "running"), (3, "gate"), (2, "recording")):
        recorder.record(step_index, 3, "fn_lead_queue_url" if phase == "gate" else "fn_build_cohort", phase)  # type: ignore[arg-type]
        progress = read_plan_progress(lakebase, actor=_ACTOR, request_id=request_id)  # type: ignore[arg-type]
        seen.append((progress.state, progress.step_index, progress.step_total))
    assert seen == [("running", 1, 3), ("running", 2, 3), ("gate", 3, 3), ("recording", 2, 3)]
    other = read_plan_progress(lakebase, actor=_OTHER, request_id=request_id)  # type: ignore[arg-type]
    assert other.state == "not_started"
    with psycopg.connect(**conn_kwargs) as conn:
        assert conn.execute("SELECT count(*) FROM mip_app.growth_agent_plan_progress").fetchone() == (1,)


def test_the_app_role_reads_inserts_and_updates_progress_and_never_deletes(
    conn_kwargs: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _migrated_with_rate_watch_rows(conn_kwargs)
    suffix = uuid4().hex[:12]
    app_role, verifier_role = f"mip_test_app_{suffix}", f"mip_test_verifier_{suffix}"
    with psycopg.connect(**conn_kwargs, autocommit=True) as conn:
        for role in (app_role, verifier_role):
            conn.execute(psql.SQL("CREATE ROLE {} LOGIN NOREPLICATION INHERIT").format(psql.Identifier(role)))
    monkeypatch.setattr(lakebase_migrate, "_resolve_app_role", lambda: app_role)
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("MIP_AI_GATEWAY_VERIFIER_CLIENT_ID", verifier_role)
    try:
        lakebase_migrate._apply_app_role_grants(
            conn_kwargs,
            role_wait_timeout_s=0,
            role_wait_interval_s=1,
            allow_absent_managed_event_triggers=True,
            allow_absent_provider_schema=True,
        )
        with psycopg.connect(**conn_kwargs) as conn:
            granted = conn.execute(
                "SELECT has_table_privilege(%(role)s, %(table)s, 'SELECT'), "
                "has_table_privilege(%(role)s, %(table)s, 'INSERT'), "
                "has_table_privilege(%(role)s, %(table)s, 'UPDATE'), "
                "has_table_privilege(%(role)s, %(table)s, 'DELETE'), "
                "has_table_privilege(%(role)s, %(table)s, 'TRUNCATE')",
                {"role": app_role, "table": "mip_app.growth_agent_plan_progress"},
            ).fetchone()
        assert granted == (True, True, True, False, False)
    finally:
        with psycopg.connect(**conn_kwargs, autocommit=True) as conn:
            for role in (app_role, verifier_role):
                conn.execute(psql.SQL("DROP OWNED BY {}").format(psql.Identifier(role)))
                conn.execute(psql.SQL("DROP ROLE {}").format(psql.Identifier(role)))


def test_a_quiet_rate_watch_is_due_by_cadence_only_under_the_real_due_sql(conn_kwargs: dict[str, str]) -> None:
    """A rate watch drafts only when its level is reached, so a quiet one never
    has drafts; without the exclusion it would be due on every scheduler pass."""

    _rate_run, rate_monitor = _migrated_with_rate_watch_rows(conn_kwargs)
    with psycopg.connect(**conn_kwargs) as conn:
        custom_run = _insert_run(conn, "custom_segment_watch")
        custom_monitor = _insert_monitor(conn, "custom_segment_watch", last_run_id=custom_run, name="Custom")
        conn.execute("UPDATE mip_app.growth_agent_monitors SET updated_at = now()")
    params = {"actor_email": _ACTOR, "limit": 50, "channels": ["slack", "teams"]}
    with psycopg.connect(**conn_kwargs, row_factory=dict_row) as conn:
        for statement in (DUE_MONITOR_LIST_SQL, DUE_MONITOR_LIST_ALL_SQL):
            due = {str(row["monitor_id"]) for row in conn.execute(statement, params).fetchall()}
            assert custom_monitor in due, "a watchlist missing its drafts is due again"
            assert rate_monitor not in due, "a quiet rate watch waits for its cadence"
        conn.execute(
            "UPDATE mip_app.growth_agent_monitors SET updated_at = now() - INTERVAL '2 days' WHERE monitor_id = %s",
            (rate_monitor,),
        )
        due = {str(row["monitor_id"]) for row in conn.execute(DUE_MONITOR_LIST_SQL, params).fetchall()}
        assert rate_monitor in due, "by cadence it is due"

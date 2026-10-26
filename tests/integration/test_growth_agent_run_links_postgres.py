"""Real-PostgreSQL contract of the W5e Growth Agent watchlist run links block (wow-ai-4).

The schema before the ``-- Growth Agent watchlist run links (W5e) ---`` block
is applied with legacy watchlists, then the full schema, then a run is saved
into an existing watchlist (the shipped RUN_LINK_INSERT_SQL), then the full
schema AGAIN through ``lakebase_migrate._run_transaction`` (each apply wrapped
in ``contract_as_of``; the executable-hook preflight inventories the catalog,
so only a re-run can fail on a new expression). Pinned: the table, its keys
and its one CHECK; the backfill links exactly the watchlist whose last run is
the actor's own, untagged and not its seed, once (a re-apply is a no-op and
leaves every other row alone); no run row is touched; the ledger row is
recorded once; the app role gets SELECT and INSERT and never UPDATE or
DELETE. Skipped unless ``MIP_TEST_POSTGRES_DSN`` names a disposable database.
Merge gate (W5e), with the four-arm EXPLAIN in test_growth_agent_runs_postgres.py.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql as psql

from backend.services.growth_agent_run_watchlist import RUN_LINK_INSERT_SQL
from jobs import lakebase_migrate
from tests.fixtures.lakebase_contract_prefix import contract_as_of

pytestmark = pytest.mark.integration

#: Dated at the W5e merge (the landing date), as in lakebase/schema.sql.
RUN_LINKS_VERSION = "2026_10_15_growth_watchlist_run_links"
_MARKER = "-- Growth Agent watchlist run links (W5e) ---"
_SCHEMA = Path("lakebase/schema.sql").read_text(encoding="utf-8")
_SEED = Path("lakebase/seed_campaigns.sql").read_text(encoding="utf-8")
_ACTOR = "lo@summit.example"
_OTHER = "other@summit.example"


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


def _run(conn: psycopg.Connection[Any], *, actor: str = _ACTOR, monitor_id: str | None = None) -> str:
    row = conn.execute(
        "INSERT INTO mip_app.growth_agent_runs (actor_email, workflow_id, workflow_title, actionable_total, route, "
        "monitor_id) VALUES (%s, 'daily_refi_brief', 'Daily refi brief', 3, '/', %s) RETURNING run_id",
        (actor, monitor_id),
    ).fetchone()
    assert row is not None
    return str(row[0])


def _monitor(conn: psycopg.Connection[Any], name: str, *, seed_run_id: str, last_run_id: str) -> str:
    row = conn.execute(
        "INSERT INTO mip_app.growth_agent_monitors (actor_email, workflow_id, name, cadence, route, seed_run_id, "
        "last_run_id) VALUES (%s, 'daily_refi_brief', %s, 'daily', '/', %s, %s) RETURNING monitor_id",
        (_ACTOR, name, seed_run_id, last_run_id),
    ).fetchone()
    assert row is not None
    return str(row[0])


def _set_last_run(conn: psycopg.Connection[Any], monitor_id: str, run_id: str) -> None:
    conn.execute("UPDATE mip_app.growth_agent_monitors SET last_run_id = %s WHERE monitor_id = %s", (run_id, monitor_id))


def _links(conn: psycopg.Connection[Any]) -> list[tuple[str, str, str]]:
    rows = conn.execute(
        "SELECT monitor_id::text, run_id::text, actor_email FROM mip_app.growth_agent_watchlist_run_links "
        "ORDER BY linked_at, monitor_id, run_id"
    ).fetchall()
    return [(str(a), str(b), str(c)) for a, b, c in rows]


def _migrated(conn_kwargs: dict[str, str]) -> dict[str, str]:
    """Legacy watchlists before the block, the full schema, a save into an existing name, the full schema again."""

    _apply(conn_kwargs, _SCHEMA[: _SCHEMA.index(_MARKER)])
    ids: dict[str, str] = {}
    with psycopg.connect(**conn_kwargs) as conn:
        seed = _run(conn)
        ids["saved_into"] = _monitor(conn, "Saved into an existing name", seed_run_id=seed, last_run_id=seed)
        ids["legacy_run"] = _run(conn)  # untagged, not the seed: the series gap
        _set_last_run(conn, ids["saved_into"], ids["legacy_run"])
        # Controls the backfill must NOT link.
        tagged_seed = _run(conn)
        ids["tagged"] = _monitor(conn, "Last run tagged", seed_run_id=tagged_seed, last_run_id=tagged_seed)
        _set_last_run(conn, ids["tagged"], _run(conn, monitor_id=ids["tagged"]))
        only_seed = _run(conn)
        ids["seed_only"] = _monitor(conn, "Last run is the seed", seed_run_id=only_seed, last_run_id=only_seed)
        own_seed = _run(conn)
        ids["foreign_last"] = _monitor(conn, "Last run is another actor's", seed_run_id=own_seed, last_run_id=own_seed)
        _set_last_run(conn, ids["foreign_last"], _run(conn, actor=_OTHER))
        ids["runs_before"] = str(conn.execute("SELECT count(*) FROM mip_app.growth_agent_runs").fetchone()[0])  # type: ignore[index]
    _apply(conn_kwargs, _SCHEMA)
    with psycopg.connect(**conn_kwargs) as conn:
        ids["saved_run"] = _run(conn)
        _set_last_run(conn, ids["seed_only"], ids["saved_run"])
        conn.execute(
            RUN_LINK_INSERT_SQL, {"monitor_id": ids["seed_only"], "run_id": ids["saved_run"], "actor_email": _ACTOR}
        )
    # The re-apply: the backfill runs again over the linked rows and must add nothing.
    _apply(conn_kwargs, _SCHEMA)
    return ids


def test_the_block_applies_twice_and_backfills_only_the_untagged_non_seed_last_run(conn_kwargs: dict[str, str]) -> None:
    ids = _migrated(conn_kwargs)
    with psycopg.connect(**conn_kwargs) as conn:
        assert _links(conn) == [
            (ids["saved_into"], ids["legacy_run"], _ACTOR),
            (ids["seed_only"], ids["saved_run"], _ACTOR),
        ]
        versions = conn.execute(
            "SELECT count(*) FROM mip_app.schema_migrations WHERE version = %s", (RUN_LINKS_VERSION,)
        ).fetchone()
        assert versions == (1,)
        # No run row was touched: the runs are what the setup wrote, plus the one saved run.
        runs = conn.execute("SELECT count(*) FROM mip_app.growth_agent_runs").fetchone()
        assert runs == (int(ids["runs_before"]) + 1,)
        untagged = conn.execute(
            "SELECT monitor_id FROM mip_app.growth_agent_runs WHERE run_id = %s", (ids["legacy_run"],)
        ).fetchone()
        assert untagged == (None,)


def test_the_table_keys_and_check(conn_kwargs: dict[str, str]) -> None:
    ids = _migrated(conn_kwargs)
    with psycopg.connect(**conn_kwargs) as conn:
        constraints = dict(
            conn.execute(
                "SELECT contype, string_agg(pg_get_constraintdef(oid), ' | ' ORDER BY conname) FROM pg_constraint "
                "WHERE conrelid = 'mip_app.growth_agent_watchlist_run_links'::regclass GROUP BY contype"
            ).fetchall()
        )
        assert constraints["p"] == "PRIMARY KEY (monitor_id, run_id)"
        assert "FOREIGN KEY (monitor_id) REFERENCES mip_app.growth_agent_monitors(monitor_id)" in constraints["f"]
        assert "FOREIGN KEY (run_id) REFERENCES mip_app.growth_agent_runs(run_id)" in constraints["f"]
        assert constraints["c"] == "CHECK ((length(actor_email) <= 320))"
        triggers = conn.execute(
            "SELECT count(*) FROM pg_trigger WHERE tgrelid = 'mip_app.growth_agent_watchlist_run_links'::regclass "
            "AND NOT tgisinternal"
        ).fetchone()
        assert triggers == (0,)
        # A duplicate pair is a no-op, an unknown run is refused by its key.
        conn.execute(RUN_LINK_INSERT_SQL, {"monitor_id": ids["saved_into"], "run_id": ids["legacy_run"], "actor_email": _ACTOR})
        assert len(_links(conn)) == 2
    with pytest.raises(psycopg.errors.ForeignKeyViolation), psycopg.connect(**conn_kwargs) as conn:
        conn.execute(RUN_LINK_INSERT_SQL, {"monitor_id": ids["saved_into"], "run_id": str(uuid4()), "actor_email": _ACTOR})


def test_the_app_role_reads_and_inserts_links_and_never_updates_or_deletes(
    conn_kwargs: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _migrated(conn_kwargs)
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
                {"role": app_role, "table": "mip_app.growth_agent_watchlist_run_links"},
            ).fetchone()
        assert granted == (True, True, False, False, False)
    finally:
        with psycopg.connect(**conn_kwargs, autocommit=True) as conn:
            for role in (app_role, verifier_role):
                conn.execute(psql.SQL("DROP OWNED BY {}").format(psql.Identifier(role)))
                conn.execute(psql.SQL("DROP ROLE {}").format(psql.Identifier(role)))

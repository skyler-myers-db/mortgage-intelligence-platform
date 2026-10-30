"""Real-Postgres contract of ``lakebase_migrate --mode plan|verify`` and the digest row.

Applies the governed schema the way ``--mode apply`` does (the four texts plus
the digest row, then the grants) and drives the read-only modes against the
result. Every statement plan and verify send is recorded through a proxy over
the real psycopg cursor and must be a read; the server must also reject a
write inside the session. Owner-level drift that only apply's replay of
schema.sql repairs must turn a noop plan into apply. Vanilla PostgreSQL has neither the Databricks
provider schema nor the managed event triggers, so the suite uses the same
explicit seams as the other real-Postgres suites. Skipped unless
``MIP_TEST_POSTGRES_DSN`` names a disposable database.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql as psql

from jobs import lakebase_migrate
from jobs import lakebase_migration_read_only as read_only
from jobs.lakebase_migration_inputs import (
    parse_schema_migrations,
    render_schema_inputs,
    schema_input_components,
    schema_input_digest,
)
from jobs.lakebase_migration_ledger import schema_input_digest_sql
from jobs.lakebase_migration_plan import CheckContext, run_plan, run_verify
from jobs.lakebase_migration_read_only import read_only_session

pytestmark = pytest.mark.integration

_DIGEST_VERSION = "2026_10_29_deployment_schema_digest"
_READ_LEAD_RE = re.compile(r"\s*(?:SELECT|SHOW|WITH)\b", re.IGNORECASE)


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


@pytest.fixture
def app_role(conn_kwargs: dict[str, str]) -> str:
    role = f"mip_test_plan_app_{uuid4().hex[:12]}"
    with psycopg.connect(**conn_kwargs, autocommit=True) as conn:
        conn.execute(
            psql.SQL("CREATE ROLE {} LOGIN NOREPLICATION INHERIT").format(psql.Identifier(role))
        )
    return role


def _inputs() -> tuple[str, tuple[str, str, str, str]]:
    return render_schema_inputs(
        Path("."), lender_name="Summit Mortgage", lender_nmls_id="", tenant_id=""
    )


def _apply(conn_kwargs: dict[str, str], app_role: str) -> None:
    """What ``main`` does in apply mode, with the vanilla-PostgreSQL seams."""

    _schema_sql, sql_texts = _inputs()
    lakebase_migrate._run_transaction(
        (*sql_texts, schema_input_digest_sql(sql_texts)),
        conn_kwargs,
        app_role=app_role,
        verify_outreach_integrity=True,
        allow_absent_managed_event_triggers=True,
        allow_absent_provider_schema=True,
    )
    lakebase_migrate._apply_app_role_grants(
        conn_kwargs,
        resolved_roles=(app_role, None),
        role_wait_timeout_s=0,
        role_wait_interval_s=1,
        allow_absent_managed_event_triggers=True,
        allow_absent_provider_schema=True,
    )


class _RecordingCursor:
    def __init__(self, cursor: Any, recorded: list[str]) -> None:
        self._cursor = cursor
        self._recorded = recorded

    def __enter__(self) -> _RecordingCursor:
        self._cursor.__enter__()
        return self

    def __exit__(self, *args: Any) -> None:
        self._cursor.__exit__(*args)

    def execute(self, statement: str, params: Any = None, *, prepare: bool | None = None) -> Any:
        assert isinstance(statement, str) and _READ_LEAD_RE.match(statement), statement[:80]
        assert prepare is True, statement[:80]
        self._recorded.append(statement)
        return self._cursor.execute(statement, params, prepare=prepare)

    def fetchall(self) -> list[Any]:
        return self._cursor.fetchall()

    def fetchone(self) -> Any:
        return self._cursor.fetchone()


class _RecordingConnection:
    def __init__(self, conn: Any, recorded: list[str]) -> None:
        object.__setattr__(self, "_conn", conn)
        object.__setattr__(self, "_recorded", recorded)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._conn, name, value)

    def cursor(self) -> _RecordingCursor:
        return _RecordingCursor(self._conn.cursor(), self._recorded)


def _read_only(
    monkeypatch: pytest.MonkeyPatch,
    conn_kwargs: dict[str, str],
    app_role: str,
    *,
    mode: str,
) -> tuple[dict[str, Any], list[str]]:
    recorded: list[str] = []
    real_connect = psycopg.connect
    ctx = CheckContext(
        app_role=app_role,
        verifier_role=None,
        allow_absent_managed_event_triggers=True,
        allow_absent_provider_schema=True,
    )
    with monkeypatch.context() as patch:
        patch.setattr(
            psycopg,
            "connect",
            lambda **kwargs: _RecordingConnection(real_connect(**kwargs), recorded),
        )
        if mode == "verify":
            report = run_verify(conn_kwargs, ctx)
        else:
            schema_sql, sql_texts = _inputs()
            report = run_plan(
                conn_kwargs,
                ctx,
                migrations=parse_schema_migrations(schema_sql),
                repo_digest=schema_input_digest(sql_texts),
                repo_components=schema_input_components(sql_texts),
            )
    assert recorded[0] == "SHOW transaction_read_only"
    return report, recorded


def _superuser(conn_kwargs: dict[str, str], statement: str) -> None:
    with psycopg.connect(**conn_kwargs, autocommit=True) as conn:
        conn.execute(statement)


def test_plan_on_an_empty_database_plans_every_version(
    monkeypatch: pytest.MonkeyPatch,
    conn_kwargs: dict[str, str],
    app_role: str,
) -> None:
    report, _recorded = _read_only(monkeypatch, conn_kwargs, app_role, mode="plan")

    declared = parse_schema_migrations(_inputs()[0])
    assert report["decision"] == "apply"
    assert report["refusals"] == []
    assert report["executor_missing"] == []
    assert report["migrations"]["pending"] == [migration.version for migration in declared]
    assert report["digest"] == "absent"
    assert report["verify"]["failed_check"] == "schema_present"


def test_after_apply_plan_is_noop_and_verify_passes_with_reads_only(
    monkeypatch: pytest.MonkeyPatch,
    conn_kwargs: dict[str, str],
    app_role: str,
) -> None:
    _apply(conn_kwargs, app_role)
    # The re-run proves the digest table's CHECKs pass the executable-hook gate.
    _apply(conn_kwargs, app_role)

    plan, plan_statements = _read_only(monkeypatch, conn_kwargs, app_role, mode="plan")
    verify, verify_statements = _read_only(monkeypatch, conn_kwargs, app_role, mode="verify")

    assert plan["decision"] == "noop", plan
    assert plan["blue_restorable"] is True
    assert plan["digest"] == "equal"
    # Apply computed the fingerprint under schema.sql's search_path; plan
    # recomputed it in a fresh session under the default one.
    assert plan["catalog"] == "equal"
    assert verify["passed"] is True, verify
    # Both sessions really ran the catalog reads, not an early exit.
    assert len(plan_statements) >= 25 and len(verify_statements) >= 20
    with psycopg.connect(**conn_kwargs) as conn:
        rows = conn.execute(
            "SELECT scope, input_digest, grant_matrix_digest FROM mip_app.deployment_schema_digest"
        ).fetchall()
        app_select = conn.execute(
            "SELECT has_table_privilege(%s, 'mip_app.deployment_schema_digest', 'SELECT')",
            (app_role,),
        ).fetchone()
    sql_texts = _inputs()[1]
    assert rows == [
        (
            "lakebase_schema",
            schema_input_digest(sql_texts),
            schema_input_components(sql_texts)["grant_matrix"],
        )
    ]
    assert app_select == (False,)


def test_plan_sees_a_pending_version_then_a_changed_digest(
    monkeypatch: pytest.MonkeyPatch,
    conn_kwargs: dict[str, str],
    app_role: str,
) -> None:
    _apply(conn_kwargs, app_role)
    _superuser(
        conn_kwargs,
        f"DELETE FROM mip_app.schema_migrations WHERE version = '{_DIGEST_VERSION}'",
    )

    pending, _recorded = _read_only(monkeypatch, conn_kwargs, app_role, mode="plan")

    assert pending["decision"] == "apply"
    assert pending["migrations"]["pending"] == [_DIGEST_VERSION]
    assert pending["migrations"]["pending_contract"] == []
    assert pending["blue_restorable"] is True

    _apply(conn_kwargs, app_role)
    _superuser(
        conn_kwargs,
        "UPDATE mip_app.deployment_schema_digest SET input_digest = repeat('0', 64)",
    )

    differs, _recorded = _read_only(monkeypatch, conn_kwargs, app_role, mode="plan")

    assert (differs["decision"], differs["digest"]) == ("apply", "differs")
    assert differs["blue_restorable"] is False


_FUNCTION = "mip_app.prevent_action_audit_mutation()"


def _same_shape_function_body_rewrite(conn_kwargs: dict[str, str]) -> str:
    """CREATE OR REPLACE the audit guard with a body that lets every mutation through."""

    with psycopg.connect(**conn_kwargs, autocommit=True) as conn:
        row = conn.execute(f"SELECT pg_get_functiondef('{_FUNCTION}'::regprocedure)").fetchone()
    assert row is not None
    head, _as, rest = str(row[0]).partition("AS $function$")
    _body, _end, tail = rest.partition("$function$")
    return f"{head}AS $function$\nBEGIN\n    RETURN COALESCE(NEW, OLD);\nEND;\n$function${tail}"


_DRIFT = {
    "function_body": _same_shape_function_body_rewrite,
    "dropped_check": lambda _kwargs: (
        "ALTER TABLE mip_app.campaigns DROP CONSTRAINT campaigns_criteria_reviewed_shape_chk"
    ),
    "dropped_unique_index": lambda _kwargs: "DROP INDEX mip_app.idx_activation_outbox_request_id",
    "deleted_seed_rows": lambda _kwargs: "DELETE FROM mip_app.activation_destinations",
}


@pytest.mark.parametrize("drift", sorted(_DRIFT))
def test_drift_only_apply_repairs_turns_noop_into_apply(
    monkeypatch: pytest.MonkeyPatch,
    conn_kwargs: dict[str, str],
    app_role: str,
    drift: str,
) -> None:
    _apply(conn_kwargs, app_role)
    _superuser(conn_kwargs, _DRIFT[drift](conn_kwargs))

    drifted, _recorded = _read_only(monkeypatch, conn_kwargs, app_role, mode="plan")
    verify, _recorded = _read_only(monkeypatch, conn_kwargs, app_role, mode="verify")

    # Inputs, ledger and every postflight still agree: only the catalog
    # fingerprint sees the drift.
    assert (drifted["digest"], drifted["migrations"]["pending"]) == ("equal", [])
    assert verify["passed"] is True
    assert (drifted["decision"], drifted["catalog"]) == ("apply", "differs"), drifted
    assert drifted["blue_restorable"] is False

    _apply(conn_kwargs, app_role)
    repaired, _recorded = _read_only(monkeypatch, conn_kwargs, app_role, mode="plan")

    assert (repaired["decision"], repaired["catalog"]) == ("noop", "equal"), repaired


def test_plan_names_an_object_the_executor_does_not_own(
    monkeypatch: pytest.MonkeyPatch,
    conn_kwargs: dict[str, str],
    app_role: str,
) -> None:
    _apply(conn_kwargs, app_role)
    other_owner = f"mip_test_plan_owner_{uuid4().hex[:12]}"
    _superuser(conn_kwargs, f'CREATE ROLE "{other_owner}" NOLOGIN')
    _superuser(conn_kwargs, f'ALTER TABLE mip_app.saved_views OWNER TO "{other_owner}"')

    report, _recorded = _read_only(monkeypatch, conn_kwargs, app_role, mode="plan")

    assert report["decision"] == "refuse"
    assert "executor_ownership" in report["refusals"]
    assert "relation:saved_views" in report["executor_missing"]
    assert other_owner not in str(report)


def test_the_server_rejects_a_write_inside_the_read_only_session(
    conn_kwargs: dict[str, str],
) -> None:
    with (
        pytest.raises(psycopg.errors.ReadOnlySqlTransaction),
        read_only_session(conn_kwargs) as cur,
    ):
        # Bypass the client guard on purpose: the server is the second layer.
        cur._cursor.execute("CREATE TABLE mip_app_read_only_probe (value integer)")

    with psycopg.connect(**conn_kwargs) as conn:
        assert conn.execute("SELECT to_regclass('mip_app_read_only_probe')").fetchone() == (None,)


_SMUGGLED_WRITE = (
    "SELECT '--'; COMMIT; CREATE TABLE public.mip_app_read_only_smuggle (value integer)"
)


def test_one_text_cannot_end_the_read_only_transaction_and_write(
    monkeypatch: pytest.MonkeyPatch,
    conn_kwargs: dict[str, str],
) -> None:
    with psycopg.connect(**conn_kwargs, autocommit=True) as conn:
        conn.execute("DROP TABLE IF EXISTS public.mip_app_read_only_smuggle")
    # The client guard refuses the text outright.
    with (
        pytest.raises(read_only.ReadOnlyStatementRefused, match="multiple statements"),
        read_only_session(conn_kwargs) as cur,
    ):
        cur.execute(_SMUGGLED_WRITE)
    # With the guard switched off, the server still parses one command only:
    # the simple-query protocol would have committed and created the table.
    monkeypatch.setattr(read_only, "assert_read_statement", lambda _statement: None)
    with (
        pytest.raises(psycopg.errors.SyntaxError, match="multiple commands"),
        read_only_session(conn_kwargs) as cur,
    ):
        cur.execute(_SMUGGLED_WRITE)

    with psycopg.connect(**conn_kwargs) as conn:
        created = conn.execute("SELECT to_regclass('public.mip_app_read_only_smuggle')").fetchone()
    assert created == (None,)

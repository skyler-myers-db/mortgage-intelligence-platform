"""Failure-atomic Lakebase schema and seed transaction."""

from __future__ import annotations

from collections.abc import Callable

from jobs.lakebase_migration_integrity import _run_outreach_integrity_probe
from jobs.lakebase_migration_schema_hooks import (
    _postflight_event_trigger_inventory,
    _postflight_trigger_inventory,
    _preflight_executable_schema_hooks,
    _quarantine_existing_reviewed_triggers,
    _quarantine_reviewed_constraints,
)


def _run_transaction(
    sql_texts: tuple[str, ...],
    conn_kwargs: dict,
    *,
    app_role: str,
    verify_outreach_integrity: bool = False,
    _run_integrity_probe_fn: Callable[..., None] = _run_outreach_integrity_probe,
) -> None:
    import psycopg  # local import so `--help` still works without the wheel

    # Schema and seed are one deployment unit. PostgreSQL DDL is transactional,
    # so a seed/constraint failure must roll back the schema changes instead of
    # leaving a partially migrated database for the still-running app.
    conn = psycopg.connect(**conn_kwargs, autocommit=False)
    try:
        with conn.cursor() as cur:
            # Existing trigger code executes during the DDL/backfill below,
            # even when the migration identity has revoked direct EXECUTE on
            # its function. Reject every catalog-shape mismatch first. Then
            # lock each affected table and transactionally remove only the
            # reviewed triggers proven to exist, so a same-shape malicious
            # function-body rewrite cannot run before schema.sql replaces the
            # function and recreates its trigger. A clean first install may
            # legitimately be missing reviewed triggers; the exact postflight
            # below proves all of them exist before commit.
            reviewed_constraints = _preflight_executable_schema_hooks(cur)
            _postflight_event_trigger_inventory(
                cur,
                app_role,
                principal_label="schema preflight",
            )
            existing_reviewed_triggers = _postflight_trigger_inventory(
                cur,
                app_role,
                principal_label="schema preflight",
                allow_missing_reviewed=True,
            )
            _quarantine_existing_reviewed_triggers(cur, existing_reviewed_triggers)
            _quarantine_reviewed_constraints(cur, reviewed_constraints)
            for sql_text in sql_texts:
                cur.execute(sql_text)
            if verify_outreach_integrity:
                _run_integrity_probe_fn(conn_kwargs, connection=conn)
            _postflight_trigger_inventory(
                cur,
                app_role,
                principal_label="schema postflight",
            )
            _postflight_event_trigger_inventory(
                cur,
                app_role,
                principal_label="schema postflight",
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

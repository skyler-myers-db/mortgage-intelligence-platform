"""Incrementally sync Lakebase lifecycle state into the configured gold catalog.

Runs as a Databricks Jobs Spark-Python task (``mip_sync_lifecycle_state``
in ``databricks.yml``). Reads the authoritative approval + outreach state
from Lakebase (``mip_app.approvals`` + ``mip_app.call_dispositions``) and
writes a keyed-by-borrower_id mirror into
``<catalog>.gold.borrower_lifecycle_state`` so UC metric views can expose
``approval_rate`` and ``outreach_rate`` without a runtime federated join.

Authority model (non-negotiable):
    Lakebase is authoritative for approval + outreach state. This job is a
    one-directional mirror. It NEVER writes back to Lakebase.

Run cadence:
    Event-triggered by the app as a durable retry after a warehouse-path
    failure, or runnable on demand via
    ``databricks bundle run mip_sync_lifecycle_state``. The write is a sparse
    Delta MERGE: only borrowers represented in Lakebase are candidates and
    unchanged rows are not updated. Borrowers with no lifecycle event rely on
    the metric views' LEFT JOIN + COALESCE defaults.

Auth model (identical to jobs/lakebase_migrate.py):
    On Databricks the task runs under the workspace identity. We fetch a
    short-lived Postgres credential via
    ``POST /api/2.0/database/credentials`` and use the identity's
    ``user_name`` as the Postgres user. ``LAKEBASE_*`` env-var overrides
    are honored for local development.

Exit codes:
    0 -- sync complete.
    2 -- psycopg / Postgres error.
    3 -- SDK / auth error resolving connection parameters.
    4 -- Spark session not available (not running on Databricks or no
         session bound). The DDL in sql/ddl/003_gold_tables.sql §7 pre-creates
         the empty target; consumers default missing rows with COALESCE.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, cast

_UC_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Canonical DDL comment contract. The MERGE preserves metadata; this mapping
# remains byte-identical to sql/ddl/003_gold_tables.sql §7 and is pinned by
# tests/unit/test_gold_column_comment_guard.py.
LIFECYCLE_COLUMN_COMMENTS: dict[str, str] = {
    "borrower_id": "Masked borrower id; matches borrower_360.borrower_id.",
    "approval_status": "pending / approved / rejected / hold. Derived from latest decided_at row in mip_app.approvals.",
    "outreach_status": "queued / actioned / none. Derived from latest outreach state.",
    "offer_code": "Latest offer_code associated with the approval decision.",
    "approved_at": "decided_at for the latest approve action; NULL when not approved.",
    "outreach_at": "Timestamp of latest outreach action.",
    "synced_at": "Last sync run that touched this row.",
    "refreshed_at": "Lakebase mirror refresh boundary for this lifecycle snapshot; distinct from the scoring gold refresh boundary.",
}


def _catalog_default() -> str:
    return (os.environ.get("MIP_DEFAULT_CATALOG") or "mip").strip() or "mip"


def _quote_uc_identifier(value: str, *, field: str) -> str:
    """Return a backtick-quoted Unity Catalog identifier, failing closed."""
    normalized = value.strip()
    if not _UC_IDENTIFIER_RE.fullmatch(normalized):
        print(
            f"[sync-lifecycle] invalid {field} identifier {value!r}; "
            "expected letters, digits, and underscores.",
            file=sys.stderr,
        )
        sys.exit(3)
    return f"`{normalized}`"


def _qualified_uc_table(catalog: str, schema: str, table: str) -> str:
    return ".".join(
        [
            _quote_uc_identifier(catalog, field="catalog"),
            _quote_uc_identifier(schema, field="schema"),
            _quote_uc_identifier(table, field="table"),
        ]
    )


def _resolve_connection() -> dict:
    """Return psycopg.connect kwargs. Mirrors jobs/lakebase_migrate.py."""
    instance_name = os.environ.get("LAKEBASE_INSTANCE_NAME", "mip-app-state")
    host = os.environ.get("LAKEBASE_HOST")
    user = os.environ.get("LAKEBASE_USER")
    password = os.environ.get("LAKEBASE_PASSWORD")

    if host and user and password:
        return {
            "host": host,
            "port": int(os.environ.get("LAKEBASE_PORT", "5432")),
            "dbname": os.environ.get("LAKEBASE_DATABASE", "mip_app_state"),
            "user": user,
            "password": password,
            "sslmode": os.environ.get("LAKEBASE_SSLMODE", "require"),
        }

    try:
        from databricks.sdk import WorkspaceClient
    except ImportError:
        print(
            "[sync-lifecycle] databricks-sdk is not installed; install it or "
            "set LAKEBASE_* env vars explicitly.",
            file=sys.stderr,
        )
        sys.exit(3)

    try:
        w = WorkspaceClient()
        me = w.current_user.me()
        identity = me.user_name or me.display_name
        if not identity:
            print(
                "[sync-lifecycle] could not resolve workspace identity.",
                file=sys.stderr,
            )
            sys.exit(3)

        inst = cast(
            dict[str, Any],
            w.api_client.do("GET", f"/api/2.0/database/instances/{instance_name}"),
        )
        resolved_host = host or inst.get("read_write_dns")
        if not resolved_host:
            print(
                f"[sync-lifecycle] instance {instance_name!r} has no "
                f"read_write_dns; check provisioning state.",
                file=sys.stderr,
            )
            sys.exit(3)

        cred = cast(
            dict[str, Any],
            w.api_client.do(
                "POST",
                "/api/2.0/database/credentials",
                body={
                    "request_id": (
                        f"mip-sync-lifecycle-" f"{os.environ.get('DATABRICKS_JOB_RUN_ID','local')}"
                    ),
                    "instance_names": [instance_name],
                },
            ),
        )
        cred_token = cred.get("token")
        if not cred_token:
            print(
                f"[sync-lifecycle] credential response missing token: {cred}",
                file=sys.stderr,
            )
            sys.exit(3)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 -- operator-facing
        print(f"[sync-lifecycle] SDK auth/resolution failed: {exc}", file=sys.stderr)
        sys.exit(3)

    return {
        "host": resolved_host,
        "port": int(os.environ.get("LAKEBASE_PORT", "5432")),
        "dbname": os.environ.get("LAKEBASE_DATABASE", "mip_app_state"),
        "user": user or identity,
        "password": password or cred_token,
        "sslmode": os.environ.get("LAKEBASE_SSLMODE", "require"),
    }


# Latest approval and latest sales disposition per borrower. This mirrors
# SalesStateStore.lifecycle_for(): outreach_status is 'actioned' when a
# call_dispositions row exists, 'queued' when an approval exists but no
# disposition has been recorded, and 'none' otherwise.
_LAKEBASE_QUERY = """
WITH latest_approvals AS (
    SELECT DISTINCT ON (a.borrower_id)
        a.borrower_id,
        a.action,
        a.offer_code,
        a.decided_at
    FROM mip_app.approvals a
    ORDER BY a.borrower_id, a.decided_at DESC
),
latest_dispositions AS (
    SELECT DISTINCT ON (d.borrower_id)
        d.borrower_id,
        d.occurred_at AS outreach_at
    FROM mip_app.call_dispositions d
    ORDER BY d.borrower_id, d.occurred_at DESC, d.created_at DESC
)
SELECT
    COALESCE(a.borrower_id, d.borrower_id)              AS borrower_id,
    CASE
        WHEN a.action = 'approve' THEN 'approved'
        WHEN a.action = 'reject'  THEN 'rejected'
        WHEN a.action = 'hold'    THEN 'hold'
        ELSE 'pending'
    END                                                  AS approval_status,
    CASE
        WHEN d.outreach_at IS NOT NULL          THEN 'actioned'
        WHEN a.action = 'approve'               THEN 'queued'
        ELSE 'none'
    END                                                  AS outreach_status,
    a.offer_code                                         AS offer_code,
    CASE WHEN a.action = 'approve' THEN a.decided_at
         ELSE NULL
    END                                                  AS approved_at,
    d.outreach_at                                        AS outreach_at
FROM latest_approvals a
FULL OUTER JOIN latest_dispositions d USING (borrower_id)
"""


def _fetch_lakebase_rows(conn_kwargs: dict) -> list[dict[str, Any]]:
    import psycopg

    with psycopg.connect(**conn_kwargs) as conn, conn.cursor() as cur:
        cur.execute(_LAKEBASE_QUERY)
        cols = [d.name for d in cur.description or ()]
        return [dict(zip(cols, row, strict=False)) for row in cur.fetchall()]


def _build_lifecycle_merge(rows: list[dict[str, Any]], *, catalog: str) -> str:
    """Build the canonical sparse Delta MERGE used by app and job paths."""
    borrower_table = _qualified_uc_table(catalog, "gold", "borrower_360")
    lifecycle_table = _qualified_uc_table(catalog, "gold", "borrower_lifecycle_state")
    lakebase_rows = _lakebase_rows_cte(rows)
    return f"""
    MERGE INTO {lifecycle_table} AS target
    USING (
      WITH lakebase_rows AS (
        {lakebase_rows}
      )
      SELECT
        l.borrower_id,
        l.approval_status,
        l.outreach_status,
        l.offer_code,
        l.approved_at,
        l.outreach_at,
        CURRENT_TIMESTAMP() AS mirror_refreshed_at
      FROM lakebase_rows AS l
      INNER JOIN {borrower_table} AS b
        ON b.borrower_id = l.borrower_id
    ) AS source
      ON target.borrower_id = source.borrower_id
    WHEN MATCHED AND NOT (
      target.approval_status <=> source.approval_status
      AND target.outreach_status <=> source.outreach_status
      AND target.offer_code <=> source.offer_code
      AND target.approved_at <=> source.approved_at
      AND target.outreach_at <=> source.outreach_at
    ) THEN UPDATE SET
      approval_status = source.approval_status,
      outreach_status = source.outreach_status,
      offer_code = source.offer_code,
      approved_at = source.approved_at,
      outreach_at = source.outreach_at,
      synced_at = source.mirror_refreshed_at,
      refreshed_at = source.mirror_refreshed_at
    WHEN NOT MATCHED THEN INSERT (
      borrower_id,
      approval_status,
      outreach_status,
      offer_code,
      approved_at,
      outreach_at,
      synced_at,
      refreshed_at
    ) VALUES (
      source.borrower_id,
      source.approval_status,
      source.outreach_status,
      source.offer_code,
      source.approved_at,
      source.outreach_at,
      source.mirror_refreshed_at,
      source.mirror_refreshed_at
    )
    """


def _build_legacy_default_prune(*, catalog: str) -> str:
    """Remove only synthetic default rows left by the retired full seed.

    Missing lifecycle rows already resolve to ``pending`` / ``none`` through
    every consumer's LEFT JOIN + COALESCE contract. Rows carrying any real
    decision, outreach event, offer, or timestamp are therefore excluded from
    this one-time-compatible cleanup.
    """
    lifecycle_table = _qualified_uc_table(catalog, "gold", "borrower_lifecycle_state")
    return f"""
    DELETE FROM {lifecycle_table}
    WHERE approval_status = 'pending'
      AND outreach_status = 'none'
      AND offer_code IS NULL
      AND approved_at IS NULL
      AND outreach_at IS NULL
    """


def _lakebase_rows_cte(rows: list[dict[str, Any]]) -> str:
    columns = (
        "borrower_id",
        "approval_status",
        "outreach_status",
        "offer_code",
        "approved_at",
        "outreach_at",
    )
    values = ",\n        ".join(
        "("
        + ", ".join(
            [
                _sql_string(row.get("borrower_id")),
                _sql_string(row.get("approval_status")),
                _sql_string(row.get("outreach_status")),
                _sql_string(row.get("offer_code")),
                _sql_timestamp(row.get("approved_at")),
                _sql_timestamp(row.get("outreach_at")),
            ]
        )
        + ")"
        for row in rows
        if row.get("borrower_id")
    )
    if values:
        return f"SELECT * FROM VALUES\n        {values}\n      AS t({', '.join(columns)})"
    return """
        SELECT
          CAST(NULL AS STRING) AS borrower_id,
          CAST(NULL AS STRING) AS approval_status,
          CAST(NULL AS STRING) AS outreach_status,
          CAST(NULL AS STRING) AS offer_code,
          CAST(NULL AS TIMESTAMP) AS approved_at,
          CAST(NULL AS TIMESTAMP) AS outreach_at
        WHERE FALSE
    """


def _sql_string(value: Any) -> str:
    if value is None:
        return "CAST(NULL AS STRING)"
    return "'" + str(value).replace("'", "''") + "'"


def _sql_timestamp(value: Any) -> str:
    if value is None:
        return "CAST(NULL AS TIMESTAMP)"
    if isinstance(value, datetime):
        dt = value.astimezone(UTC) if value.tzinfo else value
        normalized = dt.replace(tzinfo=None).isoformat(sep=" ", timespec="microseconds")
        return f"TIMESTAMP '{normalized}'"
    if isinstance(value, date):
        return f"TIMESTAMP '{value.isoformat()} 00:00:00.000000'"
    return "CAST(" + _sql_string(value) + " AS TIMESTAMP)"


def _get_spark() -> Any:
    """Return the active SparkSession. Exits 4 when unavailable."""
    try:
        from pyspark.sql import SparkSession
    except ImportError:
        print("[sync-lifecycle] pyspark unavailable; must run on Databricks.", file=sys.stderr)
        sys.exit(4)
    spark = SparkSession.getActiveSession() or SparkSession.builder.getOrCreate()
    if spark is None:
        print("[sync-lifecycle] no active SparkSession.", file=sys.stderr)
        sys.exit(4)
    return spark


def _write_gold(rows: list[dict[str, Any]], *, catalog: str) -> None:
    """Prune legacy defaults, then apply the canonical sparse MERGE."""
    spark = _get_spark()
    spark.sql(_build_legacy_default_prune(catalog=catalog))
    spark.sql(_build_lifecycle_merge(rows, catalog=catalog))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sync_lifecycle_state",
        description=(
            "Mirror Lakebase approval/outreach state into the configured "
            "gold.borrower_lifecycle_state table."
        ),
    )
    parser.add_argument(
        "--catalog",
        default=_catalog_default(),
        help=("Unity Catalog catalog for gold tables " "(default: MIP_DEFAULT_CATALOG or mip)."),
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    conn_kwargs = _resolve_connection()
    try:
        rows = _fetch_lakebase_rows(conn_kwargs)
    except Exception as exc:  # noqa: BLE001 -- operator-facing
        print(f"[sync-lifecycle] Lakebase read failed: {exc}", file=sys.stderr)
        sys.exit(2)

    print(f"[sync-lifecycle] read {len(rows)} rows from Lakebase")
    _write_gold(rows, catalog=args.catalog)
    print("[sync-lifecycle] gold mirror refreshed")


# Tolerate being exec()'d without __file__ (some Databricks notebook paths).
_ = Path


if __name__ == "__main__":
    main()

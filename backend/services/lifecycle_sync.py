"""Warehouse-backed lifecycle mirror for Lakebase approval state.

The original lifecycle mirror ran as a Spark Python Databricks Job. That
made a small Lakebase -> gold-table sync depend on cluster provisioning,
which is expensive and can block release validation when serverless Python
queues. This module keeps the same authority model but writes through the
already-provisioned SQL Warehouse.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from backend.config.settings import settings
from backend.services.databricks_sql import DatabricksSqlClient, get_sql_client
from jobs.sync_lifecycle_state import (
    LIFECYCLE_COLUMN_COMMENTS,
    _fetch_lakebase_rows,
    _qualified_uc_table,
    _resolve_connection,
)


@dataclass(frozen=True)
class LifecycleSyncResult:
    lakebase_rows: int
    mirrored_rows: int | None
    funnel_snapshot_rows: int | None


def sync_lifecycle_state_via_warehouse(
    *,
    catalog: str | None = None,
    sql_client: DatabricksSqlClient | None = None,
    record_funnel_snapshot: bool = True,
    funnel_sql_path: Path | None = None,
) -> LifecycleSyncResult:
    """Mirror Lakebase approvals into gold using only Lakebase + SQL Warehouse."""

    resolved_catalog = catalog or settings.mip_default_catalog
    client = sql_client or get_sql_client()
    rows = _fetch_lakebase_rows(_resolve_connection())
    lifecycle_table = _qualified_uc_table(resolved_catalog, "gold", "borrower_lifecycle_state")

    client.execute(_build_lifecycle_insert_overwrite(rows, catalog=resolved_catalog))
    _apply_column_comments(client, lifecycle_table)

    mirrored = client.execute_one(f"SELECT COUNT(*) AS n FROM {lifecycle_table}")
    funnel_rows: int | None = None
    if record_funnel_snapshot:
        sql_path = funnel_sql_path or Path("sql/_rendered/transformations/gold_funnel_snapshot_daily.sql")
        client.execute(_read_funnel_sql(sql_path, catalog=resolved_catalog))
        funnel_table = _qualified_uc_table(resolved_catalog, "gold", "funnel_snapshot_daily")
        funnel = client.execute_one(
            f"""
            SELECT COUNT(*) AS n
            FROM {funnel_table}
            WHERE snapshot_date = CURRENT_DATE()
            """
        )
        funnel_rows = _int_or_none(funnel.get("n") if funnel else None)

    return LifecycleSyncResult(
        lakebase_rows=len(rows),
        mirrored_rows=_int_or_none(mirrored.get("n") if mirrored else None),
        funnel_snapshot_rows=funnel_rows,
    )


def _build_lifecycle_insert_overwrite(rows: list[dict[str, Any]], *, catalog: str) -> str:
    borrower_table = _qualified_uc_table(catalog, "gold", "borrower_360")
    lifecycle_table = _qualified_uc_table(catalog, "gold", "borrower_lifecycle_state")
    lakebase_rows = _lakebase_rows_cte(rows)
    # INSERT OVERWRITE, not CTAS (external audit 2026-07-08): the app service
    # principal fires this mirror after approvals under a scoped MODIFY grant
    # on this one pre-created table (sql/ddl/003_gold_tables.sql). CTAS needs
    # CREATE/ownership the app deliberately does not hold and 403'd live
    # (PERMISSION_DENIED on MODIFY was the visible symptom). Atomic on Delta.
    return f"""
    INSERT OVERWRITE TABLE {lifecycle_table}
    WITH lakebase_rows AS (
      {lakebase_rows}
    ),
    lifecycle_valid AS (
      SELECT l.*
      FROM lakebase_rows AS l
      INNER JOIN {borrower_table} AS b
        ON b.borrower_id = l.borrower_id
    ),
    sync_anchor AS (
      SELECT CURRENT_TIMESTAMP() AS mirror_refreshed_at
    )
    SELECT
        l.borrower_id,
        l.approval_status,
        l.outreach_status,
        l.offer_code,
        l.approved_at,
        l.outreach_at,
        a.mirror_refreshed_at AS synced_at,
        a.mirror_refreshed_at AS refreshed_at
    FROM lifecycle_valid AS l
    CROSS JOIN sync_anchor AS a
    UNION ALL
    SELECT
        b.borrower_id,
        'pending'                  AS approval_status,
        'none'                     AS outreach_status,
        CAST(NULL AS STRING)       AS offer_code,
        CAST(NULL AS TIMESTAMP)    AS approved_at,
        CAST(NULL AS TIMESTAMP)    AS outreach_at,
        a.mirror_refreshed_at      AS synced_at,
        a.mirror_refreshed_at      AS refreshed_at
    FROM {borrower_table} AS b
    CROSS JOIN sync_anchor AS a
    LEFT ANTI JOIN lifecycle_valid AS l
      ON l.borrower_id = b.borrower_id
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
    if not rows:
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
    if not values:
        return _lakebase_rows_cte([])
    return f"SELECT * FROM VALUES\n        {values}\n      AS t({', '.join(columns)})"


def _apply_column_comments(client: DatabricksSqlClient, table: str) -> None:
    for column, comment in LIFECYCLE_COLUMN_COMMENTS.items():
        client.execute(
            f"COMMENT ON COLUMN {table}.{column} IS {_sql_string(comment)}"
        )


def _read_funnel_sql(path: Path, *, catalog: str) -> str:
    if not path.exists():
        path = Path("sql/transformations/gold_funnel_snapshot_daily.sql")
    sql = path.read_text(encoding="utf-8")
    if catalog != "mip":
        sql = sql.replace("mip.", f"{catalog}.")
    return sql


def _sql_string(value: Any) -> str:
    if value is None:
        return "CAST(NULL AS STRING)"
    return "'" + str(value).replace("'", "''") + "'"


def _sql_timestamp(value: Any) -> str:
    if value is None:
        return "CAST(NULL AS TIMESTAMP)"
    if isinstance(value, datetime):
        dt = value.astimezone(UTC) if value.tzinfo else value
        return f"TIMESTAMP '{dt.replace(tzinfo=None).isoformat(sep=' ', timespec='microseconds')}'"
    if isinstance(value, date):
        return f"TIMESTAMP '{value.isoformat()} 00:00:00.000000'"
    return "CAST(" + _sql_string(value) + " AS TIMESTAMP)"


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None

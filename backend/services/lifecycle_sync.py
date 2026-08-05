"""Incremental warehouse mirror for durable Lakebase lifecycle state.

The app path reads the small durable Lakebase decision set and applies the
same sparse Delta MERGE as the Databricks repair job. It never materializes
default rows for the borrower universe; metric-view LEFT JOIN + COALESCE owns
that default behavior.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.config.settings import settings
from backend.services.databricks_sql import DatabricksSqlClient, get_sql_client
from jobs.sync_lifecycle_state import (
    _build_legacy_default_prune,
    _build_lifecycle_merge,
    _ensure_lifecycle_schema,
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
    prune_legacy_defaults: bool = False,
    funnel_sql_path: Path | None = None,
) -> LifecycleSyncResult:
    """Mirror Lakebase approvals into gold using only Lakebase + SQL Warehouse."""

    resolved_catalog = catalog or settings.mip_default_catalog
    client = sql_client or get_sql_client()
    rows = _fetch_lakebase_rows(_resolve_connection())
    _ensure_lifecycle_schema(client.execute, catalog=resolved_catalog)
    if prune_legacy_defaults:
        client.execute(_build_legacy_default_prune(catalog=resolved_catalog))
    client.execute(_build_lifecycle_merge(rows, catalog=resolved_catalog))
    funnel_rows: int | None = None
    if record_funnel_snapshot:
        sql_path = funnel_sql_path or Path(
            "sql/_rendered/transformations/gold_funnel_snapshot_daily.sql"
        )
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
        # Statement Execution does not expose Delta MERGE affected-row metrics
        # consistently across warehouse versions. Avoid a 5.16M-row COUNT just
        # to decorate a background log line.
        mirrored_rows=None,
        funnel_snapshot_rows=funnel_rows,
    )


def _read_funnel_sql(path: Path, *, catalog: str) -> str:
    if not path.exists():
        path = Path("sql/transformations/gold_funnel_snapshot_daily.sql")
    sql = path.read_text(encoding="utf-8")
    if catalog != "mip":
        sql = sql.replace("mip.", f"{catalog}.")
    return sql


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None

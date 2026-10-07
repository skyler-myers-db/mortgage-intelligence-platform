"""Incremental warehouse mirror for durable Lakebase lifecycle state.

The app path reads the small durable Lakebase decision set and applies the
same sparse Delta MERGE as the Databricks repair job. It never materializes
default rows for the borrower universe; metric-view LEFT JOIN + COALESCE owns
that default behavior.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.config.settings import settings
from backend.services.databricks_sql import (
    DatabricksSqlClient,
    DatabricksSqlPermissionError,
    get_sql_client,
)
from backend.services.observability import emit
from backend.services.resilience_breaker import DependencyDownError
from jobs.sync_lifecycle_state import (
    _build_legacy_default_prune,
    _build_lifecycle_merge,
    _ensure_funnel_snapshot_schema,
    _ensure_lifecycle_schema,
    _fetch_lakebase_rows,
    _qualified_uc_table,
    _resolve_connection,
)

log = logging.getLogger(__name__)


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
    tolerate_funnel_schema_denied: bool = True,
) -> LifecycleSyncResult:
    """Mirror Lakebase approvals into gold using only Lakebase + SQL Warehouse.

    ``tolerate_funnel_schema_denied`` is for the App's own paths, which hold
    only MODIFY on the funnel table. The deploy identity (deploy step 9,
    ``tools/sync_lifecycle_warehouse.py``) passes False: a refused ALTER there
    is a misconfigured deploy and must fail it, not skip the snapshot.
    """

    resolved_catalog = catalog or settings.mip_default_catalog
    client = sql_client or get_sql_client()
    rows = _fetch_lakebase_rows(_resolve_connection())
    _ensure_lifecycle_schema(client.execute, catalog=resolved_catalog)
    if prune_legacy_defaults:
        client.execute(_build_legacy_default_prune(catalog=resolved_catalog))
    client.execute(_build_lifecycle_merge(rows, catalog=resolved_catalog))
    funnel_rows: int | None = None
    if record_funnel_snapshot and _funnel_snapshot_schema_ready(
        client, catalog=resolved_catalog, tolerate_denied=tolerate_funnel_schema_denied
    ):
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


def _funnel_snapshot_schema_ready(
    client: DatabricksSqlClient, *, catalog: str, tolerate_denied: bool
) -> bool:
    """Ensure the funnel snapshot's newer columns before its MERGE (wow-ai-3).

    The deploy identity (deploy step 9, the bundle job) adds a missing column,
    and a refusal there propagates (``tolerate_denied=False``). The App's own
    paths (Admin 'Sync workflow state') hold only MODIFY on the table, so
    their ALTER is refused when the deploy has not run yet: that skips ONLY
    the funnel snapshot write, with one warning. The lifecycle mirror above
    has already landed and the sync never answers 503 for it.
    """
    try:
        _ensure_funnel_snapshot_schema(client.execute, catalog=catalog)
    except Exception as exc:
        if not (tolerate_denied and _is_permission_refusal(exc)):
            raise
        emit(
            log,
            "funnel_snapshot_schema_denied",
            level=logging.WARNING,
            exc_type=type(exc).__name__,
        )
        return False
    return True


def _is_permission_refusal(exc: BaseException) -> bool:
    if isinstance(exc, DatabricksSqlPermissionError):
        return True
    return (
        isinstance(exc, DependencyDownError)
        and exc.kind == DependencyDownError.KIND_PERMISSION_DENIED
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

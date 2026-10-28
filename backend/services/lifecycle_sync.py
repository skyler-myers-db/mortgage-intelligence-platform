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
    _build_funnel_snapshot_schema_probe,
    _build_legacy_default_prune,
    _build_lifecycle_merge,
    _ensure_funnel_snapshot_schema,
    _ensure_lifecycle_schema,
    _fetch_lakebase_rows,
    _fetch_open_snoozes,
    _missing_funnel_snapshot_columns,
    _qualified_uc_table,
    _resolve_connection,
    wake_lead_snoozes,
)

log = logging.getLogger(__name__)

# Resolved against the source root, not the cwd: the App starts in its source
# root, but deploy step 9 and local runs need not. The deploy renders the
# catalog into _rendered/; without it the fallback swaps the catalog itself.
SOURCE_ROOT = Path(__file__).resolve().parents[2]
RENDERED_FUNNEL_SQL = (
    SOURCE_ROOT / "sql" / "_rendered" / "transformations" / "gold_funnel_snapshot_daily.sql"
)
FUNNEL_SQL = SOURCE_ROOT / "sql" / "transformations" / "gold_funnel_snapshot_daily.sql"


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
    conn_kwargs = _resolve_connection()
    rows = _fetch_lakebase_rows(conn_kwargs)
    _ensure_lifecycle_schema(client.execute, catalog=resolved_catalog)
    if prune_legacy_defaults:
        client.execute(_build_legacy_default_prune(catalog=resolved_catalog))
    client.execute(_build_lifecycle_merge(rows, catalog=resolved_catalog))
    funnel_rows: int | None = None
    if record_funnel_snapshot and _funnel_snapshot_schema_ready(
        client, catalog=resolved_catalog, tolerate_denied=tolerate_funnel_schema_denied
    ):
        sql_path = funnel_sql_path or RENDERED_FUNNEL_SQL
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

    _wake_snoozes(client, catalog=resolved_catalog, conn_kwargs=conn_kwargs)
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
    and any failure there propagates (``tolerate_denied=False``). The App's own
    paths (Admin 'Sync workflow state') hold only MODIFY on the table and never
    attempt DDL on gold (NB-f): they only PROBE information_schema. A missing
    column skips ONLY the funnel snapshot write, with one warning, until the
    deploy identity adds it; a refused probe does the same. The lifecycle
    mirror above has already landed and the sync never answers 503 for it.
    """
    if not tolerate_denied:
        _ensure_funnel_snapshot_schema(client.execute, catalog=catalog)
        return True
    try:
        rows = client.execute(_build_funnel_snapshot_schema_probe(catalog=catalog))
    except Exception as exc:
        if not _is_permission_refusal(exc):
            raise
        emit(log, "funnel_snapshot_schema_denied", level=logging.WARNING, exc_type=type(exc).__name__)
        return False
    missing = _missing_funnel_snapshot_columns(rows)
    if missing:
        emit(log, "funnel_snapshot_schema_deferred", level=logging.WARNING, missing_columns=len(missing))
        return False
    return True


def _wake_snoozes(client: DatabricksSqlClient, *, catalog: str, conn_kwargs: dict[str, Any]) -> None:
    """Wake Follow-ups snoozes on a newer signal (wow-power-3). Fail-soft, counts only.

    Never raises and never changes the sync's result, its 503 behaviour or the
    funnel path; a wake writes no audit row (the lead_snoozes row is the record).
    """
    try:
        open_count, signal, timer = wake_lead_snoozes(
            client.execute, conn_kwargs, catalog=catalog, fetch_open=_fetch_open_snoozes
        )
    except Exception as exc:  # noqa: BLE001 -- a wake must never fail the sync
        emit(log, "lead_snooze_wake_failed", level=logging.WARNING, exc_type=type(exc).__name__)
        return
    emit(log, "lead_snooze_wake", open=open_count, signal=signal, timer=timer)


def _is_permission_refusal(exc: BaseException) -> bool:
    if isinstance(exc, DatabricksSqlPermissionError):
        return True
    return (
        isinstance(exc, DependencyDownError)
        and exc.kind == DependencyDownError.KIND_PERMISSION_DENIED
    )


def _read_funnel_sql(path: Path, *, catalog: str) -> str:
    if not path.exists():
        path = FUNNEL_SQL
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

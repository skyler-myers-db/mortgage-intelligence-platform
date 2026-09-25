"""Read-only run history for the Growth Agent (audit 2026-09-21 genie-09 part 2).

Lists the caller's own rows in ``mip_app.growth_agent_runs``, newest first.
This is a passive read: it writes no audit row and touches no Unity Catalog
table (Lakebase app state only). The SQL selects no actor, criteria or route
column, and the summary model forbids them.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from backend.schemas.growth_agent_run_history import GrowthAgentRunSummary
from backend.services.error_sanitizer import safe_dependency_detail
from backend.services.growth_agent_ledger_sql import RUN_LIST_SQL
from backend.services.lakebase import LakebaseClient, LakebaseError

DEFAULT_RUN_LIST_LIMIT = 20
MAX_RUN_LIST_LIMIT = 50


def list_runs(lakebase: LakebaseClient, *, actor: str, limit: int) -> list[GrowthAgentRunSummary]:
    """Return up to ``limit`` (1..50) of ``actor``'s runs, newest first."""

    bounded = max(1, min(int(limit), MAX_RUN_LIST_LIMIT))
    try:
        rows = lakebase.fetchall(
            RUN_LIST_SQL,
            {"actor_email": actor, "limit": bounded},
            limit=bounded,
        )
    except LakebaseError as exc:
        raise HTTPException(status_code=503, detail=safe_dependency_detail("lakebase")) from exc
    return [run_summary_from_row(row) for row in rows[:bounded]]


def run_summary_from_row(row: dict[str, Any]) -> GrowthAgentRunSummary:
    """Map a ledger row to its public summary; unselected columns never pass."""

    avg = row.get("actionable_avg_score")
    audit_event_id = row.get("audit_event_id")
    return GrowthAgentRunSummary(
        run_id=str(row["run_id"]),
        workflow_id=row["workflow_id"],
        workflow_title=str(row.get("workflow_title") or ""),
        status=row.get("status") or "completed",
        broad_total=int(row.get("broad_total") or 0),
        actionable_total=int(row.get("actionable_total") or 0),
        actionable_avg_score=float(avg) if avg is not None else None,
        source_assets=[str(asset) for asset in (row.get("source_assets") or [])],
        audit_event_id=str(audit_event_id) if audit_event_id else None,
        created_at=row.get("created_at"),
    )


__all__ = [
    "DEFAULT_RUN_LIST_LIMIT",
    "MAX_RUN_LIST_LIMIT",
    "list_runs",
    "run_summary_from_row",
]

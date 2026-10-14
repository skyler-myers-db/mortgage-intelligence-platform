"""Best-effort per-step progress of a composed-plan execute (``genie-09`` part 4, W5d).

A reviewed plan runs as ONE blocking request. While it runs, the executor
records where it is in ``mip_app.growth_agent_plan_progress``, one row per
``(actor_email, request_id)``, so the composing browser can poll its own
request's progress instead of a bare "Running…". The rules:

- the row is operational progress, not an audit record: each write is one
  ``lakebase.execute`` (its own short transaction) and NEVER happens inside
  the plan's audit transaction, which stays the single place the run's
  ledger rows are written;
- a write failure never changes the trace, the audit rows or the response: it
  emits ``growth_agent_plan_progress_unavailable`` once (exception type only)
  and disables further writes for that execution;
- the read is actor-bound and audit-free; a missing row, another actor's id
  and a failed read (``growth_agent_plan_progress_read_failed``) all read as
  ``not_started``.
"""

from __future__ import annotations

import logging
from typing import Any, Literal, Protocol

from backend.schemas.growth_agent_plan_progress import (
    NOT_STARTED,
    GrowthAgentPlanProgressResponse,
)
from backend.services.lakebase import LakebaseClient
from backend.services.observability import emit

log = logging.getLogger(__name__)

PlanProgressPhase = Literal["running", "gate", "recording"]
MAX_STEP_TOOL_LENGTH = 64

PLAN_PROGRESS_UPSERT_SQL = """
INSERT INTO mip_app.growth_agent_plan_progress (
  actor_email, request_id, step_index, step_total, step_tool, phase, updated_at
) VALUES (
  %(actor_email)s, %(request_id)s, %(step_index)s, %(step_total)s, %(step_tool)s, %(phase)s, now()
)
ON CONFLICT (actor_email, request_id) DO UPDATE SET
  step_index = EXCLUDED.step_index,
  step_total = EXCLUDED.step_total,
  step_tool = EXCLUDED.step_tool,
  phase = EXCLUDED.phase,
  updated_at = now()
"""

PLAN_PROGRESS_SELECT_SQL = """
SELECT step_index, step_total, step_tool, phase, updated_at
FROM mip_app.growth_agent_plan_progress
WHERE actor_email = %(actor_email)s
  AND request_id = %(request_id)s
LIMIT 1
"""


class PlanProgressRecorder(Protocol):
    def record(self, step_index: int, step_total: int, step_tool: str, phase: PlanProgressPhase) -> None: ...


class LakebasePlanProgressRecorder:
    """One execute request's progress writer: best-effort, outside any transaction."""

    def __init__(self, lakebase: LakebaseClient, *, actor: str, request_id: str) -> None:
        self._lakebase = lakebase
        self._actor = actor
        self._request_id = request_id
        self._disabled = False

    def record(self, step_index: int, step_total: int, step_tool: str, phase: PlanProgressPhase) -> None:
        if self._disabled:
            return
        try:
            self._lakebase.execute(
                PLAN_PROGRESS_UPSERT_SQL,
                {
                    "actor_email": self._actor,
                    "request_id": self._request_id,
                    "step_index": step_index,
                    "step_total": step_total,
                    "step_tool": step_tool[:MAX_STEP_TOOL_LENGTH],
                    "phase": phase,
                },
            )
        except Exception as exc:  # noqa: BLE001 - progress is best-effort; the run's outcome never depends on it
            self._disabled = True
            emit(log, "growth_agent_plan_progress_unavailable", level=logging.WARNING, exc_type=type(exc).__name__)


def read_plan_progress(lakebase: LakebaseClient, *, actor: str, request_id: str) -> GrowthAgentPlanProgressResponse:
    """The caller's own request's progress; ``not_started`` when absent or unreadable."""

    try:
        row = lakebase.fetchone(PLAN_PROGRESS_SELECT_SQL, {"actor_email": actor, "request_id": request_id})
    except Exception as exc:  # noqa: BLE001 - a failed progress read is "not started", never an error
        emit(log, "growth_agent_plan_progress_read_failed", level=logging.WARNING, exc_type=type(exc).__name__)
        return NOT_STARTED
    if row is None:
        return NOT_STARTED
    return _from_row(row)


def _from_row(row: dict[str, Any]) -> GrowthAgentPlanProgressResponse:
    try:
        return GrowthAgentPlanProgressResponse(
            state=row["phase"],
            step_index=int(row["step_index"]),
            step_total=int(row["step_total"]),
            step_tool=str(row["step_tool"]),
            updated_at=row.get("updated_at"),
        )
    except (KeyError, TypeError, ValueError):
        return NOT_STARTED


__all__ = [
    "PLAN_PROGRESS_SELECT_SQL",
    "PLAN_PROGRESS_UPSERT_SQL",
    "LakebasePlanProgressRecorder",
    "PlanProgressPhase",
    "PlanProgressRecorder",
    "read_plan_progress",
]

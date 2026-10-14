"""GET /api/sales/workload: per-officer load for the Sales ops manager view.

Audit 2026-09-21 critic-10 item 6c (W5d w5-sales-loop CUT 2, ruled default
(h)): a sales manager or admin sees every active loan officer in scope with
their open and stale assignments and how many leads were assigned and worked
in the period and the same span before it.

- Manager or admin only (a loan officer, or an identity off the active
  roster, is 403); scoped by the manager's visible loan officers.
- One Lakebase read (mip_app.sales_team, lead_assignments,
  call_dispositions); never the warehouse.
- AUDIT-FREE: aggregate staff workload, no borrower row and no borrower id
  in the response, and no audit row is written.
- ``Cache-Control: private, no-store``.

Routers never depend on each other, so this one does not reach into the
sales router module: its 403 / 503 mapping is inline.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from backend.schemas.sales_workload import SalesWorkloadResponse, SalesWorkloadRow
from backend.services.audit_store import resolve_actor
from backend.services.error_sanitizer import safe_dependency_detail
from backend.services.lakebase import LakebaseError
from backend.services.sales_state import SalesStateStore, get_sales_state_store

router = APIRouter(tags=["sales"])

SalesStateDep = Annotated[SalesStateStore, Depends(get_sales_state_store)]

#: The longest window the Sales ops period control offers (analytics.sales-ops.period MAX_PERIOD_DAYS).
MAX_WORKLOAD_DAYS = 92


def previous_window(from_date: date, to_date: date) -> tuple[date, date]:
    """The same number of days immediately before ``from_date``."""

    span = (to_date - from_date).days
    previous_to = from_date - timedelta(days=1)
    return previous_to - timedelta(days=span), previous_to


@router.get("/sales/workload", response_model=SalesWorkloadResponse)
def sales_workload(
    request: Request,
    response: Response,
    store: SalesStateDep,
    from_date: Annotated[date, Query(alias="from")],
    to_date: Annotated[date, Query(alias="to")],
    stale_days: Annotated[int, Query(ge=1, le=30)] = 7,
) -> SalesWorkloadResponse:
    if to_date < from_date:
        raise HTTPException(status_code=422, detail="to must be on or after from")
    if (to_date - from_date).days + 1 > MAX_WORKLOAD_DAYS:
        raise HTTPException(status_code=422, detail=f"a workload window covers at most {MAX_WORKLOAD_DAYS} days")
    actor = resolve_actor(request)
    previous_from, previous_to = previous_window(from_date, to_date)
    try:
        store.require_manager_actor(actor)
        rows = store.workload(
            from_date=from_date.isoformat(),
            to_date=to_date.isoformat(),
            previous_from=previous_from.isoformat(),
            previous_to=previous_to.isoformat(),
            stale_days=stale_days,
            visible_lo_emails=store.visible_lo_emails(actor=actor),
        )
    except (KeyError, PermissionError) as exc:
        raise HTTPException(status_code=403, detail="sales operation is outside the actor scope") from exc
    except LakebaseError as exc:
        raise HTTPException(status_code=503, detail=safe_dependency_detail("lakebase")) from exc
    response.headers["Cache-Control"] = "private, no-store"
    return SalesWorkloadResponse(
        from_date=from_date.isoformat(),
        to_date=to_date.isoformat(),
        previous_from_date=previous_from.isoformat(),
        previous_to_date=previous_to.isoformat(),
        stale_days=stale_days,
        rows=[SalesWorkloadRow(**row) for row in rows],
    )

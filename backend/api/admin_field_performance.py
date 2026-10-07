"""Admin Field performance: p75 Core Web Vitals from the RUM day aggregates.

D-platform-process-d2 step 11 (audit 2026-09-21 runtime-09, quality-07). An
admin-only, user-triggered read of ``mip_app.rum_daily`` (Lakebase): no
borrower data, no identifier, no audit row and no warehouse statement.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BeforeValidator

from backend.config.settings import settings
from backend.schemas.field_performance import FieldPerformanceResponse
from backend.services.error_sanitizer import safe_dependency_detail
from backend.services.lakebase import LakebaseError, get_lakebase_client
from backend.services.rbac import AdminDep
from backend.services.rum_field_performance import FIELD_PERFORMANCE_SQL, READ_LIMIT, summarize

router = APIRouter(prefix="/admin", tags=["admin"])


def _query_int(value: object) -> object:
    """A query string arrives as text; a short decimal becomes the int the Literal checks."""
    if isinstance(value, str) and value.isdecimal() and len(value) <= 3:
        return int(value)
    return value


Days = Annotated[Literal[7, 28], BeforeValidator(_query_int), Query()]


@router.get("/field-performance", response_model=FieldPerformanceResponse)
def field_performance(response: Response, _actor: AdminDep, days: Days = 7) -> FieldPerformanceResponse:
    """p75 LCP / INP / CLS by route template, INP by interaction target, and
    client-error counts, over the last 7 or 28 UTC days.

    AUDIT EXEMPT: read-only aggregate operational telemetry; no borrower data.
    The rows are day-grain aggregates over closed vocabularies, never a
    VIEW_* read of product data. Cache-Control private, no-store.
    """
    response.headers["Cache-Control"] = "private, no-store"
    today = datetime.now(UTC).date()
    since = today - timedelta(days=days - 1)
    try:
        rows = get_lakebase_client().fetchall(FIELD_PERFORMANCE_SQL, {"since": since}, limit=READ_LIMIT)
    except LakebaseError as exc:
        raise HTTPException(status_code=503, detail=safe_dependency_detail("lakebase")) from exc
    return summarize(rows, days=days, since=since, enabled=settings.mip_rum_enabled)

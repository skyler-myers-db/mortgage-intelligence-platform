"""Personalized home summary endpoint (S4).

Thin router over :class:`backend.services.home_summary.HomeSummaryService`;
all delta math lives in the S3 KPI delta service and the deterministic
template. The actor comes from the trusted forwarded-identity headers via
``resolve_actor`` — the same identity the visit-tracking middleware records,
so "since your last login" anchors on the identity that actually logged in.
"""

from datetime import UTC, date, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from backend.schemas.home_attribution import (
    HomeAttributionMeasure,
    HomeAttributionRate,
    HomeSummaryAttributionResponse,
)
from backend.schemas.home_summary import HomeSummaryResponse
from backend.services.audit_store import resolve_actor
from backend.services.databricks_sql import DatabricksSqlError
from backend.services.error_sanitizer import safe_dependency_detail
from backend.services.home_attribution import (
    MEASURE_COLUMNS,
    RATE_SERIES_ID,
    HomeAttributionService,
    get_home_attribution_service,
)
from backend.services.home_summary import HomeSummaryService, get_home_summary_service
from backend.services.lakebase import LakebaseError
from backend.services.resilience import DependencyDownError

router = APIRouter(prefix="/home", tags=["home"])

ServiceDep = Annotated[HomeSummaryService, Depends(get_home_summary_service)]
AttributionDep = Annotated[HomeAttributionService, Depends(get_home_attribution_service)]

#: How far back a Delta Explainer baseline may reach (a year of snapshots plus slack).
ATTRIBUTION_MAX_LOOKBACK_DAYS = 400
ATTRIBUTION_BASELINE_DETAIL = (
    "The baseline must be a date within the last 400 days and not in the future."
)
#: Display labels of the measures the funnel snapshot does not attribute per
#: state yet (audit wow-ai-3): answered ``snapshotted=False`` with no read.
NOT_SNAPSHOTTED_LABELS: dict[str, str] = {"competitor_lien": "competitor liens"}


@router.get("/summary", response_model=HomeSummaryResponse)
def home_summary(request: Request, service: ServiceDep) -> HomeSummaryResponse:
    """Since-your-last-login KPI deltas for the signed-in actor.

    Aggregates the UNFILTERED headline set on both sides (live metric view
    vs persisted snapshot) so baseline and current stay apples-to-apples;
    portfolio-builder predicates never apply here.
    """
    actor = resolve_actor(request)
    try:
        return service.summary_for_actor(actor)
    except DependencyDownError as exc:
        raise HTTPException(
            status_code=503, detail=safe_dependency_detail(exc.dependency)
        ) from exc
    except LakebaseError as exc:
        raise HTTPException(
            status_code=503, detail=safe_dependency_detail("lakebase")
        ) from exc
    except DatabricksSqlError as exc:
        raise HTTPException(
            status_code=503, detail=safe_dependency_detail("warehouse")
        ) from exc


@router.get("/summary/attribution", response_model=HomeSummaryAttributionResponse)
def home_summary_attribution(
    service: AttributionDep,
    measure: Annotated[HomeAttributionMeasure, Query()],
    baseline: Annotated[date, Query()],
) -> HomeSummaryAttributionResponse:
    """Where one since-your-last-login measure moved (audit wow-ai-3).

    Whole-book state counts from the funnel snapshot nearest ``baseline``
    and the latest one, the 30-year par print on both weeks, and whether the
    offer rules changed since: gold and ref reads only. Audit-free: the
    drawer issues it only when a supported measure's evidence is open, and
    it writes no row.
    """
    today = datetime.now(UTC).date()
    if baseline > today or baseline < today - timedelta(days=ATTRIBUTION_MAX_LOOKBACK_DAYS):
        raise HTTPException(status_code=422, detail=ATTRIBUTION_BASELINE_DETAIL)
    if measure not in MEASURE_COLUMNS:
        # Data-driven: once gold.funnel_snapshot_daily carries the measure's
        # column (MEASURE_COLUMNS gains it), this short-circuit stops firing.
        return HomeSummaryAttributionResponse(
            measure=measure,
            label=NOT_SNAPSHOTTED_LABELS.get(measure, measure),
            requested_baseline_date=baseline,
            rate=HomeAttributionRate(series_id=RATE_SERIES_ID),
            snapshotted=False,
        )
    try:
        return service.attribution(measure, baseline)
    except DependencyDownError as exc:
        raise HTTPException(
            status_code=503, detail=safe_dependency_detail(exc.dependency)
        ) from exc
    except LakebaseError as exc:
        raise HTTPException(
            status_code=503, detail=safe_dependency_detail("lakebase")
        ) from exc
    except DatabricksSqlError as exc:
        raise HTTPException(
            status_code=503, detail=safe_dependency_detail("warehouse")
        ) from exc

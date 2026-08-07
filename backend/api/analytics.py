"""Native in-app analytics endpoints.

These routes let app users consume executive, geography, economics,
segment, and evidence-signal dashboards without leaving the Mortgage
Intelligence Platform for Databricks Lakeview. The repository reads the
same governed UC gold/semantic datasets as the Lakeview artifacts.
"""
from datetime import UTC, datetime
from typing import Annotated, Literal, get_args

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import ValidationError

from backend.schemas._validators_tenant import normalize_public_lender_ref
from backend.schemas.analytics import (
    AnalyticsFilters,
    EconomicsAnalyticsResponse,
    EquitySpreadPointsResponse,
    EquitySpreadViewport,
    ExecutiveAnalyticsResponse,
    GeographyAnalyticsResponse,
    SegmentAnalyticsResponse,
    SignalAnalyticsResponse,
)
from backend.schemas.funnel import (
    ApprovalFunnelResponse,
    LoanOfficerFunnelDetailResponse,
)
from backend.schemas.lead import SegmentCode
from backend.services.approval_funnel import (
    ApprovalFunnelStore,
    get_approval_funnel_store,
)
from backend.services.error_sanitizer import safe_dependency_detail
from backend.services.lakebase import LakebaseError
from backend.services.loan_officer_state import (
    LoanOfficerStateStore,
    get_loan_officer_state_store,
)
from backend.services.repositories import AnalyticsRepository, get_analytics_repository

router = APIRouter(prefix="/analytics", tags=["analytics"])

RepoDep = Annotated[AnalyticsRepository, Depends(get_analytics_repository)]
FunnelStoreDep = Annotated[ApprovalFunnelStore, Depends(get_approval_funnel_store)]
LoanOfficerStateDep = Annotated[LoanOfficerStateStore, Depends(get_loan_officer_state_store)]

_ALLOWED_SEGMENTS = set(get_args(SegmentCode))
_ALLOWED_SIGNAL_TYPES = {
    "absentee_mailing",
    "competitor_lien",
    "corporate_owner",
    "equity",
    "foreclosure_stage",
    "loan_type_fit",
    "market_trend",
    "multi_property",
    "rate_spread",
    "recent_payoff",
    "recent_refi",
    "recent_sale",
}
_ALLOWED_LENDER_RELATIONSHIPS = {
    "Current customer",
    "Former customer",
    "Competitor customer",
}


def _parse_csv(raw: str | None) -> list[str]:
    if not raw:
        return []
    out: list[str] = []
    for part in raw.split(","):
        value = part.strip()
        if not value:
            continue
        if value not in out:
            out.append(value)
    return out


def _parse_states(raw: str | None) -> list[str]:
    out: list[str] = []
    for value in _parse_csv(raw):
        normalised = value.upper()
        if len(normalised) != 2 or not normalised.isalpha():
            raise HTTPException(status_code=422, detail="states contains an invalid state code")
        if normalised not in out:
            out.append(normalised)
    return out


def _parse_segment_codes(raw: str | None) -> list[SegmentCode]:
    out: list[SegmentCode] = []
    for value in _parse_csv(raw):
        normalised = value.lower()
        if normalised not in _ALLOWED_SEGMENTS:
            raise HTTPException(status_code=422, detail="segment_codes contains an unknown segment")
        if normalised not in out:
            out.append(normalised)  # type: ignore[arg-type]
    return out


def _parse_segment_mode(raw: str) -> Literal["any", "all"]:
    mode = raw.strip().lower()
    if mode not in {"any", "all"}:
        raise HTTPException(status_code=422, detail="segment_mode must be any or all")
    return mode


def _parse_signal_types(raw: str | None) -> list[str]:
    out: list[str] = []
    for value in _parse_csv(raw):
        normalised = value.lower()
        if normalised not in _ALLOWED_SIGNAL_TYPES:
            raise HTTPException(status_code=422, detail="signal_types contains an unknown evidence signal")
        if normalised not in out:
            out.append(normalised)
    return out


def _parse_lender_relationship(raw: str | None) -> str | None:
    if raw is None:
        return None
    value = raw.strip()
    if not value or value == "All":
        return None
    if value not in _ALLOWED_LENDER_RELATIONSHIPS:
        raise HTTPException(status_code=422, detail="lender_relationship must be a reviewed option")
    return value


def _parse_target_lender_ref(raw: str | None) -> str | None:
    try:
        value = normalize_public_lender_ref(raw, allow_all=True)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="target_lender_ref must be a public-safe lender alias") from exc
    if value == "All":
        return None
    return value


def _analytics_filters(
    state: Annotated[
        str | None,
        Query(min_length=2, max_length=2, description="Deprecated single-state alias. Prefer states."),
    ] = None,
    states: Annotated[
        str | None,
        Query(description="Optional comma-separated 2-letter state filters."),
    ] = None,
    segment_codes: Annotated[
        str | None,
        Query(
            alias="segment_codes",
            description="Optional comma-separated segment code filter.",
        ),
    ] = None,
    segment_mode: Annotated[
        str,
        Query(description="Segment filter mode for multi-select codes."),
    ] = "any",
    lender_relationship: Annotated[
        str | None,
        Query(description="Optional governed lender relationship filter."),
    ] = None,
    target_lender_ref: Annotated[
        str | None,
        Query(description="Optional public-safe current lien-holder alias."),
    ] = None,
    signal_type: Annotated[
        str | None,
        Query(description="Deprecated single-signal alias. Prefer signal_types."),
    ] = None,
    signal_types: Annotated[
        str | None,
        Query(description="Optional comma-separated evidence signal filters for the Signals tab."),
    ] = None,
    days: Annotated[
        int,
        Query(ge=1, le=90, description="Evidence lookback window for the Signals tab."),
    ] = 30,
) -> AnalyticsFilters:
    parsed_states = _parse_states(states)
    if not parsed_states and state:
        parsed_states = _parse_states(state)
    parsed_signals = _parse_signal_types(signal_types)
    if not parsed_signals and signal_type:
        parsed_signals = _parse_signal_types(signal_type)
    return AnalyticsFilters(
        states=parsed_states,
        segment_codes=_parse_segment_codes(segment_codes),
        segment_mode=_parse_segment_mode(segment_mode),
        lender_relationship=_parse_lender_relationship(lender_relationship),
        target_lender_ref=_parse_target_lender_ref(target_lender_ref),
        signal_types=parsed_signals,
        days=days,
    )

FiltersDep = Annotated[AnalyticsFilters, Depends(_analytics_filters)]


@router.get("/executive", response_model=ExecutiveAnalyticsResponse)
def executive(repo: RepoDep, filters: FiltersDep) -> ExecutiveAnalyticsResponse:
    return repo.executive(filters)


@router.get("/geography", response_model=GeographyAnalyticsResponse)
def geography(repo: RepoDep, filters: FiltersDep) -> GeographyAnalyticsResponse:
    return repo.geography(filters)


@router.get("/economics", response_model=EconomicsAnalyticsResponse)
def economics(repo: RepoDep, filters: FiltersDep) -> EconomicsAnalyticsResponse:
    return repo.economics(filters)


def _equity_spread_viewport(
    equity_min: Annotated[
        int,
        Query(ge=0, le=100, description="Zoom window lower equity bound (pct)."),
    ] = 0,
    equity_max: Annotated[
        int,
        Query(ge=0, le=100, description="Zoom window upper equity bound (pct)."),
    ] = 100,
    spread_min: Annotated[
        int,
        Query(ge=-100, le=400, description="Zoom window lower rate-spread bound (bps)."),
    ] = -100,
    spread_max: Annotated[
        int,
        Query(ge=-100, le=400, description="Zoom window upper rate-spread bound (bps)."),
    ] = 400,
) -> EquitySpreadViewport:
    try:
        return EquitySpreadViewport(
            equity_min=equity_min,
            equity_max=equity_max,
            spread_min=spread_min,
            spread_max=spread_max,
        )
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail="viewport bounds must satisfy min <= max") from exc


ViewportDep = Annotated[EquitySpreadViewport, Depends(_equity_spread_viewport)]


@router.get("/economics/points", response_model=EquitySpreadPointsResponse)
def economics_points(
    repo: RepoDep,
    filters: FiltersDep,
    viewport: ViewportDep,
) -> EquitySpreadPointsResponse:
    """Real borrower points for a zoomed scatter viewport.

    Capped server-side; the payload carries the honest pre-cap total so the
    UI renders "showing N of M" instead of implying the page is everything.
    """
    return repo.economics_points(filters, viewport)


@router.get("/segments", response_model=SegmentAnalyticsResponse)
def segments(repo: RepoDep, filters: FiltersDep) -> SegmentAnalyticsResponse:
    return repo.segments(filters)


@router.get("/signals", response_model=SignalAnalyticsResponse)
def signals(repo: RepoDep, filters: FiltersDep) -> SignalAnalyticsResponse:
    return repo.signals(filters)


@router.get("/funnel", response_model=ApprovalFunnelResponse)
def approval_funnel(repo: RepoDep, store: FunnelStoreDep) -> ApprovalFunnelResponse:
    """Live approval funnel: UC population stages + Lakebase workflow stages.

    Population and high-opportunity come from the S1 headline metric view;
    approved/actioned/outcome_recorded come from the live Lakebase
    lead_assignments lifecycle, approvals ledger, and outcome feedback rows.
    """
    population = repo.funnel_population()
    try:
        return ApprovalFunnelResponse(
            generated_at=datetime.now(UTC),
            stages=store.workflow_stages(population),
            approvals=store.recent_approvals(),
            loan_officers=store.per_loan_officer(),
        )
    except LakebaseError as exc:
        raise HTTPException(status_code=503, detail=safe_dependency_detail("lakebase")) from exc


@router.get(
    "/funnel/loan-officers/{loan_officer_id}",
    response_model=LoanOfficerFunnelDetailResponse,
)
def approval_funnel_loan_officer(
    loan_officer_id: str,
    store: FunnelStoreDep,
    lifecycle: LoanOfficerStateDep,
) -> LoanOfficerFunnelDetailResponse:
    """Per-loan-officer drill-down: stage counts, live assignments, outcomes."""
    try:
        officer = store.officer_row(loan_officer_id)
        if officer is None:
            raise HTTPException(status_code=404, detail="loan officer not found")
        return LoanOfficerFunnelDetailResponse(
            officer=officer,
            assignments=lifecycle.list_assignments(loan_officer_id=loan_officer_id),
            outcome_counts=store.outcome_counts(loan_officer_id),
        )
    except LakebaseError as exc:
        raise HTTPException(status_code=503, detail=safe_dependency_detail("lakebase")) from exc

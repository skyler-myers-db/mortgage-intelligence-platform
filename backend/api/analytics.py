"""Native in-app analytics endpoints.

These routes let app users consume executive, geography, economics,
segment, and evidence-signal dashboards without leaving the Mortgage
Intelligence Platform for Databricks Lakeview. The repository reads the
same governed UC gold/semantic datasets as the Lakeview artifacts.
"""
from typing import Annotated, get_args

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.schemas.analytics import (
    AnalyticsFilters,
    EconomicsAnalyticsResponse,
    ExecutiveAnalyticsResponse,
    GeographyAnalyticsResponse,
    SegmentAnalyticsResponse,
    SignalAnalyticsResponse,
)
from backend.schemas.lead import SegmentCode
from backend.services.repositories import AnalyticsRepository, get_analytics_repository

router = APIRouter(prefix="/analytics", tags=["analytics"])

RepoDep = Annotated[AnalyticsRepository, Depends(get_analytics_repository)]

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
        if value not in _ALLOWED_SEGMENTS:
            raise HTTPException(status_code=422, detail="segment_codes contains an unknown segment")
        if value not in out:
            out.append(value)  # type: ignore[arg-type]
    return out


def _parse_signal_types(raw: str | None) -> list[str]:
    out: list[str] = []
    for value in _parse_csv(raw):
        normalised = value.lower()
        if normalised not in _ALLOWED_SIGNAL_TYPES:
            raise HTTPException(status_code=422, detail="signal_types contains an unknown evidence signal")
        if normalised not in out:
            out.append(normalised)
    return out


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
        Query(pattern="^(any|all)$", description="Segment filter mode for multi-select codes."),
    ] = "any",
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
        segment_mode="all" if segment_mode == "all" else "any",
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


@router.get("/segments", response_model=SegmentAnalyticsResponse)
def segments(repo: RepoDep, filters: FiltersDep) -> SegmentAnalyticsResponse:
    return repo.segments(filters)


@router.get("/signals", response_model=SignalAnalyticsResponse)
def signals(repo: RepoDep, filters: FiltersDep) -> SignalAnalyticsResponse:
    return repo.signals(filters)

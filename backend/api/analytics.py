"""Native in-app analytics endpoints.

These routes let app users consume executive, geography, economics,
segment, and evidence-signal dashboards without leaving the Mortgage
Intelligence Platform for Databricks Lakeview. The repository reads the
same governed UC gold/semantic datasets as the Lakeview artifacts.
"""
from typing import Annotated

from fastapi import APIRouter, Depends

from backend.schemas.analytics import (
    EconomicsAnalyticsResponse,
    ExecutiveAnalyticsResponse,
    GeographyAnalyticsResponse,
    SegmentAnalyticsResponse,
    SignalAnalyticsResponse,
)
from backend.services.repositories import AnalyticsRepository, get_analytics_repository

router = APIRouter(prefix="/analytics", tags=["analytics"])

RepoDep = Annotated[AnalyticsRepository, Depends(get_analytics_repository)]


@router.get("/executive", response_model=ExecutiveAnalyticsResponse)
def executive(repo: RepoDep) -> ExecutiveAnalyticsResponse:
    return repo.executive()


@router.get("/geography", response_model=GeographyAnalyticsResponse)
def geography(repo: RepoDep) -> GeographyAnalyticsResponse:
    return repo.geography()


@router.get("/economics", response_model=EconomicsAnalyticsResponse)
def economics(repo: RepoDep) -> EconomicsAnalyticsResponse:
    return repo.economics()


@router.get("/segments", response_model=SegmentAnalyticsResponse)
def segments(repo: RepoDep) -> SegmentAnalyticsResponse:
    return repo.segments()


@router.get("/signals", response_model=SignalAnalyticsResponse)
def signals(repo: RepoDep) -> SignalAnalyticsResponse:
    return repo.signals()

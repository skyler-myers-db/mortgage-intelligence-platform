"""Typed contract for the Home Delta Explainer (``GET /api/home/summary/attribution``).

Audit 2026-09-21 ``wow-ai-3``. A "since your last login" number opens its
own attribution: which states gained or lost borrowers between the funnel
snapshot nearest the actor's baseline and the latest one, the 30-year par
print on both weeks, and whether the offer rules changed since. Every count
is a whole-book (addressable) snapshot count read from
``mip.gold.funnel_snapshot_daily``; the rate and rule facts are things that
COINCIDED with the change, never presented as its causes.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

HomeAttributionMeasure = Literal[
    "refi_economics_screen",
    "high_opportunity",
    "offers_recommended",
    "listed_for_sale",
    "competitor_lien",
]

#: The fixed note every response carries.
ATTRIBUTION_NOTE = "These coincided with the change; they are not shown as causes."


class HomeAttributionState(BaseModel):
    """One state's count at the two snapshots; None where it has no row."""

    state: str
    baseline_count: int | None = None
    current_count: int | None = None
    change: int | None = None


class HomeAttributionRate(BaseModel):
    """The weekly par print for the baseline week and the latest week."""

    series_id: str
    baseline_week: date | None = None
    baseline_pct: float | None = None
    latest_week: date | None = None
    latest_pct: float | None = None


class HomeSummaryAttributionResponse(BaseModel):
    """Where one headline measure moved between two funnel snapshots."""

    measure: HomeAttributionMeasure
    label: str
    population: Literal["addressable"] = "addressable"
    requested_baseline_date: date
    baseline_snapshot_date: date | None = None
    current_snapshot_date: date | None = None
    #: True when no snapshot exists for the requested date and the nearest one is used.
    nearest_snapshot: bool = False
    baseline_total: int | None = None
    current_total: int | None = None
    total_change: int | None = None
    #: Sorted by the size of the change, largest first, then by state.
    states: list[HomeAttributionState] = Field(default_factory=list)
    #: The total change no state row accounts for.
    unattributed_change: int | None = None
    rate: HomeAttributionRate
    offer_rules_last_updated: datetime | None = None
    offer_rules_changed_since_baseline: bool | None = None
    sources: list[str] = Field(default_factory=list)
    note: str = ATTRIBUTION_NOTE
    #: False when the daily funnel snapshot has no per-state column for this
    #: measure yet: the route answers without reading anything, and the
    #: drawer says so instead of showing an empty breakdown.
    snapshotted: bool = True

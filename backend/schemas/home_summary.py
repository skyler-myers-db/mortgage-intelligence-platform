"""Typed contract for the S4 personalized home summary.

The summary is a thin presentation layer over the S3 last-login delta
service (``backend/services/kpi_deltas.py``). Every number the frontend
renders comes from a :class:`HomeSummaryHighlight`, whose ``display``
token is the EXACT string the UI shows and the exact token the optional
Genie phrasing is validated against — the deterministic template is the
source of truth and model output may only rephrase it, never alter it.

``status`` mirrors the S3 edge contract:

* ``first_visit``  — no recorded previous visit; ``highlights`` carry
  the live current numbers, never fake deltas.
* ``no_baseline``  — a previous visit exists but the snapshot table is
  empty (pre-backfill install); again current numbers only.
* ``delta``        — baseline + deltas resolved; ``highlights`` carry
  the signed since-your-last-login movements.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from backend.schemas.kpi_deltas import HeadlineKpis, KpiDeltas

HomeSummaryStatus = Literal["delta", "first_visit", "no_baseline"]
PhrasingSource = Literal["deterministic", "genie"]

#: UC / Lakebase objects every summary number traces to. The frontend
#: EvidenceDrawer cites both: current readings come from the live
#: headline metric view, baselines from the persisted snapshot rows.
CURRENT_METRICS_SOURCE = "mip.semantics.portfolio_headline_metric_view"
BASELINE_SNAPSHOT_SOURCE = "mip_app.kpi_snapshots"


class HomeSummaryHighlight(BaseModel):
    """One number in the summary sentence, traceable to its sources.

    ``display`` is the exact rendered token (e.g. ``+1.5%``, ``+2,250``
    or ``5,240,100``); ``value_token`` is the unsigned magnitude used to
    locate the number inside a Genie-phrased sentence (``1.5%``,
    ``2,250``). Keeping both here means the backend validator and the
    frontend chip-wrapper agree character-for-character.
    """

    measure: str
    label: str
    display: str
    value_token: str
    current: int | float
    baseline: int | float | None = None
    delta: int | float | None = None
    delta_pct: float | None = None


class HomeSummaryResponse(BaseModel):
    """The ``GET /api/home/summary`` contract."""

    status: HomeSummaryStatus
    previous_visit_at: datetime | None = None
    baseline_snapshot_at: datetime | None = None
    #: The sentence to render. Deterministic template by default; a
    #: Genie rephrasing ONLY when it passed exact-number validation.
    headline: str
    phrasing_source: PhrasingSource = "deterministic"
    #: Honest reason the phrasing is deterministic (mirrors the growth
    #: co-pilot's ``fallback_reason`` vocabulary). ``None`` when the
    #: phrasing actually came from Genie.
    phrasing_fallback_reason: str | None = None
    highlights: list[HomeSummaryHighlight] = Field(default_factory=list)
    current: HeadlineKpis
    baseline: HeadlineKpis | None = None
    deltas: KpiDeltas | None = None
    current_source: str = CURRENT_METRICS_SOURCE
    baseline_source: str = BASELINE_SNAPSHOT_SOURCE

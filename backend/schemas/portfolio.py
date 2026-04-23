from datetime import datetime

from pydantic import BaseModel, Field


class KpiTrend(BaseModel):
    """A single KPI's 7-day history + delta.

    ``series`` is ordered oldest → newest; may be shorter than 7 if the
    daily funnel snapshot hasn't accumulated that much history yet. ``delta_pct``
    is signed percent change from series[0] to series[-1]; ``None`` when the
    series has fewer than 2 points or the starting value is zero.
    """

    series: list[float]
    delta_pct: float | None = None
    direction: str = "flat"  # "up" | "down" | "flat"


class PortfolioPreview(BaseModel):
    # Real counts from the gold layer.
    marketable_population: int
    high_intent_leads: int
    avg_score: int = Field(ge=0, le=100)
    # Real lifecycle counts from ``mip.gold.funnel_snapshot_daily``: how many
    # borrowers have been approved and how many have had at least one
    # outreach action recorded. ``None`` if the snapshot table is empty
    # (first-boot of a workspace before the sync job has run).
    approved_count: int | None = None
    in_outreach_count: int | None = None
    # Optional trend histories keyed by KPI field name. When absent, the UI
    # renders the KPI without a sparkline.
    trends: dict[str, KpiTrend] = Field(default_factory=dict)
    # MAX(snapshot_at) from funnel_snapshot_daily — the most recent time the
    # gold mirror was refreshed. Rendered in the user's local timezone. None
    # when no snapshot rows exist yet.
    data_refreshed_at: datetime | None = None

    # ---- DEPRECATED fields kept to keep older clients rendering cleanly --
    # `projected_contact_to_app` and `cost_per_contact` were hardcoded
    # constants (9.7 / 2.18) in a previous slice. They do NOT map to any
    # real source table (Cotality never provided them; they're lender-side
    # CRM / campaign data). Left here as always-None so the old client
    # deserializes without error, but the updated UI ignores them.
    projected_contact_to_app: float | None = None
    cost_per_contact: float | None = None


class PortfolioCriteria(BaseModel):
    geography: str | None = None
    occupancy: str | None = None
    lien_status: str | None = None
    lender_relationship: str | None = None
    product: str | None = None
    min_equity_pct: float | None = None
    # Alternative entry point the frontend uses — a display label like
    # "≥ 25%" / "Any". Resolved server-side to ``min_equity_pct``.
    min_equity_pct_label: str | None = None


class PortfolioPreviewRequest(BaseModel):
    criteria: PortfolioCriteria = PortfolioCriteria()


class PortfolioCreateRequest(BaseModel):
    name: str = "My Portfolio"
    criteria: PortfolioCriteria = PortfolioCriteria()


class PortfolioCreateResponse(BaseModel):
    portfolio_id: str
    name: str
    marketable_population: int

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
    # Headline KPIs — ALL from mip.gold.borrower_360 / funnel_snapshot_daily,
    # which are derived from Cotality Delta Share + public FRED data. No
    # lender CRM / campaign / app-activity signal contributes to these.
    marketable_population: int
    high_intent_leads: int
    top_tier_opportunities: int | None = None  # opportunity_score >= 75
    offers_recommended: int | None = None       # recommended_offer_code != 'nurture'
    avg_score: int = Field(ge=0, le=100)
    # Optional trend histories keyed by KPI field name. When absent, the UI
    # renders the KPI without a sparkline.
    trends: dict[str, KpiTrend] = Field(default_factory=dict)
    # MAX(snapshot_at) from funnel_snapshot_daily — the most recent time the
    # gold mirror was refreshed. Rendered in the user's local timezone.
    data_refreshed_at: datetime | None = None
    # Lakebase-sourced lifecycle counts (your team's activity in the app).
    # Not Cotality-derived — hidden from the headline strip but exposed here
    # for panels that explicitly surface operator activity (e.g. admin
    # audit trail summary).
    approved_count: int | None = None
    in_outreach_count: int | None = None

    # R5-20: server-authoritative "this workspace has never had a gold
    # refresh" flag. Frontend used to infer day-0 from
    # ``data_refreshed_at is None`` + ``marketable_population == 0``, but
    # during a partial CTAS roll those two facts desynchronise (row in
    # borrower_360 with no snapshot row yet, or vice versa) and the UI
    # renders a mixed state -- KPI cards for a population that isn't
    # really there. Authoritative signal is ``COUNT(*) FROM
    # mip.gold.lead_population == 0``. Additive on the wire; default
    # ``False`` keeps pre-R5-20 clients parsing.
    day_zero: bool = False


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

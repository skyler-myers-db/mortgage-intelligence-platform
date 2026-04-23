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
    marketable_population: int
    high_intent_leads: int
    avg_score: int = Field(ge=0, le=100)
    # Optional — no real source table yet; returns ``None`` so the UI
    # renders em-dash rather than a plausible-but-fake number.
    projected_contact_to_app: float | None = None
    cost_per_contact: float | None = None
    # Optional trend histories keyed by KPI field name. When absent, the UI
    # renders the KPI without a sparkline.
    trends: dict[str, KpiTrend] = Field(default_factory=dict)


class PortfolioCriteria(BaseModel):
    geography: str | None = None
    occupancy: str | None = None
    lien_status: str | None = None
    lender_relationship: str | None = None
    product: str | None = None
    min_equity_pct: float | None = None


class PortfolioPreviewRequest(BaseModel):
    criteria: PortfolioCriteria = PortfolioCriteria()


class PortfolioCreateRequest(BaseModel):
    name: str = "My Portfolio"
    criteria: PortfolioCriteria = PortfolioCriteria()


class PortfolioCreateResponse(BaseModel):
    portfolio_id: str
    name: str
    marketable_population: int

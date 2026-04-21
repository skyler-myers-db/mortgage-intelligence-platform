from pydantic import BaseModel, Field


class PortfolioPreview(BaseModel):
    marketable_population: int
    high_intent_leads: int
    avg_score: int = Field(ge=0, le=100)
    projected_contact_to_app: float
    cost_per_contact: float


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
    name: str = "Demo Portfolio"
    criteria: PortfolioCriteria = PortfolioCriteria()


class PortfolioCreateResponse(BaseModel):
    portfolio_id: str
    name: str
    marketable_population: int

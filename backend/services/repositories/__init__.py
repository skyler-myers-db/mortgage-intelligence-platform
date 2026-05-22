"""Repository seam — Protocols + factories the routers depend on.

Routers import from this package (not from ``.protocols`` /
``.in_process`` / ``.factory`` directly) so the public surface stays
small and the Slice-4 swap to Databricks-backed implementations is a
single-file edit in ``.factory``.
"""
from backend.services.repositories.factory import (
    get_analytics_repository,
    get_borrower_repository,
    get_genie_answer_repository,
    get_geo_repository,
    get_lead_repository,
    get_offer_repository,
    get_outreach_repository,
    get_portfolio_repository,
    get_segment_repository,
)
from backend.services.repositories.protocols import (
    AnalyticsRepository,
    BorrowerRepository,
    GenieAnswerRepository,
    GeoRepository,
    LeadRepository,
    OfferRepository,
    OutreachRepository,
    PortfolioRepository,
    SegmentRepository,
)

__all__ = [
    "AnalyticsRepository",
    "BorrowerRepository",
    "GenieAnswerRepository",
    "GeoRepository",
    "LeadRepository",
    "OfferRepository",
    "OutreachRepository",
    "PortfolioRepository",
    "SegmentRepository",
    "get_analytics_repository",
    "get_borrower_repository",
    "get_genie_answer_repository",
    "get_geo_repository",
    "get_lead_repository",
    "get_offer_repository",
    "get_outreach_repository",
    "get_portfolio_repository",
    "get_segment_repository",
]

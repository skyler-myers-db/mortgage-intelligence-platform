"""Repository factory — single choke point for backend swap-out.

Every FastAPI router uses these factories via ``Depends(...)``. Today
they always return the temporary ``InProcessMock*Repository`` adapters
that delegate to ``backend.services.mock_data`` — Slice 0 is a pure
refactor with zero behavior change.

TODO-SLICE-4: replace each factory body with a dispatch that returns
the real ``Databricks<Domain>Repository`` against ``mip_demo.gold.*``.
The Protocol contracts defined in ``.protocols`` are the hand-off
point; Slice 4 must not edit router code, only these factories and the
new ``databricks_*.py`` implementations.

Importing these as FastAPI dependencies keeps the swap testable — a
test can override ``app.dependency_overrides[get_portfolio_repository]``
to inject a stub without monkey-patching module globals.
"""
from __future__ import annotations

from backend.services.repositories.in_process import (
    InProcessMockBorrowerRepository,
    InProcessMockGenieAnswerRepository,
    InProcessMockLeadRepository,
    InProcessMockOfferRepository,
    InProcessMockOutreachRepository,
    InProcessMockPortfolioRepository,
    InProcessMockSegmentRepository,
)
from backend.services.repositories.protocols import (
    BorrowerRepository,
    GenieAnswerRepository,
    LeadRepository,
    OfferRepository,
    OutreachRepository,
    PortfolioRepository,
    SegmentRepository,
)

# Single process-lifetime instances — the in-process mocks are
# stateless, so reusing them avoids allocating a fresh adapter on every
# request. Slice 4 will keep the same pattern for connection-pool-
# backed Databricks repositories.
_PORTFOLIO_REPO: PortfolioRepository = InProcessMockPortfolioRepository()
_SEGMENT_REPO: SegmentRepository = InProcessMockSegmentRepository()
_LEAD_REPO: LeadRepository = InProcessMockLeadRepository()
_BORROWER_REPO: BorrowerRepository = InProcessMockBorrowerRepository()
_OFFER_REPO: OfferRepository = InProcessMockOfferRepository()
_OUTREACH_REPO: OutreachRepository = InProcessMockOutreachRepository()
_GENIE_REPO: GenieAnswerRepository = InProcessMockGenieAnswerRepository()


def get_portfolio_repository() -> PortfolioRepository:
    # TODO-SLICE-4: return DatabricksPortfolioRepository when real path is wired.
    return _PORTFOLIO_REPO


def get_segment_repository() -> SegmentRepository:
    # TODO-SLICE-4: return DatabricksSegmentRepository when real path is wired.
    return _SEGMENT_REPO


def get_lead_repository() -> LeadRepository:
    # TODO-SLICE-4: return DatabricksLeadRepository when real path is wired.
    return _LEAD_REPO


def get_borrower_repository() -> BorrowerRepository:
    # TODO-SLICE-4: return DatabricksBorrowerRepository when real path is wired.
    return _BORROWER_REPO


def get_offer_repository() -> OfferRepository:
    # TODO-SLICE-4: return DatabricksOfferRepository when real path is wired.
    return _OFFER_REPO


def get_outreach_repository() -> OutreachRepository:
    # TODO-SLICE-4: return DatabricksOutreachRepository when real path is wired.
    return _OUTREACH_REPO


def get_genie_answer_repository() -> GenieAnswerRepository:
    # TODO-SLICE-4: return DatabricksGenieAnswerRepository when real path is wired.
    return _GENIE_REPO

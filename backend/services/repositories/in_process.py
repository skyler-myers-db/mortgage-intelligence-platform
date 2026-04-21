"""In-process mock repository implementations.

TEMPORARY — retired by Slice 4, replaced by ``Databricks<Domain>Repository``
that reads from ``mip_demo.gold.*`` via
``backend.services.databricks_sql``.

Do not extend these classes; add new behavior to the Protocol in
``backend.services.repositories.protocols`` first, then implement on
the Databricks side. Routers depend on the Protocol, not the concrete
class — that is the seam that lets Slice 4 swap these out cleanly.

Every method here is a thin delegation to
``backend.services.mock_data`` so Slice 0 is a pure refactor with zero
behavior change.
"""
from __future__ import annotations

from backend.schemas.common import EvidenceEvent
from backend.schemas.lead import Borrower360, LeadSummary, SegmentSummary
from backend.schemas.portfolio import (
    PortfolioCreateRequest,
    PortfolioCreateResponse,
    PortfolioPreview,
    PortfolioPreviewRequest,
)
from backend.services import mock_data
from backend.services.genie_answers import GenieMessageResponse
from backend.services.genie_answers import respond as _genie_respond


class InProcessMockPortfolioRepository:
    """TEMPORARY — retired by Slice 4, replaced by
    ``DatabricksPortfolioRepository``. Do not extend this class; add
    new behavior to ``PortfolioRepository`` Protocol first."""

    def preview(self, request: PortfolioPreviewRequest | None) -> PortfolioPreview:
        _ = request  # criteria don't shift the preview numbers yet; deterministic payload.
        return mock_data.PORTFOLIO

    def create(self, payload: PortfolioCreateRequest) -> PortfolioCreateResponse:
        return PortfolioCreateResponse(
            portfolio_id="demo-portfolio",
            name=payload.name,
            marketable_population=mock_data.PORTFOLIO.marketable_population,
        )

    def get(self, portfolio_id: str) -> dict[str, object]:
        return {
            "portfolio_id": portfolio_id,
            "status": "ready",
            "marketable_population": mock_data.PORTFOLIO.marketable_population,
        }


class InProcessMockSegmentRepository:
    """TEMPORARY — retired by Slice 4, replaced by
    ``DatabricksSegmentRepository``. Do not extend this class; add new
    behavior to ``SegmentRepository`` Protocol first."""

    def list(self, portfolio_id: str | None) -> list[SegmentSummary]:
        # portfolio_id is accepted for forward-compat; mock mode ignores it.
        _ = portfolio_id
        return mock_data.SEGMENTS


class InProcessMockLeadRepository:
    """TEMPORARY — retired by Slice 4, replaced by
    ``DatabricksLeadRepository``. Do not extend this class; add new
    behavior to ``LeadRepository`` Protocol first."""

    def list(self, segment: str | None, portfolio_id: str | None) -> list[LeadSummary]:
        _ = portfolio_id
        leads = [LeadSummary(**b.model_dump()) for b in mock_data.BORROWERS]
        if segment:
            leads = [lead for lead in leads if segment in lead.segment_codes]
        return leads


class InProcessMockBorrowerRepository:
    """TEMPORARY — retired by Slice 4, replaced by
    ``DatabricksBorrowerRepository``. Do not extend this class; add new
    behavior to ``BorrowerRepository`` Protocol first."""

    def get(self, borrower_id: str) -> Borrower360 | None:
        for b in mock_data.BORROWERS:
            if b.borrower_id == borrower_id:
                return b
        return None

    def evidence(self, borrower_id: str) -> list[EvidenceEvent] | None:
        borrower = self.get(borrower_id)
        if borrower is None:
            return None
        return borrower.evidence_events


class InProcessMockOfferRepository:
    """TEMPORARY — retired by Slice 4, replaced by
    ``DatabricksOfferRepository``. Do not extend this class; add new
    behavior to ``OfferRepository`` Protocol first."""

    def get_offer_inputs(self, borrower_id: str) -> dict[str, object] | None:
        return mock_data.BORROWER_OFFER_INPUTS.get(borrower_id)


class InProcessMockOutreachRepository:
    """TEMPORARY — retired by Slice 4, replaced by
    ``DatabricksOutreachRepository``. Do not extend this class; add new
    behavior to ``OutreachRepository`` Protocol first."""

    def find_borrower(self, borrower_id: str) -> Borrower360 | None:
        for b in mock_data.BORROWERS:
            if b.borrower_id == borrower_id:
                return b
        return None


class InProcessMockGenieAnswerRepository:
    """TEMPORARY — retired by Slice 4, replaced by
    ``DatabricksGenieAnswerRepository``. Do not extend this class; add
    new behavior to ``GenieAnswerRepository`` Protocol first.

    Delegates to the existing deterministic catalog matcher in
    ``backend.services.genie_answers`` so the booth-demo behavior is
    byte-identical to before the seam was introduced.
    """

    def respond(self, question: str) -> GenieMessageResponse:
        return _genie_respond(question)

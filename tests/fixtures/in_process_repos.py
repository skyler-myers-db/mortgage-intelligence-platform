"""In-process synthetic repositories -- TEST FIXTURE ONLY.

Moved in Slice 4 from ``backend/services/repositories/in_process.py``
to ``tests/fixtures/in_process_repos.py`` so nothing under
``backend/`` can accidentally import a mock-backed repo. Tests inject
these via FastAPI ``dependency_overrides`` in ``tests/conftest.py``;
production code paths are served by the ``Databricks*Repository``
classes in ``backend.services.repositories.databricks_repo``.

Each class below implements one Protocol from
``backend.services.repositories.protocols`` and delegates to the
synthetic population in ``tests/fixtures/mock_population.py``.
"""
from __future__ import annotations

from backend.schemas.common import EvidenceEvent
from backend.schemas.geo import StateRollup, StateRollupResponse
from backend.schemas.lead import Borrower360, LeadSummary, SegmentSummary
from backend.schemas.portfolio import (
    PortfolioCreateRequest,
    PortfolioCreateResponse,
    PortfolioPreview,
    PortfolioPreviewRequest,
)
from backend.services.genie_answers import GenieMessageResponse
from backend.services.genie_answers import respond as _genie_respond
from tests.fixtures import mock_population as mock_data


class InProcessMockPortfolioRepository:
    """Test fixture implementing ``PortfolioRepository`` from the synthetic population."""

    def preview(self, request: PortfolioPreviewRequest | None) -> PortfolioPreview:
        _ = request  # criteria don't shift the preview numbers yet; deterministic payload.
        return mock_data.PORTFOLIO

    def create(self, payload: PortfolioCreateRequest) -> PortfolioCreateResponse:
        return PortfolioCreateResponse(
            portfolio_id="module0-portfolio",
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
    """Test fixture implementing ``SegmentRepository`` from the synthetic population."""

    def list(self, portfolio_id: str | None) -> list[SegmentSummary]:
        _ = portfolio_id
        return mock_data.SEGMENTS


class InProcessMockLeadRepository:
    """Test fixture implementing ``LeadRepository`` from the synthetic population."""

    def list(self, segment: str | None, portfolio_id: str | None) -> list[LeadSummary]:
        _ = portfolio_id
        leads = [LeadSummary(**b.model_dump()) for b in mock_data.BORROWERS]
        if segment:
            leads = [lead for lead in leads if segment in lead.segment_codes]
        return leads


class InProcessMockBorrowerRepository:
    """Test fixture implementing ``BorrowerRepository`` from the synthetic population."""

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
    """Test fixture implementing ``OfferRepository`` from the synthetic population."""

    def get_offer_inputs(self, borrower_id: str) -> dict[str, object] | None:
        return mock_data.BORROWER_OFFER_INPUTS.get(borrower_id)


class InProcessMockOutreachRepository:
    """Test fixture implementing ``OutreachRepository`` from the synthetic population."""

    def find_borrower(self, borrower_id: str) -> Borrower360 | None:
        for b in mock_data.BORROWERS:
            if b.borrower_id == borrower_id:
                return b
        return None


class InProcessMockGenieAnswerRepository:
    """Test fixture wrapping the deterministic Genie answer catalog."""

    def respond(self, question: str) -> GenieMessageResponse:
        return _genie_respond(question)


# Fixture per-state rollups — covers the 6-state Delta Share footprint
# so /api/geo/state-rollups returns realistic shapes under test and
# exercises the same code path the UI hits in prod (no secondary code
# branch for "no data"). Numbers are proportional to the Apr-2026 share
# probe in docs/data-sources-gap-analysis.md §1.
_FIXTURE_STATE_ROLLUPS: list[StateRollup] = [
    StateRollup(state="IL", addressable=1860, in_the_money=720, top_tier_opportunities=420, avg_score=84),
    StateRollup(state="CA", addressable=900,  in_the_money=420, top_tier_opportunities=260, avg_score=83),
    StateRollup(state="FL", addressable=760,  in_the_money=320, top_tier_opportunities=200, avg_score=81),
    StateRollup(state="TX", addressable=750,  in_the_money=340, top_tier_opportunities=220, avg_score=82),
    StateRollup(state="WA", addressable=740,  in_the_money=300, top_tier_opportunities=190, avg_score=81),
    StateRollup(state="CO", addressable=160,  in_the_money=62,  top_tier_opportunities=42,  avg_score=80),
]


class InProcessMockGeoRepository:
    """Test fixture implementing ``GeoRepository`` from the synthetic rollups."""

    def state_rollups(self) -> StateRollupResponse:
        return StateRollupResponse(
            rollups=list(_FIXTURE_STATE_ROLLUPS),
            snapshot_date="2026-04-22",
        )

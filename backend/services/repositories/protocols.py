"""Repository Protocol seams for Module 0 domain data access.

Routers under ``backend/api/*`` depend on these Protocols, never on
concrete classes. This decouples the FastAPI surface from the data
backend so the Slice-4 Databricks implementations (``DatabricksBorrower
Repository`` etc.) can drop in against ``mip.gold.*`` without
reshaping any router contract.

Method signatures return the exact Pydantic shapes the routers emit
today (``Borrower360``, ``LeadSummary``, ``SegmentSummary``,
``PortfolioPreview``, ``EvidenceEvent``, ``GenieMessageResponse``) —
which are the same shapes the gold tables in
``docs/data-contract-module0.md`` §3 project into. Lookups that can
legitimately miss return ``None``; routers translate that to HTTP 404
so error-translation stays a router concern, not a data-layer one.

The Protocols are ``@runtime_checkable`` so tests that want to assert
duck-typed compliance can do so cheaply.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from backend.schemas.common import EvidenceEvent
from backend.schemas.geo import (
    CountyRollupResponse,
    StateRollupResponse,
    ZipRollupResponse,
)
from backend.schemas.lead import Borrower360, LeadSummary, SegmentSummary
from backend.schemas.portfolio import (
    PortfolioCreateRequest,
    PortfolioCreateResponse,
    PortfolioPreview,
    PortfolioPreviewRequest,
)


@runtime_checkable
class PortfolioRepository(Protocol):
    """Portfolio preview / create / read.

    Slice-4 backing: ``mip.gold.borrower_360`` rolled up to portfolio
    aggregates via ``sql/metric_views/lead_generation_metric_view.sql``.
    """

    def preview(self, request: PortfolioPreviewRequest | None) -> PortfolioPreview:
        ...

    def create(self, payload: PortfolioCreateRequest) -> PortfolioCreateResponse:
        ...

    def get(self, portfolio_id: str) -> dict[str, object]:
        ...


@runtime_checkable
class SegmentRepository(Protocol):
    """Segment summaries for the segment-intelligence row.

    Slice-4 backing: ``mip.gold.segment_population`` filtered to
    ``state='_ALL'`` per data-contract §3.6, projected to
    ``SegmentSummary``.
    """

    def list(self, portfolio_id: str | None) -> list[SegmentSummary]:
        ...


@runtime_checkable
class LeadRepository(Protocol):
    """Ranked leads for the queue view.

    Slice-4 backing: ``mip.gold.lead_population`` projected to
    ``LeadSummary`` per data-contract §3.5. ``segment`` filters against
    ``segment_codes``; ``portfolio_id`` is accepted for forward-compat.
    """

    def list(
        self,
        segment: str | None,
        portfolio_id: str | None,
        limit: int | None = None,
        state: str | None = None,
        zip_code: str | None = None,
    ) -> list[LeadSummary]:
        """Return up to ``limit`` ranked leads.

        ``state`` / ``zip_code`` (optional, 2026-05-04 FIX β): when
        provided, the implementation queries ``mip.gold.borrower_360``
        directly (no score floor) instead of ``mip.gold.lead_population``,
        so the returned rows match the per-geo addressable counts the
        map tooltips report. Without these, the call returns the
        national top-N by score from ``lead_population`` (the
        existing top-of-queue behaviour).
        """
        ...


@runtime_checkable
class BorrowerRepository(Protocol):
    """Borrower-360 and evidence lookups.

    Slice-4 backing: ``mip.gold.borrower_360`` projected to
    ``Borrower360`` per data-contract §3.2; ``evidence`` reads from
    ``mip.gold.evidence_events`` per §3.4. Returns ``None`` for a
    missing ``borrower_id`` so the router can raise its own 404 with
    context.
    """

    def get(self, borrower_id: str) -> Borrower360 | None:
        ...

    def evidence(self, borrower_id: str) -> list[EvidenceEvent] | None:
        ...


@runtime_checkable
class OfferRepository(Protocol):
    """Inputs for the offer-orchestrator recommendation.

    Returns the boolean + numeric columns from ``gold.borrower_360``
    that feed ``fn_next_best_offer`` (rate_spread_bps, equity_pct,
    has_permit, listed_for_sale, is_investor, is_current_customer,
    is_competitor_lien) plus the precomputed ``offer_code``. Rationale
    / alternatives / sources composition remains in the router, since
    those are configured against ``backend.config.settings`` thresholds
    and would not come from gold unchanged.
    """

    def get_offer_inputs(self, borrower_id: str) -> dict[str, object] | None:
        ...


@runtime_checkable
class OutreachRepository(Protocol):
    """Borrower lookup for outreach-draft composition.

    Slice-4 backing: same ``gold.borrower_360`` projection as
    ``BorrowerRepository.get`` — the Protocol is carved separately so
    outreach can add draft-specific columns (channel preferences, opt-
    out flags) without widening the borrower surface.
    """

    def find_borrower(self, borrower_id: str) -> Borrower360 | None:
        ...


@runtime_checkable
class GeoRepository(Protocol):
    """Geography rollups for the USChoroplethMap drill.

    Drives the hover tooltip + fill levels on ``segment-intelligence``
    and ``home`` without fabricating numbers.

    Backings (slice13-accuracy-validation):
    * ``state_rollups`` — ``mip.gold.funnel_snapshot_daily`` (latest
      snapshot, state rows) LEFT JOIN ``mip.gold.state_top_segment``
      for the ``top_segment_code`` extension.
    * ``county_rollups`` — ``mip.gold.county_rollup`` filtered to the
      given state at the latest snapshot_date.
    * ``zip_rollups`` — ``mip.gold.zip_rollup`` filtered to the given
      5-char county FIPS at the latest snapshot_date.

    Every method returns a structured response with ``snapshot_date``
    so the UI can show a "data as of YYYY-MM-DD" provenance chip.
    """

    def state_rollups(
        self,
        segment_codes: list[str] | None = None,
    ) -> StateRollupResponse:
        """Per-state aggregates for the latest snapshot.

        ``segment_codes``: optional non-empty list of SegmentCode values
        (itm, listed, permit, investor, equity, retention). When
        provided, the per-state counts reflect ONLY borrowers whose
        ``segment_codes`` array overlaps the filter — distinct-counted
        across multi-segment borrowers so a borrower in {itm,
        retention} is counted once when the filter is {itm, retention}.

        When ``segment_codes`` is None or empty, the cross-segment
        ``_ALL`` rollup is returned (the prior unfiltered behaviour).
        """
        ...

    def county_rollups(self, state: str) -> CountyRollupResponse:
        ...

    def zip_rollups(self, fips_5: str) -> ZipRollupResponse:
        ...


@runtime_checkable
class GenieAnswerRepository(Protocol):
    """Deterministic Genie answer lookup.

    Slice-4 backing: stays deterministic (answer catalog pulls from a
    population snapshot). In a live-Genie path, the implementation wraps
    ``backend.services.genie_client`` with a catalog-fallback.
    """

    def respond(self, question: str) -> object:
        """Return a ``GenieMessageResponse``. Typed as ``object`` here
        to avoid a forward-import cycle with ``backend.services
        .genie_answers``; routers re-annotate to the concrete model."""
        ...

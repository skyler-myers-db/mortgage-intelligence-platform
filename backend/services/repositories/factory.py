"""Repository factory -- single choke point for backend wiring.

FastAPI routers inject one of these helpers via ``Depends(...)``. The
factories resolve the process-wide ``Databricks*Repository`` instances
(Slice 4 and onward); Genie is wired to the Databricks Conversation API
and fails visibly through the degraded-state path instead of substituting
local answer data.

Test processes override every factory through FastAPI's
``app.dependency_overrides[get_*_repository]`` so unit tests never
touch the warehouse -- see ``tests/conftest.py``.
"""
from __future__ import annotations

from threading import Lock

from backend.services.repositories.protocols import (
    BorrowerRepository,
    GenieAnswerRepository,
    GeoRepository,
    LeadRepository,
    OfferRepository,
    OutreachRepository,
    PortfolioRepository,
    SegmentRepository,
)

# Lazy process-wide singletons. Each factory returns the same instance
# on repeat calls so SQL client pools / keep-alive sockets persist
# across requests. Test processes bypass these via dependency_overrides.
_PORTFOLIO_REPO: PortfolioRepository | None = None
_SEGMENT_REPO: SegmentRepository | None = None
_LEAD_REPO: LeadRepository | None = None
_BORROWER_REPO: BorrowerRepository | None = None
_OFFER_REPO: OfferRepository | None = None
_OUTREACH_REPO: OutreachRepository | None = None
_GENIE_REPO: GenieAnswerRepository | None = None
_GEO_REPO: GeoRepository | None = None
_LOCK = Lock()


def _live_borrower_repo() -> BorrowerRepository:
    """Construct the Databricks borrower repo (only on first call)."""
    global _BORROWER_REPO
    if _BORROWER_REPO is not None:
        return _BORROWER_REPO
    from backend.services.databricks_sql import get_sql_client
    from backend.services.repositories.databricks_repo import (
        DatabricksBorrowerRepository,
    )

    with _LOCK:
        if _BORROWER_REPO is None:
            _BORROWER_REPO = DatabricksBorrowerRepository(get_sql_client())
        return _BORROWER_REPO


def get_portfolio_repository() -> PortfolioRepository:
    global _PORTFOLIO_REPO
    if _PORTFOLIO_REPO is not None:
        return _PORTFOLIO_REPO
    from backend.config.settings import settings
    from backend.services.databricks_sql import get_sql_client
    from backend.services.repositories.databricks_repo import (
        DatabricksPortfolioRepository,
    )

    with _LOCK:
        if _PORTFOLIO_REPO is None:
            _PORTFOLIO_REPO = DatabricksPortfolioRepository(
                get_sql_client(),
                # Portfolio preview is a 5.16M-row aggregate. Use the
                # longer Slice-13 preview TTL (default 120s) instead of
                # the stock 30s so burst-traffic cache misses don't
                # dominate /api/portfolio/preview p95.
                cache_ttl_s=settings.mip_portfolio_preview_ttl_s,
            )
        return _PORTFOLIO_REPO


def get_segment_repository() -> SegmentRepository:
    global _SEGMENT_REPO
    if _SEGMENT_REPO is not None:
        return _SEGMENT_REPO
    from backend.config.settings import settings
    from backend.services.databricks_sql import get_sql_client
    from backend.services.repositories.databricks_repo import (
        DatabricksSegmentRepository,
    )

    with _LOCK:
        if _SEGMENT_REPO is None:
            _SEGMENT_REPO = DatabricksSegmentRepository(
                get_sql_client(),
                cache_ttl_s=settings.mip_cache_ttl_s,
            )
        return _SEGMENT_REPO


def get_lead_repository() -> LeadRepository:
    global _LEAD_REPO
    if _LEAD_REPO is not None:
        return _LEAD_REPO
    from backend.services.databricks_sql import get_sql_client
    from backend.services.repositories.databricks_repo import (
        DatabricksLeadRepository,
    )

    with _LOCK:
        if _LEAD_REPO is None:
            _LEAD_REPO = DatabricksLeadRepository(get_sql_client())
        return _LEAD_REPO


def get_borrower_repository() -> BorrowerRepository:
    return _live_borrower_repo()


def get_offer_repository() -> OfferRepository:
    global _OFFER_REPO
    if _OFFER_REPO is not None:
        return _OFFER_REPO
    from backend.services.databricks_sql import get_sql_client
    from backend.services.repositories.databricks_repo import (
        DatabricksOfferRepository,
    )

    with _LOCK:
        if _OFFER_REPO is None:
            _OFFER_REPO = DatabricksOfferRepository(get_sql_client())
        return _OFFER_REPO


def get_outreach_repository() -> OutreachRepository:
    global _OUTREACH_REPO
    if _OUTREACH_REPO is not None:
        return _OUTREACH_REPO
    from backend.services.databricks_sql import get_sql_client
    from backend.services.repositories.databricks_repo import (
        DatabricksOutreachRepository,
    )

    with _LOCK:
        if _OUTREACH_REPO is None:
            # Outreach shares the borrower repo's read path so pool use
            # and projection logic stay in one place.
            borrower_repo = _live_borrower_repo()
            # mypy can't statically prove _live_borrower_repo() returned
            # the concrete DatabricksBorrowerRepository, but at runtime
            # it always does.
            _OUTREACH_REPO = DatabricksOutreachRepository(
                get_sql_client(), borrower_repo  # type: ignore[arg-type]
            )
        return _OUTREACH_REPO


def get_geo_repository() -> GeoRepository:
    """Return the Databricks-backed per-state rollup repository.

    Used by ``/api/geo/state-rollups`` to feed the USChoroplethMap
    hover/fill with real numbers from ``mip.gold.funnel_snapshot_daily``
    and current geography rollups.
    """
    global _GEO_REPO
    if _GEO_REPO is not None:
        return _GEO_REPO
    from backend.services.databricks_sql import get_sql_client
    from backend.services.repositories.databricks_repo import (
        DatabricksGeoRepository,
    )

    with _LOCK:
        if _GEO_REPO is None:
            _GEO_REPO = DatabricksGeoRepository(get_sql_client())
        return _GEO_REPO


def get_genie_answer_repository() -> GenieAnswerRepository:
    """Return the live Genie repository backed by the real Mortgage
    Lead Intelligence space.

    Hits the Databricks Genie Conversation API through
    ``ResilientGenieClient`` (breaker key ``"genie"``). If Genie is
    unavailable, the repository returns an honest "warming up" degraded
    message with static follow-up suggestions only; it does not serve a
    local answer body or table rows.
    """
    global _GENIE_REPO
    if _GENIE_REPO is not None:
        return _GENIE_REPO
    from backend.services.databricks_sql import get_sql_client
    from backend.services.genie_client import get_genie_client
    from backend.services.repositories.databricks_repo import (
        DatabricksGenieRepository,
    )

    with _LOCK:
        if _GENIE_REPO is None:
            _GENIE_REPO = DatabricksGenieRepository(
                get_genie_client(),
                get_sql_client(),
            )
        return _GENIE_REPO


def _reset_singletons_for_tests() -> None:
    """Test helper -- drop every cached repository so
    ``tests/conftest.py`` can rewire dependency_overrides cleanly.
    NOT for production use.
    """
    global _PORTFOLIO_REPO, _SEGMENT_REPO, _LEAD_REPO, _BORROWER_REPO
    global _OFFER_REPO, _OUTREACH_REPO, _GENIE_REPO, _GEO_REPO
    with _LOCK:
        _PORTFOLIO_REPO = None
        _SEGMENT_REPO = None
        _LEAD_REPO = None
        _BORROWER_REPO = None
        _OFFER_REPO = None
        _OUTREACH_REPO = None
        _GENIE_REPO = None
        _GEO_REPO = None

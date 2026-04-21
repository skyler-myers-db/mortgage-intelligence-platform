"""Repository factory -- single choke point for backend wiring.

FastAPI routers inject one of these helpers via ``Depends(...)``. The
factories resolve the process-wide ``Databricks*Repository`` instances
(Slice 4 and onward); Genie stays on the in-process deterministic
catalog until Slice 7 grounds it against the real semantic views.

Test processes override every factory through FastAPI's
``app.dependency_overrides[get_*_repository]`` so unit tests never
touch the warehouse -- see ``tests/conftest.py``.
"""
from __future__ import annotations

from threading import Lock

from backend.services.repositories.protocols import (
    BorrowerRepository,
    GenieAnswerRepository,
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
                cache_ttl_s=settings.mip_cache_ttl_s,
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


def get_genie_answer_repository() -> GenieAnswerRepository:
    """Return the deterministic Genie catalog -- not Databricks-backed yet.

    Slice 7 replaces this with a real Genie API client grounded against
    ``mip_demo.semantics.*``. The current in-memory catalog stays the
    booth path because it's zero-latency and evidence-cited.
    """
    global _GENIE_REPO
    if _GENIE_REPO is not None:
        return _GENIE_REPO
    # Inline a tiny Protocol-compliant adapter that delegates to the
    # deterministic responder. This replaces the old
    # ``InProcessMockGenieAnswerRepository`` living under tests/fixtures;
    # production code cannot import from tests/.
    from backend.services.genie_answers import GenieMessageResponse as _GMR
    from backend.services.genie_answers import respond as _respond

    class _GenieAnswerAdapter:
        def respond(self, question: str) -> _GMR:  # pragma: no cover -- trivial
            return _respond(question)

    with _LOCK:
        if _GENIE_REPO is None:
            _GENIE_REPO = _GenieAnswerAdapter()
        return _GENIE_REPO


def _reset_singletons_for_tests() -> None:
    """Test helper -- drop every cached repository so
    ``tests/conftest.py`` can rewire dependency_overrides cleanly.
    NOT for production use.
    """
    global _PORTFOLIO_REPO, _SEGMENT_REPO, _LEAD_REPO, _BORROWER_REPO
    global _OFFER_REPO, _OUTREACH_REPO, _GENIE_REPO
    with _LOCK:
        _PORTFOLIO_REPO = None
        _SEGMENT_REPO = None
        _LEAD_REPO = None
        _BORROWER_REPO = None
        _OFFER_REPO = None
        _OUTREACH_REPO = None
        _GENIE_REPO = None

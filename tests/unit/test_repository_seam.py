"""Guards the Protocol seam introduced in Slice 0.

The invariant these tests protect: FastAPI routers under
``backend/api/`` depend on ``backend.services.repositories`` Protocols
via ``Depends(get_*_repository)``, never on ``backend.services.mock_data``
directly. When Slice 4 lands, the factory swaps in
``Databricks<Domain>Repository`` without touching router code — but
only if the routers have not quietly re-grown a direct mock_data
import in the meantime. These tests are the tripwire.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from backend.services.repositories import (
    BorrowerRepository,
    GenieAnswerRepository,
    LeadRepository,
    OfferRepository,
    OutreachRepository,
    PortfolioRepository,
    SegmentRepository,
    get_borrower_repository,
    get_genie_answer_repository,
    get_lead_repository,
    get_offer_repository,
    get_outreach_repository,
    get_portfolio_repository,
    get_segment_repository,
)

# The six routers the Slice-0 rewire targeted. Kept as a module-level
# tuple so the tripwire is obvious when someone adds a seventh.
REWIRED_ROUTERS: tuple[str, ...] = (
    "portfolio.py",
    "leads.py",
    "segments.py",
    "borrowers.py",
    "offers.py",
    "outreach.py",
)


def _router_source(name: str) -> str:
    path = Path(__file__).resolve().parents[2] / "backend" / "api" / name
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize("router_file", REWIRED_ROUTERS)
def test_rewired_routers_do_not_import_mock_data(router_file: str) -> None:
    """Slice-0 acceptance: no rewired router imports ``mock_data``
    directly. If this fails, the Slice-4 swap to Databricks repos will
    silently skip the offending router and the booth demo will diverge
    from real-data in one spot nobody notices until production.
    """
    source = _router_source(router_file)
    # Accept neither `from backend.services import mock_data` nor a
    # sneaky `import backend.services.mock_data`.
    assert "from backend.services import mock_data" not in source, (
        f"{router_file} still imports mock_data directly — should use repositories."
    )
    assert "backend.services.mock_data" not in source, (
        f"{router_file} references backend.services.mock_data — should use repositories."
    )


@pytest.mark.parametrize("router_file", REWIRED_ROUTERS)
def test_rewired_routers_use_depends_on_repositories(router_file: str) -> None:
    """Every rewired router must name at least one Protocol factory in
    a ``Depends(...)`` signature — the wire that FastAPI uses to inject
    the repository. Catches accidental regressions like someone
    replacing a Depends with a module-level singleton import.
    """
    source = _router_source(router_file)
    assert "from backend.services.repositories import" in source, (
        f"{router_file} does not import from backend.services.repositories"
    )
    # At least one Depends(get_*_repository) reference anywhere in the file.
    assert re.search(r"Depends\(get_\w+_repository\)", source), (
        f"{router_file} has no Depends(get_*_repository) — Protocol seam bypassed."
    )


def test_factories_return_protocol_compliant_implementations() -> None:
    """The in-process mocks must satisfy their Protocols structurally.
    ``@runtime_checkable`` means ``isinstance`` checks the method names
    only (not their signatures) — a weaker guarantee than static typing
    but still catches dropped methods during a future refactor.
    """
    assert isinstance(get_portfolio_repository(), PortfolioRepository)
    assert isinstance(get_segment_repository(), SegmentRepository)
    assert isinstance(get_lead_repository(), LeadRepository)
    assert isinstance(get_borrower_repository(), BorrowerRepository)
    assert isinstance(get_offer_repository(), OfferRepository)
    assert isinstance(get_outreach_repository(), OutreachRepository)
    assert isinstance(get_genie_answer_repository(), GenieAnswerRepository)


def test_factories_are_process_singletons() -> None:
    """Calling a factory twice returns the same instance — the Slice-4
    Databricks repos will own connection pools, and the factory is the
    right place to own the single-process lifetime.
    """
    assert get_portfolio_repository() is get_portfolio_repository()
    assert get_borrower_repository() is get_borrower_repository()
    assert get_lead_repository() is get_lead_repository()

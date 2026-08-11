"""Guards the Protocol seam introduced in Slice 0, hardened in Slice 4.

The invariant these tests protect: FastAPI routers under
``backend/api/`` depend on ``backend.services.repositories`` Protocols
via ``Depends(get_*_repository)``, never on any synthetic-population
module. Slice 4 moved the synthetic fixtures to
``tests/fixtures/mock_population.py`` and
``tests/fixtures/in_process_repos.py``; nothing under ``backend/`` is
allowed to reference either. If a future edit re-introduces a direct
import path, the factory swap to real data silently skips that router
and the UI diverges in one spot nobody notices until
production. These tests are the tripwire.
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

from backend.main import app
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
    """No rewired router imports a synthetic-population module.

    Slice 4 moved the synthetic data out of ``backend/`` entirely, so
    the forbidden patterns are:
      - ``backend.services.mock_data``          (Slice-0 legacy path)
      - ``tests.fixtures.mock_population``      (Slice-4 test fixture)
      - ``tests.fixtures.in_process_repos``     (Slice-4 test fixture)

    If a router re-grows any of these, the production path is
    contaminated by synthetic data and the parity between warehouse
    rows and what the UI renders is broken.
    """
    source = _router_source(router_file)
    forbidden = (
        "backend.services.mock_data",
        "tests.fixtures.mock_population",
        "tests.fixtures.in_process_repos",
    )
    for token in forbidden:
        assert token not in source, (
            f"{router_file} imports forbidden synthetic path {token!r} -- "
            f"routers must depend only on the Protocol seam."
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


def test_dependency_overrides_satisfy_protocols() -> None:
    """Under the test harness every factory resolves via FastAPI's
    ``dependency_overrides`` to an in-process stub. Each stub must be
    structurally Protocol-compliant -- ``@runtime_checkable`` means
    ``isinstance`` checks method names only (not signatures), a weaker
    guarantee than static typing but still catches dropped methods.
    """
    overrides = app.dependency_overrides
    assert isinstance(overrides[get_portfolio_repository](), PortfolioRepository)
    assert isinstance(overrides[get_segment_repository](), SegmentRepository)
    assert isinstance(overrides[get_lead_repository](), LeadRepository)
    assert isinstance(overrides[get_borrower_repository](), BorrowerRepository)
    assert isinstance(overrides[get_offer_repository](), OfferRepository)
    assert isinstance(overrides[get_outreach_repository](), OutreachRepository)
    assert isinstance(overrides[get_genie_answer_repository](), GenieAnswerRepository)


def test_dependency_overrides_accept_every_protocol_parameter() -> None:
    """Method NAMES are not the contract; parameters are.

    ``@runtime_checkable`` compares names only, so a double can satisfy
    ``isinstance`` while rejecting an argument the router really sends. That
    is what happened to the Lead Queue: ``LeadRepository.list`` grew
    ``min_opportunity_score`` / ``min_rate_spread_bps`` for governed Genie
    cohort replay, the in-process double did not, and every floor-carrying
    cohort raised ``TypeError: got an unexpected keyword argument`` -- an
    opaque 500 rather than a readable assertion (adversarial review
    2026-08-11). This closes the gap for every repository at once.
    """

    overrides = app.dependency_overrides
    pairs = (
        (PortfolioRepository, get_portfolio_repository),
        (SegmentRepository, get_segment_repository),
        (LeadRepository, get_lead_repository),
        (BorrowerRepository, get_borrower_repository),
        (OfferRepository, get_offer_repository),
        (OutreachRepository, get_outreach_repository),
        (GenieAnswerRepository, get_genie_answer_repository),
    )
    drift: list[str] = []
    for protocol, factory in pairs:
        double = overrides[factory]()
        for name, member in vars(protocol).items():
            if name.startswith("_") or not callable(member):
                continue
            implementation = getattr(double, name, None)
            if implementation is None:
                drift.append(f"{type(double).__name__}.{name} is missing")
                continue
            declared = inspect.signature(member).parameters
            accepted = inspect.signature(implementation).parameters
            if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in accepted.values()):
                # `**kwargs` swallows the drift instead of reporting it, so
                # the double must name what it accepts.
                drift.append(f"{type(double).__name__}.{name} hides drift behind **kwargs")
                continue
            missing = [p for p in declared if p != "self" and p not in accepted]
            if missing:
                drift.append(f"{type(double).__name__}.{name} rejects {missing}")
    assert not drift, "test doubles diverged from the repository Protocols: " + "; ".join(drift)


def test_every_factory_is_overridden_under_pytest() -> None:
    """Slice-4 guarantee: no unit test ever hits the warehouse. The
    conftest must install an override for every live factory. If a new
    factory is added, this test fails until it's wired into the
    dependency-override session fixture.
    """
    overrides = app.dependency_overrides
    required = [
        get_portfolio_repository,
        get_segment_repository,
        get_lead_repository,
        get_borrower_repository,
        get_offer_repository,
        get_outreach_repository,
        get_genie_answer_repository,
    ]
    missing = [fn.__name__ for fn in required if fn not in overrides]
    assert not missing, (
        f"conftest is missing dependency overrides for: {missing}. "
        "Every production factory MUST be stubbed for unit tests."
    )

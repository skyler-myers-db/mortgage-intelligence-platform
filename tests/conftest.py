"""Shared pytest fixtures for the Module 0 backend.

Slice-4 invariant: the production FastAPI app wires every repository to
the live Databricks SQL warehouse. Unit tests must never open a
warehouse connection; we achieve that by overriding each factory via
``app.dependency_overrides`` with the synthetic in-process
implementations in ``tests/fixtures/in_process_repos.py``.

The override is installed by an autouse session fixture so every unit-
test module automatically sees the stubbed routers. Tests that want to
swap in a custom stub for a single route can layer their own override
on top and clear it in teardown -- dependency_overrides is a plain
dict.
"""
from __future__ import annotations

from collections.abc import Iterator

import pytest

from backend.main import app
from backend.services.repositories import (
    get_borrower_repository,
    get_genie_answer_repository,
    get_lead_repository,
    get_offer_repository,
    get_outreach_repository,
    get_portfolio_repository,
    get_segment_repository,
)
from tests.fixtures.in_process_repos import (
    InProcessMockBorrowerRepository,
    InProcessMockGenieAnswerRepository,
    InProcessMockLeadRepository,
    InProcessMockOfferRepository,
    InProcessMockOutreachRepository,
    InProcessMockPortfolioRepository,
    InProcessMockSegmentRepository,
)


@pytest.fixture(scope="session", autouse=True)
def _install_dependency_overrides() -> Iterator[None]:
    """Swap every live repository factory for its in-process stub.

    ``scope="session"`` + ``autouse=True`` means the override is active
    for the whole test run -- no individual test needs to remember to
    apply it. Teardown restores the original (empty) overrides dict so
    pytest can be re-entered cleanly in watch mode.
    """
    portfolio = InProcessMockPortfolioRepository()
    segment = InProcessMockSegmentRepository()
    lead = InProcessMockLeadRepository()
    borrower = InProcessMockBorrowerRepository()
    offer = InProcessMockOfferRepository()
    outreach = InProcessMockOutreachRepository()
    genie = InProcessMockGenieAnswerRepository()

    app.dependency_overrides[get_portfolio_repository] = lambda: portfolio
    app.dependency_overrides[get_segment_repository] = lambda: segment
    app.dependency_overrides[get_lead_repository] = lambda: lead
    app.dependency_overrides[get_borrower_repository] = lambda: borrower
    app.dependency_overrides[get_offer_repository] = lambda: offer
    app.dependency_overrides[get_outreach_repository] = lambda: outreach
    app.dependency_overrides[get_genie_answer_repository] = lambda: genie
    try:
        yield
    finally:
        app.dependency_overrides.clear()

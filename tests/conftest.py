"""Shared pytest fixtures for the Module 0 backend.

Slice-4 invariant: the production FastAPI app wires every repository to
the live Databricks SQL warehouse. Unit tests must never open a
warehouse connection; we achieve that by overriding each factory via
``app.dependency_overrides`` with the synthetic in-process
implementations in ``tests/fixtures/in_process_repos.py``.

Slice 5 extends the override set to cover the audit store and the
Lakebase client so no test opens a Postgres connection either. The
audit surface is served by an ``InMemoryAuditStore`` (fast, unit-test
friendly) and the Lakebase client is a minimal fake that records
execute / fetchone / fetchall calls for assertion.

The override is installed by an autouse session fixture so every unit-
test module automatically sees the stubbed routers. Tests that want to
swap in a custom stub for a single route can layer their own override
on top and clear it in teardown -- dependency_overrides is a plain
dict.
"""
from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from backend.main import app
from backend.services.audit_store import InMemoryAuditStore, get_audit_store
from backend.services.lakebase import get_lakebase_client
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


class _FakeLakebaseClient:
    """Test-only Lakebase stand-in.

    Records every execute / fetchone / fetchall call so assertions
    can introspect what would have been written. Returns a
    deterministic shape from ``fetchone`` (used by the Lakebase audit
    store's INSERT ... RETURNING) and an empty list from ``fetchall``.
    """

    def __init__(self) -> None:
        self.executes: list[tuple[str, dict[str, Any]]] = []
        self.fetchones: list[tuple[str, dict[str, Any]]] = []
        self.fetchalls: list[tuple[str, dict[str, Any], int]] = []

    def execute(self, sql: str, params: dict[str, Any] | None = None) -> None:
        self.executes.append((sql, params or {}))

    def executemany(self, sql: str, params_list: list[dict[str, Any]]) -> None:
        for p in params_list:
            self.executes.append((sql, p))

    def fetchone(
        self, sql: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        self.fetchones.append((sql, params or {}))
        from datetime import UTC, datetime
        from uuid import uuid4

        return {"audit_id": uuid4(), "event_at": datetime.now(UTC)}

    def fetchall(
        self,
        sql: str,
        params: dict[str, Any] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        self.fetchalls.append((sql, params or {}, limit))
        return []


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
    audit = InMemoryAuditStore()
    lakebase = _FakeLakebaseClient()

    app.dependency_overrides[get_portfolio_repository] = lambda: portfolio
    app.dependency_overrides[get_segment_repository] = lambda: segment
    app.dependency_overrides[get_lead_repository] = lambda: lead
    app.dependency_overrides[get_borrower_repository] = lambda: borrower
    app.dependency_overrides[get_offer_repository] = lambda: offer
    app.dependency_overrides[get_outreach_repository] = lambda: outreach
    app.dependency_overrides[get_genie_answer_repository] = lambda: genie
    app.dependency_overrides[get_audit_store] = lambda: audit
    app.dependency_overrides[get_lakebase_client] = lambda: lakebase
    try:
        yield
    finally:
        app.dependency_overrides.clear()

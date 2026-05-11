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
from fastapi.testclient import TestClient

from backend.main import app
from backend.services.admin_rules import (
    AdminRulesService,
    get_admin_rules_service,
)
from backend.services.audit_store import InMemoryAuditStore, get_audit_store
from backend.services.lakebase import get_lakebase_client
from backend.services.repositories import (
    get_borrower_repository,
    get_genie_answer_repository,
    get_geo_repository,
    get_lead_repository,
    get_offer_repository,
    get_outreach_repository,
    get_portfolio_repository,
    get_segment_repository,
)
from backend.services.workspace_store import (
    InMemoryWorkspaceStore,
    get_workspace_store,
)
from tests.fixtures.in_process_repos import (
    InProcessMockBorrowerRepository,
    InProcessMockGenieAnswerRepository,
    InProcessMockGeoRepository,
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

        if "FROM mip_app.approvals" in sql and "request_id" in sql:
            return None
        return {"audit_id": uuid4(), "event_at": datetime.now(UTC)}

    def fetchall(
        self,
        sql: str,
        params: dict[str, Any] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        self.fetchalls.append((sql, params or {}, limit))
        return []


class _FakeAdminSqlClient:
    """Test-only SQL client for the admin-rules service.

    Pre-programs two query shapes the service issues:

    * ``SELECT ... FROM mip.ref.offer_rules_config`` -> canonical five
      threshold rows mirroring the seed file.
    * ``SELECT ... FROM mip.gold.borrower_360`` -> operating market-rate row
      injected into the admin rules response.
    * ``DESCRIBE DETAIL`` / ``SELECT COUNT(*)`` against each silver
      table -> deterministic row counts + a fixed ``lastModified``
      timestamp so the sources endpoint reports LIVE for every wired
      source in unit tests.

    Any query not matching the above returns an empty list; tests that
    want to exercise 503 paths install their own fake that raises.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []

    def execute(
        self,
        statement: str,
        parameters: Any = None,
    ) -> list[dict[str, Any]]:
        self.calls.append(statement)
        s = statement.strip().upper()
        # Catalog-agnostic match: ``.REF.OFFER_RULES_CONFIG`` so the stub
        # keeps matching when ``MIP_DEFAULT_CATALOG`` is overridden (e.g.
        # ``mip_demo`` locally, ``mip_prod`` in prod deploys).
        if ".REF.OFFER_RULES_CONFIG" in s:
            return [
                {"key": "mip_min_spread_bps",           "value": 75.0,    "unit": "bps",           "label": "Min spread (bps)",            "description": "desc", "sort_order": 1, "last_updated": "2026-04-22 12:00:00"},
                {"key": "mip_min_equity_pct",           "value": 15.0,    "unit": "pct",           "label": "Min equity (%)",              "description": "desc", "sort_order": 2, "last_updated": "2026-04-22 12:00:00"},
                {"key": "mip_heloc_equity_min_pct",     "value": 35.0,    "unit": "pct",           "label": "HELOC equity floor (%)",      "description": "desc", "sort_order": 3, "last_updated": "2026-04-22 12:00:00"},
                {"key": "mip_cashout_equity_min_pct",   "value": 25.0,    "unit": "pct",           "label": "Cash-out equity floor (%)",   "description": "desc", "sort_order": 4, "last_updated": "2026-04-22 12:00:00"},
                {"key": "mip_retention_min_spread_bps", "value": 50.0,    "unit": "bps",           "label": "Retention min spread (bps)",  "description": "desc", "sort_order": 5, "last_updated": "2026-04-22 12:00:00"},
            ]
        if ".GOLD.BORROWER_360" in s and "MARKET_RATE_FRACTION" in s:
            return [{"rate_fraction": 0.0637, "last_updated": "2026-05-07 12:00:00"}]
        if s.startswith("DESCRIBE DETAIL"):
            return [{"lastModified": "2026-04-22T12:00:00.000Z"}]
        if "COUNT(*)" in s and "FROM" in s:
            return [{"row_count": 1000}]
        return []

    def execute_one(
        self,
        statement: str,
        parameters: Any = None,
    ) -> dict[str, Any] | None:
        rows = self.execute(statement, parameters)
        return rows[0] if rows else None


# -----------------------------------------------------------------------
# Admin-header auto-injection. Slice-RBAC wired ``require_admin`` onto
# every ``/api/admin/*`` route, so unit tests that hit the admin surface
# must now carry an ``X-Forwarded-Groups`` header including ``mip-admin``.
#
# Rather than thread ``headers=...`` through every call site, we wrap
# ``TestClient.__init__`` at conftest import time (NOT inside a fixture)
# so any module that does ``client = TestClient(app)`` at import time --
# the existing pattern in ``test_api_routes.py`` and
# ``test_admin_rules.py`` -- picks up the default header. Because
# ``conftest.py`` is imported by pytest before collection of peer
# modules, the wrap is in place before those module-level clients are
# constructed.
#
# Tests that need to exercise the DENY path (403 on missing / wrong
# group) pass ``headers={"X-Forwarded-Groups": ""}`` explicitly on the
# call -- httpx merges per-call headers over instance defaults, so an
# empty-value override wins.
# -----------------------------------------------------------------------


_ADMIN_HEADERS: dict[str, str] = {"X-Forwarded-Groups": "mip-admin"}


def _wrap_testclient_with_admin_headers() -> None:
    """One-shot wrap of ``TestClient.__init__`` applied at conftest load.

    Idempotent -- re-running under pytest-watch/pytest-xdist is a no-op
    because we set a sentinel on the class.
    """
    if getattr(TestClient, "_mip_admin_header_wrap_installed", False):
        return
    original_init = TestClient.__init__

    def _patched_init(self: TestClient, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        for k, v in _ADMIN_HEADERS.items():
            self.headers.setdefault(k, v)

    TestClient.__init__ = _patched_init  # type: ignore[method-assign]
    TestClient._mip_admin_header_wrap_installed = True  # type: ignore[attr-defined]


_wrap_testclient_with_admin_headers()


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
    geo = InProcessMockGeoRepository()
    audit = InMemoryAuditStore()
    lakebase = _FakeLakebaseClient()
    workspace = InMemoryWorkspaceStore()
    admin_rules = AdminRulesService(_FakeAdminSqlClient())

    app.dependency_overrides[get_portfolio_repository] = lambda: portfolio
    app.dependency_overrides[get_segment_repository] = lambda: segment
    app.dependency_overrides[get_lead_repository] = lambda: lead
    app.dependency_overrides[get_borrower_repository] = lambda: borrower
    app.dependency_overrides[get_offer_repository] = lambda: offer
    app.dependency_overrides[get_outreach_repository] = lambda: outreach
    app.dependency_overrides[get_genie_answer_repository] = lambda: genie
    app.dependency_overrides[get_geo_repository] = lambda: geo
    app.dependency_overrides[get_audit_store] = lambda: audit
    app.dependency_overrides[get_lakebase_client] = lambda: lakebase
    app.dependency_overrides[get_workspace_store] = lambda: workspace
    app.dependency_overrides[get_admin_rules_service] = lambda: admin_rules
    try:
        yield
    finally:
        app.dependency_overrides.clear()

"""/api/leads ``limit`` query param + ``X-Truncated-At`` header contract.

Hole-finder round 2 #24 (2026-04-23): the repository previously hardcoded
``LIMIT 500`` in the SQL, which silently capped every lender at 500 in-
the-money borrowers regardless of their book size. These tests pin:

1. The default limit (500) still applies when the caller omits ``limit``.
2. Callers can ask for up to ``MAX_LEAD_LIMIT`` (5000); anything larger
   is rejected with HTTP 422.
3. Non-positive limits are rejected with HTTP 422 (prevents ``LIMIT 0``
   masquerading as "no results").
4. When the repository returns exactly ``limit`` rows the router emits
   ``X-Truncated-At`` so the LeadTable footer can show "capped at N".
5. A short result set (under the cap) does NOT emit the header.
"""
from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from backend.api.leads import DEFAULT_LEAD_LIMIT, MAX_LEAD_LIMIT
from backend.main import app
from backend.services.repositories.databricks_repo import DatabricksLeadRepository


def test_default_limit_constants_align_with_repository():
    """Router's default / max must match the repository's — drifting would
    mean the router advertises a limit it can't actually plumb."""
    assert DEFAULT_LEAD_LIMIT == DatabricksLeadRepository.DEFAULT_LIMIT
    assert MAX_LEAD_LIMIT == DatabricksLeadRepository.MAX_LIMIT


def test_bound_limit_clamps_caller_input():
    """Defensive clamping at the repo layer: ``None`` / non-positive
    fall back to DEFAULT_LIMIT; values above MAX_LIMIT clamp down."""
    assert DatabricksLeadRepository._bound_limit(None) == DatabricksLeadRepository.DEFAULT_LIMIT
    assert DatabricksLeadRepository._bound_limit(0) == DatabricksLeadRepository.DEFAULT_LIMIT
    assert DatabricksLeadRepository._bound_limit(-1) == DatabricksLeadRepository.DEFAULT_LIMIT
    assert DatabricksLeadRepository._bound_limit(42) == 42
    assert DatabricksLeadRepository._bound_limit(100_000) == DatabricksLeadRepository.MAX_LIMIT


def test_segment_filter_clause_supports_multi_select_all_mode():
    """Segments-page multi-select is a narrowing filter: selecting ITM
    plus equity should require both segment codes, not either one."""
    clause, params = DatabricksLeadRepository._segment_filter_clause(
        segment=None,
        segment_codes=["itm", "equity"],
        segment_mode="all",
    )
    assert clause == (
        "array_contains(segment_codes, :segment_0) AND "
        "array_contains(segment_codes, :segment_1)"
    )
    assert params == {"segment_0": "itm", "segment_1": "equity"}


def test_limit_query_param_rejects_zero_and_negative():
    """``?limit=0`` and ``?limit=-1`` must be 422s — a caller asking for
    no rows is almost certainly a bug, not a valid request."""
    client = TestClient(app)
    assert client.get("/api/leads?limit=0").status_code == 422
    assert client.get("/api/leads?limit=-5").status_code == 422


def test_limit_query_param_rejects_above_max():
    """Above MAX_LEAD_LIMIT must 422 rather than silently clamp — it's a
    programming error the caller needs to notice, not paper over."""
    client = TestClient(app)
    too_large = MAX_LEAD_LIMIT + 1
    assert client.get(f"/api/leads?limit={too_large}").status_code == 422


def test_truncation_header_present_when_limit_reached(monkeypatch: Any):
    """When the repository returns exactly ``limit`` rows, the router
    sets ``X-Truncated-At`` so the UI can render 'Showing N — refine
    filters'."""
    from backend.api import leads as leads_api
    from backend.schemas.lead import LeadSummary
    from backend.services.repositories import get_lead_repository

    class _FullRepo:
        def list(
            self,
            segment: str | None,
            portfolio_id: str | None,
            limit: int | None = None,
            state: str | None = None,
            zip_code: str | None = None,
            segment_codes: list[str] | None = None,
            segment_mode: str = "any",
        ) -> list[LeadSummary]:
            _ = (segment, portfolio_id, state, zip_code, segment_codes, segment_mode)
            # Emit exactly `limit` rows so the header kicks in.
            n = limit or DEFAULT_LEAD_LIMIT
            return [
                LeadSummary(
                    borrower_id=f"B-{i:05d}",
                    display_name="Synthetic Owner",
                    city="Chicago",
                    state="IL",
                    zip="60611",
                    segment_codes=["itm"],
                    equity_estimate=100000,
                    rate_spread_bps=150,
                    opportunity_score=80,
                    confidence=80,
                    recommended_offer="refi",
                    why_now="test",
                    evidence_ids=[],
                    approval_status="pending",
                )
                for i in range(n)
            ]

    _ = leads_api  # silence unused
    # Save + restore the session-level conftest override so this test
    # leaves the shared dependency graph untouched.
    prior = app.dependency_overrides.get(get_lead_repository)
    app.dependency_overrides[get_lead_repository] = _FullRepo
    try:
        client = TestClient(app)
        r = client.get("/api/leads?limit=5")
        assert r.status_code == 200
        assert r.headers.get("X-Truncated-At") == "5"
        assert len(r.json()) == 5
    finally:
        if prior is None:
            app.dependency_overrides.pop(get_lead_repository, None)
        else:
            app.dependency_overrides[get_lead_repository] = prior


def test_truncation_header_absent_when_under_limit():
    """If the resultset is smaller than ``limit``, the header must not be
    set — otherwise the UI would falsely advertise truncation."""
    from backend.schemas.lead import LeadSummary
    from backend.services.repositories import get_lead_repository

    class _SparseRepo:
        def list(
            self,
            segment: str | None,
            portfolio_id: str | None,
            limit: int | None = None,
            state: str | None = None,
            zip_code: str | None = None,
            segment_codes: list[str] | None = None,
            segment_mode: str = "any",
        ) -> list[LeadSummary]:
            _ = (segment, portfolio_id, limit, state, zip_code, segment_codes, segment_mode)
            return [
                LeadSummary(
                    borrower_id="B-00001",
                    display_name="Synthetic Owner",
                    city="Chicago",
                    state="IL",
                    zip="60611",
                    segment_codes=["itm"],
                    equity_estimate=100000,
                    rate_spread_bps=150,
                    opportunity_score=80,
                    confidence=80,
                    recommended_offer="refi",
                    why_now="test",
                    evidence_ids=[],
                    approval_status="pending",
                )
            ]

    prior = app.dependency_overrides.get(get_lead_repository)
    app.dependency_overrides[get_lead_repository] = _SparseRepo
    try:
        client = TestClient(app)
        r = client.get("/api/leads?limit=500")
        assert r.status_code == 200
        assert "X-Truncated-At" not in r.headers
        assert len(r.json()) == 1
    finally:
        if prior is None:
            app.dependency_overrides.pop(get_lead_repository, None)
        else:
            app.dependency_overrides[get_lead_repository] = prior

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

from pathlib import Path

from fastapi.testclient import TestClient

from backend.api.leads import DEFAULT_LEAD_LIMIT, MAX_LEAD_LIMIT
from backend.main import app
from backend.schemas.lead import LeadSummary
from backend.services.repositories.databricks_repo import DatabricksLeadRepository
from backend.services.scoring import HIGH_OPPORTUNITY_THRESHOLD

FUNNEL_STAGE_PREDICATES = {
    "addressable": "",
    "in_the_money": "b.in_the_money = TRUE",
    "high_opportunity": f"b.opportunity_score >= {HIGH_OPPORTUNITY_THRESHOLD}",
    "offer_recommended": (
        "b.recommended_offer_code IS NOT NULL "
        "AND b.recommended_offer_code <> 'nurture'"
    ),
    "approved": "COALESCE(ls.approval_status, 'pending') = 'approved'",
    "actioned": "COALESCE(ls.outreach_status, 'none') = 'actioned'",
}

FUNNEL_SNAPSHOT_EXPRESSIONS = {
    "addressable": "CAST(COUNT(*) AS INT)                                                       AS addressable_borrowers",
    "in_the_money": (
        "CAST(SUM(CASE WHEN in_the_money THEN 1 ELSE 0 END) AS INT)                  "
        "AS in_the_money_borrowers"
    ),
    "high_opportunity": (
        "CAST(SUM(CASE WHEN mip.gold.fn_high_opportunity(opportunity_score) "
        "THEN 1 ELSE 0 END) AS INT) AS high_opportunity_borrowers"
    ),
    "offer_recommended": (
        "recommended_offer_code IS NOT NULL\n"
        "                     AND recommended_offer_code <> 'nurture'"
    ),
    "approved": "CAST(SUM(CASE WHEN approval_status = 'approved' THEN 1 ELSE 0 END) AS INT)",
    "actioned": "CAST(SUM(CASE WHEN outreach_status = 'actioned' THEN 1 ELSE 0 END) AS INT)",
}


def _lead(
    borrower_id: str,
    *,
    state: str = "IL",
    zip_code: str = "60617",
    segment_codes: list[str] | None = None,
) -> LeadSummary:
    display_token = borrower_id.replace("B-", "").lower()
    if len(display_token) < 3:
        display_token = display_token.ljust(3, "x")
    return LeadSummary(
        borrower_id=borrower_id,
        display_name=f"Borrower {display_token}",
        city="Chicago",
        state=state,
        zip=zip_code,
        segment_codes=segment_codes or ["itm"],
        equity_estimate=100000,
        rate_spread_bps=150,
        opportunity_score=80,
        confidence=80,
        recommended_offer="Refinance + HELOC",
        why_now="test",
        evidence_ids=[],
        approval_status="pending",
    )


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


def test_geo_in_clause_supports_multi_zip_and_state_lists() -> None:
    params: dict[str, object] = {}

    zip_clause = DatabricksLeadRepository._in_clause(
        column="zip",
        prefix="zip",
        values=["60617", "60628"],
        params=params,
    )
    state_clause = DatabricksLeadRepository._in_clause(
        column="state",
        prefix="state",
        values=["IL"],
        params=params,
    )

    assert zip_clause == "AND zip IN (:zip_0, :zip_1)"
    assert state_clause == "AND state = :state_0"
    assert params == {"zip_0": "60617", "zip_1": "60628", "state_0": "IL"}


def test_lead_repository_applies_portfolio_criteria_to_borrower_360_path() -> None:
    from backend.schemas.portfolio import PortfolioCriteria

    captured: dict[str, object] = {}

    class _Client:
        def execute(self, sql: str, params: dict[str, object] | None = None) -> list[dict[str, object]]:
            captured["sql"] = sql
            captured["params"] = params or {}
            return []

    repo = DatabricksLeadRepository(_Client())  # type: ignore[arg-type]
    rows = repo.list(
        segment=None,
        portfolio_id=None,
        portfolio_criteria=PortfolioCriteria(
            occupancy="Owner-occupied",
            lien_status="Open 1st lien",
            lender_relationship="Current customer",
            target_lender_ref="Competitor B",
            product="Refi",
            min_equity_pct_label="≥ 25%",
        ),
    )

    assert rows == []
    sql = str(captured["sql"])
    params = captured["params"]
    assert "borrower_360" in sql
    assert "lead_population" not in sql
    assert "is_owner_occupied = TRUE" in sql
    assert "current_lien_balance > 0" in sql
    assert "COALESCE(second_pos_amount, 0) = 0" in sql
    assert "is_current_customer = TRUE" in sql
    assert "current_lender_ref = :target_lender_ref" in sql
    assert "recommended_offer_code IN (:product_0, :product_1)" in sql
    assert "equity_pct >= :equity_floor" in sql
    assert params == {
        "target_lender_ref": "Competitor B",
        "product_0": "refi",
        "product_1": "refi_plus_heloc",
        "equity_floor": 25,
    }


def test_lead_repository_applies_exact_funnel_stage_predicates_to_borrower_360() -> None:
    captured: dict[str, object] = {}

    class _Client:
        def execute_one(
            self,
            sql: str,
            params: dict[str, object] | None = None,
        ) -> dict[str, object]:
            captured["sql"] = sql
            captured["params"] = params or {}
            return {"n": 23}

    repo = DatabricksLeadRepository(_Client(), cache_ttl_s=0)  # type: ignore[arg-type]

    assert repo.count(segment=None, portfolio_id=None, funnel_stage="approved") == 23

    sql = str(captured["sql"])
    assert "borrower_360" in sql
    assert "lead_population" not in sql
    assert FUNNEL_STAGE_PREDICATES["approved"] in sql
    assert captured["params"] == {}


def test_lead_repository_pins_every_funnel_stage_to_snapshot_predicate() -> None:
    class _Client:
        def execute_one(
            self,
            sql: str,
            params: dict[str, object] | None = None,
        ) -> dict[str, object]:
            _ = (sql, params)
            return {"n": 1}

    repo = DatabricksLeadRepository(_Client(), cache_ttl_s=0)  # type: ignore[arg-type]
    snapshot_sql = Path("sql/transformations/gold_funnel_snapshot_daily.sql").read_text()

    for stage, predicate in FUNNEL_STAGE_PREDICATES.items():
        clause = repo._funnel_stage_filter_clause(stage)
        assert clause == (f"AND {predicate}" if predicate else "")
        assert FUNNEL_SNAPSHOT_EXPRESSIONS[stage] in snapshot_sql


def test_lead_repository_applies_all_funnel_stages_through_borrower_360() -> None:
    captured_sql: dict[str, str] = {}

    class _Client:
        def execute_one(
            self,
            sql: str,
            params: dict[str, object] | None = None,
        ) -> dict[str, object]:
            _ = params
            captured_sql[current_stage] = sql
            return {"n": 1}

    repo = DatabricksLeadRepository(_Client(), cache_ttl_s=0)  # type: ignore[arg-type]

    for current_stage in FUNNEL_STAGE_PREDICATES:
        assert repo.count(segment=None, portfolio_id=None, funnel_stage=current_stage) == 1
        sql = captured_sql[current_stage]
        assert "borrower_360" in sql
        assert "lead_population" not in sql
        predicate = FUNNEL_STAGE_PREDICATES[current_stage]
        if predicate:
            assert predicate in sql


def test_lead_repository_addressable_funnel_stage_uses_full_borrower_360_population() -> None:
    captured: dict[str, object] = {}

    class _Client:
        def execute_one(
            self,
            sql: str,
            params: dict[str, object] | None = None,
        ) -> dict[str, object]:
            captured["sql"] = sql
            captured["params"] = params or {}
            return {"n": 5160000}

    repo = DatabricksLeadRepository(_Client(), cache_ttl_s=0)  # type: ignore[arg-type]

    assert repo.count(segment=None, portfolio_id=None, funnel_stage="addressable") == 5160000

    sql = str(captured["sql"])
    assert "borrower_360" in sql
    assert "lead_population" not in sql
    assert "recommended_offer_code" not in sql
    assert captured["params"] == {}


def test_lead_repository_caches_identical_list_and_count_reads() -> None:
    calls: dict[str, int] = {"execute": 0, "execute_one": 0}

    class _Client:
        def execute(
            self,
            sql: str,
            params: dict[str, object] | None = None,
        ) -> list[dict[str, object]]:
            _ = (sql, params)
            calls["execute"] += 1
            return [_lead("B-CACHE").model_dump(mode="python")]

        def execute_one(
            self,
            sql: str,
            params: dict[str, object] | None = None,
        ) -> dict[str, object]:
            _ = (sql, params)
            calls["execute_one"] += 1
            return {"n": 1}

    repo = DatabricksLeadRepository(_Client(), cache_ttl_s=60.0)  # type: ignore[arg-type]

    first = repo.list(segment=None, portfolio_id=None, limit=10)
    first[0].segment_codes.append("listed")
    second = repo.list(segment=None, portfolio_id=None, limit=10)

    assert calls["execute"] == 1
    assert second[0].borrower_id == "B-CACHE"
    assert second[0].segment_codes == ["itm"]
    assert repo.count(segment=None, portfolio_id=None) == 1
    assert repo.count(segment=None, portfolio_id=None) == 1
    assert calls["execute_one"] == 1


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


def test_segment_filter_clause_supports_multi_select_any_mode():
    """Genie/open-cohort routes can request broad OR semantics explicitly."""
    clause, params = DatabricksLeadRepository._segment_filter_clause(
        segment=None,
        segment_codes=["itm", "equity"],
        segment_mode="any",
    )
    assert clause == (
        "(array_contains(segment_codes, :segment_0) OR "
        "array_contains(segment_codes, :segment_1))"
    )
    assert params == {"segment_0": "itm", "segment_1": "equity"}


def test_leads_route_segment_mode_any_vs_all_changes_membership() -> None:
    from backend.services.repositories import get_lead_repository

    captured_modes: list[str] = []

    class _SegmentModeRepo:
        rows = [
            _lead("B-ITM", segment_codes=["itm"]),
            _lead("B-EQ", segment_codes=["equity"]),
            _lead("B-BOTH", segment_codes=["itm", "equity"]),
        ]

        @classmethod
        def _filter(
            cls,
            segment: str | None,
            segment_codes: list[str] | None,
            segment_mode: str,
        ) -> list[LeadSummary]:
            requested = set(segment_codes or ([segment] if segment else []))
            if not requested:
                return cls.rows
            if segment_mode == "all":
                return [
                    row
                    for row in cls.rows
                    if requested.issubset(set(row.segment_codes))
                ]
            return [
                row
                for row in cls.rows
                if bool(requested.intersection(set(row.segment_codes)))
            ]

        def list(
            self,
            segment: str | None,
            portfolio_id: str | None,
            limit: int | None = None,
            state: str | None = None,
            zip_code: str | None = None,
            county_fips: str | None = None,
            state_codes: list[str] | None = None,
            zip_codes: list[str] | None = None,
            borrower_ids: list[str] | None = None,
            segment_codes: list[str] | None = None,
            segment_mode: str = "any",
            target_lender_ref: str | None = None,
            cohort_id: str | None = None,
            portfolio_criteria: object | None = None,
            **_kwargs: object,
        ) -> list[LeadSummary]:
            _ = (
                segment,
                portfolio_id,
                limit,
                state,
                zip_code,
                state_codes,
                zip_codes,
                borrower_ids,
                target_lender_ref,
                cohort_id,
                portfolio_criteria,
                _kwargs,
            )
            captured_modes.append(segment_mode)
            return self._filter(segment, segment_codes, segment_mode)

        def count(
            self,
            segment: str | None,
            portfolio_id: str | None,
            state: str | None = None,
            zip_code: str | None = None,
            county_fips: str | None = None,
            state_codes: list[str] | None = None,
            zip_codes: list[str] | None = None,
            borrower_ids: list[str] | None = None,
            segment_codes: list[str] | None = None,
            segment_mode: str = "any",
            target_lender_ref: str | None = None,
            cohort_id: str | None = None,
            portfolio_criteria: object | None = None,
            **_kwargs: object,
        ) -> int:
            _ = (
                portfolio_id,
                state,
                zip_code,
                county_fips,
                state_codes,
                zip_codes,
                borrower_ids,
                target_lender_ref,
                cohort_id,
                portfolio_criteria,
                _kwargs,
            )
            return len(self._filter(segment, segment_codes, segment_mode))

    prior = app.dependency_overrides.get(get_lead_repository)
    app.dependency_overrides[get_lead_repository] = _SegmentModeRepo
    try:
        client = TestClient(app)

        any_response = client.get(
            "/api/leads?segment_codes=ITM,Equity&segment_mode=ANY&limit=5000"
        )
        all_response = client.get(
            "/api/leads?segment_codes=ITM,Equity&segment_mode=ALL&limit=5000"
        )
        itm_response = client.get("/api/leads?segment=itm&limit=5000")
        equity_response = client.get("/api/leads?segment=equity&limit=5000")
    finally:
        if prior is None:
            app.dependency_overrides.pop(get_lead_repository, None)
        else:
            app.dependency_overrides[get_lead_repository] = prior

    assert any_response.status_code == 200
    assert all_response.status_code == 200
    assert itm_response.status_code == 200
    assert equity_response.status_code == 200
    assert captured_modes == ["any", "all", "any", "any"]
    any_rows = any_response.json()
    all_rows = all_response.json()
    any_total = int(any_response.headers["X-Total-Matching"])
    all_total = int(all_response.headers["X-Total-Matching"])
    itm_total = int(itm_response.headers["X-Total-Matching"])
    equity_total = int(equity_response.headers["X-Total-Matching"])
    assert (itm_total, equity_total, any_total, all_total) == (2, 2, 3, 1)
    assert any_total == itm_total + equity_total - all_total
    assert len(any_rows) == any_total
    assert len(all_rows) == all_total
    assert len(any_rows) > len(all_rows)
    assert all(
        {"itm", "equity"}.issubset(set(row["segment_codes"]))
        for row in all_rows
    )
    assert any(
        "itm" in row["segment_codes"] and "equity" not in row["segment_codes"]
        for row in any_rows
    )
    assert any(
        "equity" in row["segment_codes"] and "itm" not in row["segment_codes"]
        for row in any_rows
    )


def test_leads_rejects_unknown_segment_codes() -> None:
    response = TestClient(app).get("/api/leads?segment_codes=itm,unknown")

    assert response.status_code == 422
    assert "unknown segment" in response.text


def test_leads_rejects_malformed_single_geo_params() -> None:
    client = TestClient(app)

    assert client.get("/api/leads?state=12").status_code == 422
    assert client.get("/api/leads?zip=abcde").status_code == 422
    assert client.get("/api/leads?county=abcde").status_code == 422


def test_leads_accepts_genie_cohort_route_query_lengths() -> None:
    from backend.services.repositories import get_lead_repository

    class _EmptyRepo:
        def list(self, *args: object, **kwargs: object) -> list[LeadSummary]:
            _ = (args, kwargs)
            return []

    zips = ",".join(f"{60000 + i:05d}" for i in range(101))
    borrower_ids = ",".join(f"B-{10000 + i:05d}" for i in range(100))
    previous = app.dependency_overrides.get(get_lead_repository)
    app.dependency_overrides[get_lead_repository] = _EmptyRepo
    try:
        client = TestClient(app)
        assert client.get(f"/api/leads?zips={zips}&limit=1").status_code == 200
        assert client.get(f"/api/leads?borrower_ids={borrower_ids}&limit=1").status_code == 200
    finally:
        if previous is None:
            app.dependency_overrides.pop(get_lead_repository, None)
        else:
            app.dependency_overrides[get_lead_repository] = previous


def test_leads_route_replays_portfolio_builder_criteria_to_repository() -> None:
    from backend.services.repositories import get_lead_repository

    captured: dict[str, object] = {}

    class _PortfolioCriteriaRepo:
        def list(
            self,
            segment: str | None,
            portfolio_id: str | None,
            limit: int | None = None,
            state: str | None = None,
            zip_code: str | None = None,
            county_fips: str | None = None,
            state_codes: list[str] | None = None,
            zip_codes: list[str] | None = None,
            borrower_ids: list[str] | None = None,
            segment_codes: list[str] | None = None,
            segment_mode: str = "any",
            target_lender_ref: str | None = None,
            cohort_id: str | None = None,
            portfolio_criteria: object | None = None,
            **_kwargs: object,
        ) -> list[LeadSummary]:
            captured.update(
                {
                    "segment": segment,
                    "portfolio_id": portfolio_id,
                    "limit": limit,
                    "state": state,
                    "zip_code": zip_code,
                    "state_codes": state_codes,
                    "zip_codes": zip_codes,
                    "borrower_ids": borrower_ids,
                    "segment_codes": segment_codes,
                    "segment_mode": segment_mode,
                    "target_lender_ref": target_lender_ref,
                    "cohort_id": cohort_id,
                    "portfolio_criteria": portfolio_criteria,
                    "extra_kwargs": _kwargs,
                }
            )
            return [_lead("B-PORT")]

    prior = app.dependency_overrides.get(get_lead_repository)
    app.dependency_overrides[get_lead_repository] = _PortfolioCriteriaRepo
    try:
        client = TestClient(app)
        response = client.get(
            "/api/leads",
            params={
                "geography": "All",
                "occupancy": "Owner-occupied",
                "lien_status": "Open 1st lien",
                "lender_relationship": "Current customer",
                "target_lender_ref": "Competitor B",
                "product": "Refi",
                "min_equity_pct_label": "≥ 25%",
            },
        )
    finally:
        if prior is None:
            app.dependency_overrides.pop(get_lead_repository, None)
        else:
            app.dependency_overrides[get_lead_repository] = prior

    assert response.status_code == 200
    assert captured["target_lender_ref"] == "Competitor B"
    criteria = captured["portfolio_criteria"]
    from backend.schemas.portfolio import PortfolioCriteria

    assert isinstance(criteria, PortfolioCriteria)
    assert criteria.geography == "All"
    assert criteria.occupancy == "Owner-occupied"
    assert criteria.lien_status == "Open 1st lien"
    assert criteria.lender_relationship == "Current customer"
    assert criteria.target_lender_ref == "Competitor B"
    assert criteria.product == "Refi"
    assert criteria.min_equity_pct_label == "≥ 25%"


def test_leads_route_audits_safe_portfolio_criteria() -> None:
    from backend.services.audit_store import get_audit_store
    from backend.services.repositories import get_lead_repository
    from tests.fixtures.in_memory_audit_store import InMemoryAuditStore

    audit = InMemoryAuditStore()
    prior_audit = app.dependency_overrides.get(get_audit_store)
    prior_repo = app.dependency_overrides.get(get_lead_repository)

    class _PortfolioAuditRepo:
        def list(self, **kwargs: object) -> list[LeadSummary]:
            _ = kwargs
            return [_lead("B-PORT")]

    app.dependency_overrides[get_audit_store] = lambda: audit
    app.dependency_overrides[get_lead_repository] = _PortfolioAuditRepo
    try:
        response = TestClient(app).get(
            "/api/leads",
            params={
                "geography": "All",
                "occupancy": "Owner-occupied",
                "target_lender_ref": "Competitor B",
                "limit": "10",
            },
            headers={"X-Forwarded-Email": "qa@example.com"},
        )
    finally:
        if prior_audit is None:
            app.dependency_overrides.pop(get_audit_store, None)
        else:
            app.dependency_overrides[get_audit_store] = prior_audit
        if prior_repo is None:
            app.dependency_overrides.pop(get_lead_repository, None)
        else:
            app.dependency_overrides[get_lead_repository] = prior_repo

    assert response.status_code == 200
    event = audit.list(limit=1)[0]
    assert event.action == "view_leads_ranked"
    assert event.payload_json["target_lender_ref"] == "Competitor B"
    assert event.payload_json["portfolio_criteria"] == {
        "geography": "All",
        "occupancy": "Owner-occupied",
        "marketing_eligibility": "Eligible only",
        "target_lender_ref": "Competitor B",
    }


def test_leads_route_passes_genie_multi_zip_cohort_to_repository() -> None:
    from backend.services.repositories import get_lead_repository

    captured: dict[str, object] = {}

    class _CohortRepo:
        def list(
            self,
            segment: str | None,
            portfolio_id: str | None,
            limit: int | None = None,
            state: str | None = None,
            zip_code: str | None = None,
            county_fips: str | None = None,
            state_codes: list[str] | None = None,
            zip_codes: list[str] | None = None,
            borrower_ids: list[str] | None = None,
            segment_codes: list[str] | None = None,
            segment_mode: str = "any",
            target_lender_ref: str | None = None,
            cohort_id: str | None = None,
            portfolio_criteria: object | None = None,
            **_kwargs: object,
        ) -> list[LeadSummary]:
            captured.update(
                {
                    "segment": segment,
                    "portfolio_id": portfolio_id,
                    "limit": limit,
                    "state": state,
                    "zip_code": zip_code,
                    "state_codes": state_codes,
                    "zip_codes": zip_codes,
                    "borrower_ids": borrower_ids,
                    "segment_codes": segment_codes,
                    "segment_mode": segment_mode,
                    "target_lender_ref": target_lender_ref,
                    "cohort_id": cohort_id,
                    "portfolio_criteria": portfolio_criteria,
                    "extra_kwargs": _kwargs,
                }
            )
            return [_lead("B-60617")]

    prior = app.dependency_overrides.get(get_lead_repository)
    app.dependency_overrides[get_lead_repository] = _CohortRepo
    try:
        client = TestClient(app)
        response = client.get("/api/leads?segment=itm&zips=60617,60628")
        assert response.status_code == 200
        assert captured["segment"] == "itm"
        assert captured["zip_codes"] == ["60617", "60628"]
        assert captured["state_codes"] is None
        assert captured["borrower_ids"] is None
    finally:
        if prior is None:
            app.dependency_overrides.pop(get_lead_repository, None)
        else:
            app.dependency_overrides[get_lead_repository] = prior


def test_leads_route_passes_county_filter_to_repository() -> None:
    from backend.services.repositories import get_lead_repository

    captured: dict[str, object] = {}

    class _CountyRepo:
        def list(self, **kwargs: object) -> list[LeadSummary]:
            captured.update(kwargs)
            return [_lead("B-COUNTY", state="FL", zip_code="33311")]

    prior = app.dependency_overrides.get(get_lead_repository)
    app.dependency_overrides[get_lead_repository] = _CountyRepo
    try:
        response = TestClient(app).get(
            "/api/leads?state=FL&county=12011&segment_codes=itm,equity&segment_mode=all"
        )
    finally:
        if prior is None:
            app.dependency_overrides.pop(get_lead_repository, None)
        else:
            app.dependency_overrides[get_lead_repository] = prior

    assert response.status_code == 200
    assert captured["state"] == "FL"
    assert captured["county_fips"] == "12011"
    assert captured["segment_codes"] == ["itm", "equity"]
    assert captured["segment_mode"] == "all"


def test_leads_route_passes_exact_funnel_stage_without_loose_status_filter() -> None:
    from backend.services.repositories import get_lead_repository

    captured: dict[str, object] = {}

    class _FunnelRepo:
        def list(self, **kwargs: object) -> list[LeadSummary]:
            captured["list"] = kwargs
            return [_lead("B-FUN01")]

        def count(self, **kwargs: object) -> int:
            captured["count"] = kwargs
            return 23

    prior = app.dependency_overrides.get(get_lead_repository)
    app.dependency_overrides[get_lead_repository] = _FunnelRepo
    try:
        response = TestClient(app).get("/api/leads?funnel_stage=approved&limit=50")
    finally:
        if prior is None:
            app.dependency_overrides.pop(get_lead_repository, None)
        else:
            app.dependency_overrides[get_lead_repository] = prior

    assert response.status_code == 200, response.text
    assert response.headers["X-Total-Matching"] == "23"
    assert captured["list"]["funnel_stage"] == "approved"
    assert captured["count"]["funnel_stage"] == "approved"
    assert captured["list"]["approval_status"] is None
    criteria = captured["list"]["portfolio_criteria"]
    assert criteria.marketing_eligibility == "Eligible only"


def test_funnel_stage_default_limit_fetches_one_extra_and_slices(monkeypatch) -> None:
    """Default 500-row drilldowns should not depend on exact ``LIMIT 500`` SQL.

    Databricks SQL result caching can briefly retain an exact-limit result
    during a gold/lifecycle refresh. The repository asks SQL for one extra row
    and slices back to the public cap, which keeps the API contract unchanged
    while avoiding stale exact-limit cache collisions.
    """

    captured: dict[str, object] = {}

    class _Client:
        def execute(
            self,
            sql: str,
            params: dict[str, object] | None = None,
        ) -> list[dict[str, object]]:
            captured["sql"] = sql
            captured["params"] = params or {}
            return [
                _lead(f"B-{i:05d}").model_dump(mode="python")
                for i in range(25)
            ]

        def execute_one(
            self,
            sql: str,
            params: dict[str, object] | None = None,
        ) -> dict[str, object]:
            _ = (sql, params)
            return {"n": 25}

    monkeypatch.setattr(
        "backend.services.repositories.databricks_leads.time.time_ns",
        lambda: 123456789,
    )
    repo = DatabricksLeadRepository(_Client(), cache_ttl_s=0)  # type: ignore[arg-type]

    rows = repo.list(segment=None, portfolio_id=None, funnel_stage="approved")

    assert len(rows) == 25
    assert "LIMIT 501" in str(captured["sql"])
    assert "LIMIT 500" not in str(captured["sql"])
    assert "AND 123456789 = 123456789" in str(captured["sql"])


def test_funnel_stage_count_uses_freshness_clause(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _Client:
        def execute(
            self,
            sql: str,
            params: dict[str, object] | None = None,
        ) -> list[dict[str, object]]:
            _ = (sql, params)
            return []

        def execute_one(
            self,
            sql: str,
            params: dict[str, object] | None = None,
        ) -> dict[str, object]:
            captured["sql"] = sql
            captured["params"] = params or {}
            return {"n": 23}

    monkeypatch.setattr(
        "backend.services.repositories.databricks_leads.time.time_ns",
        lambda: 987654321,
    )
    repo = DatabricksLeadRepository(_Client(), cache_ttl_s=0)  # type: ignore[arg-type]

    assert repo.count(segment=None, portfolio_id=None, funnel_stage="approved") == 23
    assert "AND 987654321 = 987654321" in str(captured["sql"])
    assert "COALESCE(ls.approval_status, 'pending') = 'approved'" in str(captured["sql"])


def test_repository_internal_extra_fetch_preserves_requested_cap() -> None:
    captured: dict[str, object] = {}

    class _Client:
        def execute(
            self,
            sql: str,
            params: dict[str, object] | None = None,
        ) -> list[dict[str, object]]:
            captured["sql"] = sql
            _ = params
            return [
                _lead(f"B-{i:05d}").model_dump(mode="python")
                for i in range(11)
            ]

        def execute_one(
            self,
            sql: str,
            params: dict[str, object] | None = None,
        ) -> dict[str, object]:
            _ = (sql, params)
            return {"n": 11}

    repo = DatabricksLeadRepository(_Client(), cache_ttl_s=0)  # type: ignore[arg-type]

    rows = repo.list(segment=None, portfolio_id=None, limit=10)

    assert len(rows) == 10
    assert "LIMIT 11" in str(captured["sql"])


def test_leads_route_passes_genie_borrower_id_cohort_to_repository() -> None:
    from backend.services.repositories import get_lead_repository

    captured: dict[str, object] = {}

    class _BorrowerCohortRepo:
        def list(
            self,
            segment: str | None,
            portfolio_id: str | None,
            limit: int | None = None,
            state: str | None = None,
            zip_code: str | None = None,
            county_fips: str | None = None,
            state_codes: list[str] | None = None,
            zip_codes: list[str] | None = None,
            borrower_ids: list[str] | None = None,
            segment_codes: list[str] | None = None,
            segment_mode: str = "any",
            target_lender_ref: str | None = None,
            cohort_id: str | None = None,
            portfolio_criteria: object | None = None,
            **_kwargs: object,
        ) -> list[LeadSummary]:
            captured.update(
                {
                    "segment": segment,
                    "portfolio_id": portfolio_id,
                    "limit": limit,
                    "state": state,
                    "zip_code": zip_code,
                    "state_codes": state_codes,
                    "zip_codes": zip_codes,
                    "borrower_ids": borrower_ids,
                    "segment_codes": segment_codes,
                    "segment_mode": segment_mode,
                    "target_lender_ref": target_lender_ref,
                    "cohort_id": cohort_id,
                    "portfolio_criteria": portfolio_criteria,
                    "extra_kwargs": _kwargs,
                }
            )
            return [_lead("B-11111", state="WA", zip_code="98118")]

    prior = app.dependency_overrides.get(get_lead_repository)
    app.dependency_overrides[get_lead_repository] = _BorrowerCohortRepo
    try:
        client = TestClient(app)
        response = client.get("/api/leads?borrower_ids=B-11111,B-22222")
        assert response.status_code == 200
        assert captured["borrower_ids"] == ["B-11111", "B-22222"]
        assert captured["zip_codes"] is None
        assert captured["state_codes"] is None
    finally:
        if prior is None:
            app.dependency_overrides.pop(get_lead_repository, None)
        else:
            app.dependency_overrides[get_lead_repository] = prior


def test_leads_route_audits_genie_cohort_filters() -> None:
    from backend.services.audit_store import get_audit_store
    from backend.services.repositories import get_lead_repository
    from tests.fixtures.in_memory_audit_store import InMemoryAuditStore

    audit = InMemoryAuditStore()
    prior_audit = app.dependency_overrides.get(get_audit_store)
    prior_repo = app.dependency_overrides.get(get_lead_repository)

    class _AuditRepo:
        def list(
            self,
            segment: str | None,
            portfolio_id: str | None,
            limit: int | None = None,
            state: str | None = None,
            zip_code: str | None = None,
            county_fips: str | None = None,
            state_codes: list[str] | None = None,
            zip_codes: list[str] | None = None,
            borrower_ids: list[str] | None = None,
            segment_codes: list[str] | None = None,
            segment_mode: str = "any",
            target_lender_ref: str | None = None,
            cohort_id: str | None = None,
            portfolio_criteria: object | None = None,
            **_kwargs: object,
        ) -> list[LeadSummary]:
            _ = (
                segment,
                portfolio_id,
                limit,
                state,
                zip_code,
                state_codes,
                zip_codes,
                borrower_ids,
                segment_codes,
                segment_mode,
                target_lender_ref,
                cohort_id,
                portfolio_criteria,
                _kwargs,
            )
            return [_lead("B-48291")]

    app.dependency_overrides[get_audit_store] = lambda: audit
    app.dependency_overrides[get_lead_repository] = _AuditRepo
    try:
        client = TestClient(app)
        response = client.get(
            "/api/leads"
            "?states=IL,TX"
            "&zips=60611"
            "&borrower_ids=B-48291"
            "&segment_codes=ITM,Equity"
            "&segment_mode=ALL"
            "&limit=10",
            headers={"X-Forwarded-Email": "qa@example.com"},
        )
    finally:
        if prior_audit is None:
            app.dependency_overrides.pop(get_audit_store, None)
        else:
            app.dependency_overrides[get_audit_store] = prior_audit
        if prior_repo is None:
            app.dependency_overrides.pop(get_lead_repository, None)
        else:
            app.dependency_overrides[get_lead_repository] = prior_repo

    assert response.status_code == 200
    event = audit.list(limit=1)[0]
    assert event.action == "view_leads_ranked"
    assert event.event_type == "VIEW_LEADS"
    assert event.subject_segment == "itm,equity"
    assert event.payload_json["states"] == ["IL", "TX"]
    assert event.payload_json["zips"] == ["60611"]
    assert event.payload_json["borrower_ids"] == ["B-48291"]
    assert event.payload_json["segment_codes"] == ["itm", "equity"]
    assert event.payload_json["segment_mode"] == "all"


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


def test_truncation_header_present_when_limit_reached():
    """When the repository returns exactly ``limit`` rows, the router
    sets ``X-Truncated-At`` so the UI can render 'Showing N — refine
    filters'."""
    from backend.api import leads as leads_api
    from backend.services.repositories import get_lead_repository

    class _FullRepo:
        def list(
            self,
            segment: str | None,
            portfolio_id: str | None,
            limit: int | None = None,
            state: str | None = None,
            zip_code: str | None = None,
            county_fips: str | None = None,
            state_codes: list[str] | None = None,
            zip_codes: list[str] | None = None,
            borrower_ids: list[str] | None = None,
            segment_codes: list[str] | None = None,
            segment_mode: str = "any",
            target_lender_ref: str | None = None,
            cohort_id: str | None = None,
            portfolio_criteria: object | None = None,
            **_kwargs: object,
        ) -> list[LeadSummary]:
            _ = (
                segment,
                portfolio_id,
                state,
                zip_code,
                state_codes,
                zip_codes,
                borrower_ids,
                segment_codes,
                segment_mode,
                target_lender_ref,
                cohort_id,
                portfolio_criteria,
                _kwargs,
            )
            # Emit exactly `limit` rows so the header kicks in.
            n = limit or DEFAULT_LEAD_LIMIT
            return [
                _lead(f"B-{i:05d}", zip_code="60611")
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
    from backend.services.repositories import get_lead_repository

    class _SparseRepo:
        def list(
            self,
            segment: str | None,
            portfolio_id: str | None,
            limit: int | None = None,
            state: str | None = None,
            zip_code: str | None = None,
            county_fips: str | None = None,
            state_codes: list[str] | None = None,
            zip_codes: list[str] | None = None,
            borrower_ids: list[str] | None = None,
            segment_codes: list[str] | None = None,
            segment_mode: str = "any",
            target_lender_ref: str | None = None,
            cohort_id: str | None = None,
            portfolio_criteria: object | None = None,
            **_kwargs: object,
        ) -> list[LeadSummary]:
            _ = (
                segment,
                portfolio_id,
                limit,
                state,
                zip_code,
                state_codes,
                zip_codes,
                borrower_ids,
                segment_codes,
                segment_mode,
                target_lender_ref,
                cohort_id,
                portfolio_criteria,
                _kwargs,
            )
            return [_lead("B-00001", zip_code="60611")]

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


def test_leads_route_rejects_raw_target_lender_ref_before_repo() -> None:
    from backend.services.repositories import get_lead_repository

    class _CaptureRepo:
        def __init__(self) -> None:
            self.calls = 0

        def list(self, **kwargs: object) -> list[LeadSummary]:
            _ = kwargs
            self.calls += 1
            return []

    repo = _CaptureRepo()
    prior = app.dependency_overrides.get(get_lead_repository)
    app.dependency_overrides[get_lead_repository] = lambda: repo
    try:
        response = TestClient(app).get(
            "/api/leads?target_lender_ref=Wells%20Fargo%20Bank"
        )
    finally:
        if prior is None:
            app.dependency_overrides.pop(get_lead_repository, None)
        else:
            app.dependency_overrides[get_lead_repository] = prior

    assert response.status_code == 422
    assert repo.calls == 0

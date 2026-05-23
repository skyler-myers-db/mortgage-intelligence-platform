from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.main import app
from backend.schemas.analytics import AnalyticsFilters
from backend.services.repositories.databricks_analytics import DatabricksAnalyticsRepository


def test_native_analytics_routes_return_typed_app_payloads() -> None:
    client = TestClient(app)

    executive = client.get("/api/v1/analytics/executive")
    assert executive.status_code == 200
    assert set(executive.json()) == {"totals", "stages", "score_distribution"}

    geography = client.get("/api/v1/analytics/geography")
    assert geography.status_code == 200
    assert set(geography.json()) == {"state_opportunities", "state_avm_values", "top_zips"}

    economics = client.get("/api/v1/analytics/economics")
    assert economics.status_code == 200
    assert set(economics.json()) == {"rate_spread_histogram", "equity_vs_spread", "top_borrowers"}

    segments = client.get("/api/v1/analytics/segments")
    assert segments.status_code == 200
    assert set(segments.json()) == {
        "scope",
        "overview",
        "counts",
        "average_scores",
        "by_state",
        "top_segments_by_state",
    }
    assert segments.json()["scope"]["code"] == "full_population_pre_suppression"

    signals = client.get("/api/v1/analytics/signals")
    assert signals.status_code == 200
    assert set(signals.json()) == {"evidence_daily", "evidence_by_signal", "evidence_examples"}

    filtered = client.get(
        "/api/v1/analytics/signals?states=IL,CA&segment_codes=itm,equity&signal_types=equity,rate_spread&days=7",
    )
    assert filtered.status_code == 200

    legacy = client.get("/api/v1/analytics/signals?state=IL&signal_type=equity")
    assert legacy.status_code == 200

    invalid = client.get("/api/v1/analytics/signals?signal_types=owner_name")
    assert invalid.status_code == 422


class _AnalyticsSqlClient:
    def __init__(self) -> None:
        self.statements: list[str] = []
        self.parameters: list[object | None] = []

    def execute(self, statement: str, _parameters: object | None = None) -> list[dict[str, object]]:
        self.statements.append(statement)
        self.parameters.append(_parameters)
        if "AS addressable_borrowers" in statement:
            return [{
                "snapshot_date": "2026-05-18",
                "addressable_borrowers": 10,
                "in_the_money_borrowers": 4,
                "high_opportunity_borrowers": 3,
                "offer_recommended_borrowers": 5,
                "approved_borrowers": 2,
                "actioned_borrowers": 1,
            }]
        if "FLOOR(opportunity_score / 5)" in statement:
            return [{"score_bucket": 80, "borrower_count": 2}]
        if "COUNT(DISTINCT clip)" in statement:
            return [{"state": "IL", "borrower_count": 3, "mean_opportunity_score": 81, "in_the_money_borrowers": 2}]
        if "SUM(avm_value)" in statement:
            return [{"state": "IL", "total_avm_value_usd": 100, "total_lien_balance_usd": 50, "total_equity_usd": 50}]
        if "HAVING SUM(CASE WHEN in_the_money" in statement:
            return [{"state": "IL", "zip": "60611", "city": "Chicago", "borrower_count": 2, "in_the_money_borrowers": 2, "mean_opportunity_score": 84, "mean_rate_spread_bps": 88}]
        if "FLOOR(rate_spread_bps / 25)" in statement:
            return [{"spread_bucket_bps": 75, "borrower_count": 4}]
        if "SIZE(b.segment_codes)" in statement:
            return [{
                "borrower_id": "B-48291",
                "display_name": "Borrower 48291",
                "segment": "In the Money",
                "state": "IL",
                "equity_pct": 42,
                "rate_spread_bps": 88,
                "opportunity_score": 91,
            }]
        if "ROW_NUMBER() OVER (ORDER BY b.opportunity_score DESC, b.clip)" in statement:
            return [{"borrower_id": "B-48291", "display_name": "Owner anon", "state": "IL", "city": "Chicago", "opportunity_score": 91, "rate_spread_bps": 88, "equity_pct": 42, "recommended_offer": "Refi", "rank_overall": 1}]
        if "segment_dim AS" in statement:
            return [{"segment_code": "itm", "name": "In the Money", "borrower_count": 10, "mean_opportunity_score": 81, "delta_vs_prior_label": "+1%", "description": "test", "approval_rate": 1.2, "outreach_rate": 0.5, "mean_rate_spread_bps": 90, "mean_equity_pct": 40, "in_the_money_borrowers": 8}]
        if "COUNT(*) AS borrower_count" in statement and "GROUP BY state, segment_code" in statement:
            return [{"state": "IL", "segment_code": "itm", "segment_name": "In the Money", "borrower_count": 10}]
        if "ROW_NUMBER() OVER (PARTITION BY state" in statement:
            return [{"state": "IL", "segment_code": "itm", "segment_name": "In the Money", "borrower_count": 10, "state_rank": 1}]
        if "e.signal_value AS signal_value" in statement:
            return [{
                "borrower_id": "B-48291",
                "display_name": "Owner anon",
                "state": "IL",
                "signal_type": "rate_spread",
                "source_product": "Voluntary Lien",
                "signal_value": "+88 bps",
                "display_text": "Current lien rate is 88 bps vs. par.",
                "confidence": 0.92,
                "timestamp": "2026-05-18",
            }]
        if "AVG(e.confidence)" in statement:
            return [{"signal_type": "rate_spread", "source_product": "Voluntary Lien", "source_table": "mip.silver.lien_current", "event_count": 3, "mean_confidence": 0.9}]
        if "GROUP BY TO_DATE(e.`timestamp`), e.signal_type" in statement:
            return [{"event_date": "2026-05-18", "signal_type": "rate_spread", "event_count": 3}]
        raise AssertionError(statement)


def test_analytics_repository_uses_governed_gold_and_semantic_sql() -> None:
    client = _AnalyticsSqlClient()
    repo = DatabricksAnalyticsRepository(client)  # type: ignore[arg-type]

    assert repo.executive().totals.addressable_borrowers == 10
    assert repo.executive().totals.approved_borrowers == 2
    assert repo.geography().top_zips[0].zip == "60611"
    assert repo.economics().equity_vs_spread[0].borrower_id == "B-48291"
    assert repo.economics().top_borrowers[0].borrower_id == "B-48291"
    assert repo.segments().overview[0].segment_code == "itm"
    assert repo.segments().scope.code == "full_population_pre_suppression"
    assert "marketable-lead filters" in repo.segments().scope.description
    assert repo.signals().evidence_by_signal[0].source_product == "Voluntary Lien"
    assert repo.signals().evidence_by_signal[0].source_table == "mip.silver.lien_current"
    assert repo.signals().evidence_examples[0].borrower_id == "B-48291"

    assert client.statements
    assert all("mip.raw." not in sql and "mip.silver." not in sql for sql in client.statements)
    assert all("mip.gold." in sql or "mip.semantics." in sql for sql in client.statements)
    assert all("primary_segment" in sql or " segment " not in sql for sql in client.statements)
    scatter_sql = next(sql for sql in client.statements if "SIZE(b.segment_codes)" in sql)
    assert "borrower_id AS borrower_id" in scatter_sql
    assert "display_name AS display_name" in scatter_sql
    assert "b.segment_codes[0]" in scatter_sql
    assert "primary_segment" not in scatter_sql
    assert "ORDER BY opportunity_score" not in scatter_sql
    assert "clip AS" not in scatter_sql
    top_zip_sql = next(sql for sql in client.statements if "zip_base" in sql)
    assert "GROUP BY state, zip " in top_zip_sql
    assert "GROUP BY state, zip, city" not in top_zip_sql
    assert "AVG(CASE WHEN in_the_money THEN opportunity_score END)" in top_zip_sql
    assert "AVG(CASE WHEN in_the_money THEN rate_spread_bps END)" in top_zip_sql
    state_sql = next(sql for sql in client.statements if "COUNT(DISTINCT clip)" in sql)
    assert "ORDER BY in_the_money_borrowers DESC, mean_opportunity_score DESC" in state_sql
    top_borrower_sql = next(sql for sql in client.statements if "ROW_NUMBER() OVER (ORDER BY b.opportunity_score DESC, b.clip)" in sql)
    assert "FROM mip.gold.borrower_360" in top_borrower_sql
    assert "lead_population" not in top_borrower_sql


def test_analytics_repository_binds_filters_without_string_interpolation() -> None:
    client = _AnalyticsSqlClient()
    repo = DatabricksAnalyticsRepository(client)  # type: ignore[arg-type]

    filtered = repo.signals(
        filters=AnalyticsFilters(
            states=["IL", "CA"],
            segment_codes=["itm"],
            signal_types=["equity", "rate_spread"],
            lender_relationship="Competitor customer",
            target_lender_ref="Competitor B",
            days=7,
        ),
    )
    assert filtered.evidence_by_signal[0].event_count == 3
    assert any("b.state IN (:state_0, :state_1)" in sql for sql in client.statements)
    assert any("array_contains(b.segment_codes, :segment_0)" in sql for sql in client.statements)
    assert any("b.is_competitor_lien = TRUE" in sql for sql in client.statements)
    assert any("b.current_lender_ref = :target_lender_ref" in sql for sql in client.statements)
    assert any("e.signal_type IN (:signal_type_0, :signal_type_1)" in sql for sql in client.statements)
    assert all("IL" not in sql and "equity" not in sql and "Competitor B" not in sql for sql in client.statements)
    assert {
        "state_0": "IL",
        "state_1": "CA",
        "segment_0": "itm",
        "target_lender_ref": "Competitor B",
        "signal_type_0": "equity",
        "signal_type_1": "rate_spread",
        "days": 7,
    } in client.parameters


def test_analytics_routes_validate_lender_overlay_filters() -> None:
    client = TestClient(app)

    valid = client.get(
        "/api/v1/analytics/executive?lender_relationship=Competitor%20customer&target_lender_ref=Competitor%20B",
    )
    assert valid.status_code == 200

    raw_lender = client.get("/api/v1/analytics/executive?target_lender_ref=Wells%20Fargo%20Bank")
    assert raw_lender.status_code == 422

    bad_relationship = client.get("/api/v1/analytics/executive?lender_relationship=Wholesale%20partner")
    assert bad_relationship.status_code == 422


def test_analytics_filters_reject_raw_target_lender_ref_direct_construction() -> None:
    with pytest.raises(ValidationError):
        AnalyticsFilters(target_lender_ref="Wells Fargo Bank")

    assert AnalyticsFilters(target_lender_ref="All").target_lender_ref is None

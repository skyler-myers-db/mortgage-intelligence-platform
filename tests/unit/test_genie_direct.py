"""Focused coverage for direct trusted-SQL Genie answers."""
from __future__ import annotations

from typing import Any, cast

import pytest

from backend.services.repositories.databricks_genie_direct import direct_canonical_response


class _UniversalSqlClient:
    def __init__(self, row: dict[str, Any] | None = None) -> None:
        self.row = {
            "approval_rate": 42.5,
            "approved_borrowers": 12,
            "avg_equity_pct": 51.2,
            "avg_heloc_propensity_score": 67.8,
            "avg_home_equity_pct": 48.1,
            "avg_in_the_money_rate_spread_bps": 141.2,
            "avg_lead_score": 74.4,
            "avg_opportunity_score": 76.5,
            "avg_refi_rate_spread_bps": 139.9,
            "avg_rate_spread_bps": 132.4,
            "avg_score": 82.1,
            "borrower_id": "B-000000000001",
            "borrowers": 321,
            "cash_out_borrowers": 222,
            "city": "Chicago",
            "count": 321,
            "equity_capacity_borrowers": 456,
            "equity_estimate": 425000,
            "equity_pct": 52.0,
            "heloc_propensity_triggers": 44,
            "home_equity_candidates": 234,
            "in_the_money_borrowers": 1234,
            "in_the_money_leads": 345,
            "investor_borrowers": 321,
            "lead_score": 91,
            "leading_offer_code": "refi",
            "leading_recommended_offer": "Rate refinance",
            "lockin_borrowers": 654,
            "marketable_borrowers": 987,
            "marketable_population": 79730,
            "median_rate_pct": 2.625,
            "opportunity_score": 88,
            "overlap_borrowers": 111,
            "rank_overall": 1,
            "ranked_leads": 20234,
            "recommended_offer": "Rate refinance",
            "recommended_offer_code": "refi",
            "refi_plus_home_equity_candidates": 111,
            "refi_propensity_triggers": 55,
            "refinance_candidates": 345,
            "refreshed_at": "2026-06-17T14:33:04.239Z",
            "related_property_count": 4,
            "retention_risk_borrowers": 77,
            "segment_code": "itm",
            "signal_type": "rate_spread",
            "state": "IL",
            "total_matching": 3,
            "top_tier_borrowers": 222,
            "week_bucket": "current",
            "zip": "60617",
        }
        if row:
            self.row.update(row)
        self.statements: list[str] = []
        self.parameters: list[Any] = []

    def execute_one(self, statement: str, parameters: Any = None) -> dict[str, Any] | None:
        self.statements.append(statement)
        self.parameters.append(parameters)
        return dict(self.row)

    def execute(self, statement: str, parameters: Any = None) -> list[dict[str, Any]]:
        self.statements.append(statement)
        self.parameters.append(parameters)
        first = dict(self.row)
        second = dict(self.row)
        second["state"] = "CA"
        second["zip"] = "90001"
        second["week_bucket"] = "prior"
        return [first, second]


@pytest.mark.parametrize(
    ("question", "expected_asset"),
    [
        (
            "How many borrowers across the current Cotality data coverage are currently in-the-money, and what is the average rate spread?",
            "mip.gold.borrower_360",
        ),
        (
            "How many borrowers have at least 35% modeled equity across the current Cotality data coverage?",
            "mip.gold.borrower_360",
        ),
        (
            "What is the addressable market size -- how many eligible borrowers across the current Cotality data coverage?",
            "mip.gold.borrower_360",
        ),
        ("How many ranked leads are in the Lead Queue?", "mip.gold.lead_population"),
        (
            "Show the top 10 borrowers by lead score across the current Cotality data coverage.",
            "mip.gold.lead_population",
        ),
        ("Show me the top 10 borrowers by lead score in Illinois.", "mip.gold.lead_population"),
        (
            "Top 5 ZIP codes by HELOC-eligible borrowers with equity at least 35%.",
            "mip.gold.borrower_360",
        ),
        (
            "If I only have 10k touches this week, where should I focus and what offer should I lead with?",
            "mip.gold.borrower_360",
        ),
        ("Which state has the most cash-out opportunity right now?", "mip.gold.borrower_360"),
        (
            "Show the top 10 cash-out candidates by estimated equity across the current Cotality data coverage.",
            "mip.gold.borrower_360",
        ),
        (
            "Which listed-for-sale borrowers should get purchase financing help first?",
            "mip.gold.borrower_360",
        ),
        (
            "Show the top 20 masked borrower IDs in the Investor/Multi-Property segment by related property count.",
            "mip.gold.borrower_360",
        ),
        (
            "Which borrower signals should I compare before choosing between refinance and home-equity outreach?",
            "mip.gold.borrower_360",
        ),
        ("What are the strongest refinance opportunity drivers right now?", "mip.gold.evidence_events"),
        (
            "How should I think about in-the-money versus top-tier opportunity?",
            "mip.gold.borrower_360",
        ),
        (
            "Show the Investor / Multi-Property segment broken down by state.",
            "mip.gold.segment_population",
        ),
        (
            "What is the mean rate spread by segment across the current Cotality data coverage?",
            "mip.gold.borrower_360",
        ),
        ("Which segments have the highest approval rate?", "mip.semantics.segment_performance_metric_view"),
        ("Compare mean lead score by current coverage state.", "mip.gold.borrower_360"),
        ("How many evidence events were recorded yesterday, grouped by trigger type?", "mip.gold.evidence_events"),
        ("Compare this week's lead score distribution to last week's.", "mip.gold.funnel_snapshot_daily"),
        ("What is the approval trend over the last 30 days?", "mip.gold.funnel_snapshot_daily"),
        (
            "How many new evidence events have fired this quarter, grouped by trigger type?",
            "mip.gold.evidence_events",
        ),
        ("What offer mix is recommended for the In-the-Money segment?", "mip.gold.borrower_360"),
        (
            "Which borrowers got a HELOC recommendation across the current Cotality data coverage?",
            "mip.gold.borrower_360",
        ),
        (
            "Break down listed-for-sale borrowers by loan product and average current rate.",
            "mip.gold.borrower_360",
        ),
        ("How big is the lock-in cohort?", "mip.gold.lockin_cohort"),
        ("What is the median rate for the lock-in cohort?", "mip.gold.lockin_cohort"),
        ("Break down the lock-in cohort by state.", "mip.gold.lockin_cohort"),
        ("Show the top cohorts.", "mip.gold.segment_population"),
        (
            "List borrowers in the retention list with competitor lien evidence.",
            "mip.gold.evidence_events",
        ),
        ("How many current customers are at retention-risk?", "mip.gold.borrower_360"),
        ("How many in-the-money borrowers in Chicago?", "mip.gold.borrower_360"),
        ("Compare the top five markets by mean lead score using MSA.", "mip.gold.borrower_360"),
        ("How many borrowers are currently in-the-money?", "mip.gold.borrower_360"),
        ("How many borrowers in Illinois are in the money?", "mip.gold.borrower_360"),
    ],
)
def test_direct_canonical_questions_return_trusted_sql(question: str, expected_asset: str) -> None:
    client = _UniversalSqlClient()

    response = direct_canonical_response(question, cast(Any, client))

    assert response is not None, question
    assert response.source == "trusted_sql"
    assert response.proof is not None
    assert response.proof.trusted is True
    assert response.sql_query
    assert client.statements
    assert expected_asset in " ".join(response.trusted_assets)


def test_direct_ranked_lead_population_rejects_non_numeric_count() -> None:
    client = _UniversalSqlClient({"ranked_leads": None})

    response = direct_canonical_response("How many ranked leads are in the Lead Queue?", cast(Any, client))

    assert response is None


def test_direct_top_cohorts_uses_borrower_facing_heloc_intent_language() -> None:
    client = _UniversalSqlClient(
        {
            "segment_code": "permit",
            "name": "HELOC Intent",
            "borrowers": 450790,
        }
    )

    response = direct_canonical_response("Show the top cohorts.", cast(Any, client))

    assert response is not None
    assert "HELOC Intent cohort" in response.answer
    assert "permit segment code" not in response.answer
    assert "`permit`" not in response.answer

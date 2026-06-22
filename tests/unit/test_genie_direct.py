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
            "equity_band": "75%+",
            "equity_estimate": 425000,
            "equity_pct": 52.0,
            "borrower_share_pct": 42.0,
            "heloc_propensity_triggers": 44,
            "home_equity_candidates": 234,
            "in_the_money_borrowers": 1234,
            "in_the_money_leads": 345,
            "investor_borrowers": 321,
            "lead_score": 91,
            "leading_offer_code": "refi",
            "leading_recommended_offer": "Rate refinance",
            "listed_borrowers": 45,
            "avg_listing_days_on_market": 18.4,
            "avg_listing_price": 425000,
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


class _RetentionEligibilitySqlClient(_UniversalSqlClient):
    def execute(self, statement: str, parameters: Any = None) -> list[dict[str, Any]]:
        self.statements.append(statement)
        self.parameters.append(parameters)
        sql = statement.lower()
        if "action_ready_retention_borrowers" in sql:
            return [
                {
                    "retention_segment_borrowers": 16557,
                    "marketing_eligible_retention_borrowers": 0,
                    "action_ready_retention_borrowers": 0,
                    "refreshed_at": "2026-06-17T14:33:04.239Z",
                }
            ]
        if "array_contains(segment_codes, 'retention')" in sql:
            return []
        return super().execute(statement, parameters)


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
        ("Show the distribution of home equity.", "mip.gold.borrower_360"),
        ("Break down home equity across the borrower population.", "mip.gold.borrower_360"),
        ("Breakdown of modeled equity by band.", "mip.gold.borrower_360"),
        (
            "What is the addressable market size -- how many eligible borrowers across the current Cotality data coverage?",
            "mip.gold.borrower_360",
        ),
        ("How many ranked leads are in the Lead Queue?", "mip.gold.lead_population"),
        (
            "Show the top 10 borrowers by lead score across the current Cotality data coverage.",
            "mip.gold.lead_population",
        ),
        (
            "What are the overall best borrowers for any offer?",
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
        (
            "Among listed-for-sale borrowers, what is the average listing days on market by state for the top five states?",
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
    assert response.elapsed_ms is not None
    assert response.proof.elapsed_ms == response.elapsed_ms
    assert response.sql_query
    assert client.statements
    assert expected_asset in " ".join(response.trusted_assets)


@pytest.mark.parametrize(
    ("question", "expected_state", "expected_sql_fragment", "expected_answer_fragment"),
    [
        (
            "best cash-out borrowers in Texas",
            "TX",
            "recommended_offer_code = 'cash_out'",
            "cash-out refinance borrowers",
        ),
        (
            "best HELOC borrowers in California",
            "CA",
            "has_heloc_propensity_trigger = TRUE",
            "home-equity / HELOC borrowers",
        ),
        (
            "best listed borrowers in Texas",
            "TX",
            "listed_for_sale = TRUE",
            "listed-for-sale purchase borrowers",
        ),
        (
            "best investor borrowers in Florida",
            "FL",
            "array_contains(segment_codes, 'investor')",
            "Investor / Multi-Property borrowers",
        ),
        (
            "best retention borrowers in Illinois",
            "IL",
            "array_contains(segment_codes, 'retention')",
            "retention-risk borrowers",
        ),
        (
            "best in-the-money borrowers in Illinois",
            "IL",
            "in_the_money = TRUE",
            "Prime Refi Candidate borrowers",
        ),
    ],
)
def test_direct_specific_intent_state_top_borrowers_do_not_use_generic_lead_population(
    question: str,
    expected_state: str,
    expected_sql_fragment: str,
    expected_answer_fragment: str,
) -> None:
    client = _UniversalSqlClient()

    response = direct_canonical_response(question, cast(Any, client))

    assert response is not None
    assert response.source == "trusted_sql"
    assert response.trusted_assets == ["mip.gold.borrower_360"]
    assert response.sql_query is not None
    assert "mip.gold.lead_population" not in response.sql_query
    assert "state = :state" in response.sql_query
    assert expected_sql_fragment in response.sql_query
    assert client.parameters[-1] == {"state": expected_state}
    assert expected_answer_fragment in response.answer


@pytest.mark.parametrize(
    ("question", "expected_sql_fragment"),
    [
        ("best cash-out candidates across the current Cotality coverage", "recommended_offer_code = 'cash_out'"),
        ("best HELOC candidates across the current Cotality coverage", "has_heloc_propensity_trigger = TRUE"),
        ("best listed borrowers across the current Cotality coverage", "listed_for_sale = TRUE"),
        ("best investor borrowers across the current Cotality coverage", "array_contains(segment_codes, 'investor')"),
    ],
)
def test_direct_specific_intent_global_top_borrowers_do_not_use_generic_lead_population(
    question: str,
    expected_sql_fragment: str,
) -> None:
    client = _UniversalSqlClient()

    response = direct_canonical_response(question, cast(Any, client))

    assert response is not None
    assert response.trusted_assets == ["mip.gold.borrower_360"]
    assert response.sql_query is not None
    assert "mip.gold.lead_population" not in response.sql_query
    assert "state = :state" not in response.sql_query
    assert expected_sql_fragment in response.sql_query


def test_direct_retention_competitor_lien_prompt_is_not_front_run_by_top_borrowers() -> None:
    client = _UniversalSqlClient()

    response = direct_canonical_response(
        "retention borrowers with competitor-lien evidence in Illinois",
        cast(Any, client),
    )

    assert response is not None
    assert response.sql_query is not None
    assert "mip.gold.lead_population" not in response.sql_query
    assert "b.state = :state" in response.sql_query
    assert "signal_type = 'competitor_lien'" in response.sql_query
    assert client.parameters[-1] == {"state": "IL"}
    assert "mip.gold.evidence_events" in response.trusted_assets


def test_direct_best_retention_empty_answer_explains_eligibility_gate() -> None:
    client = _RetentionEligibilitySqlClient()

    response = direct_canonical_response("best retention borrowers in Illinois", cast(Any, client))

    assert response is not None
    assert response.source == "trusted_sql"
    assert response.sql_query is not None
    assert "action_ready_retention_borrowers" in response.sql_query
    assert client.parameters[-1] == {"state": "IL"}
    assert response.metric_value == "0"
    assert response.actions == []
    assert "16,557 borrowers in the Retention Risk segment" in response.answer
    assert "marketing-eligibility and opt-in consent filters" in response.answer
    assert "Competitor-lien evidence questions use a separate evidence workflow" in response.answer


def test_direct_global_best_retention_empty_answer_explains_eligibility_gate() -> None:
    client = _RetentionEligibilitySqlClient()

    response = direct_canonical_response(
        "best retention borrowers across the current Cotality coverage",
        cast(Any, client),
    )

    assert response is not None
    assert response.source == "trusted_sql"
    assert response.sql_query is not None
    assert "action_ready_retention_borrowers" in response.sql_query
    assert client.parameters[-1] is None
    assert response.metric_value == "0"
    assert response.actions == []
    assert "current coverage has 16,557 borrowers in the Retention Risk segment" in response.answer
    assert "marketing-eligibility and opt-in consent filters" in response.answer


def test_direct_equity_line_hyphen_routes_to_heloc_intent() -> None:
    client = _UniversalSqlClient()

    response = direct_canonical_response("highest equity-line borrowers in Texas", cast(Any, client))

    assert response is not None
    assert response.source == "trusted_sql"
    assert response.trusted_assets == ["mip.gold.borrower_360"]
    assert response.sql_query is not None
    assert "has_heloc_propensity_trigger = TRUE" in response.sql_query
    assert client.parameters[-1] == {"state": "TX"}
    assert "home-equity / HELOC borrowers" in response.answer


def test_direct_multi_intent_answer_discloses_primary_ranking_lens() -> None:
    client = _UniversalSqlClient()

    response = direct_canonical_response("best HELOC and cash-out borrowers in Texas", cast(Any, client))

    assert response is not None
    assert response.source == "trusted_sql"
    assert response.sql_query is not None
    assert "recommended_offer_code = 'cash_out'" in response.sql_query
    assert "I detected additional intent language (home-equity / HELOC)" in response.answer
    assert "ask for a combined segment if you want an intersection" in response.answer


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

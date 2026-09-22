"""Home approval-queue banner: N is the default Prime Refi queue's count.

2026-09-21 UI/UX audit, flow-v1. Home's "Approval queue" banner quoted the
whole-book refinance-economics screen while its button opened
``/lead-queue?segment=itm``, which applies the contact-eligibility predicate
by default. Live: 74,335 on the banner, 3,217 in the queue footer.

The banner now states "N contactable of M ...", and takes N from
``POST /api/portfolio/preview`` under ``marketing_eligibility: 'Eligible
only'`` -- deliberately NOT from ``GET /api/leads``, which writes a
``VIEW_LEADS`` audit row per call. That is only honest if the preview's N is
the number the queue footer (``X-Total-Matching``) reports for that link.
These tests pin the four facts that make the two counts the same count, so a
change to any of them fails here instead of silently re-opening the gap:

1. the queue's default criteria are the criteria the banner sends;
2. the queue compiles them with the SAME predicate builder the preview uses,
   and both apply the one ``eligible_sql_predicate()``;
3. with those criteria the queue counts ``gold.borrower_360`` -- NOT
   ``gold.lead_population``, whose CTAS keeps ``opportunity_score >= 50`` --
   and the headline view the preview aggregates is an unfiltered projection
   of that same table;
4. ``in_the_money`` (what the preview sums) is exactly what puts ``'itm'``
   in ``segment_codes`` (what the queue filters on).
"""

from __future__ import annotations

import re
from pathlib import Path

from backend.schemas.portfolio import PortfolioCriteria
from backend.services.eligibility import eligible_sql_predicate
from backend.services.lead_query_helpers import portfolio_criteria_from_query
from backend.services.repositories.databricks_lead_cohorts import (
    LeadCohortFilters,
    LeadCohortQueries,
)
from backend.services.repositories.databricks_portfolio import DatabricksPortfolioRepository

_REPO_ROOT = Path(__file__).resolve().parents[2]

# What frontend/src/routes/home.approval-banner.tsx posts as `criteria`.
_BANNER_CRITERIA = {"marketing_eligibility": "Eligible only"}


def _normalize(sql: str) -> str:
    return " ".join(sql.split())


def _default_queue_criteria() -> PortfolioCriteria | None:
    """Criteria GET /api/leads builds for ``/lead-queue?segment=itm``.

    The route's ``marketing_eligibility`` query parameter defaults to
    "Eligible only"; every other portfolio filter is absent on that link.
    """
    return portfolio_criteria_from_query(
        geography=None,
        occupancy=None,
        lien_status=None,
        lender_relationship=None,
        product=None,
        target_lender_ref=None,
        min_equity_pct_label=None,
        min_equity_pct=None,
        marketing_eligibility="Eligible only",
    )


def _default_itm_queue_sql() -> tuple[str, bool]:
    queries = LeadCohortQueries(None, cache_ttl_s=0)  # type: ignore[arg-type]
    sql, _params, uses_lead_population = queries.matched_cohort_sql(
        LeadCohortFilters(segment="itm", portfolio_criteria=_default_queue_criteria()),
        include_lead_columns=False,
    )
    return _normalize(sql), uses_lead_population


def test_leads_route_defaults_to_the_eligibility_the_banner_counts() -> None:
    source = (_REPO_ROOT / "backend" / "api" / "leads.py").read_text()
    assert 'marketing_eligibility: MarketingEligibilityParam = "Eligible only"' in source


def test_banner_criteria_are_the_default_queue_criteria() -> None:
    assert _default_queue_criteria() == PortfolioCriteria.model_validate(_BANNER_CRITERIA)


def test_preview_and_queue_apply_the_one_eligibility_predicate() -> None:
    where, params = DatabricksPortfolioRepository._build_preview_predicates(
        PortfolioCriteria.model_validate(_BANNER_CRITERIA)
    )
    queue_sql, _ = _default_itm_queue_sql()
    predicate = _normalize(eligible_sql_predicate())

    assert params == {}
    # Eligibility is the ONLY predicate the banner's preview applies ...
    assert _normalize(where) == f"WHERE {predicate}"
    # ... and the queue applies that same clause verbatim, plus the segment.
    assert _normalize(where).removeprefix("WHERE ") in queue_sql
    assert "array_contains(segment_codes, :seg)" in queue_sql


def test_default_itm_queue_counts_borrower_360_not_the_score_floored_table() -> None:
    queue_sql, uses_lead_population = _default_itm_queue_sql()

    assert uses_lead_population is False
    assert ".gold.borrower_360 b " in queue_sql
    assert "lead_population" not in queue_sql
    assert "opportunity_score >=" not in queue_sql

    # Non-vacuity control: WITHOUT the eligibility criteria the same segment
    # read does go to lead_population (score >= 50). If this stops holding,
    # the assertion above proves nothing about the default queue.
    queries = LeadCohortQueries(None, cache_ttl_s=0)  # type: ignore[arg-type]
    bare_sql, _params, bare_uses_lead_population = queries.matched_cohort_sql(
        LeadCohortFilters(segment="itm"),
        include_lead_columns=False,
    )
    assert bare_uses_lead_population is True
    assert "lead_population" in bare_sql


def test_preview_sums_in_the_money_over_an_unfiltered_view_of_borrower_360() -> None:
    template = _normalize(DatabricksPortfolioRepository._PREVIEW_SQL_TEMPLATE)
    assert "SUM(CASE WHEN in_the_money THEN 1 ELSE 0 END) AS high_intent_leads" in template
    assert ".semantics.portfolio_headline_metric_view AS headline" in template

    view = (_REPO_ROOT / "sql" / "metric_views" / "portfolio_headline_metric_view.sql").read_text()
    select = view.split("COMMENT ON VIEW")[0]
    assert "FROM mip.gold.borrower_360 AS b;" in select
    # A WHERE in the view would make the preview a subset of what the queue reads.
    assert not re.search(r"^\s*WHERE\b", select, flags=re.MULTILINE)
    assert re.search(r"^\s*b\.in_the_money,", select, flags=re.MULTILINE)


def test_itm_segment_membership_is_exactly_in_the_money() -> None:
    gold = (_REPO_ROOT / "sql" / "transformations" / "gold_borrower_360.sql").read_text()
    matches = re.findall(r"CASE WHEN\s+(\S+)\s+THEN 'itm'\s+END", gold)
    assert matches == ["s.in_the_money"]

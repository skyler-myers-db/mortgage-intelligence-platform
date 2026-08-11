"""A cohort floor may only come from a predicate that really filters rows.

The Genie -> Lead Queue handoff replays the answer's own thresholds so the
cohort reproduces the population the user just read. Reading those thresholds
by TEXT SHAPE instead of by FILTER POSITION lifts bounds out of expressions
that select nothing, and a wrong floor is worse than no floor:

* no floor replays BROADER, which ``X-Cohort-Count-Delta`` and
  ``X-Cohort-Unreplayable-Filters`` already surface;
* a wrong floor replays NARROWER in silence, the user acts on a truncated
  list, and the same threshold is persisted into the Lakebase cohort row, the
  draft-campaign criteria, and the VIEW_LEADS audit metadata -- so the
  approval record asserts a threshold the answer never applied.

The worst shape is the one the deployed Genie space TEACHES (aggregate
breakdown over the answer's population). Measured live on paychex gold
2026-08-11: for ``WHERE state = 'IL'``, a floor lifted out of
``COUNT_IF(opportunity_score >= 75)`` narrows the eligible cohort from 76,711
to 128 -- 599x -- while the answer's own count is 76,711.

Every case below is either a captured live shape, an in-repo constant, or a
statement form reviewers found the previous text-matching reader mishandled.

Sibling gate: ``tests/unit/test_genie_cohort_criteria_source.py`` pins that the
same criteria may never be inferred from the QUESTION'S wording, which has no
position to gate on at all.
"""

from __future__ import annotations

import re
from types import SimpleNamespace
from typing import Any

import pytest

from backend.services.genie_sql_predicates import read_sql_filters
from backend.services.repositories import databricks_genie_canonical as canonical
from backend.services.repositories.databricks_genie_actions import (
    _numeric_floors_from_sql,
    _portfolio_criteria_from_sql,
    _route_from_answer_rows,
)
from backend.services.scoring import HIGH_OPPORTUNITY_THRESHOLD

_B360 = "mip.gold.borrower_360"


def _floors(sql: str) -> dict[str, int]:
    return _numeric_floors_from_sql(sql)


def _disclosed(sql: str) -> tuple[str, ...]:
    return read_sql_filters(sql).unreplayable


# --------------------------------------------------------------------------
# The canonical corpus: every statement this repo can serve, hand-declared.
#
# A reviewer specified this test: run EVERY `_CANONICAL_*_SQL` constant through
# the extractor and pin the result. It is the test that would have caught the
# aggregate-breakdown defect, because the repo's own
# `_CANONICAL_ITM_TOP_TIER_COMPARE_SQL` (served for "how many are in the money
# vs top tier") lifted `min_opportunity_score = 75` from a COUNT_IF that
# reports a breakdown OF the answer's population -- live, itm 3,217 -> 244 --
# while the answer text says "3,217 borrowers are in-the-money".
#
# Only three statements carry a real top-level threshold. Each was read by
# hand against its WHERE clause; every other entry is `{}` on purpose, and a
# NEW constant fails `test_canonical_corpus_declaration_is_exhaustive` until
# someone declares what it should lift.
# --------------------------------------------------------------------------
_EXPECTED_CANONICAL_FLOORS: dict[str, dict[str, int]] = {
    # WHERE ... AND equity_pct >= 15 -- a real top-level conjunct.
    "_CANONICAL_ADDRESSABLE_MARKET_SQL": {"min_equity_pct": 15},
    # WHERE equity_pct >= 35.
    "_CANONICAL_HELOC_COUNT_SQL": {"min_equity_pct": 35},
    # WHERE equity_pct >= 35 AND zip IS NOT NULL ...
    "_CANONICAL_HELOC_TOP_ZIPS_SQL": {"min_equity_pct": 35},
    "_CANONICAL_APPROVAL_TREND_30D_SQL": {},
    "_CANONICAL_CASH_OUT_TOP_STATE_SQL": {},
    "_CANONICAL_CURRENT_CUSTOMER_RETENTION_RISK_SQL": {},
    # Threshold is a bound parameter inside COUNT_IF/CASE in the select list;
    # the statement has no WHERE at all, so nothing is filtered.
    "_CANONICAL_EQUITY_THRESHOLD_COUNT_SQL": {},
    "_CANONICAL_EQUITY_THRESHOLD_STRICT_COUNT_SQL": {},
    "_CANONICAL_EVIDENCE_EVENTS_THIS_QUARTER_SQL": {},
    "_CANONICAL_EVIDENCE_EVENTS_YESTERDAY_SQL": {},
    "_CANONICAL_HELOC_RECOMMENDATION_BORROWERS_SQL": {},
    # Banded CASE ladder inside a CTE: projects labels, selects nothing.
    "_CANONICAL_HOME_EQUITY_DISTRIBUTION_SQL": {},
    "_CANONICAL_INVESTOR_COUNT_SQL": {},
    "_CANONICAL_INVESTOR_SEGMENT_BY_STATE_SQL": {},
    "_CANONICAL_INVESTOR_TOP_BY_RELATED_PROPERTY_SQL": {},
    "_CANONICAL_ITM_BY_STATE_SQL": {},
    "_CANONICAL_ITM_COUNT_AVG_SPREAD_SQL": {},
    "_CANONICAL_ITM_COUNT_BY_CITY_SQL": {},
    "_CANONICAL_ITM_COUNT_BY_STATE_SQL": {},
    "_CANONICAL_ITM_COUNT_SQL": {},
    "_CANONICAL_ITM_OFFER_MIX_SQL": {},
    "_CANONICAL_ITM_SHARE_SQL": {},
    "_CANONICAL_ITM_TOP_LEAD_QUEUE_ZIPS_SQL": {},
    # The in-repo P0 trigger: COUNT_IF breakdowns in the select list, WHERE is
    # eligibility + consent only.
    "_CANONICAL_ITM_TOP_TIER_COMPARE_SQL": {},
    "_CANONICAL_ITM_TOP_ZIPS_SQL": {},
    "_CANONICAL_LEAD_SCORE_WEEKLY_DISTRIBUTION_SQL": {},
    "_CANONICAL_LISTED_BY_PRODUCT_RATE_SQL": {},
    "_CANONICAL_LISTED_COUNT_BY_STATE_SQL": {},
    "_CANONICAL_LISTED_COUNT_SQL": {},
    "_CANONICAL_LISTED_DAYS_ON_MARKET_BY_STATE_SQL": {},
    "_CANONICAL_LISTED_PURCHASE_TOP_SQL": {},
    "_CANONICAL_LOCKIN_BY_STATE_SQL": {},
    "_CANONICAL_LOCKIN_COHORT_SIZE_SQL": {},
    "_CANONICAL_LOCKIN_MEDIAN_RATE_SQL": {},
    "_CANONICAL_MEAN_LEAD_SCORE_BY_STATE_SQL": {},
    "_CANONICAL_MEAN_RATE_SPREAD_BY_SEGMENT_SQL": {},
    "_CANONICAL_MSA_SCORE_SQL": {},
    "_CANONICAL_NEGATIVE_EQUITY_COUNT_SQL": {},
    "_CANONICAL_RANKED_LEAD_POPULATION_SQL": {},
    "_CANONICAL_REFI_DRIVER_SQL": {},
    "_CANONICAL_REFI_EQUITY_SIGNAL_COMPARE_SQL": {},
    "_CANONICAL_RETENTION_COMPETITOR_LIEN_LIST_BY_STATE_SQL": {},
    "_CANONICAL_RETENTION_COMPETITOR_LIEN_LIST_SQL": {},
    "_CANONICAL_RETENTION_ELIGIBILITY_SUMMARY_BY_STATE_SQL": {},
    "_CANONICAL_RETENTION_ELIGIBILITY_SUMMARY_GLOBAL_SQL": {},
    "_CANONICAL_SEGMENT_APPROVAL_RATE_SQL": {},
    "_CANONICAL_STRATEGY_BOARD_SQL": {},
    # `WHEN equity_pct >= 35` inside the why_now CASE driver.
    "_CANONICAL_TOP_BORROWERS_ALL_SEGMENTS_SQL": {},
    "_CANONICAL_TOP_BORROWERS_BY_STATE_INTENT_SQL[cash_out]": {},
    "_CANONICAL_TOP_BORROWERS_BY_STATE_INTENT_SQL[heloc]": {},
    "_CANONICAL_TOP_BORROWERS_BY_STATE_INTENT_SQL[investor]": {},
    "_CANONICAL_TOP_BORROWERS_BY_STATE_INTENT_SQL[listed]": {},
    "_CANONICAL_TOP_BORROWERS_BY_STATE_INTENT_SQL[refi]": {},
    "_CANONICAL_TOP_BORROWERS_BY_STATE_INTENT_SQL[retention]": {},
    "_CANONICAL_TOP_BORROWERS_BY_STATE_SQL": {},
    "_CANONICAL_TOP_BORROWERS_GLOBAL_INTENT_SQL[cash_out]": {},
    "_CANONICAL_TOP_BORROWERS_GLOBAL_INTENT_SQL[heloc]": {},
    "_CANONICAL_TOP_BORROWERS_GLOBAL_INTENT_SQL[investor]": {},
    "_CANONICAL_TOP_BORROWERS_GLOBAL_INTENT_SQL[listed]": {},
    "_CANONICAL_TOP_BORROWERS_GLOBAL_INTENT_SQL[refi]": {},
    "_CANONICAL_TOP_BORROWERS_GLOBAL_INTENT_SQL[retention]": {},
    "_CANONICAL_TOP_BORROWERS_GLOBAL_SQL": {},
    "_CANONICAL_TOP_CASH_OUT_BORROWERS_BY_STATE_SQL": {},
    "_CANONICAL_TOP_CASH_OUT_BORROWERS_GLOBAL_SQL": {},
    "_CANONICAL_TOP_CASH_OUT_BY_EQUITY_SQL": {},
    "_CANONICAL_TOP_COHORTS_SQL": {},
    "_CANONICAL_TOP_HELOC_BORROWERS_BY_STATE_SQL": {},
    "_CANONICAL_TOP_HELOC_BORROWERS_GLOBAL_SQL": {},
    "_CANONICAL_TOP_INVESTOR_BORROWERS_BY_STATE_SQL": {},
    "_CANONICAL_TOP_INVESTOR_BORROWERS_GLOBAL_SQL": {},
    "_CANONICAL_TOP_LISTED_BORROWERS_BY_STATE_SQL": {},
    "_CANONICAL_TOP_LISTED_BORROWERS_GLOBAL_SQL": {},
    "_CANONICAL_TOP_REFI_BORROWERS_BY_STATE_SQL": {},
    "_CANONICAL_TOP_REFI_BORROWERS_GLOBAL_SQL": {},
    "_CANONICAL_TOP_RETENTION_BORROWERS_BY_STATE_SQL": {},
    "_CANONICAL_TOP_RETENTION_BORROWERS_GLOBAL_SQL": {},
}


def _canonical_statements() -> list[tuple[str, str]]:
    """Every canonical SQL constant, including the per-intent dict entries."""

    found: list[tuple[str, str]] = []
    for name, value in sorted(vars(canonical).items()):
        if not re.fullmatch(r"_CANONICAL_[A-Z0-9_]*SQL", name):
            continue
        if isinstance(value, str):
            found.append((name, value))
        elif isinstance(value, dict):
            for key, nested in sorted(value.items()):
                if isinstance(nested, str):
                    found.append((f"{name}[{key}]", nested))
    return found


def test_canonical_corpus_declaration_is_exhaustive() -> None:
    discovered = {name for name, _ in _canonical_statements()}
    assert discovered, "canonical SQL constants are no longer discoverable"
    assert discovered == set(_EXPECTED_CANONICAL_FLOORS), (
        "a canonical statement changed: declare by hand what it may lift"
    )


@pytest.mark.parametrize("name,sql", _canonical_statements(), ids=lambda value: value[:60])
def test_canonical_statement_lifts_only_its_declared_floor(name: str, sql: str) -> None:
    assert _floors(sql) == _EXPECTED_CANONICAL_FLOORS[name]


# --- P0: aggregate breakdowns report the population, they do not narrow it --


def test_aggregate_breakdown_is_not_a_filter() -> None:
    """The idiom the deployed Genie space teaches (space yml lines 759-761).

    Live 2026-08-11: IL eligible 76,711 borrowers, of which 128 clear 75.
    Lifting 75 here truncates the cohort 599x under the same heading.
    """

    sql = (
        "SELECT COUNT(*) AS borrowers, COUNT_IF(opportunity_score >= 75) AS top_tier, "
        f"MAX(refreshed_at) FROM {_B360} WHERE state = 'IL'"
    )
    assert _floors(sql) == {}
    assert _disclosed(sql) == ()


def test_aggregate_filter_where_is_not_a_filter() -> None:
    sql = (
        "SELECT COUNT(*) FILTER (WHERE opportunity_score >= 80) AS top_tier "
        f"FROM {_B360} WHERE state = 'IL'"
    )
    assert _floors(sql) == {}


def test_the_repo_serves_that_shape_itself() -> None:
    """`databricks_genie_direct` serves this constant for the ITM compare."""

    assert _floors(canonical._CANONICAL_ITM_TOP_TIER_COMPARE_SQL) == {}


# --- P1: complement, disjunction, and presentation contexts -----------------


@pytest.mark.parametrize(
    "sql",
    [
        # Exact complement of the answer's population.
        f"SELECT * FROM {_B360} WHERE NOT (opportunity_score >= 80)",
        f"SELECT * FROM {_B360} WHERE NOT opportunity_score >= 80",
        # The answer is a SUPERSET of the threshold.
        f"SELECT * FROM {_B360} WHERE state = 'IL' OR opportunity_score >= 80",
        f"SELECT * FROM {_B360} WHERE (state = 'IL' OR opportunity_score >= 80) AND zip IS NOT NULL",
        # Inverted anti-join: the threshold selects who is EXCLUDED.
        f"SELECT * FROM {_B360} WHERE borrower_id NOT IN "
        "(SELECT borrower_id FROM mip.gold.lead_population WHERE opportunity_score >= 80)",
        # Presentation only.
        f"SELECT * FROM {_B360} WHERE state = 'IL' ORDER BY opportunity_score > 80 DESC",
        f"SELECT * FROM {_B360} WHERE state = 'IL' GROUP BY opportunity_score >= 80",
        # A string literal is a label, not a predicate.
        f"SELECT 'opportunity_score >= 90' AS lbl FROM {_B360} WHERE state = 'IL'",
        f"SELECT * FROM {_B360} WHERE label = 'opportunity_score >= 90'",
        # Inverted UC function.
        f"SELECT * FROM {_B360} WHERE NOT mip.gold.fn_high_opportunity(opportunity_score)",
        f"SELECT * FROM {_B360} WHERE state = 'IL' OR mip.gold.fn_high_opportunity(opportunity_score)",
    ],
)
def test_non_narrowing_contexts_lift_no_floor(sql: str) -> None:
    assert _floors(sql) == {}


# --- P2: real floors that used to be dropped in silence ---------------------


def test_bound_parameter_is_disclosed_not_dropped() -> None:
    """The value never reaches us, so the divergence must be visible.

    This is the form the repo's OWN canonical SQL uses
    (`_CANONICAL_EQUITY_THRESHOLD_COUNT_SQL`, the growth-agent plan executor).
    """

    sql = f"SELECT * FROM {_B360} WHERE state = 'IL' AND opportunity_score >= :min_score"
    assert _floors(sql) == {}
    assert _disclosed(sql) == ("opportunity_score_threshold",)
    positional = f"SELECT * FROM {_B360} WHERE opportunity_score >= ?"
    assert _floors(positional) == {}
    assert _disclosed(positional) == ("opportunity_score_threshold",)


def test_negative_bounds_are_read_and_disclosed() -> None:
    """`rate_spread_bps` is signed: 2,561,392 of 5,156,184 live rows are < 0.

    The old regex bound (`\\d{1,4}`) could not express -25 at all. It is read
    now, but the reviewed cohort vocabulary rejects a negative floor (a 400
    would kill the whole action), so it is disclosed rather than replayed.
    """

    sql = f"SELECT * FROM {_B360} WHERE state = 'IL' AND rate_spread_bps >= -25"
    assert _floors(sql) == {}
    assert _disclosed(sql) == ("rate_spread_bps_threshold",)
    assert read_sql_filters(f"SELECT * FROM {_B360} WHERE rate_spread_bps >= 0").floors == {
        "min_rate_spread_bps": 0
    }


def test_mirrored_and_between_bounds_are_real_floors() -> None:
    assert _floors(f"SELECT * FROM {_B360} WHERE 80 <= opportunity_score") == {
        "min_opportunity_score": 80
    }
    # `79 < score` is `score >= 80` on a whole-number column.
    assert _floors(f"SELECT * FROM {_B360} WHERE 79 < opportunity_score") == {
        "min_opportunity_score": 80
    }
    assert _floors(f"SELECT * FROM {_B360} WHERE opportunity_score BETWEEN 80 AND 100") == {
        "min_opportunity_score": 80
    }
    # BETWEEN owns its AND: the second bound must not split the conjunction.
    assert _floors(
        f"SELECT * FROM {_B360} WHERE opportunity_score BETWEEN 80 AND 100 AND state = 'IL'"
    ) == {"min_opportunity_score": 80}


# --- Behaviour that must survive the rework --------------------------------


def test_live_captured_turn_still_lifts_its_floor() -> None:
    """Verbatim SQL from the deployed space, 2026-08-11."""

    live = (
        "SELECT\n  COUNT(*) AS in_the_money_borrowers,\n"
        "  MAX(refreshed_at) AS refreshed_at\n"
        f"FROM {_B360}\n"
        "WHERE in_the_money = TRUE\n  AND state = 'IL'\n"
        "  AND opportunity_score >= 80\n  AND opportunity_score IS NOT NULL"
    )
    assert _floors(live) == {"min_opportunity_score": 80}


def test_strict_inequality_normalizes_on_whole_number_columns() -> None:
    # Verified live 2026-08-11: opportunity_score, equity_pct and
    # rate_spread_bps are all `int` in gold with zero fractional rows, so
    # `> n` is exactly `>= n + 1`.
    assert _floors(f"SELECT * FROM {_B360} b WHERE b.rate_spread_bps > 99") == {
        "min_rate_spread_bps": 100
    }
    # A fractional literal rounds DOWN, so the replay can only be broader.
    assert _floors(f"SELECT * FROM {_B360} WHERE equity_pct >= 34.5") == {"min_equity_pct": 34}


def test_canonical_high_opportunity_function_still_reads_its_constant() -> None:
    assert _floors(
        f"SELECT * FROM {_B360} WHERE mip.gold.fn_high_opportunity(opportunity_score)"
    ) == {"min_opportunity_score": HIGH_OPPORTUNITY_THRESHOLD}
    assert _floors(
        f"SELECT * FROM {_B360} WHERE fn_high_opportunity(opportunity_score) = TRUE "
        "AND state = 'IL'"
    ) == {"min_opportunity_score": HIGH_OPPORTUNITY_THRESHOLD}
    # In the select list it labels rows; it does not select them.
    assert _floors(f"SELECT fn_high_opportunity(opportunity_score) AS t FROM {_B360}") == {}


@pytest.mark.parametrize(
    "sql",
    [
        # A CASE projects a label; it selects no rows.
        f"SELECT CASE WHEN opportunity_score >= 80 THEN 'a' ELSE 'b' END FROM {_B360}",
        f"SELECT CASE WHEN x THEN CASE WHEN opportunity_score >= 80 THEN 1 END END FROM {_B360}",
        # Comments are not predicates. The planned deep sweep appends each
        # sub-question as a trailing comment, so a question that merely
        # MENTIONS a threshold would otherwise become a filter.
        f"SELECT * FROM {_B360} -- opportunity_score >= 80\nWHERE state='IL'",
        f"SELECT * FROM {_B360} /* opportunity_score >= 80 */ WHERE state='IL'",
        f"SELECT * FROM {_B360} WHERE state='IL' -- [How many have opportunity_score >= 80?]",
    ],
)
def test_non_filtering_thresholds_lift_no_floor(sql: str) -> None:
    assert _floors(sql) == {}


def test_a_real_predicate_still_wins_past_a_case_expression() -> None:
    sql = (
        "SELECT CASE WHEN opportunity_score >= 80 THEN 'a' END "
        f"FROM {_B360} WHERE opportunity_score >= 90"
    )
    assert _floors(sql) == {"min_opportunity_score": 90}


def test_disagreeing_bounds_and_out_of_range_bounds_are_disclosed() -> None:
    conflict = f"SELECT * FROM {_B360} WHERE opportunity_score >= 80 AND opportunity_score >= 90"
    assert _floors(conflict) == {}
    assert _disclosed(conflict) == ("opportunity_score_threshold",)
    out_of_range = f"SELECT * FROM {_B360} WHERE opportunity_score >= 900"
    assert _floors(out_of_range) == {}
    assert _disclosed(out_of_range) == ("opportunity_score_threshold",)
    assert _floors(f"SELECT * FROM {_B360} WHERE state = 'IL'") == {}


# --- Position gating, clause by clause -------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        # A CTE body is not the outer statement's filter.
        f"WITH c AS (SELECT * FROM {_B360} WHERE opportunity_score >= 80) SELECT COUNT(*) FROM c",
        # Nor is a subquery in FROM.
        f"SELECT COUNT(*) FROM (SELECT * FROM {_B360} WHERE opportunity_score >= 80) t",
        # HAVING filters groups, not rows.
        f"SELECT state FROM {_B360} GROUP BY state HAVING opportunity_score >= 80",
        # An outer join's ON does not filter the preserved side.
        f"SELECT * FROM {_B360} a LEFT JOIN mip.gold.lead_population b "
        "ON b.opportunity_score >= 80 WHERE a.state = 'IL'",
        f"SELECT * FROM {_B360} a LEFT OUTER JOIN mip.gold.lead_population b "
        "ON b.opportunity_score >= 80",
        # A set operation is a union of populations, not one filter.
        f"SELECT * FROM {_B360} WHERE opportunity_score >= 80 "
        f"UNION ALL SELECT * FROM {_B360} WHERE state = 'IL'",
    ],
)
def test_thresholds_outside_the_outer_filter_lift_nothing(sql: str) -> None:
    assert _floors(sql) == {}


@pytest.mark.parametrize(
    "sql",
    [
        # An inner join's ON does filter both sides.
        f"SELECT * FROM {_B360} a JOIN mip.gold.lead_population b "
        "ON b.borrower_id = a.borrower_id AND b.opportunity_score >= 80",
        f"SELECT * FROM {_B360} a INNER JOIN mip.gold.lead_population b "
        "ON b.opportunity_score >= 80",
        # QUALIFY is applied to the result rows.
        f"SELECT * FROM {_B360} WHERE state = 'IL' QUALIFY opportunity_score >= 80",
        # Redundant parentheses do not change the position.
        f"SELECT * FROM {_B360} WHERE (state = 'IL' AND (opportunity_score >= 80))",
        # A trailing semicolon is a terminator, not a second statement.
        f"SELECT * FROM {_B360} WHERE opportunity_score >= 80;",
    ],
)
def test_thresholds_in_the_outer_filter_lift_their_floor(sql: str) -> None:
    assert _floors(sql) == {"min_opportunity_score": 80}


@pytest.mark.parametrize(
    "sql",
    [
        # A wrapped or computed column is a different population.
        f"SELECT * FROM {_B360} WHERE ABS(rate_spread_bps) >= 25",
        f"SELECT * FROM {_B360} WHERE opportunity_score + 5 >= 80",
        f"SELECT * FROM {_B360} WHERE CAST(opportunity_score AS INT) >= 80",
        # An upper bound is not a floor.
        f"SELECT * FROM {_B360} WHERE opportunity_score <= 80",
        f"SELECT * FROM {_B360} WHERE opportunity_score IS NOT NULL",
        # Unterminated quoting/commenting: refuse rather than guess.
        f"SELECT * FROM {_B360} WHERE state = 'IL AND opportunity_score >= 80",
        f"SELECT * FROM {_B360} /* WHERE opportunity_score >= 80",
        # Not a statement this reader reasons about.
        f"UPDATE {_B360} SET x = 1 WHERE opportunity_score >= 80",
    ],
)
def test_shapes_the_reader_refuses(sql: str) -> None:
    assert _floors(sql) == {}


def test_reader_never_raises_on_junk() -> None:
    for junk in ("", "   ", "SELECT", "((((", "'", "--", "SELECT * FROM t WHERE", None):
        assert _numeric_floors_from_sql(junk) == {}


# --- Portfolio criteria ride the same gate ---------------------------------
#
# `min_equity_pct_label` compiles to `equity_pct >=` in the queue, so a
# criterion lifted out of a breakdown narrows the cohort exactly as a bad
# floor does.


def test_portfolio_criteria_come_from_filter_position_only() -> None:
    breakdown = (
        "SELECT COUNT(*) AS borrowers, COUNT_IF(equity_pct >= 25) AS equity_rich, "
        "COUNT_IF(is_owner_occupied = TRUE) AS owner_occupied "
        f"FROM {_B360} WHERE state = 'IL'"
    )
    assert _portfolio_criteria_from_sql(breakdown) == {}
    real = (
        f"SELECT COUNT(*) FROM {_B360} "
        "WHERE state = 'IL' AND is_owner_occupied = TRUE AND equity_pct >= 25"
    )
    criteria = _portfolio_criteria_from_sql(real)
    assert criteria["occupancy"] == "Owner-occupied"
    assert criteria["min_equity_pct_label"] == "≥ 25%"
    assert _portfolio_criteria_from_sql(f"SELECT * FROM {_B360} WHERE is_owner_occupied") == {
        "occupancy": "Owner-occupied"
    }


# --- The handoff: what the route carries -----------------------------------


def _route(question: str, sql: str, rows: list[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    return _route_from_answer_rows(
        question=question,
        rows=rows,
        borrower_ids=[],
        sql_query=sql,
    )


def test_breakdown_answer_hands_off_the_population_it_reported() -> None:
    route, filters = _route(
        "how many borrowers in Illinois are top tier",
        "SELECT COUNT(*) AS borrowers, COUNT_IF(opportunity_score >= 75) AS top_tier "
        f"FROM {_B360} WHERE state = 'IL'",
        [{"state": "IL", "borrowers": 76711, "top_tier": 128}],
    )
    assert filters["states"] == ["IL"]
    assert "min_opportunity_score" not in filters
    assert "min_opportunity_score" not in route


def test_unreplayable_threshold_is_disclosed_on_the_route_not_applied() -> None:
    route, filters = _route(
        "how many borrowers in Illinois clear the score cut",
        f"SELECT COUNT(*) FROM {_B360} WHERE state = 'IL' AND opportunity_score >= :min_score",
        [{"state": "IL"}],
    )
    assert filters["states"] == ["IL"]
    assert filters["opportunity_score_threshold"] is True
    assert "min_opportunity_score" not in filters
    # A disclosure is never a URL parameter: the queue cannot apply it.
    assert "opportunity_score_threshold" not in route


def test_disclosure_never_stands_alone() -> None:
    """A cohort with no replayable filter is rejected downstream.

    If the disclosure were the only entry, `_suggest_genie_actions` would
    offer an "open cohort" action that 400s at `_materialize_genie_cohort`.
    """

    _route_str, filters = _route(
        "how many borrowers clear the score cut",
        f"SELECT COUNT(*) FROM {_B360} WHERE opportunity_score >= :min_score",
        [{"borrowers": 12}],
    )
    assert filters == {}


def test_disclosure_survives_all_five_closed_vocabularies() -> None:
    """One key must clear every gate between the answer and the approval.

    Adding a `result_filters` key touches five independent closed
    vocabularies; an unknown key raises `AuditMetadataValueViolation` (a
    RuntimeError, so the action 500s AFTER the Lakebase cohort row is
    written) or makes a Genie draft campaign permanently unapprovable.
    """

    import json

    from backend.schemas.portfolio import project_public_campaign_json_field
    from backend.services.campaign_treatment import cohort_filters_from_campaign_criteria
    from backend.services.genie_actions import (
        _audit_payload,
        _cohort_route_filters,
        _reviewed_audit_metadata,
    )

    _route_str, result_filters = _route(
        "how many borrowers in Illinois clear the score cut",
        f"SELECT COUNT(*) FROM {_B360} WHERE state = 'IL' AND opportunity_score >= :min_score",
        [{"state": "IL"}],
    )
    payload = SimpleNamespace(
        action_type="open_cohort",
        conversation_id="conv-1",
        message_id="msg-1",
        question_hash="q-1",
        borrower_ids=[],
        route="/lead-queue",
        criteria={"source": "genie", "row_count": 1, "result_filters": result_filters},
    )

    # 1 + 2: the cohort keeps the replayable filter and names the rest.
    cohort_filters = _cohort_route_filters(payload, [])
    assert cohort_filters["states"] == ["IL"]
    assert cohort_filters["unreplayable_filters"] == ["opportunity_score_threshold"]
    assert "opportunity_score_threshold" not in cohort_filters

    # 3: the audit ledger accepts what the action just decided.
    metadata = json.loads(_reviewed_audit_metadata("genie.open_cohort", _audit_payload(payload)))
    assert metadata["result_filters"]["unreplayable_filters"] == ["opportunity_score_threshold"]

    # 4: a draft campaign built from it stays approvable.
    projected = project_public_campaign_json_field(
        "criteria",
        {
            "source": "genie",
            "marketing_eligibility": "Eligible only",
            "borrower_ids": [],
            "criteria_hash": "abc123",
            "criteria_keys": ["result_filters"],
            "source_assets": [],
            "conversation_id": "conv-1",
            "message_id": "msg-1",
            "question_hash": "q-1",
            "row_count": 1,
            "route": "/lead-queue",
            "result_filters": cohort_filters,
        },
    )
    assert isinstance(projected, dict)
    assert projected["result_filters"]["unreplayable_filters"] == ["opportunity_score_threshold"]

    # 5: the approved treatment set is the queue's, with no invented floor.
    treatment = cohort_filters_from_campaign_criteria(
        {"source": "genie", "result_filters": cohort_filters}
    )
    assert treatment.state_codes == ["IL"]
    assert treatment.min_opportunity_score is None


# --- Segment mode is position-gated too ------------------------------------
#
# Same P0 class as the numeric floors, and the same failure direction:
# "all" is an INTERSECTION, so a wrong "all" opens a SMALLER cohort than the
# answer described. The previous reader regexed the whole statement and called
# it an intersection whenever the text between two array_contains calls held
# "and" and no "or" — true for two calls in different CTEs or inside COUNT_IF.


def _mode(sql: str, codes: list[str]) -> str:
    from backend.services.repositories.databricks_genie_actions import (
        _segment_mode_from_sql,
    )

    return _segment_mode_from_sql(sql, codes)


def test_a_real_top_level_intersection_is_still_all() -> None:
    sql = (
        "SELECT * FROM mip.gold.borrower_360 "
        "WHERE array_contains(segment_codes,'itm') "
        "AND array_contains(segment_codes,'equity')"
    )
    assert _mode(sql, ["itm", "equity"]) == "all"


@pytest.mark.parametrize(
    "sql",
    [
        # Union — never an intersection.
        "SELECT * FROM mip.gold.borrower_360 WHERE array_contains(segment_codes,'itm') "
        "OR array_contains(segment_codes,'equity')",
        # Breakdown columns: the curated space teaches this idiom.
        "SELECT COUNT_IF(array_contains(segment_codes,'itm')) AS a, "
        "COUNT_IF(array_contains(segment_codes,'equity')) AS b "
        "FROM mip.gold.borrower_360 WHERE state='IL'",
        # Two filtered CTEs unioned back together.
        "WITH a AS (SELECT * FROM t WHERE array_contains(segment_codes,'itm')), "
        "b AS (SELECT * FROM t WHERE array_contains(segment_codes,'equity')) "
        "SELECT * FROM a UNION SELECT * FROM b",
        # A CASE arm projects a label and selects nothing.
        "SELECT CASE WHEN array_contains(segment_codes,'itm') AND "
        "array_contains(segment_codes,'equity') THEN 1 END FROM t",
    ],
)
def test_non_filtering_segment_pairs_never_become_an_intersection(sql: str) -> None:
    assert _mode(sql, ["itm", "equity"]) == "any"


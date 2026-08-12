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


def test_negative_bounds_are_read_and_REPLAYED() -> None:
    """`rate_spread_bps` is signed: 2,561,392 of 5,156,184 live rows are < 0.

    This test previously asserted the negative was DISCLOSED, and justified it
    with "the reviewed cohort vocabulary rejects a negative floor" -- which the
    same branch had already made false. The vocabularies were widened to
    -1000..5000, but this module kept its own ceiling table with a hardcoded 0
    lower bound (a sixth copy the consolidation missed), so the floor died
    here and the queue kept opening 76,711 for an answer of 39,053.

    A test that pins the defect is worse than no test: it makes the broken
    state look deliberate. It now asserts the floor reaches the cohort.
    """

    sql = f"SELECT * FROM {_B360} WHERE state = 'IL' AND rate_spread_bps >= -25"
    assert _floors(sql) == {"min_rate_spread_bps": -25}
    assert _disclosed(sql) == ()
    assert read_sql_filters(f"SELECT * FROM {_B360} WHERE rate_spread_bps >= 0").floors == {
        "min_rate_spread_bps": 0
    }
    # Out of the reviewed domain is still disclosed, never guessed.
    assert _floors(f"SELECT * FROM {_B360} WHERE rate_spread_bps >= -2000") == {}


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


# --- Position is not grain -------------------------------------------------
#
# An outer WHERE is in filter position even when the outermost FROM is a CTE
# that re-aggregated the same column name. The bound then filters GROUPS and
# the queue replays it against BORROWERS.


_REAGGREGATING_CTE_SQL = """
WITH state_scores AS (
  SELECT state, CAST(ROUND(AVG(opportunity_score)) AS INT) AS opportunity_score,
         COUNT(*) AS total_matching_borrowers
  FROM mip.gold.borrower_360 GROUP BY state)
SELECT state, opportunity_score, total_matching_borrowers
FROM state_scores WHERE opportunity_score >= 40 ORDER BY opportunity_score DESC
"""


def test_a_bound_lifted_from_a_reaggregating_cte_is_disclosed_not_replayed() -> None:
    """The grain defect, measured live on paychex gold 2026-08-11.

    ``opportunity_score >= 40`` over ``state_scores`` selects the states whose
    AVERAGE clears 40 — FL and CA, 70,576 eligible borrowers. Replayed as
    ``b.opportunity_score >= 40`` per borrower the queue shows 44,268: 26,308
    dropped, 37%, and ``unreplayable`` was empty so nothing said so.
    """

    assert _floors(_REAGGREGATING_CTE_SQL) == {}
    assert _disclosed(_REAGGREGATING_CTE_SQL) == ("opportunity_score_threshold",)
    # The layer that shipped the truncation: the handoff itself. Rows are the
    # ones the live warehouse returned for this statement, 2026-08-11.
    route, filters = _route(
        "which states average an opportunity score of 40 or better",
        _REAGGREGATING_CTE_SQL,
        [
            {"state": "FL", "opportunity_score": 40, "total_matching_borrowers": 752_572},
            {"state": "CA", "opportunity_score": 40, "total_matching_borrowers": 900_371},
        ],
    )
    assert filters["states"] == ["FL", "CA"]
    assert "min_opportunity_score" not in filters
    assert "min_opportunity_score" not in route
    assert filters["opportunity_score_threshold"] is True


@pytest.mark.parametrize(
    "sql",
    [
        # The same rebinding through a derived table rather than a CTE.
        "SELECT state, opportunity_score FROM (SELECT state, AVG(opportunity_score) AS "
        f"opportunity_score FROM {_B360} GROUP BY state) t WHERE opportunity_score >= 40",
        # A rename: the outer name is not the base column it looks like.
        "WITH s AS (SELECT state, avg_score AS opportunity_score FROM mip.gold.state_scores) "
        "SELECT * FROM s WHERE opportunity_score >= 40",
        # An unresolvable source rebinds everything it offers.
        "WITH s AS (SELECT state, opportunity_score FROM mip.gold.borrower_360) "
        "SELECT * FROM missing_cte WHERE opportunity_score >= 40",
    ],
)
def test_a_rebound_column_never_becomes_a_floor(sql: str) -> None:
    assert _floors(sql) == {}
    assert _disclosed(sql) == ("opportunity_score_threshold",)


@pytest.mark.parametrize(
    "sql",
    [
        # A CTE that only filters keeps the base column, so the floor stands.
        f"WITH eligible AS (SELECT * FROM {_B360} WHERE marketing_eligible = TRUE) "
        "SELECT * FROM eligible WHERE opportunity_score >= 80",
        # An explicit pass-through, including the `x AS x` spelling.
        f"WITH e AS (SELECT borrower_id, opportunity_score AS opportunity_score FROM {_B360}) "
        "SELECT * FROM e WHERE opportunity_score >= 80",
        # Projected through a GROUP BY it has to be a grouping key, so
        # filtering the groups selects exactly the base rows.
        f"WITH g AS (SELECT opportunity_score, COUNT(*) AS n FROM {_B360} "
        "GROUP BY opportunity_score) SELECT * FROM g WHERE opportunity_score >= 80",
    ],
)
def test_a_pass_through_column_still_lifts_its_floor(sql: str) -> None:
    """The gate refuses rebinding, not every CTE."""

    assert _floors(sql) == {"min_opportunity_score": 80}
    assert _disclosed(sql) == ()


def test_a_lateral_view_rebinds_its_own_alias_and_nothing_else() -> None:
    """"... by segment" is a MIP idiom, and it must not cost the floor.

    ``FROM borrower_360 LATERAL VIEW explode(segment_codes) seg AS
    segment_code`` multiplies rows but leaves every base column alone, so a
    bound on a base column still selects the same borrowers. Refusing the
    whole statement would drop the floor on the shape the repo itself serves
    (``_CANONICAL_MEAN_RATE_SPREAD_BY_SEGMENT_SQL``); only the alias column is
    rebound.
    """

    explode = f"FROM {_B360} LATERAL VIEW explode(segment_codes) seg AS segment_code"
    assert _floors(f"SELECT segment_code, COUNT(*) {explode} WHERE rate_spread_bps >= 25 "
                   "GROUP BY segment_code") == {"min_rate_spread_bps": 25}
    outer = f"FROM {_B360} LATERAL VIEW OUTER explode(segment_codes) seg AS segment_code"
    assert _floors(f"SELECT segment_code {outer} WHERE opportunity_score >= 80") == {
        "min_opportunity_score": 80
    }
    # The alias itself is not the base column it is named after.
    aliased = (
        f"SELECT segment_code FROM {_B360} "
        "LATERAL VIEW explode(scores) seg AS opportunity_score WHERE opportunity_score >= 80"
    )
    assert _floors(aliased) == {}
    assert _disclosed(aliased) == ("opportunity_score_threshold",)


def test_a_rebound_column_cannot_carry_a_criterion_either() -> None:
    """`min_equity_pct_label` narrows exactly as a floor does."""

    sql = (
        f"WITH s AS (SELECT state, AVG(equity_pct) AS equity_pct, "
        f"MAX(is_owner_occupied) AS is_owner_occupied FROM {_B360} GROUP BY state) "
        "SELECT * FROM s WHERE equity_pct >= 40 AND is_owner_occupied = TRUE"
    )
    assert _portfolio_criteria_from_sql(sql) == {}
    assert _floors(sql) == {}
    assert _disclosed(sql) == ("equity_pct_threshold",)


# --- A string literal is data, never a predicate ---------------------------
#
# The tokenizer opaques literals, so a threshold inside one was already out of
# reach of `floors`. `predicates` re-sliced the RAW sql, so the criteria
# readers regexed the literal's body and DATA fabricated narrowing criteria
# (adversarial review 2026-08-11).


@pytest.mark.parametrize(
    "literal",
    [
        "is_owner_occupied = true",
        "recommended_offer_code in (heloc, refi_plus_heloc)",
        "equity_pct >= 40",
        "coalesce(related_property_count, 1) >= 5",
        "current_lien_balance is null and second_pos_amount is null",
        "listed_for_sale = true",
    ],
)
def test_a_string_literal_body_cannot_spell_a_criterion(literal: str) -> None:
    sql = f"SELECT * FROM {_B360} WHERE state = 'IL' AND note = '{literal}'"
    assert _portfolio_criteria_from_sql(sql) == {}
    assert _floors(sql) == {}
    assert literal not in " ".join(read_sql_filters(sql).predicates)


def test_opaquing_literals_keeps_the_predicates_that_really_are_predicates() -> None:
    """The offer-code vocabulary is spelled in literals, so they must survive."""

    sql = (
        f"SELECT * FROM {_B360} WHERE recommended_offer_code IN ('heloc','refi_plus_heloc') "
        "AND is_owner_occupied = TRUE AND equity_pct >= 25 AND state = 'IL'"
    )
    assert _portfolio_criteria_from_sql(sql) == {
        "occupancy": "Owner-occupied",
        "product": "HELOC",
        "min_equity_pct_label": "≥ 25%",
    }
    assert _floors(sql) == {"min_equity_pct": 25}
    assert "state = 'il'" in read_sql_filters(sql).predicates


def test_a_string_literal_body_cannot_fabricate_an_intersection() -> None:
    """`mode="all"` is the narrowing direction, and a literal could spell it."""

    attack = (
        f"SELECT * FROM {_B360} WHERE array_contains(segment_codes,'itm') "
        'AND note = "array_contains(segment_codes,\'equity\')"'
    )
    # The literal is opaqued before this reader ever sees it, so the spelled
    # conjunct is not even visible as a segment predicate: the cohort is the
    # one real conjunct, not the intersection the literal asked for.
    assert _segments(attack) == (["itm"], "any", False)
    real = (
        f"SELECT * FROM {_B360} WHERE array_contains(segment_codes,'itm') "
        "AND array_contains(segment_codes,'equity')"
    )
    assert _segments(real) == (["itm", "equity"], "all", False)


def test_a_comment_inside_a_literal_no_longer_truncates_the_predicate() -> None:
    """Stripping comments from the raw slice used to cut a literal in half."""

    sql = f"SELECT * FROM {_B360} WHERE note = 'a--b' AND is_owner_occupied = TRUE"
    assert _portfolio_criteria_from_sql(sql) == {"occupancy": "Owner-occupied"}


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


def _segments(sql: str) -> tuple[list[str], str, bool]:
    from backend.services.genie_sql_predicates import read_sql_filters
    from backend.services.repositories.databricks_genie_actions import (
        _segment_selection_from_sql,
    )

    return _segment_selection_from_sql(read_sql_filters(sql))


def test_a_real_top_level_intersection_is_still_all() -> None:
    sql = (
        "SELECT * FROM mip.gold.borrower_360 "
        "WHERE array_contains(segment_codes,'itm') "
        "AND array_contains(segment_codes,'equity')"
    )
    assert _segments(sql) == (["itm", "equity"], "all", False)


def test_a_pure_disjunction_is_the_union_the_queue_replays_as_any() -> None:
    sql = (
        "SELECT * FROM mip.gold.borrower_360 "
        "WHERE (array_contains(segment_codes,'itm') "
        "OR array_contains(segment_codes,'equity')) AND state='IL'"
    )
    assert _segments(sql) == (["itm", "equity"], "any", False)


@pytest.mark.parametrize(
    "sql",
    [
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
        # The shape that motivated the whole change: segment is what the answer
        # GROUPS BY, so the answer spans every segment and filters on none.
        "SELECT sc, COUNT(*) FROM mip.gold.borrower_360 "
        "LATERAL VIEW EXPLODE(segment_codes) s AS sc GROUP BY sc",
    ],
)
def test_segments_the_answer_never_filtered_on_yield_no_cohort(sql: str) -> None:
    codes, mode, unreadable = _segments(sql)
    assert (codes, mode) == ([], "any")
    assert unreadable is False


@pytest.mark.parametrize(
    ("sql", "why"),
    [
        (
            # Live case: the queue replayed the strict second branch and opened
            # 1,991 against an answer reporting 19,166.
            "SELECT * FROM mip.gold.borrower_360 WHERE "
            "(array_contains(segment_codes,'retention') "
            "OR recommended_offer_code='retention')",
            "an OR across two columns is broader than either column alone",
        ),
        (
            "SELECT * FROM mip.gold.borrower_360 "
            "WHERE NOT array_contains(segment_codes,'itm')",
            "negation inverts membership and the queue has no NOT",
        ),
        (
            "SELECT * FROM mip.gold.borrower_360 "
            "WHERE array_contains(segment_codes,'not_a_reviewed_code')",
            "an unreviewed code is outside the replayable vocabulary",
        ),
        (
            "SELECT * FROM mip.gold.borrower_360 WHERE "
            "(array_contains(segment_codes,'itm') OR array_contains(segment_codes,'equity')) "
            "AND array_contains(segment_codes,'listed')",
            "a conjunction of disjunctions has no single any/all reading",
        ),
    ],
)
def test_unreadable_segment_predicates_are_disclosed_not_guessed(sql: str, why: str) -> None:
    codes, mode, unreadable = _segments(sql)
    assert (codes, mode) == ([], "any"), why
    assert unreadable is True, why



# --- The flag-column map is pinned to the gold CASE ladder ------------------


def test_segment_flag_columns_match_the_gold_case_ladder() -> None:
    """`WHERE in_the_money` may stand in for `itm` only while the SQL says so.

    ``_SEGMENT_FLAG_COLUMNS`` claims a boolean column selects EXACTLY a
    segment's population. That is true only because the arm building the code
    in gold_borrower_360 is nothing but that column, and nothing stops someone
    from making an arm compound later -- at which point the queue would replay
    a cohort the answer never described, silently and in the narrowing
    direction. So the claim is re-derived from the SQL on every run.

    It fails in BOTH directions on purpose: a mapped arm that stops being one
    bare column, and a compound arm that becomes one without being added.
    """

    from pathlib import Path

    from backend.services.repositories.databricks_genie_actions import (
        _SEGMENT_FLAG_COLUMNS,
    )

    sql = Path("sql/transformations/gold_borrower_360.sql").read_text()
    ladder = sql.split("with_segments AS (", 1)[1].split("AS segment_codes", 1)[0]
    ladder = re.sub(r"--[^\n]*", " ", ladder)
    arms = {
        code: re.sub(r"\s+", " ", expression).strip()
        for expression, code in re.findall(
            r"CASE\s+WHEN\s+(.+?)\s+THEN\s+'([a-z_]+)'\s+END", ladder, re.DOTALL
        )
    }
    assert arms, "could not parse the segment CASE ladder"

    expected_single = {code: col for col, code in _SEGMENT_FLAG_COLUMNS.items()}
    for code, expression in arms.items():
        bare = re.fullmatch(r"(?:\w+\.)?(\w+)", expression)
        mapped = expected_single.get(code)
        if mapped is not None:
            assert bare is not None, (
                f"segment {code!r} is mapped to a single boolean column but its arm is "
                f"now compound ({expression!r}); drop it from _SEGMENT_FLAG_COLUMNS"
            )
            assert bare.group(1) == mapped, (
                f"segment {code!r} is now defined by {bare.group(1)!r}, "
                f"not {mapped!r}"
            )
        else:
            assert bare is None, (
                f"segment {code!r} is now exactly the column {expression!r}; add it to "
                "_SEGMENT_FLAG_COLUMNS so the queue can replay the answer's own filter"
            )
    assert set(expected_single) <= set(arms), (
        f"mapped segments missing from the ladder: {set(expected_single) - set(arms)}"
    )


# --- Refused spans may only be re-read when the refusal was SHAPE alone -----
#
# `_segment_selection_from_sql` re-reads conjuncts the floor reader refused,
# because a real segment filter is usually an OR and the splitter will not
# break an OR apart. Adversarial review 2026-08-11 found two ways that let a
# STRICT SUBSET of the answer's population through with no disclosure at all.


@pytest.mark.parametrize(
    ("sql", "why"),
    [
        (
            # LEFT/RIGHT are join keywords AND string builtins. The call used
            # to close the filter region mid-OR, leaving the fragment
            # "array_contains(segment_codes,'itm') or" -- which reads as a
            # pure membership test. Answer: itm UNION zip-prefix. Replay was:
            # itm alone.
            f"SELECT COUNT(*) FROM {_B360} "
            "WHERE array_contains(segment_codes,'itm') OR LEFT(zip, 3) = '606'",
            "a dangling OR is a fragment of a longer disjunction",
        ),
        (
            f"SELECT * FROM {_B360} WHERE in_the_money OR RIGHT(zip,2)='01'",
            "same truncation reached through the flag-column spelling",
        ),
        (
            # Two truncated regions compounded into an INTERSECTION: answer
            # was (itm ∪ z) ∩ (listed ∪ c), replay was itm ∩ listed.
            f"SELECT COUNT(*) FROM {_B360} b JOIN mip.gold.evidence_events e "
            "ON array_contains(b.segment_codes,'itm') OR LEFT(b.zip,3)='606' "
            "WHERE array_contains(b.segment_codes,'listed') OR LEFT(b.city,1)='C'",
            "two truncations must not compound into a narrowing intersection",
        ),
        (
            # The grain gate refused this leaf; re-reading it reopened exactly
            # the failure the module's "Position is not grain" section exists
            # to prevent. Answer: itm UNION listed. Replay was: itm.
            "WITH pool AS (SELECT clip, state, (in_the_money OR listed_for_sale) "
            f"AS in_the_money FROM {_B360}) "
            "SELECT state, COUNT(*) FROM pool WHERE in_the_money GROUP BY state",
            "a rebound column is not the base column it shadows",
        ),
        (
            "WITH pool AS (SELECT clip, state, ARRAY('itm','listed','equity') "
            f"AS segment_codes FROM {_B360}) "
            "SELECT state FROM pool WHERE array_contains(segment_codes,'itm')",
            "a fabricated array column is not the borrower's segment list",
        ),
    ],
)
def test_a_refused_span_is_never_re_read_into_a_narrower_cohort(sql: str, why: str) -> None:
    codes, mode, unreadable = _segments(sql)
    assert (codes, mode) == ([], "any"), why
    assert unreadable is True, why


def test_a_partially_unread_segment_axis_is_still_disclosed() -> None:
    """`itm AND (listed OR score >= 80)` replays `itm` -- broader, and it says so.

    The disclosure used to be suppressed whenever any readable conjunct had
    already set `segment_codes`, on the reasoning that the queue was then "not
    broader on this axis". It is: the answer intersected a second segment
    predicate that the queue cannot express.
    """

    codes, mode, unreadable = _segments(
        f"SELECT * FROM {_B360} WHERE array_contains(segment_codes,'itm') "
        "AND (array_contains(segment_codes,'listed') OR opportunity_score >= 80)"
    )
    assert (codes, mode) == (["itm"], "any")
    assert unreadable is True


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        # A genuine parenthesised disjunction still reads exactly -- the
        # surgical LEFT/RIGHT rule must not stop `WHERE (` opening a region.
        (
            f"SELECT * FROM {_B360} WHERE (array_contains(segment_codes,'itm') "
            "OR array_contains(segment_codes,'equity')) AND state='IL'",
            (["itm", "equity"], "any", False),
        ),
        # Two membership tests, one code: the arity guard counts MATCHES, not
        # deduped codes, or this legitimate canonical shape would be refused.
        (
            f"SELECT * FROM {_B360} "
            "WHERE array_contains(segment_codes,'investor') OR is_investor = TRUE",
            (["investor"], "any", False),
        ),
        # A real LEFT JOIN is untouched by the function-call rule.
        (
            f"SELECT * FROM {_B360} b LEFT JOIN mip.gold.evidence_events e "
            "ON e.clip = b.clip WHERE in_the_money",
            (["itm"], "any", False),
        ),
    ],
)
def test_legitimate_disjunctions_still_replay_exactly(
    sql: str, expected: tuple[list[str], str, bool]
) -> None:
    assert _segments(sql) == expected


# --- A city answer hands off a city cohort, or says why it could not -------
#
# Measured live on paychex gold 2026-08-11 against an answer stating
# Chicago = 523,010 cash-out candidates, a city-grain answer degraded two ways:
#
#   rows [{city}]        -> no geography at all: 3,474,216 opened, 6.6x
#   rows [{city, state}] -> the STATE substituted for the city: 1,181,043, 2.3x
#
# The second was worse because it looked deliberate. It is now EXACT: the row
# carries both halves of the key, so the cohort opens `cities=CHICAGO~IL` and
# `states` is absent. The first still cannot be keyed -- a bare name is
# ambiguous across states (CYPRESS is CA 14,630 / TX 1) -- so it keeps the
# disclosure and adds no geography at all.


_CITY_SQL = (
    f"SELECT city, COUNT(*) AS n FROM {_B360} "
    "WHERE recommended_offer_code = 'cash_out' GROUP BY city"
)


def test_a_city_only_answer_discloses_that_the_cohort_is_broader() -> None:
    _, filters = _route_from_answer_rows(
        question="Which city has the most cash-out candidates?",
        rows=[{"city": "CHICAGO", "n": 523010}],
        borrower_ids=[],
        sql_query=_CITY_SQL,
    )

    assert filters["city_grain_unreplayable"] is True
    assert "states" not in filters
    # Fail closed: a name with no state beside it is not a key.
    assert "cities" not in filters


def test_a_city_answer_that_carries_state_opens_the_city_not_the_state() -> None:
    """The row carries both halves of the key, so the cohort is exact.

    This inverts the pre-slice expectation. `states == ["IL"]` used to be
    asserted here: that is the 2.3x substitution (Chicago's 523,010 opening
    all 1,181,043 of IL) and it is the shape this filter exists to kill.
    """

    route, filters = _route_from_answer_rows(
        question="Which city has the most cash-out candidates?",
        rows=[{"city": "CHICAGO", "state": "IL", "n": 523010}],
        borrower_ids=[],
        sql_query=_CITY_SQL,
    )

    assert filters["cities"] == ["CHICAGO~IL"]
    # The pair already carries the state. Emitting `states` alongside it is
    # the substitution, one indirection later.
    assert "states" not in filters
    assert "city_grain_unreplayable" not in filters
    # `~` is RFC-3986 unreserved, so the link stays readable.
    assert "cities=CHICAGO~IL" in route


def test_a_state_answer_is_not_labelled_city_grain() -> None:
    """The control: no city column, no disclosure."""

    _, filters = _route_from_answer_rows(
        question="Which state has the most cash-out candidates?",
        rows=[{"state": "IL", "n": 1181043}],
        borrower_ids=[],
        sql_query=_CITY_SQL,
    )

    assert filters["states"] == ["IL"]
    assert "city_grain_unreplayable" not in filters


def test_a_borrower_list_that_projects_city_is_not_labelled_city_grain() -> None:
    """`borrower_ids` pins the cohort exactly, so nothing is broader.

    The first false positive this disclosure produced: an answer listing
    borrowers with a `city` column is not a city-grain answer.
    """

    _, filters = _route_from_answer_rows(
        question="Show me the top borrowers.",
        rows=[
            {"borrower_id": "B-102FL7THC6Q3L", "city": "Seattle", "state": "WA"},
            {"borrower_id": "B-11111111111AA", "city": "Chicago", "state": "IL"},
        ],
        borrower_ids=["B-102FL7THC6Q3L", "B-11111111111AA"],
        sql_query=f"SELECT borrower_id, city, state FROM {_B360}",
    )

    assert "city_grain_unreplayable" not in filters
    assert filters["borrower_ids"] == ["B-102FL7THC6Q3L", "B-11111111111AA"]

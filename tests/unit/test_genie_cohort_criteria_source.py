"""Cohort criteria may only be read from the answer's own SQL, never its wording.

`_portfolio_criteria_from_question` inferred reviewed Portfolio Builder
criteria from the QUESTION'S WORDING and merged them under the SQL reading. It
was deleted on 2026-08-11. SQL has a position to gate on -- top-level AND
conjuncts of the outermost statement, see
`tests/unit/test_genie_sql_floor_extraction.py` for the sibling gate -- and
prose has none, so the heuristic could not tell a filter the answer applied
from a word the answer merely mentioned.

Every key it could emit (`occupancy`, `lien_status`, `owner_link`,
`purchase_intent`, `product`, `lender_relationship`, `min_equity_pct_label`)
compiles to an extra `AND` in the Lead Queue WHERE, so every one of them
NARROWS. Measured over a 51-question corpus (the `genie/sample_questions.md`
prompts, the canonical-SQL question texts, three persona asks, ten adversarial
phrasings): it emitted on 12 questions and on all 12 added at least one key the
answer's SQL did not contain.

Each case below is one of those measurements, taken live against paychex gold
on 2026-08-11.
"""

from __future__ import annotations

from typing import Any

import pytest

from backend.services.repositories.databricks_genie_actions import (
    _PRODUCT_LABEL_CODES,
    _REPLAYABLE_ROUTE_FILTER_KEYS,
    _portfolio_criteria_from_sql,
    _route_from_answer_rows,
)

_B360 = "mip.gold.borrower_360"


def _route(question: str, sql: str, rows: list[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    return _route_from_answer_rows(
        question=question,
        rows=rows,
        borrower_ids=[],
        sql_query=sql,
    )
# on 2026-08-11. SQL has a position to gate on -- top-level AND-conjuncts of
# the outermost statement -- and prose has none, so the heuristic could not
# tell a filter the answer applied from a word the answer merely mentioned.
#
# Measured on paychex gold 2026-08-11, over the 51-question corpus (the
# `genie/sample_questions.md` prompts, the canonical-SQL question texts, three
# persona asks, and ten adversarial phrasings): the prose reader emitted a
# criterion on 12 questions, and on all 12 it emitted at least one key the
# answer's own SQL did not contain. Every key it could emit NARROWS.
#
# Each case below is one of those live measurements.


@pytest.mark.parametrize(
    ("question", "sql", "harm"),
    [
        pytest.param(
            "Show the top 20 masked borrower IDs in the Investor/Multi-Property "
            "segment by related property count.",
            f"SELECT borrower_id, related_property_count FROM {_B360} "
            "WHERE array_contains(segment_codes, 'investor') "
            "ORDER BY related_property_count DESC LIMIT 10",
            # `owner_link="Multi-property (2-4)"` compiles to
            # `COALESCE(related_property_count,1) BETWEEN 2 AND 4` against an
            # answer ordered by that column DESC. Live: 10 of 10 returned
            # borrowers dropped, every one at 3,686 properties.
            "owner_link",
            id="ranking-column-becomes-its-own-ceiling",
        ),
        pytest.param(
            "How many of our current customers are at risk of going to a competitor?",
            f"SELECT COUNT(*) FROM {_B360} WHERE is_current_customer = TRUE "
            "AND (array_contains(segment_codes, 'retention') "
            "OR recommended_offer_code = 'retention')",
            # `product="Retention"` collapsed the OR onto its second branch.
            # Live: the answer reports 19,166 and the queue opened 1,991.
            "product",
            id="or-branch-collapsed-to-a-strict-and",
        ),
        pytest.param(
            "Show me borrowers who are NOT owner-occupied but have a permit.",
            f"SELECT borrower_id FROM {_B360} WHERE has_heloc_propensity_trigger = TRUE",
            # Negation is invisible to a keyword match, so the queue opened the
            # complement. Live: 39,969 asked for, 410,821 opened, intersection
            # zero -- not a truncation, a substitution.
            "occupancy",
            id="negation-inverts-the-population",
        ),
        pytest.param(
            "Compare owner-occupied vs non-owner-occupied in-the-money borrowers.",
            f"SELECT is_owner_occupied, COUNT(*) FROM {_B360} GROUP BY is_owner_occupied",
            "occupancy",
            id="comparison-answered-over-both-sides",
        ),
        pytest.param(
            "Which segment converts best: HELOC, cash-out, or retention?",
            f"SELECT recommended_offer_code, COUNT(*) FROM {_B360} "
            "GROUP BY recommended_offer_code",
            "product",
            id="enumeration-is-not-a-filter",
        ),
        pytest.param(
            "Everyone except cash-out candidates — who are they?",
            f"SELECT borrower_id FROM {_B360} WHERE recommended_offer_code <> 'cash_out'",
            "product",
            id="exclusion-read-as-inclusion",
        ),
        pytest.param(
            "Which borrowers have LTV above 25%?",
            f"SELECT borrower_id FROM {_B360} WHERE ltv_pct > 25",
            # A bare percentage carries no dimension: LTV >= 25% is roughly
            # equity <= 75%, the opposite of `min_equity_pct_label="≥ 25%"`.
            "min_equity_pct_label",
            id="bare-percentage-has-no-dimension",
        ),
        # These two never fired: the pattern demanded the dimension BEFORE the
        # number, so the most natural phrasing was a blind spot while "LTV
        # above 25%" above was a hit. They are pinned so a future attempt to
        # "fix" the blind spot cannot reintroduce the reader.
        pytest.param(
            "which borrowers have more than 25% equity",
            f"SELECT borrower_id FROM {_B360} WHERE equity_pct > 25",
            "min_equity_pct_label",
            id="bare-percentage-plain-phrasing",
        ),
        pytest.param(
            "show me borrowers with 25% equity in Texas",
            f"SELECT borrower_id, state FROM {_B360} WHERE state = 'TX'",
            "min_equity_pct_label",
            id="bare-percentage-with-geography",
        ),
        pytest.param(
            "What share of the book is free and clear versus carrying an open HELOC?",
            f"SELECT COUNT_IF(second_pos_amount > 0) AS heloc, COUNT(*) FROM {_B360}",
            "lien_status",
            id="share-of-the-whole-book",
        ),
    ],
)
def test_question_wording_never_attaches_a_cohort_criterion(
    question: str, sql: str, harm: str
) -> None:
    _route_str, filters = _route(question, sql, [{"borrowers": 1}])
    criteria = filters.get("portfolio_criteria") or {}
    assert harm not in criteria, (
        f"{harm!r} was inferred from the question's wording; the answer's SQL never applied it"
    )
    # Whatever criteria survive must be readable back out of the SQL alone.
    assert criteria == {
        key: value for key, value in _portfolio_criteria_from_sql(sql).items() if key in criteria
    }


def test_route_criteria_are_exactly_the_sql_reading() -> None:
    """No wording-derived key may ride along, for any question at all."""

    sql = f"SELECT COUNT(*) FROM {_B360} WHERE is_owner_occupied = TRUE AND equity_pct >= 40"
    loud = (
        "Show me owner-occupied cash-out and HELOC borrowers who are current customers "
        "with a permit, listed for sale, multi-property, free and clear, at risk of going "
        "to a competitor, with equity above 25%."
    )
    _route_str, filters = _route(loud, sql, [{"borrowers": 1}])
    assert filters["portfolio_criteria"] == _portfolio_criteria_from_sql(sql)
    assert filters["portfolio_criteria"] == {
        "occupancy": "Owner-occupied",
        "min_equity_pct_label": "≥ 40%",
    }


@pytest.mark.parametrize(
    ("question", "sql"),
    [
        (
            "Show the top 20 masked borrower IDs in the Investor/Multi-Property segment "
            "by related property count.",
            f"SELECT borrower_id FROM {_B360} WHERE array_contains(segment_codes, 'investor')",
        ),
        (
            "How many of our current customers are at risk of going to a competitor?",
            f"SELECT COUNT(*) FROM {_B360} WHERE is_current_customer = TRUE",
        ),
        (
            "Which borrowers on our retention list have a competitor lien filed "
            "in the last 30 days?",
            f"SELECT borrower_id FROM {_B360} WHERE array_contains(segment_codes, 'retention')",
        ),
        (
            "Show the top 10 cash-out candidates by estimated equity across the current "
            "Cotality data coverage.",
            f"SELECT borrower_id FROM {_B360} WHERE recommended_offer_code = 'cash_out'",
        ),
    ],
)
def test_questions_that_lost_a_prose_criterion_still_replay(question: str, sql: str) -> None:
    """Removing the criterion must not cost the question its cohort.

    Trading a truncated cohort for no cohort at all would swap one defect for
    another: `_materialize_genie_cohort` rejects a cohort with no replayable
    filter, so the "open cohort" action would 400.
    """

    _route_str, filters = _route(question, sql, [{"borrowers": 1, "state": "IL"}])
    assert _REPLAYABLE_ROUTE_FILTER_KEYS & set(filters), filters


# --- The product label the answer's own SQL pinned --------------------------


def test_product_label_needs_the_exact_offer_code_set() -> None:
    """A label is a code SET, not a synonym for one code.

    The queue compiles `HELOC` to `recommended_offer_code IN ('heloc',
    'refi_plus_heloc')`. Replaying that label off `= 'heloc'` alone would open
    a BROADER cohort than the answer; replaying it off a mixed set would open a
    narrower one. Only exact equality is faithful.
    """

    def product(predicate: str) -> str | None:
        return _portfolio_criteria_from_sql(f"SELECT 1 FROM {_B360} WHERE {predicate}").get(
            "product"
        )

    # Exact: replayable.
    assert product("recommended_offer_code = 'cash_out'") == "Cash-out"
    assert product("recommended_offer_code IN ('heloc', 'refi_plus_heloc')") == "HELOC"
    assert product("recommended_offer_code IN ('refi', 'refi_plus_heloc')") == "Refi"
    assert product("recommended_offer_code = 'retention'") == "Retention"
    # Strict subset (would broaden), mixed set (would narrow), disagreeing
    # conjuncts (ambiguous): none are replayable as a reviewed label.
    assert product("recommended_offer_code = 'heloc'") is None
    assert product("recommended_offer_code IN ('heloc', 'cash_out')") is None
    assert (
        product("recommended_offer_code = 'cash_out' AND recommended_offer_code = 'retention'")
        is None
    )
    # Position still governs: a breakdown column is not a filter.
    assert (
        _portfolio_criteria_from_sql(
            "SELECT COUNT_IF(recommended_offer_code = 'cash_out') AS c "
            f"FROM {_B360} WHERE state = 'IL'"
        ).get("product")
        is None
    )


def test_product_label_codes_match_the_queue_vocabulary() -> None:
    """The map is duplicated to keep this module free of the SQL/Lakebase imports.

    A drift between the two silently changes which answers are replayable, so
    pin it rather than trust the comment.
    """

    from backend.services.repositories.databricks_portfolio import PORTFOLIO_PRODUCT_CODES

    assert {label.lower(): set(codes) for label, codes in _PRODUCT_LABEL_CODES.items()} == {
        label: set(codes) for label, codes in PORTFOLIO_PRODUCT_CODES.items()
    }

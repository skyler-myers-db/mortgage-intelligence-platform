"""Figures verified against the rows (audit 2026-09-21 ``genie-10`` phase 1).

``check_numeric_claims`` runs today's per-claim evaluation once and keeps
what it proved: the count of checked figures, the supported ones and how
each was supported. Pinned here: the counts, one derivation per support
class, the 40-item cap, that an unsupported number is never listed, that
``_unsupported_answer_numeric_claims`` is exactly ``.unsupported``, and that
the adapter attaches ``proof.claims`` only to prose that ships.
"""

from __future__ import annotations

from typing import Any

import pytest

from backend.services.genie_answers import GenieClaimsSummary
from backend.services.repositories.databricks_genie_numeric import (
    _unsupported_answer_numeric_claims,
    check_numeric_claims,
    claims_summary,
)

_ROWS: list[dict[str, Any]] = [
    {"state": "IL", "borrowers": 48396, "avg_rate_spread_bps": 182.5, "in_the_money_share": 0.483},
    {"state": "TX", "borrowers": 10914, "avg_rate_spread_bps": 141.0, "in_the_money_share": 0.221},
]


def _derivations(text: str, rows: list[dict[str, Any]] = _ROWS) -> dict[str, str]:
    return {item.token: item.derivation for item in check_numeric_claims(text, rows, "q").verified}


@pytest.mark.parametrize(
    ("text", "token", "derivation"),
    [
        ("Illinois leads with 48,396 borrowers.", "48,396", "returned_value"),
        ("Illinois has a 48.3% in-the-money share.", "48.3%", "returned_value"),
        ("Together the two states hold 59,310 borrowers.", "59,310", "derived_from_rows"),
        ("Illinois holds 81.6% of these borrowers.", "81.6%", "derived_from_rows"),
        ("Both states carry over 40,000 borrowers at the top.", "40,000", "bound"),
    ],
)
def test_each_support_class_gets_its_derivation(text: str, token: str, derivation: str) -> None:
    assert _derivations(text)[token] == derivation


@pytest.mark.parametrize(
    ("extra", "derivation"),
    [
        ({"period": "2026-09-30"}, "derived_from_rows"),
        ({"refreshed_at": "2026-09-30 04:00:00"}, "derived_from_rows"),
        ({"property_id": "P2026"}, "derived_from_rows"),
        # Control: a number inside a free-text cell is still one a reader finds.
        ({"note": "2,026 borrowers in scope"}, "returned_value"),
    ],
)
def test_a_date_or_identifier_cell_never_labels_a_figure_a_returned_value(
    extra: dict[str, str], derivation: str
) -> None:
    # 2,026 is the two states' total; the year in a date cell (or the digits
    # of an identifier) must not relabel it. The verdict is unchanged.
    rows = [{"state": "IL", "borrowers": 1000, **extra}, {"state": "TX", "borrowers": 1026, **extra}]
    check = check_numeric_claims("Together the two states hold 2,026 borrowers.", rows, "q")

    assert (check.total, check.unsupported) == (1, [])
    assert {item.token: item.derivation for item in check.verified} == {"2,026": derivation}


def test_a_row_count_is_derived_from_the_rows() -> None:
    rows = [{"state": "IL"}, {"state": "TX"}, {"state": "CA"}]

    assert _derivations("The 3 states listed hold the borrowers.", rows) == {"3": "derived_from_rows"}


def test_the_counts_and_the_wrapper_are_exactly_the_old_verdict() -> None:
    text = "Illinois leads with 48,396 borrowers and Texas has 99,999 borrowers; 59,310 borrowers in total."
    check = check_numeric_claims(text, _ROWS, "q")

    assert check.total == 3
    assert check.verified_count == 2
    assert check.unsupported == ["unsupported_numeric_claim"]
    assert _unsupported_answer_numeric_claims(text, _ROWS, "q") == check.unsupported
    assert "99,999" not in {item.token for item in check.verified}, "an unsupported number is never listed"
    assert claims_summary(check) == GenieClaimsSummary(
        verified=2,
        total=3,
        items=[item for item in claims_summary(check).items],  # type: ignore[union-attr]
    )


def test_nothing_checked_is_no_summary() -> None:
    assert check_numeric_claims(None, _ROWS, "q").total == 0
    assert claims_summary(check_numeric_claims("No figures here.", _ROWS, "q")) is None


def test_the_verified_list_is_capped_at_forty() -> None:
    rows = [{"borrowers": n * 7} for n in range(1, 61)]
    text = "; ".join(f"{n * 7} borrowers" for n in range(1, 61))
    check = check_numeric_claims(text, rows, "q")

    assert check.verified_count == 60
    assert len(check.verified) == 40
    summary = claims_summary(check, section="Market size")
    assert summary is not None and (summary.verified, summary.total, len(summary.items)) == (60, 60, 40)
    assert {item.section for item in summary.items} == {"Market size"}


def test_the_summary_refuses_more_verified_than_checked() -> None:
    with pytest.raises(ValueError):
        GenieClaimsSummary(verified=3, total=2, items=[])


# ------------------------------------------------------ the adapter attaches


def _adapt(answer_text: str) -> Any:
    from backend.services.genie_client import GenieResponse
    from backend.services.repositories.databricks_genie import _adapt_genie_response

    result = GenieResponse(
        answer_text=answer_text,
        sql_query="SELECT state, COUNT(*) AS borrowers FROM mip.gold.borrower_360 GROUP BY state",
        sql_result_rows=[{"state": "IL", "borrowers": 48396}, {"state": "TX", "borrowers": 10914}],
        conversation_id="conv-1",
        message_id="msg-1",
        trusted_assets=["mip.gold.borrower_360"],
    )
    return _adapt_genie_response("How many borrowers are in each state?", result)


def test_shipped_prose_carries_its_verified_figures_and_withheld_prose_carries_none() -> None:
    shipped = _adapt("Illinois leads with 48,396 borrowers, ahead of Texas at 10,914.")
    withheld = _adapt("Illinois leads with 77,777 borrowers.")

    assert shipped.proof.claims is not None
    assert (shipped.proof.claims.verified, shipped.proof.claims.total) == (2, 2)
    assert [item.token for item in shipped.proof.claims.items] == ["48,396", "10,914"]
    assert withheld.proof.claims is None

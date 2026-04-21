"""Matcher tests for the widened Genie deterministic catalog.

Each case is an audience-realistic question paired with the intent we
expect the scored matcher to pick — a mix of direct hits and near-misses
that would have collided in the naive ``if token in q`` matcher.
Off-topic questions must fall through to the warm generic fallback.
"""
from __future__ import annotations

import pytest

from backend.services.genie_answers import match_intent, respond


@pytest.mark.parametrize(
    "question, expected_intent",
    [
        # --- Geography -----------------------------------------------------
        ("Which ZIPs have the most in-the-money refi candidates?", "itm_zips"),
        ("top zips by ITM volume", "itm_zips"),
        ("How many in-the-money borrowers in Travis County?", "travis_county"),
        ("Where's the biggest HELOC opportunity by state?", "heloc_by_state"),
        ("Which state leads on HELOC?", "heloc_by_state"),
        # --- Offer / branch ------------------------------------------------
        ("Show me all the purchase-mortgage candidates.", "purchase"),
        ("listed for sale borrowers", "purchase"),
        ("How many borrowers qualify for refi + HELOC cross-sell?", "refi_plus_heloc"),
        ("who gets both refi and HELOC?", "refi_plus_heloc"),
        # --- Segment comparisons -------------------------------------------
        ("Which segment converts best?", "best_converting"),
        ("best converting segment this quarter", "best_converting"),
        ("How does Listed for Sale compare to Permit Activity on avg score?", "listed_vs_permit"),
        # --- Borrower lookups ---------------------------------------------
        ("Show me the top 10 highest-score borrowers.", "top_borrowers"),
        ("give me the top 20 borrowers", "top_borrowers"),
        ("Who in Austin is in the money?", "austin_itm"),
        ("austin borrowers ranked", "austin_itm"),
        # --- Trend / pipeline ---------------------------------------------
        ("How is our cost per contact trending?", "cost_per_contact"),
        ("What's the lift from adding permit data?", "permit_lift"),
        # --- Policy / threshold -------------------------------------------
        ("What if we raised the HELOC equity floor to 50%?", "heloc_floor_50"),
        ("How many approvals were logged today?", "approvals_today"),
        # --- Canonical three (regression) ---------------------------------
        ("What about retention risk?", "retention"),
        ("show me the in the money segment", "in_the_money"),
        ("permits and home equity — what do we see?", "heloc"),
    ],
)
def test_matcher_picks_expected_intent(question: str, expected_intent: str) -> None:
    assert match_intent(question) == expected_intent


@pytest.mark.parametrize(
    "question",
    [
        "What's the weather in Tokyo?",
        "Tell me a joke about mortgages.",
        "asdfghjkl",
        "",
        "   ",
    ],
)
def test_offtopic_falls_through_to_warm_fallback(question: str) -> None:
    assert match_intent(question) is None
    r = respond(question)
    assert r.source == "deterministic_fallback"
    assert "segments" in r.answer.lower()
    assert r.follow_up_questions  # warm fallback still nudges the stage


def test_response_carries_question_and_uc_sources() -> None:
    r = respond("Which ZIPs have the most in-the-money refi candidates?")
    assert r.question == "Which ZIPs have the most in-the-money refi candidates?"
    assert any(a.startswith("mip_demo.") for a in r.trusted_assets)
    assert r.table_rows and len(r.table_rows) >= 3


def test_top_borrowers_cites_real_population() -> None:
    from backend.services.mock_data import BORROWERS

    r = respond("top 10 borrowers by score")
    assert r.table_rows is not None
    assert len(r.table_rows) == 10
    real_ids = {b.borrower_id for b in BORROWERS}
    for row in r.table_rows:
        assert row["borrower_id"] in real_ids, row

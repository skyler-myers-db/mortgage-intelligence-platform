"""Parity tests for backend.services.scoring.next_best_offer.

Fixtures in tests/fixtures/next_best_offer_golden.json are the contract;
the SQL UDF mip_demo.gold.fn_next_best_offer is validated against the
same set by sql/fixtures/next_best_offer_validation.sql.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.services.scoring import NBO_PRODUCT_LABELS, next_best_offer

FIXTURE_PATH = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "fixtures"
    / "next_best_offer_golden.json"
)

with FIXTURE_PATH.open() as f:
    FIXTURE = json.load(f)

GOLDEN_CASES = FIXTURE["cases"]

_EXPECTED_CODES = {
    "purchase",
    "refi_plus_heloc",
    "heloc",
    "refi",
    "cash_out",
    "investor",
    "retention",
    "nurture",
}


@pytest.mark.parametrize(
    "case",
    GOLDEN_CASES,
    ids=[c["id"] for c in GOLDEN_CASES],
)
def test_next_best_offer_matches_golden_fixture(case: dict) -> None:
    """Every golden case must produce the SQL-pinned expected_offer."""
    assert next_best_offer(**case["inputs"]) == case["expected_offer"], case.get("note", "")


def test_nbo_product_labels_covers_all_eight_codes() -> None:
    """Label map is the contract for API-boundary rendering — must be complete."""
    assert set(NBO_PRODUCT_LABELS.keys()) == _EXPECTED_CODES


def test_all_null_signals_with_real_thresholds_land_in_nurture() -> None:
    """All-NULL signal row with live thresholds must fall through to 'nurture'.

    This is the production-shaped NULL case (case_14 in the golden
    fixture): the warehouse always supplies real thresholds from admin
    config, but individual borrower signals may be NULL. Coercion to
    0/FALSE under a real ``min_spread_bps`` (75) blocks every branch.
    """
    assert (
        next_best_offer(
            rate_spread_bps=None,
            equity_pct=None,
            has_permit=None,
            listed_for_sale=None,
            is_investor=None,
            is_current_customer=None,
            is_competitor_lien=None,
            min_spread_bps=75,
            min_equity_pct=15,
            heloc_equity_min_pct=35,
            cashout_equity_min=25,
            retention_min_spread=50,
        )
        == "nurture"
    )


def test_next_best_offer_importable_from_scoring_module() -> None:
    """The primitive and label map must both live in backend.services.scoring."""
    from backend.services import scoring

    assert hasattr(scoring, "next_best_offer")
    assert hasattr(scoring, "NBO_PRODUCT_LABELS")
    assert scoring.NBO_PRODUCT_LABELS["refi_plus_heloc"] == "Refinance + HELOC"

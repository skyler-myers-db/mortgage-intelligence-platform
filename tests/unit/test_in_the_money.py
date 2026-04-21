"""Parity tests for backend.services.scoring.in_the_money.

Fixtures in tests/fixtures/in_the_money_golden.json are the contract;
the SQL UDF mip_demo.gold.fn_in_the_money is validated against the same
set by sql/fixtures/in_the_money_validation.sql.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.services.scoring import in_the_money

FIXTURE_PATH = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "fixtures"
    / "in_the_money_golden.json"
)

with FIXTURE_PATH.open() as f:
    FIXTURE = json.load(f)

GOLDEN_CASES = FIXTURE["cases"]


@pytest.mark.parametrize(
    "case",
    GOLDEN_CASES,
    ids=[c["id"] for c in GOLDEN_CASES],
)
def test_in_the_money_matches_golden_fixture(case: dict) -> None:
    """Every golden case must produce the SQL-pinned expected_itm."""
    assert in_the_money(**case["inputs"]) is case["expected_itm"], case.get("note", "")


def test_in_the_money_null_min_spread_returns_false() -> None:
    """Missing threshold -> FALSE. Unknown must not become GO for outreach."""
    assert in_the_money(150, 40, None, 15) is False


def test_in_the_money_null_min_equity_returns_false() -> None:
    """Symmetric safety pin for the equity threshold."""
    assert in_the_money(150, 40, 75, None) is False

"""Parity tests for backend.services.scoring.rate_spread_bps.

Fixtures in tests/fixtures/rate_spread_golden.json are the contract;
the SQL UDF mip.gold.fn_rate_spread is validated against the same
set by sql/fixtures/rate_spread_validation.sql.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.services.scoring import rate_spread_bps

FIXTURE_PATH = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "fixtures"
    / "rate_spread_golden.json"
)

with FIXTURE_PATH.open() as f:
    FIXTURE = json.load(f)

GOLDEN_CASES = FIXTURE["cases"]


@pytest.mark.parametrize(
    "case",
    GOLDEN_CASES,
    ids=[c["id"] for c in GOLDEN_CASES],
)
def test_rate_spread_matches_golden_fixture(case: dict) -> None:
    """Every golden case must produce the SQL-pinned expected_bps."""
    assert rate_spread_bps(**case["inputs"]) == case["expected_bps"], case.get("note", "")


def test_rate_spread_fits_in_int32_range() -> None:
    """Defense-in-depth: extreme inputs still yield an INT32-representable value."""
    result = rate_spread_bps(0.50, 0.0)
    assert -2_147_483_648 <= result <= 2_147_483_647
    assert result == 5000

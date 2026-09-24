"""Parity tests for backend.services.scoring.rate_spread_bps.

Fixtures in tests/fixtures/rate_spread_golden.json are the contract;
the SQL UDF mip.gold.fn_rate_spread is validated against the same
set by sql/fixtures/rate_spread_validation.sql.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.services.scoring import (
    RATE_SCENARIO_MAX_SHIFT_BPS,
    RATE_SPREAD_BPS_PER_UNIT,
    rate_spread_bps,
    rate_spread_bps_at_par_shift,
)

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


@pytest.mark.parametrize(
    "case",
    GOLDEN_CASES,
    ids=[c["id"] for c in GOLDEN_CASES],
)
def test_zero_par_shift_is_the_live_spread(case: dict) -> None:
    """A 0 bps scenario is exactly today's spread, rounding included."""
    inputs = case["inputs"]
    assert rate_spread_bps_at_par_shift(inputs["current_rate"], inputs["market_rate"], 0) == case["expected_bps"]


@pytest.mark.parametrize("shift_bps", [-RATE_SCENARIO_MAX_SHIFT_BPS, -25, 25, RATE_SCENARIO_MAX_SHIFT_BPS])
def test_par_shift_moves_the_spread_one_for_one(shift_bps: int) -> None:
    """Par rising N bps takes N bps off the spread (a clean, rounding-free case)."""
    assert rate_spread_bps_at_par_shift(0.0650, 0.0600, shift_bps) == 50 - shift_bps


def test_par_shift_composes_the_live_mirror() -> None:
    """The scenario is fn_rate_spread against a moved par, so it rounds half-even like it."""
    for shift_bps in range(-RATE_SCENARIO_MAX_SHIFT_BPS, RATE_SCENARIO_MAX_SHIFT_BPS + 1):
        moved = 0.06125 + shift_bps / RATE_SPREAD_BPS_PER_UNIT
        assert rate_spread_bps_at_par_shift(0.07, 0.06125, shift_bps) == rate_spread_bps(0.07, moved)


def test_par_shift_keeps_the_no_signal_rule() -> None:
    """A missing rate on either side is still 'no signal', whatever the shift."""
    assert rate_spread_bps_at_par_shift(None, 0.06, 50) == 0
    assert rate_spread_bps_at_par_shift(0.07, None, 50) == 0


def test_bps_scale_is_the_sql_scale() -> None:
    """fn_rate_spread multiplies by 10000; the mirror's constant is that number."""
    assert RATE_SPREAD_BPS_PER_UNIT == 10000.0
    assert RATE_SCENARIO_MAX_SHIFT_BPS == 100

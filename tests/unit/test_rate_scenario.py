"""Rate-scenario arithmetic (Rate Lever, audit wow-stage-1).

Every scenario step is a different market rate run through the canonical
primitives, never a shift of rounded spread bins. The golden fixture pins
cases where the two methods disagree (half-basis-point raws under BROUND's
half-even rule), and these tests recompute every case through
``rate_scenario`` so a bin-shifting or DECIMAL-literal regression turns them
red.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any

import pytest

from backend.services import rate_scenario, scoring
from backend.services.rate_scenario import (
    RATE_SCENARIO_STEPS_BPS,
    SCENARIO_ITM_SQL,
    scenario_in_the_money,
    scenario_market_rate,
)

GOLDEN_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "rate_scenario_golden.json"


def _golden() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    return data


CASES: list[dict[str, Any]] = _golden()["cases"]


def _raw(case: dict[str, Any]) -> float:
    return (
        case["note_rate_fraction"]
        - scenario_market_rate(case["market_rate_fraction"], case["step_bps"])
    ) * scoring.RATE_SPREAD_BPS_PER_UNIT


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_golden_case_recomputes_through_the_scoring_helper(case: dict[str, Any]) -> None:
    note = case["note_rate_fraction"]
    market = case["market_rate_fraction"]
    step = case["step_bps"]
    spread = scoring.rate_spread_bps_at_par_shift(note, market, step)
    assert spread == case["expected_spread_bps"]
    assert (
        scenario_in_the_money(
            note,
            market,
            step,
            case["equity_pct"],
            case["min_spread_bps"],
            case["min_equity_pct"],
        )
        is case["expected_in_the_money"]
    )
    # The verdict IS fn_in_the_money over the direct spread: no second rule.
    assert case["expected_in_the_money"] is scoring.in_the_money(
        spread, case["equity_pct"], case["min_spread_bps"], case["min_equity_pct"]
    )
    if note is None:
        assert spread == 0
        assert case["raw_spread_bps"] is None
    else:
        # The raw the fixture records is the float the helper rounds.
        assert _raw(case) == case["raw_spread_bps"]
        assert case["bin_shift_spread_bps"] == scoring.rate_spread_bps(note, market) - step


def test_golden_cases_are_not_vacuous() -> None:
    """The fixture must carry the cases that separate the methods."""
    tags = [set(case["tags"]) for case in CASES]
    boundary = [case for case in CASES if "half_bps_boundary" in case["tags"]]
    assert len(CASES) >= 12
    assert len(boundary) >= 3
    for case in boundary:
        raw = case["raw_spread_bps"]
        assert abs(abs(raw - int(raw)) - 0.5) < 1e-9, case["id"]
    disagree = [case for case in CASES if "bin_shift_disagrees" in case["tags"]]
    assert len(disagree) >= 2
    for case in disagree:
        assert case["bin_shift_spread_bps"] != case["expected_spread_bps"], case["id"]
    # At least two disagreements change the in-the-money verdict itself.
    assert sum("bin_shift_flips_verdict" in t for t in tags) >= 2
    equality = [case for case in CASES if "threshold_equality" in case["tags"]]
    verdicts = {case["expected_in_the_money"] for case in equality}
    assert verdicts == {True, False}
    at_threshold = [
        case
        for case in equality
        if case["expected_spread_bps"] == case["min_spread_bps"]
        and case["equity_pct"] >= case["min_equity_pct"]
    ]
    assert at_threshold and all(case["expected_in_the_money"] for case in at_threshold)
    nulls = [case for case in CASES if case["note_rate_fraction"] is None]
    assert {case["step_bps"] for case in nulls} >= {min(RATE_SCENARIO_STEPS_BPS), 0, max(RATE_SCENARIO_STEPS_BPS)}
    assert all(case["expected_spread_bps"] == 0 for case in nulls)
    # Every case sits on the grid the table is built over.
    assert {case["step_bps"] for case in CASES} <= set(RATE_SCENARIO_STEPS_BPS)


def test_bin_shifting_is_not_the_scenario_rule() -> None:
    """Direct evaluation and bin shifting disagree on the half-bps cases."""
    for case in (c for c in CASES if "bin_shift_disagrees" in c["tags"]):
        shifted = scoring.rate_spread_bps(case["note_rate_fraction"], case["market_rate_fraction"]) - case["step_bps"]
        direct = scoring.rate_spread_bps_at_par_shift(
            case["note_rate_fraction"], case["market_rate_fraction"], case["step_bps"]
        )
        assert shifted != direct, case["id"]


def test_grid_is_symmetric_and_bounded_by_the_shared_maximum() -> None:
    steps = RATE_SCENARIO_STEPS_BPS
    assert list(steps) == sorted(steps)
    assert 0 in steps
    assert max(abs(step) for step in steps) == scoring.RATE_SCENARIO_MAX_SHIFT_BPS
    assert [-step for step in reversed(steps)] == list(steps)
    assert _golden()["grid_bps"] == list(steps)


def test_step_expression_uses_the_shared_scale_in_double_arithmetic() -> None:
    """The SQL step divides by the scoring scale, cast to DOUBLE on both sides."""
    normalized = " ".join(SCENARIO_ITM_SQL.split())
    match = re.search(
        r"market_rate_fraction \+ CAST\(step_bps AS DOUBLE\) / CAST\((\d+) AS DOUBLE\)",
        normalized,
    )
    assert match, normalized
    assert float(match.group(1)) == scoring.RATE_SPREAD_BPS_PER_UNIT
    # A DECIMAL literal (10000.0) would change the arithmetic.
    assert "10000.0" not in normalized
    assert "rate_spread_bps" not in normalized.replace("rate_spread_bps(", "")


def test_scenario_market_rate_matches_the_scoring_par_shift() -> None:
    for step in RATE_SCENARIO_STEPS_BPS:
        market = 0.063
        assert scenario_market_rate(market, step) == market + step / scoring.RATE_SPREAD_BPS_PER_UNIT
        note = 0.07125
        assert scoring.rate_spread_bps(note, scenario_market_rate(market, step)) == (
            scoring.rate_spread_bps_at_par_shift(note, market, step)
        )


def test_module_defines_no_bps_constant_of_its_own() -> None:
    """The basis-point scale has one home: scoring.RATE_SPREAD_BPS_PER_UNIT."""
    tree = ast.parse(Path(rate_scenario.__file__).read_text(encoding="utf-8"))
    numbers = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, int | float) and not isinstance(node.value, bool)
    ]
    assert scoring.RATE_SPREAD_BPS_PER_UNIT not in numbers
    strings = [node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)]
    # Docstrings may say "/ 10000"; no SQL text may carry the scale literally.
    assert not any("CAST(10000" in text or "10000.0" in text for text in strings)

"""score_points on Lead Queue rows (audit wow-stage-2, gold half).

``score_points_from_row`` projects the five gold ``*_points`` columns into
``LeadScorePoints`` only when they are all present and sum, banker-rounded and
clipped to 0..100, to the row's own opportunity_score. It never fabricates:
an absent, NULL or inconsistent row gets None, and a mismatch warns once per
process with counts only.
"""

from __future__ import annotations

import json
import logging
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from backend.services import lead_score_points as lsp
from backend.services.scoring import _LEAD_SCORE_WEIGHTS, lead_score

REPO = Path(__file__).resolve().parents[2]
_KEYS = [key for key, _column in lsp.SCORE_POINT_KEYS]
_COLUMNS = [column for _key, column in lsp.SCORE_POINT_KEYS]


@pytest.fixture(autouse=True)
def _reset() -> None:
    lsp._reset_lead_score_points_for_tests()


def _row(points: list[Any], score: Any) -> dict[str, Any]:
    return {**dict(zip(_COLUMNS, points, strict=True)), "opportunity_score": score}


def _gold_points(components: dict[str, int]) -> list[Decimal]:
    """What gold's CAST(w * sub AS DECIMAL(5,2)) yields: exact decimal products."""
    return [weight * Decimal(components[key]) for weight, key in zip(_LEAD_SCORE_WEIGHTS, _KEYS, strict=True)]


def test_points_that_sum_to_the_score_are_projected_in_the_canonical_keys() -> None:
    points = lsp.score_points_from_row(_row(["29.75", "18.00", "13.50", "6.50", "4.00"], 72))

    assert points is not None
    assert points.model_dump() == {
        "economic_incentive": 29.75,
        "intent_trigger": 18.0,
        "fit": 13.5,
        "relationship": 6.5,
        "evidence": 4.0,
    }


@pytest.mark.parametrize(
    "values",
    [
        ["29.75", "18.00", "13.50", "6.50", "4.00"],
        [29.75, 18.0, 13.5, 6.5, 4.0],
        [Decimal("29.75"), Decimal("18.00"), Decimal("13.50"), Decimal("6.50"), Decimal("4.00")],
    ],
    ids=["str", "float", "decimal"],
)
def test_every_decimal_wire_shape_is_accepted(values: list[Any]) -> None:
    assert lsp.score_points_from_row(_row(values, 72)) is not None


def test_the_golden_lead_score_cases_round_trip_through_the_points() -> None:
    cases = json.loads((REPO / "tests/fixtures/lead_score_golden.json").read_text(encoding="utf-8"))
    assert cases
    for case in cases:
        components = case["inputs"]
        if any(components[key] is None for key in _KEYS):
            continue
        score = lead_score(*(components[key] for key in _KEYS))
        assert score == case["expected_score"], case["id"]
        projected = lsp.score_points_from_row(_row([str(p) for p in _gold_points(components)], score))
        assert projected is not None, case["id"]


def test_banker_rounding_decides_a_half_point_sum() -> None:
    # 72.50 rounds half-to-even to 72, never up to 73.
    half = ["30.00", "18.00", "13.50", "6.50", "4.50"]
    assert lsp.score_points_from_row(_row(half, 72)) is not None
    assert lsp.score_points_from_row(_row(half, 73)) is None
    # 73.50 rounds to 74.
    odd_half = ["30.00", "19.00", "13.50", "6.50", "4.50"]
    assert lsp.score_points_from_row(_row(odd_half, 74)) is not None


@pytest.mark.parametrize("missing", _COLUMNS)
def test_any_absent_or_null_point_gives_none(missing: str) -> None:
    complete = _row(["29.75", "18.00", "13.50", "6.50", "4.00"], 72)
    absent = {key: value for key, value in complete.items() if key != missing}
    assert lsp.score_points_from_row(absent) is None
    assert lsp.score_points_from_row({**complete, missing: None}) is None


@pytest.mark.parametrize("bad", ["-0.01", "100.01", "NaN", "Infinity", "abc", True])
def test_an_out_of_contract_value_gives_none(bad: Any) -> None:
    assert lsp.score_points_from_row(_row([bad, "18.00", "13.50", "6.50", "4.00"], 72)) is None


def test_a_missing_or_malformed_score_gives_none() -> None:
    points = ["29.75", "18.00", "13.50", "6.50", "4.00"]
    assert lsp.score_points_from_row({**_row(points, 72), "opportunity_score": None}) is None
    assert lsp.score_points_from_row({**_row(points, 72), "opportunity_score": "x"}) is None


def test_a_mismatch_gives_none_and_warns_once_with_counts_only(caplog: pytest.LogCaptureFixture) -> None:
    disagreeing = _row(["29.75", "18.00", "13.50", "6.50", "4.00"], 90)
    with caplog.at_level(logging.WARNING):
        assert lsp.score_points_from_row({**disagreeing, "borrower_id": "B-OGCTESTROW001"}) is None
        assert lsp.score_points_from_row(disagreeing) is None

    warned = [r for r in caplog.records if getattr(r, "mip_event", None) == "lead_score_points_mismatch"]
    assert len(warned) == 1, "one warning per process"
    assert warned[0].mip_extras == {"mismatches": 1}  # type: ignore[attr-defined]
    assert "B-OGCTESTROW001" not in caplog.text


def test_the_redacted_lead_row_carries_the_projection_additively() -> None:
    from backend.services.pii_redaction import redact_lead_row

    base = {
        "borrower_id": "B-OGCTESTROW001",
        "display_name": "Owner ab12cd34",
        "opportunity_score": 72,
    }
    with_points = redact_lead_row({**base, **dict(zip(_COLUMNS, ["29.75", "18.00", "13.50", "6.50", "4.00"], strict=True))})
    without = redact_lead_row(base)

    assert without["score_points"] is None
    assert with_points["score_points"] == {
        "economic_incentive": 29.75,
        "intent_trigger": 18.0,
        "fit": 13.5,
        "relationship": 6.5,
        "evidence": 4.0,
    }
    assert {key: value for key, value in with_points.items() if key != "score_points"} == {
        key: value for key, value in without.items() if key != "score_points"
    }
    for column in _COLUMNS:
        assert column not in with_points, "the raw gold columns never leave the redactor"

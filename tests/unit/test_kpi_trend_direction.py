"""KpiTrend.direction is a closed vocabulary on the wire (audit quality-04 P2, W5e w5-wire-types A3).

The field is response-only and has one producer, ``build_kpi_trend``. The
schema declares ``Literal["up", "down", "flat"]`` so the generated client type
is that union, not ``string``. These tests pin both halves: the producer
never emits anything else over a grid of series shapes, and the committed
OpenAPI baseline carries exactly that enum with the ``flat`` default.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.schemas.portfolio import KpiTrend
from backend.services.repositories.databricks_portfolio_predicates import build_kpi_trend

DIRECTIONS = {"up", "down", "flat"}
BASELINE = Path(__file__).resolve().parents[1] / "fixtures" / "openapi_baseline.json"


def _points(values: list[float]) -> list[tuple[str, float]]:
    return [(f"2026-10-{day:02d}", value) for day, value in enumerate(values, start=1)]


SERIES = {
    "rising": [100.0, 104.0, 110.0, 120.0],
    "falling": [120.0, 110.0, 101.0, 90.0],
    "flat": [100.0, 100.0, 100.0],
    "near_flat_up": [1000.0, 1004.0],
    "near_flat_down": [1000.0, 996.0],
    "zero_start": [0.0, 0.0, 50.0, 60.0],
    "all_zero": [0.0, 0.0, 0.0],
    "single": [42.0],
    "empty": [],
    "zero_end": [80.0, 0.0],
}


@pytest.mark.parametrize("name", sorted(SERIES))
def test_the_producer_emits_only_the_closed_vocabulary(name: str) -> None:
    trend = build_kpi_trend(_points(SERIES[name]))

    assert trend.direction in DIRECTIONS
    assert type(trend.model_dump(mode="json")["direction"]) is str


def test_the_producer_reaches_every_member_and_nothing_else() -> None:
    seen = {build_kpi_trend(_points(values)).direction for values in SERIES.values()}
    grid = [10.0, 0.0, 100.0, 99.6, 100.4, 250.0]
    for length in (1, 2, 3):
        for combo in itertools.product(grid, repeat=length):
            seen.add(build_kpi_trend(_points(list(combo))).direction)

    assert seen == DIRECTIONS


def test_the_named_shapes_read_the_expected_direction() -> None:
    assert build_kpi_trend(_points(SERIES["rising"])).direction == "up"
    assert build_kpi_trend(_points(SERIES["falling"])).direction == "down"
    assert build_kpi_trend(_points(SERIES["flat"])).direction == "flat"
    assert build_kpi_trend(_points(SERIES["single"])).direction == "flat"
    assert build_kpi_trend(_points(SERIES["empty"])).direction == "flat"
    # A zero start is trimmed to the first non-zero point, then compared.
    assert build_kpi_trend(_points(SERIES["zero_start"])).direction == "up"


def test_the_model_refuses_a_value_outside_the_vocabulary() -> None:
    with pytest.raises(ValidationError):
        KpiTrend.model_validate({"series": [1.0, 2.0], "direction": "sideways"})

    assert KpiTrend(series=[]).direction == "flat"


def test_the_committed_openapi_baseline_pins_the_enum() -> None:
    spec = json.loads(BASELINE.read_text(encoding="utf-8"))
    direction = spec["components"]["schemas"]["KpiTrend"]["properties"]["direction"]

    assert sorted(direction["enum"]) == sorted(DIRECTIONS)
    assert direction["type"] == "string"
    assert direction["default"] == "flat"

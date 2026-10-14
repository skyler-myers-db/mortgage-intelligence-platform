"""The reviewed rate watch's evaluation (audit 2026-09-21 ``wow-ai-2``, W5d).

A ``rate_trigger_watch`` monitor stores one watched 30-year par level, saved
from a Rate Lever step. Each run reads two gold tables through their shared,
cached repositories (never the run's SQL client, ``borrower_360`` or silver):

- the latest weekly print from ``gold.rate_window_weekly`` (its ``is_latest``
  week), compared with the level at 2 dp (``at_or_below``: print <= level;
  ``at_or_above``: print >= level; no print: not compared);
- the Rate Lever grid from ``gold.rate_sensitivity_rollup`` at the saved step:
  the in-the-money and contactable counts summed over states.

A grid that is not built, or a step no longer on today's grid, counts 0 and
the print is still compared. Nothing here raises for "not built": one raise
would abort the whole run-due-all batch. A warehouse failure propagates as for
every other workflow. The copy states a scenario and a comparison, never a
forecast, and the only review signal is the ``Watched rate level`` check: the
draft gate drafts a note only when it is ``review_required``.
"""

from __future__ import annotations

import math
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from fastapi import HTTPException

from backend.schemas.analytics_rate_window import RateWindowResponse
from backend.schemas.geo_rate_sensitivity import RateSensitivityResponse
from backend.schemas.growth_agent import GrowthAgentPolicyCheck, GrowthAgentToolStep
from backend.services.growth_agent_workflows import (
    RATE_SENSITIVITY_ROLLUP,
    RATE_WINDOW_WEEKLY,
    RateTrigger,
    RateTriggerDirection,
)
from backend.services.repositories.factory import (
    get_rate_sensitivity_repository,
    get_rate_window_repository,
)

RATE_TRIGGER_CHECK_LABEL = "Watched rate level"
RATE_TRIGGER_SERIES_ID = "MORTGAGE30US"
MAX_STEP_BPS = 100
STEP_STRIDE_BPS = 25
_CENT = Decimal("0.01")
_MALFORMED = "saved monitor has malformed criteria"


def direction_for_step(step_bps: int) -> RateTriggerDirection:
    """A falling par is watched from above, a rising one from below."""

    return "at_or_below" if step_bps < 0 else "at_or_above"


def valid_step(value: object) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and value != 0
        and -MAX_STEP_BPS <= value <= MAX_STEP_BPS
        and value % STEP_STRIDE_BPS == 0
    )


def valid_level(value: object) -> bool:
    """A percent strictly inside (0, 25) with at most 2 decimals."""

    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        return False
    try:
        exact = Decimal(str(value))
    except InvalidOperation:
        return False
    return Decimal(0) < exact < Decimal(25) and exact == exact.quantize(_CENT)


def rate_trigger_from_criteria(criteria: dict[str, object]) -> RateTrigger:
    """Parse a stored ``rate_trigger`` strictly; anything else is the 409 malformed-criteria."""

    raw = criteria.get("rate_trigger")
    if not isinstance(raw, dict):
        raise HTTPException(status_code=409, detail=_MALFORMED)
    step = raw.get("step_bps")
    level = raw.get("level_pct")
    base = raw.get("base_market_rate_pct")
    direction = raw.get("direction")
    if (
        raw.get("series_id") != RATE_TRIGGER_SERIES_ID
        or not valid_step(step)
        or not valid_level(level)
        or isinstance(base, bool)
        or not isinstance(base, int | float)
        or not 0 < float(base) < 25
        or direction not in ("at_or_below", "at_or_above")
    ):
        raise HTTPException(status_code=409, detail=_MALFORMED)
    assert isinstance(step, int) and isinstance(level, int | float)  # narrowed by the checks above
    if direction != direction_for_step(step):
        raise HTTPException(status_code=409, detail=_MALFORMED)
    return RateTrigger(
        step_bps=step,
        level_pct=float(level),
        direction=direction_for_step(step),
        base_market_rate_pct=float(base),
    )


def crossed(print_pct: float | None, level_pct: float, direction: RateTriggerDirection) -> bool | None:
    """Whether the print reached the level, compared at 2 dp; None without a print."""

    if print_pct is None:
        return None
    observed = Decimal(str(print_pct)).quantize(_CENT)
    level = Decimal(str(level_pct)).quantize(_CENT)
    return observed <= level if direction == "at_or_below" else observed >= level


def latest_print(window: RateWindowResponse) -> tuple[float | None, str | None]:
    latest = next((week for week in window.weeks if week.is_latest), None)
    if latest is None:
        return None, None
    return float(latest.market_rate_pct), latest.week


def _grid_counts(grid: RateSensitivityResponse, step_bps: int) -> tuple[int, int, float | None]:
    if not grid.built or step_bps not in grid.steps_bps:
        return 0, 0, None
    index = grid.steps_bps.index(step_bps)
    in_the_money = sum(state.in_the_money[index] for state in grid.states if index < len(state.in_the_money))
    contactable = sum(
        state.contactable_in_the_money[index]
        for state in grid.states
        if state.contactable_in_the_money is not None and index < len(state.contactable_in_the_money)
    )
    par = grid.scenario_market_rate_pct[index] if index < len(grid.scenario_market_rate_pct) else None
    return in_the_money, contactable, par


def load_rate_trigger_metrics(trigger: RateTrigger) -> dict[str, Any]:
    """The run's counts and comparison, from the two gold rate tables only."""

    print_pct, print_week = latest_print(get_rate_window_repository().rate_window())
    grid = get_rate_sensitivity_repository().rate_sensitivity()
    in_the_money, contactable, scenario_par = _grid_counts(grid, trigger.step_bps)
    return {
        "broad_total": in_the_money,
        "actionable_total": contactable,
        "broad_avg_score": None,
        "actionable_avg_score": None,
        "avg_rate_spread_bps": None,
        "avg_equity_pct": None,
        "rate_trigger": {
            "latest_print_pct": print_pct,
            "latest_print_week": print_week,
            "level_pct": trigger.level_pct,
            "direction": trigger.direction,
            "crossed": crossed(print_pct, trigger.level_pct, trigger.direction),
            "scenario_par_pct": scenario_par,
            "today_par_pct": grid.base_market_rate_pct if grid.built else None,
        },
    }


def _evaluation(metrics: dict[str, Any]) -> dict[str, Any]:
    value = metrics.get("rate_trigger")
    return value if isinstance(value, dict) else {}


def _direction_words(direction: object) -> str:
    return "at or below" if direction == "at_or_below" else "at or above"


def rate_trigger_check(metrics: dict[str, Any]) -> GrowthAgentPolicyCheck:
    """The one review signal: review_required when reached, passed when not, blocked without a print."""

    evaluation = _evaluation(metrics)
    level = f"{float(evaluation.get('level_pct') or 0):.2f}%"
    reached = evaluation.get("crossed")
    status: Literal["passed", "review_required", "blocked"]
    if reached is None:
        status, detail = "blocked", "No weekly print is available; nothing was compared."
    elif reached:
        status = "review_required"
        detail = (
            f"The latest weekly 30-year print is {_direction_words(evaluation.get('direction'))} "
            f"the watched {level} level."
        )
    else:
        status, detail = "passed", f"The latest weekly 30-year print has not reached the watched {level} level."
    return GrowthAgentPolicyCheck(label=RATE_TRIGGER_CHECK_LABEL, status=status, detail=detail)


def _scenario_detail(trigger: RateTrigger, metrics: dict[str, Any]) -> str:
    evaluation = _evaluation(metrics)
    today = evaluation.get("today_par_pct")
    if today is None:
        return "Rate scenarios are not built yet, so the counts are 0."
    if evaluation.get("scenario_par_pct") is None:
        return f"The {trigger.step_bps:+d} bps step is not on today's scenario grid, so the counts are 0."
    return (
        f"Counts are the {trigger.step_bps:+d} bps scenario at today's {float(today):.2f}% par, not a "
        f"forecast: {int(metrics['broad_total']):,} borrowers clear the refi screen, "
        f"{int(metrics['actionable_total']):,} of them contactable."
    )


def rate_trigger_tool_steps(
    trigger: RateTrigger,
    metrics: dict[str, Any],
    *,
    tool_detail: str,
    result_hash: str,
) -> list[GrowthAgentToolStep]:
    """The rate watch's read steps. No agent tool runs: tool_name stays None."""

    print_pct = _evaluation(metrics).get("latest_print_pct")
    return [
        GrowthAgentToolStep(
            label="Read the latest weekly 30-year print",
            status="completed" if print_pct is not None else "blocked",
            detail=(
                f"The latest weekly 30-year print is {float(print_pct):.2f}%."
                if print_pct is not None
                else "No weekly print is available."
            ),
            source_asset=RATE_WINDOW_WEEKLY,
            tool_name=None,
            result_hash=result_hash,
        ),
        GrowthAgentToolStep(
            label="Count the rate lever scenario",
            status="completed",
            detail=_scenario_detail(trigger, metrics),
            source_asset=RATE_SENSITIVITY_ROLLUP,
            tool_name=None,
            result_hash=result_hash,
        ),
        GrowthAgentToolStep(
            label="Prepare governed next step",
            status="review_required",
            detail=tool_detail,
            source_asset=None,
            tool_name=None,
            result_hash=result_hash,
        ),
    ]


def rate_trigger_reached(policy_checks: object) -> bool:
    """Whether a stored run's ``Watched rate level`` check asks for review."""

    if not isinstance(policy_checks, list):
        return False
    return any(
        isinstance(check, dict)
        and check.get("label") == RATE_TRIGGER_CHECK_LABEL
        and check.get("status") == "review_required"
        for check in policy_checks
    )


__all__ = [
    "RATE_TRIGGER_CHECK_LABEL",
    "crossed",
    "direction_for_step",
    "latest_print",
    "load_rate_trigger_metrics",
    "rate_trigger_check",
    "rate_trigger_from_criteria",
    "rate_trigger_reached",
    "rate_trigger_tool_steps",
    "valid_level",
    "valid_step",
]

"""Red flags for the live Rate Lever grid (``GET /api/geo/rate-sensitivity``).

Wave-3 remainder: ``tools/verify_live.py`` probes the endpoint after the state
rollups and runs these structural checks on the answer. A correct grid
satisfies every one of them whatever the data, so a flag means a broken
refresh or projection that unit tests on fixtures cannot see:

* the grid is built, with a non-empty, strictly ascending ``steps_bps`` that
  includes step 0, and one scenario par rate per step;
* there is at least one state;
* per state: one in-the-money count per step, none above ``addressable``;
  ``rate_movable`` not above ``addressable``; the contactable subset (when
  reported) one per step and never above in-the-money; and in-the-money never
  INCREASES along ascending steps (par rising cannot add in-the-money
  borrowers).

Per-state problems are reported for the first ``MAX_STATE_OFFENDERS`` states.
"""

from __future__ import annotations

from typing import Any

PROBE_NAME = "geo.rate_sensitivity"
MAX_STATE_OFFENDERS = 5


def _ints(value: Any) -> list[int] | None:
    if not isinstance(value, list) or not all(isinstance(item, int) for item in value):
        return None
    return value


def _state_problems(row: Any, step_count: int) -> list[str]:
    if not isinstance(row, dict):
        return ["row is not an object"]
    problems: list[str] = []
    addressable = row.get("addressable")
    rate_movable = row.get("rate_movable")
    in_the_money = _ints(row.get("in_the_money"))
    if in_the_money is None or len(in_the_money) != step_count:
        problems.append("in_the_money length differs from steps_bps")
        in_the_money = None
    else:
        if isinstance(addressable, int) and any(value > addressable for value in in_the_money):
            problems.append("in_the_money above addressable")
        if any(later > earlier for earlier, later in zip(in_the_money, in_the_money[1:], strict=False)):
            problems.append("in_the_money increases as par rises")
    if isinstance(rate_movable, int) and isinstance(addressable, int) and rate_movable > addressable:
        problems.append("rate_movable above addressable")
    contactable_raw = row.get("contactable_in_the_money")
    if contactable_raw is not None:
        contactable = _ints(contactable_raw)
        if contactable is None or len(contactable) != step_count:
            problems.append("contactable_in_the_money length differs from steps_bps")
        elif in_the_money is not None and any(
            reachable > total for reachable, total in zip(contactable, in_the_money, strict=True)
        ):
            problems.append("contactable_in_the_money above in_the_money")
    return problems


def rate_sensitivity_flags(sample: Any) -> list[str]:
    """Red flags for one ``/api/geo/rate-sensitivity`` payload (empty when clean)."""
    if not isinstance(sample, dict):
        return [f"{PROBE_NAME}: payload is not an object"]
    if sample.get("built") is not True:
        return [f"{PROBE_NAME}: built is not true (the gold refresh has not built the grid)"]
    flags: list[str] = []
    steps = _ints(sample.get("steps_bps")) or []
    if not steps:
        flags.append(f"{PROBE_NAME}: steps_bps is empty")
    else:
        if 0 not in steps:
            flags.append(f"{PROBE_NAME}: steps_bps is missing step 0")
        if any(later <= earlier for earlier, later in zip(steps, steps[1:], strict=False)):
            flags.append(f"{PROBE_NAME}: steps_bps is not strictly ascending")
    rates = sample.get("scenario_market_rate_pct")
    if not isinstance(rates, list) or len(rates) != len(steps):
        flags.append(f"{PROBE_NAME}: scenario_market_rate_pct length differs from steps_bps")
    states = sample.get("states")
    if not isinstance(states, list) or not states:
        flags.append(f"{PROBE_NAME}: states is empty")
        return flags
    offenders: list[str] = []
    for row in states:
        problems = _state_problems(row, len(steps))
        if problems:
            label = row.get("state") if isinstance(row, dict) else None
            offenders.append(f"{label or '?'}: {'; '.join(problems)}")
    flags.extend(f"{PROBE_NAME}: {line}" for line in offenders[:MAX_STATE_OFFENDERS])
    if len(offenders) > MAX_STATE_OFFENDERS:
        flags.append(f"{PROBE_NAME}: and {len(offenders) - MAX_STATE_OFFENDERS} more states")
    return flags


__all__ = ["MAX_STATE_OFFENDERS", "PROBE_NAME", "rate_sensitivity_flags"]

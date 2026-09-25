"""Rate-scenario arithmetic shared by the Rate Lever's two SQL sites and its tests.

The Rate Lever (2026-09-21 UI/UX audit, wow-stage-1) asks one question of the
book: if the 30-year par rate moved by ``step_bps``, how many borrowers would
clear this refresh's refi screen? A scenario is only a different market rate,
so each step re-runs the canonical primitives against
``market + step / 10000``:

    fn_in_the_money(fn_rate_spread(note, market + step / 10000),
                    equity_pct, min_spread_bps, min_equity_pct)

It never shifts rounded spread bins. ``fn_rate_spread`` BROUNDs half-even, so
``rate_spread_bps(note, market) - step`` disagrees with the direct evaluation
whenever the raw spread sits on a half basis point and the step is odd
(eighth-point coupons land there); the golden fixture
``tests/fixtures/rate_scenario_golden.json`` pins such cases.

This module owns three things and nothing else:

* ``RATE_SCENARIO_STEPS_BPS`` -- the grid the gold CTAS explodes and the
  endpoint pivots. Its widest step is ``scoring.RATE_SCENARIO_MAX_SHIFT_BPS``.
* ``NOTE_RATE_GATE_SQL`` / ``SCENARIO_ITM_SQL`` -- the single text of the note
  gate and of the per-step rule. ``sql/transformations/
  gold_rate_sensitivity_rollup.sql`` (the precomputed addressable grid) and
  the repository's live contactable aggregate both embed them;
  ``tests/unit/test_rate_sensitivity_sql_contract.py`` proves both texts
  carry them verbatim.
* ``scenario_in_the_money`` -- the Python mirror, composed from the scoring
  helpers so the basis-point scale (``scoring.RATE_SPREAD_BPS_PER_UNIT``) and
  the rounding live in one place. No bps constant is defined here.
"""

from __future__ import annotations

from backend.services import scoring
from backend.services.databricks_sql_helpers import qualify

# The scenario grid, in basis points of par-rate move (positive = par rises).
# Nine steps of 25 bps; the ends are the shared maximum shift.
RATE_SCENARIO_STEPS_BPS: tuple[int, ...] = (-100, -75, -50, -25, 0, 25, 50, 75, 100)

# The integer scale fn_rate_spread multiplies by, spelled for SQL. Derived from
# the scoring constant, never restated, and cast to DOUBLE at both operands so
# the step divides in IEEE doubles exactly as Python's ``step / 10000.0`` does
# (a DECIMAL literal such as 10000.0 would change the arithmetic).
_BPS_SCALE_SQL = f"CAST({int(scoring.RATE_SPREAD_BPS_PER_UNIT)} AS DOUBLE)"

_BOUNDED_NOTE = f"{qualify('gold', 'fn_bounded_mortgage_rate')}(lc.first_pos_rate)"

# borrower_360's active-lien and clamp gate, over ``gold.borrower_360 AS b``
# LEFT JOINed to ``silver.lien_current AS lc``. A paid-down lien
# (current_rate = 0), a rate AT the 1% / 15% clamp, or a missing lien row
# yields NULL: fn_rate_spread then returns its no-signal 0 at every step, so
# the borrower stays in the population and the scenario never moves them.
NOTE_RATE_GATE_SQL = (
    f"CASE WHEN b.current_rate > 0 AND {_BOUNDED_NOTE} > 0.01 AND {_BOUNDED_NOTE} < 0.15 "
    f"THEN {_BOUNDED_NOTE} END"
)

# The per-step rule over collapsed book cells carrying ``note_rate_fraction``,
# ``market_rate_fraction`` (borrower_360's par), ``equity_pct`` and the two
# applied thresholds, CROSS JOINed to a grid carrying ``step_bps``.
SCENARIO_ITM_SQL = (
    f"{qualify('gold', 'fn_in_the_money')}("
    f"{qualify('gold', 'fn_rate_spread')}("
    "note_rate_fraction, "
    f"market_rate_fraction + CAST(step_bps AS DOUBLE) / {_BPS_SCALE_SQL}"
    "), equity_pct, min_spread_bps_applied, min_equity_pct_applied)"
)


def scenario_market_rate(market_fraction: float, step_bps: int) -> float:
    """The par rate a step describes, as a fraction: ``market + step / 10000``."""
    return market_fraction + float(step_bps) / scoring.RATE_SPREAD_BPS_PER_UNIT


def scenario_in_the_money(
    note_rate_fraction: float | None,
    market_rate_fraction: float | None,
    step_bps: int,
    equity_pct: int | None,
    min_spread_bps: int | None,
    min_equity_pct: int | None,
) -> bool:
    """Python mirror of ``SCENARIO_ITM_SQL`` for one borrower at one step.

    A ``None`` note is the gated no-signal case: 0 bps at every step, exactly
    as ``fn_rate_spread`` returns for a NULL input.
    """
    return scoring.in_the_money(
        scoring.rate_spread_bps_at_par_shift(note_rate_fraction, market_rate_fraction, step_bps),
        equity_pct,
        min_spread_bps,
        min_equity_pct,
    )


__all__ = [
    "NOTE_RATE_GATE_SQL",
    "RATE_SCENARIO_STEPS_BPS",
    "SCENARIO_ITM_SQL",
    "scenario_in_the_money",
    "scenario_market_rate",
]

"""Module 0 canonical scoring primitives (Python parity with SQL).

Each function here mirrors a Unity Catalog UDF and is pinned by a
golden-fixture JSON that the SQL side validates against the same inputs:

- ``lead_score``         -> ``sql/uc_functions/fn_lead_score.sql``
                            + ``tests/fixtures/lead_score_golden.json``
                            (case_05 / case_07 lock banker's rounding).
- ``rate_spread_bps``    -> ``sql/uc_functions/fn_rate_spread.sql``
                            + ``tests/fixtures/rate_spread_golden.json``
                            (case_05 pins round-half-to-even at 162.5).
- ``in_the_money``       -> ``sql/uc_functions/fn_in_the_money.sql``
                            + ``tests/fixtures/in_the_money_golden.json``
                            (cases 04/05 pin the inclusive ``>=`` boundary).

Weights for ``lead_score`` (non-negotiable): 0.35 / 0.30 / 0.15 / 0.10 / 0.10.
NULL (``None``) components coerce to 0 (or ``False`` for ``in_the_money``).
"""

from __future__ import annotations

_INT32_MIN = -2_147_483_648
_INT32_MAX = 2_147_483_647


def lead_score(
    economic_incentive: int | None,
    intent_trigger: int | None,
    fit: int | None,
    relationship: int | None,
    evidence: int | None,
) -> int:
    """Return the integer opportunity score in [0, 100].

    Mirrors ``mip_demo.gold.fn_lead_score``. Uses Python's built-in
    ``round()`` which applies banker's rounding, matching Databricks
    ``BROUND``. Any ``None`` component is treated as 0 to match the
    SQL ``COALESCE(..., 0)`` contract.
    """
    weighted_sum = (
        0.35 * (economic_incentive or 0)
        + 0.30 * (intent_trigger or 0)
        + 0.15 * (fit or 0)
        + 0.10 * (relationship or 0)
        + 0.10 * (evidence or 0)
    )
    return max(0, min(100, round(weighted_sum)))


def rate_spread_bps(
    current_rate: float | None,
    market_rate: float | None,
) -> int:
    """Return basis-point spread of ``current_rate`` over ``market_rate``.

    Mirrors ``mip_demo.gold.fn_rate_spread``. Rates are expressed as
    fractions (0.0575 == 5.75%), matching the SQL signature. A ``None``
    on either side returns 0 ("no signal == no opportunity", keeps
    downstream columns NOT NULL so ``fn_in_the_money``'s ``>=`` does not
    short-circuit). Result is rounded via Python ``round()`` for
    banker's-rounding parity with ``BROUND`` and clipped to INT32 range
    to mirror the SQL ``CAST AS INT``.
    """
    if current_rate is None or market_rate is None:
        return 0
    return max(_INT32_MIN, min(_INT32_MAX, round((current_rate - market_rate) * 10000)))


def in_the_money(
    rate_spread_bps: int | None,
    equity_pct: int | None,
    min_spread_bps: int | None,
    min_equity_pct: int | None,
) -> bool:
    """Return True iff borrower clears BOTH the spread and equity thresholds.

    Mirrors ``mip_demo.gold.fn_in_the_money``. Any ``None`` argument
    returns ``False`` (unknown must not silently become GO for outreach).
    The ``>=`` comparison is inclusive — a borrower exactly at the
    threshold IS in the money (golden cases 04/05).
    """
    if (
        rate_spread_bps is None
        or equity_pct is None
        or min_spread_bps is None
        or min_equity_pct is None
    ):
        return False
    return (rate_spread_bps >= min_spread_bps) and (equity_pct >= min_equity_pct)

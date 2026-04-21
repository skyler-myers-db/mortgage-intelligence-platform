"""Module 0 canonical lead scorer (Python parity with SQL).

The SQL contract lives in ``sql/uc_functions/fn_lead_score.sql``. Parity
between this Python implementation and the Databricks ``fn_lead_score``
UDF is pinned by the golden fixtures in
``tests/fixtures/lead_score_golden.json``; case_05 and case_07 in
particular lock the banker's-rounding (round-half-to-even) behavior.

Weights (non-negotiable): 0.35 / 0.30 / 0.15 / 0.10 / 0.10.
NULL (``None``) components coerce to 0. Final score is clipped to [0, 100].
"""

from __future__ import annotations


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

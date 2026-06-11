"""Module 0 canonical scoring primitives (Python parity with SQL).

Each function here mirrors a Unity Catalog UDF and is pinned by a
golden-fixture JSON that the SQL side validates against the same inputs:

- ``lead_score``         -> ``sql/uc_functions/fn_lead_score.sql``
                            + ``tests/fixtures/lead_score_golden.json``
                            (case_05 / case_07 lock banker's rounding;
                            case_13 locks exact-decimal arithmetic in the
                            float-drift zone).
- ``rate_spread_bps``    -> ``sql/uc_functions/fn_rate_spread.sql``
                            + ``tests/fixtures/rate_spread_golden.json``
                            (case_05 pins round-half-to-even at 162.5).
- ``in_the_money``       -> ``sql/uc_functions/fn_in_the_money.sql``
                            + ``tests/fixtures/in_the_money_golden.json``
                            (cases 04/05 pin the inclusive ``>=`` boundary).
- ``next_best_offer``    -> ``sql/uc_functions/fn_next_best_offer.sql``
                            + ``tests/fixtures/next_best_offer_golden.json``
                            (case_12 pins the fixture refi_plus_heloc shift;
                            cases 09/10 pin the HELOC-equity boundary).

Weights for ``lead_score`` (non-negotiable): 0.35 / 0.30 / 0.15 / 0.10 / 0.10.
NULL (``None``) score components coerce to 0. ``in_the_money`` returns
``False`` on any NULL input. ``next_best_offer`` coerces missing borrower
signals to 0/FALSE, but returns ``nurture`` when any threshold is NULL so
misconfigured decisioning cannot become a positive outreach lane.
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal

_INT32_MIN = -2_147_483_648
_INT32_MAX = 2_147_483_647

# fn_lead_score weights as EXACT decimals. Spark parses the SQL literals
# (0.35 etc.) as DECIMAL, so the UDF computes the weighted sum exactly and
# BROUNDs the exact value. Python float arithmetic drifts on ~0.67% of the
# input lattice (e.g. (92,94,94,85,25): exact sum 85.5 -> 86, float sum
# 85.49999999999999 -> 85), which surfaced as false "integrity gap" warnings
# in the borrower proof drawer (2026-06-11 audit, P1-1). Decimal weights are
# the parity fix; tests/unit/test_scoring.py sweeps the full boundary.
_LEAD_SCORE_WEIGHTS = (
    Decimal("0.35"),  # economic_incentive
    Decimal("0.30"),  # intent_trigger
    Decimal("0.15"),  # fit
    Decimal("0.10"),  # relationship
    Decimal("0.10"),  # evidence
)
_DECIMAL_ONE = Decimal("1")

# Human labels for the eight offer_codes. Source of truth for the
# OfferRecommendation.product_label rendered at the API boundary; tests assert
# parity with ``tests/fixtures/next_best_offer_golden.json`` without requiring
# production code to import from test fixtures at module load.
NBO_PRODUCT_LABELS: dict[str, str] = {
    "purchase": "Purchase Mortgage",
    "refi_plus_heloc": "Refinance + HELOC",
    "heloc": "HELOC",
    "refi": "Refinance",
    "cash_out": "Cash-out Refi",
    "investor": "Investor Product",
    "retention": "Retention",
    "nurture": "Nurture",
}


# ---------------------------------------------------------------------------
# Source-label registry (2026-04-22 persona-review fix).
# -----------------------------------------------------------------------------
# Maps the raw Unity Catalog object names we cite as evidence (and the
# legacy short aliases the LeadTable still hard-codes) to the business-
# friendly labels that render on compliance-visible surfaces. The drawer
# lineage link continues to use the raw UC name so a compliance reviewer
# can still trace to the exact object; only the chip text changes.
#
# Usage:
#
#     from backend.services.scoring import source_display_label
#     label = source_display_label("mip.gold.fn_in_the_money")
#     # -> "In-the-money rule"
#
# Unknown entries fall back to the last dotted segment (the pre-existing
# `shortSourceLabel` behaviour) so adding a new UC object is non-breaking.
# ---------------------------------------------------------------------------
SOURCE_DISPLAY_LABELS: dict[str, str] = {
    # UC function evidence
    "mip.gold.fn_rate_spread":      "Market rate comparison",
    "mip.gold.fn_in_the_money":     "In-the-money rule",
    "mip.gold.fn_next_best_offer":  "Next-best-offer model",
    "mip.gold.fn_lead_score":       "Lead score model",
    # UC table evidence
    "mip.gold.borrower_360":        "Borrower dossier",
    "mip.gold.borrower_dossier":    "Borrower dossier",
    "mip.gold.lead_population":     "Ranked lead population",
    "mip.gold.lead_scores":         "Lead scores",
    "mip.gold.evidence_events":     "Evidence stream",
    "mip.gold.property_owner_bridge": "Owner Link bridge",
    # Short aliases used on RowPreview and app proof chips.
    "fn_rate_spread":               "Market rate comparison",
    "fn_in_the_money":              "In-the-money rule",
    "fn_next_best_offer":           "Next-best-offer model",
    "fn_lead_score":                "Lead score model",
    "rules.itm_v3":                 "In-the-Money logic",
    "mlflow.mtg_nbo_v3":            "Next-best-offer model v3",
    "permits.building":             "Building permit signal",
    "borrower_dossier":             "Borrower dossier",
}


def source_display_label(name: str | None) -> str:
    """Return the business-friendly label for a UC source name.

    Unknown names fall back to the last dotted segment (e.g.
    ``mip.silver.property_master`` -> ``"property_master"``). ``None`` or
    empty input returns an empty string so callers can pass raw data
    through without guarding.

    Multi-catalog note: the registry keys embed the default ``mip.*``
    prefix for legacy compatibility, but we retry the lookup against the
    ``schema.object`` suffix (e.g. ``gold.fn_in_the_money``) so a
    customer deploying with ``mip_prod.*`` still resolves the business
    label instead of leaking the raw object name.
    """
    if not name:
        return ""
    if name in SOURCE_DISPLAY_LABELS:
        return SOURCE_DISPLAY_LABELS[name]
    # Multi-catalog fallback: strip the leading catalog token and re-try
    # the lookup against ``schema.object`` (or just ``object`` for the
    # short-alias bucket). This keeps business labels stable even when
    # the workspace catalog is ``mip_prod`` / ``lender_uc`` / etc.
    parts = name.split(".")
    if len(parts) >= 2:
        schema_object = ".".join(parts[-2:])  # e.g. "gold.fn_in_the_money"
        prefixed = f"mip.{schema_object}"      # e.g. "mip.gold.fn_in_the_money"
        if prefixed in SOURCE_DISPLAY_LABELS:
            return SOURCE_DISPLAY_LABELS[prefixed]
    # Fall back to the frontend's legacy behaviour so an unmapped source
    # still renders something reasonable rather than the full FQN.
    last = name.rsplit(".", 1)[-1]
    if last in SOURCE_DISPLAY_LABELS:
        return SOURCE_DISPLAY_LABELS[last]
    return last


def lead_score(
    economic_incentive: int | None,
    intent_trigger: int | None,
    fit: int | None,
    relationship: int | None,
    evidence: int | None,
) -> int:
    """Return the integer opportunity score in [0, 100].

    Mirrors ``mip.gold.fn_lead_score`` EXACTLY: the SQL literals are
    DECIMAL in Spark, so the UDF computes the weighted sum in exact
    decimal arithmetic and ``BROUND``s (half-to-even) the exact value.
    Python must therefore use ``decimal.Decimal`` — ``round()`` on a
    binary float bankers-rounds a *different number* on exact-.5
    boundaries (float drift), which diverged from SQL on ~0.67% of the
    input lattice and rendered false integrity-gap warnings in the
    borrower proof drawer. Any ``None`` component is treated as 0 to
    match the SQL ``COALESCE(..., 0)`` contract.
    """
    components = (economic_incentive, intent_trigger, fit, relationship, evidence)
    weighted_sum = sum(
        (weight * Decimal(component or 0)
         for weight, component in zip(_LEAD_SCORE_WEIGHTS, components, strict=True)),
        start=Decimal(0),
    )
    rounded = int(weighted_sum.quantize(_DECIMAL_ONE, rounding=ROUND_HALF_EVEN))
    return max(0, min(100, rounded))


def rate_spread_bps(
    current_rate: float | None,
    market_rate: float | None,
) -> int:
    """Return basis-point spread of ``current_rate`` over ``market_rate``.

    Mirrors ``mip.gold.fn_rate_spread``. Rates are expressed as
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

    Mirrors ``mip.gold.fn_in_the_money``. Any ``None`` argument
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


def next_best_offer(
    rate_spread_bps: int | None,
    equity_pct: int | None,
    has_permit: bool | None,
    listed_for_sale: bool | None,
    is_investor: bool | None,
    is_current_customer: bool | None,
    is_competitor_lien: bool | None,
    min_spread_bps: int | None,
    min_equity_pct: int | None,
    heloc_equity_min_pct: int | None,
    cashout_equity_min: int | None,
    retention_min_spread: int | None,
) -> str:
    """Return the lowercase offer code for the winning branch.

    Mirrors ``mip.gold.fn_next_best_offer``. First match wins across
    the priority-ordered decision tree documented in the SQL header:
    listed -> refi_plus_heloc -> heloc -> refi -> cash_out -> investor
    -> retention -> nurture. Borrower-signal ``None`` coerces to 0/FALSE,
    but threshold ``None`` returns ``'nurture'`` before the tree runs.
    A missing threshold is a configuration failure, not permission to
    treat 0 >= 0 as a positive eligibility signal.
    """
    if (
        min_spread_bps is None
        or min_equity_pct is None
        or heloc_equity_min_pct is None
        or cashout_equity_min is None
        or retention_min_spread is None
    ):
        return "nurture"

    spread = rate_spread_bps or 0
    equity = equity_pct or 0
    permit = bool(has_permit)
    listed = bool(listed_for_sale)
    investor = bool(is_investor)
    customer = bool(is_current_customer)
    competitor_lien = bool(is_competitor_lien)
    min_sp = min_spread_bps
    min_eq = min_equity_pct
    heloc_min = heloc_equity_min_pct
    cashout_min = cashout_equity_min
    retention_min = retention_min_spread

    if listed:
        return "purchase"
    if spread >= min_sp and equity >= heloc_min:
        return "refi_plus_heloc"
    if permit and equity >= heloc_min:
        return "heloc"
    if spread >= min_sp and equity >= min_eq:
        return "refi"
    if equity >= cashout_min:
        return "cash_out"
    if investor:
        return "investor"
    if customer and (spread >= retention_min or competitor_lien):
        return "retention"
    return "nurture"

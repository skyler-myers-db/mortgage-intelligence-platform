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
- ``next_best_offer``    -> ``sql/uc_functions/fn_next_best_offer.sql``
                            + ``tests/fixtures/next_best_offer_golden.json``
                            (case_12 pins the B-48294 'refi_plus_heloc' shift;
                            cases 09/10 pin the HELOC-equity boundary).

Weights for ``lead_score`` (non-negotiable): 0.35 / 0.30 / 0.15 / 0.10 / 0.10.
NULL (``None``) components coerce to 0 (or ``False`` for ``in_the_money``
and the boolean arguments of ``next_best_offer``).
"""

from __future__ import annotations

import json
from pathlib import Path

_INT32_MIN = -2_147_483_648
_INT32_MAX = 2_147_483_647

_NBO_FIXTURE_PATH = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "fixtures"
    / "next_best_offer_golden.json"
)
with _NBO_FIXTURE_PATH.open() as _f:
    _NBO_FIXTURE = json.load(_f)

# Human labels for the eight offer_codes. Source of truth for the
# OfferRecommendation.product_label rendered at the API boundary — the
# scoring primitive itself only returns the lowercase code.
NBO_PRODUCT_LABELS: dict[str, str] = dict(_NBO_FIXTURE["product_labels"])


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
    # Short aliases used on RowPreview / historical code paths
    "fn_rate_spread":               "Market rate comparison",
    "fn_in_the_money":              "In-the-money rule",
    "fn_next_best_offer":           "Next-best-offer model",
    "fn_lead_score":                "Lead score model",
    "rules.itm_v3":                 "Rate + equity ruleset",
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
    """
    if not name:
        return ""
    if name in SOURCE_DISPLAY_LABELS:
        return SOURCE_DISPLAY_LABELS[name]
    # Fall back to the frontend's legacy behaviour so an unmapped source
    # still renders something reasonable rather than the full FQN.
    last = name.rsplit(".", 1)[-1]
    return last


def lead_score(
    economic_incentive: int | None,
    intent_trigger: int | None,
    fit: int | None,
    relationship: int | None,
    evidence: int | None,
) -> int:
    """Return the integer opportunity score in [0, 100].

    Mirrors ``mip.gold.fn_lead_score``. Uses Python's built-in
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
    -> retention -> nurture. Numeric ``None`` coerces to 0 and boolean
    ``None`` coerces to ``False`` to match ``COALESCE(..., 0/FALSE)`` —
    so an all-NULL row lands in ``'nurture'`` (the safe lane).
    """
    spread = rate_spread_bps or 0
    equity = equity_pct or 0
    permit = bool(has_permit)
    listed = bool(listed_for_sale)
    investor = bool(is_investor)
    customer = bool(is_current_customer)
    competitor_lien = bool(is_competitor_lien)
    min_sp = min_spread_bps or 0
    min_eq = min_equity_pct or 0
    heloc_min = heloc_equity_min_pct or 0
    cashout_min = cashout_equity_min or 0
    retention_min = retention_min_spread or 0

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

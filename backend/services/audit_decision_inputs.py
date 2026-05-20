"""Decision-input snapshots for forensic audit metadata.

The append-only audit ledger must preserve the rule inputs that existed
when a borrower was viewed, recommended, or approved. Gold tables refresh
in place, so a later reconstruction cannot rely on current warehouse rows
for the exact rate/equity/trigger values the decision used.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

DecisionInputValue = int | bool

DECISION_INPUT_KEYS: tuple[str, ...] = (
    "rate_spread_bps",
    "equity_pct",
    "has_permit",
    "listed_for_sale",
    "is_investor",
    "is_current_customer",
    "is_competitor_lien",
)


def _coerce_int(value: Any) -> int:
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "t", "true", "y", "yes"}
    return bool(value)


def decision_inputs_from_offer_inputs(
    inputs: Mapping[str, object],
) -> dict[str, DecisionInputValue]:
    """Return the seven scoring signals used by ``fn_next_best_offer``."""

    return {
        "rate_spread_bps": _coerce_int(inputs.get("rate_spread_bps")),
        "equity_pct": _coerce_int(inputs.get("equity_pct")),
        "has_permit": _coerce_bool(inputs.get("has_permit")),
        "listed_for_sale": _coerce_bool(inputs.get("listed_for_sale")),
        "is_investor": _coerce_bool(inputs.get("is_investor")),
        "is_current_customer": _coerce_bool(inputs.get("is_current_customer")),
        "is_competitor_lien": _coerce_bool(inputs.get("is_competitor_lien")),
    }


def decision_inputs_from_borrower(borrower: object) -> dict[str, DecisionInputValue]:
    """Return decision inputs from a redacted ``Borrower360``-shaped object."""

    why_panel = getattr(borrower, "why_panel", None)
    return {
        "rate_spread_bps": _coerce_int(getattr(borrower, "rate_spread_bps", 0)),
        "equity_pct": _coerce_int(getattr(why_panel, "equity_pct", 0)),
        "has_permit": _coerce_bool(getattr(borrower, "has_permit", False)),
        "listed_for_sale": _coerce_bool(getattr(borrower, "listed_for_sale", False)),
        "is_investor": _coerce_bool(getattr(borrower, "is_investor", False)),
        "is_current_customer": _coerce_bool(
            getattr(borrower, "is_current_customer", False)
        ),
        "is_competitor_lien": _coerce_bool(
            getattr(borrower, "is_competitor_lien", False)
        ),
    }

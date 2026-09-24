"""Deterministic 'what would change this?' margins for the borrower proof.

wow-ai-1 phase 1: every margin is recomputed from inputs and thresholds that
already sit on the ``gold.borrower_dossier`` row, through the reviewed
scoring mirrors in ``backend/services/scoring.py``. Nothing here is a model
output, a forecast or a credit decision:

- ``spread_screen``     materialized ``rate_spread_bps`` minus the applied
                        refi-screen minimum;
- ``par_break_even``    the smallest par move inside the Rate Lever's
                        +/-``RATE_SCENARIO_MAX_SHIFT_BPS`` grid at which the
                        refi screen flips, through
                        ``rate_spread_bps_at_par_shift`` (half-even rounding,
                        the live ``fn_rate_spread`` mirror);
- ``equity_floor``      ``equity_pct`` against the equity minimum of the
                        displayed offer's branch;
- ``offer_flip_equity`` the nearest equity level either way at which
                        ``next_best_offer`` picks a different offer;
- ``offer_flip_par``    the nearest par move either way (same grid) at which
                        it does.

Only ``equity_pct`` and the par-derived spread are perturbed; every boolean
signal and every threshold stays the row's own. The par margins fail closed
(``unavailable``) unless the lien rate sits inside the SQL's active-lien /
clamp gate, a par rate exists, and the reconstructed note rate reproduces the
materialized spread exactly. Margins never add a known data gap and never
change ``trusted``: they explain the row, they do not audit it.

``_offer_inputs`` is the one coercion of the offer-tree inputs: the proof's
recomputed offer (``databricks_borrower_proof._recomputed_offer_code``) and
these margins read the identical values. The row-value coercions live here so
that module can import them without a cycle.
"""

from __future__ import annotations

import math
from typing import Any, NamedTuple

from backend.schemas.proof import ProofMargin, ProofMarginDirection, ProofMarginKey
from backend.services.databricks_sql_helpers import qualify
from backend.services.repositories.databricks_shared import _coerce_bool
from backend.services.scoring import (
    RATE_SCENARIO_MAX_SHIFT_BPS,
    RATE_SPREAD_BPS_PER_UNIT,
    next_best_offer,
    offer_display_label,
    rate_spread_bps,
    rate_spread_bps_at_par_shift,
)

# The SQL gate on the lien rate (gold_borrower_360.sql rate_spread_bps):
# first_pos_rate strictly inside (1%, 15%) on an active lien. current_rate is
# first_pos_rate * 100 as a DOUBLE and 0.0 when no active lien remains.
_MIN_GATED_RATE_PCT = 1.0
_MAX_GATED_RATE_PCT = 15.0
# current_rate is PERCENT form; seven decimals recover the fractional note
# rate exactly for every eighth-point coupon (pinned by the unit test).
_NOTE_RATE_DECIMALS = 7
_EQUITY_MIN_PCT = 0
_EQUITY_MAX_PCT = 100

_LISTING_HOLDS = "The listing signal selects this offer; no rate or equity change alters it"
_NOT_COMPUTED = "Not computed for this row"


def _int_value(row: dict[str, Any], key: str, default: int = 0) -> int:
    value = row.get(key)
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float_value(row: dict[str, Any], key: str, default: float = 0.0) -> float:
    value = row.get(key)
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class OfferInputs(NamedTuple):
    """``next_best_offer``'s keyword arguments, coerced once from the row."""

    rate_spread_bps: int
    equity_pct: int
    has_permit: bool
    listed_for_sale: bool
    is_investor: bool
    is_current_customer: bool
    is_competitor_lien: bool
    min_spread_bps: int
    min_equity_pct: int
    heloc_equity_min_pct: int
    cashout_equity_min: int
    retention_min_spread: int


def _offer_inputs(row: dict[str, Any]) -> OfferInputs:
    return OfferInputs(
        rate_spread_bps=_int_value(row, "rate_spread_bps"),
        equity_pct=_int_value(row, "equity_pct"),
        has_permit=(
            _coerce_bool(row.get("has_permit"))
            or _coerce_bool(row.get("has_heloc_propensity_trigger"))
        ),
        listed_for_sale=_coerce_bool(row.get("listed_for_sale")),
        is_investor=_coerce_bool(row.get("is_investor")),
        is_current_customer=_coerce_bool(row.get("is_current_customer")),
        is_competitor_lien=_coerce_bool(row.get("is_competitor_lien")),
        min_spread_bps=_int_value(row, "min_spread_bps_applied", 75),
        min_equity_pct=_int_value(row, "min_equity_pct_applied", 15),
        heloc_equity_min_pct=_int_value(row, "heloc_equity_min_applied", 35),
        cashout_equity_min=_int_value(row, "cashout_equity_min_applied", 25),
        retention_min_spread=_int_value(row, "retention_min_spread_applied", 50),
    )


def _offer_for(inputs: OfferInputs) -> str:
    return next_best_offer(**inputs._asdict())


class _Par(NamedTuple):
    note_rate: float
    market_rate: float


def _par_gate(row: dict[str, Any], inputs: OfferInputs) -> _Par | str:
    """The note and par rates when the par margins may be computed, else why not."""

    current_rate_pct = _float_value(row, "current_rate")
    market_rate = _float_value(row, "market_rate_fraction")
    if not (
        math.isfinite(current_rate_pct)
        and _MIN_GATED_RATE_PCT < current_rate_pct < _MAX_GATED_RATE_PCT
    ):
        return "no in-range current lien rate on this row"
    if not (math.isfinite(market_rate) and market_rate > 0):
        return "no par rate on this row"
    note_rate = round(current_rate_pct / 100.0, _NOTE_RATE_DECIMALS)
    if rate_spread_bps(note_rate, market_rate) != inputs.rate_spread_bps:
        return "recomputed spread differs from the materialized spread"
    return _Par(note_rate=note_rate, market_rate=market_rate)


def _bps(value: int) -> str:
    return f"{value} bp" if abs(value) == 1 else f"{value} bps"


def _pts(value: int) -> str:
    return f"{value} pt" if abs(value) == 1 else f"{value} pts"


def _par_pct(market_rate: float, shift_bps: int = 0) -> str:
    return f"{(market_rate + shift_bps / RATE_SPREAD_BPS_PER_UNIT) * 100:.2f}%"


def _par_move(market_rate: float, shift_bps: int) -> str:
    verb = "rises" if shift_bps > 0 else "falls"
    return f"par {verb} {_bps(abs(shift_bps))} to {_par_pct(market_rate, shift_bps)}"


def _margin(
    key: ProofMarginKey,
    label: str,
    value_text: str,
    threshold: str,
    direction: ProofMarginDirection,
    source: str,
) -> ProofMargin:
    return ProofMargin(
        key=key,
        label=label,
        value_text=value_text,
        threshold=threshold,
        direction=direction,
        source=qualify("gold", source),
    )


def _spread_screen(inputs: OfferInputs) -> ProofMargin:
    margin = inputs.rate_spread_bps - inputs.min_spread_bps
    clears = margin >= 0
    if margin == 0:
        value_text = "Meets the refi screen exactly"
    elif clears:
        value_text = f"Clears the refi screen by {_bps(margin)}"
    else:
        value_text = f"{_bps(-margin)} below the refi screen"
    return _margin(
        "spread_screen",
        "Refi screen",
        value_text,
        f"spread {_bps(inputs.rate_spread_bps)} · screen {_bps(inputs.min_spread_bps)}",
        "clears" if clears else "short",
        "fn_in_the_money",
    )


def _par_break_even(inputs: OfferInputs, par: _Par | str) -> ProofMargin:
    label = "Par break-even"
    if isinstance(par, str):
        return _margin("par_break_even", label, _NOT_COMPUTED, par, "unavailable", "fn_rate_spread")
    clears = inputs.rate_spread_bps >= inputs.min_spread_bps
    # The spread falls as par rises, so only one direction can flip the screen.
    step = 1 if clears else -1
    for magnitude in range(1, RATE_SCENARIO_MAX_SHIFT_BPS + 1):
        shift = step * magnitude
        spread = rate_spread_bps_at_par_shift(par.note_rate, par.market_rate, shift)
        if (spread >= inputs.min_spread_bps) != clears:
            action = "Drops below" if clears else "Clears"
            value_text = f"{action} the refi screen if 30-year {_par_move(par.market_rate, shift)}"
            return _margin(
                "par_break_even", label, value_text, f"par {_par_pct(par.market_rate)}", "flips", "fn_rate_spread"
            )
    return _margin(
        "par_break_even",
        label,
        f"Holds for any par move within ±{RATE_SCENARIO_MAX_SHIFT_BPS} bps",
        f"par {_par_pct(par.market_rate)}",
        "holds",
        "fn_rate_spread",
    )


def _equity_floor(inputs: OfferInputs, dossier_offer: str) -> ProofMargin:
    if dossier_offer in {"refi_plus_heloc", "heloc"}:
        floor, name = inputs.heloc_equity_min_pct, "home-equity threshold"
    elif dossier_offer == "cash_out":
        floor, name = inputs.cashout_equity_min, "cash-out threshold"
    else:
        floor, name = inputs.min_equity_pct, "refi equity threshold"
    margin = inputs.equity_pct - floor
    clears = margin >= 0
    if margin == 0:
        value_text = f"Meets the {name} exactly"
    elif clears:
        value_text = f"Clears the {name} by {_pts(margin)}"
    else:
        value_text = f"{_pts(-margin)} below the {name}"
    return _margin(
        "equity_floor",
        "Equity threshold",
        value_text,
        f"equity {inputs.equity_pct}% · threshold {floor}%",
        "clears" if clears else "short",
        "fn_next_best_offer",
    )


def _offer_label(code: str) -> str:
    return offer_display_label(code)


def _equity_flips(inputs: OfferInputs, offer: str) -> list[str]:
    found: list[str] = []
    start = min(max(inputs.equity_pct, _EQUITY_MIN_PCT - 1), _EQUITY_MAX_PCT + 1)
    for levels in (
        range(start - 1, _EQUITY_MIN_PCT - 1, -1),
        range(start + 1, _EQUITY_MAX_PCT + 1),
    ):
        for level in levels:
            code = _offer_for(inputs._replace(equity_pct=level))
            if code != offer:
                found.append(f"Becomes {_offer_label(code)} at {level}% equity")
                break
    return found


def _par_flips(inputs: OfferInputs, offer: str, par: _Par) -> list[str]:
    found: list[str] = []
    for step in (1, -1):
        for magnitude in range(1, RATE_SCENARIO_MAX_SHIFT_BPS + 1):
            shift = step * magnitude
            spread = rate_spread_bps_at_par_shift(par.note_rate, par.market_rate, shift)
            code = _offer_for(inputs._replace(rate_spread_bps=spread))
            if code != offer:
                found.append(f"Becomes {_offer_label(code)} if {_par_move(par.market_rate, shift)}")
                break
    return found


def _flip_margin(
    key: ProofMarginKey,
    label: str,
    flips: list[str],
    holds_text: str,
    threshold: str,
    listed: bool,
) -> ProofMargin:
    if flips:
        return _margin(key, label, " · ".join(flips), threshold, "flips", "fn_next_best_offer")
    return _margin(
        key, label, _LISTING_HOLDS if listed else holds_text, threshold, "holds", "fn_next_best_offer"
    )


def build_proof_margins(row: dict[str, Any], *, dossier_offer: str) -> list[ProofMargin]:
    """The five margins for one dossier row, in display order."""

    inputs = _offer_inputs(row)
    par = _par_gate(row, inputs)
    margins = [_spread_screen(inputs), _par_break_even(inputs, par), _equity_floor(inputs, dossier_offer)]

    offer = _offer_for(inputs)
    if offer != dossier_offer:
        drift = "recomputed offer differs from the dossier offer"
        margins.append(
            _margin("offer_flip_equity", "Offer change by equity", _NOT_COMPUTED, drift, "unavailable", "fn_next_best_offer")
        )
        margins.append(
            _margin("offer_flip_par", "Offer change by par", _NOT_COMPUTED, drift, "unavailable", "fn_next_best_offer")
        )
        return margins

    margins.append(
        _flip_margin(
            "offer_flip_equity",
            "Offer change by equity",
            _equity_flips(inputs, offer),
            f"No equity level from {_EQUITY_MIN_PCT}% to {_EQUITY_MAX_PCT}% changes this offer",
            f"equity {inputs.equity_pct}%",
            inputs.listed_for_sale,
        )
    )
    if isinstance(par, str):
        margins.append(
            _margin("offer_flip_par", "Offer change by par", _NOT_COMPUTED, par, "unavailable", "fn_next_best_offer")
        )
    else:
        margins.append(
            _flip_margin(
                "offer_flip_par",
                "Offer change by par",
                _par_flips(inputs, offer, par),
                f"No par move within ±{RATE_SCENARIO_MAX_SHIFT_BPS} bps changes this offer",
                f"par {_par_pct(par.market_rate)}",
                inputs.listed_for_sale,
            )
        )
    return margins

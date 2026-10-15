"""Canonical segment display labels (critic-11 / D-platform-process-g step 7).

One home for the words the App shows for a segment code, so the API, the
Genie rescue texts and the analytics SQL all say the same thing:

* ``SEGMENT_DISPLAY_LABELS`` covers all 13 gold codes. ``itm`` reads
  "In the Money" (the prototype and CLAUDE.md vocabulary); every other label
  is byte-identical to the gold ``meta`` VALUES table in
  ``sql/transformations/gold_segment_population.sql``.
  ``tests/unit/test_segment_label_parity.py`` pins that and the frontend
  ``segmentMetadata.ts`` names.
* ``LEGACY_SEGMENT_NAMES`` maps a retired display name to its code. Stored
  audit rows and a gold table not yet refreshed can still carry the old
  name; it is an INPUT alias only and is never rendered.

Codes, column names, measures and glossary ids never change here: only the
display words do.
"""

from __future__ import annotations

from collections.abc import Iterable

SEGMENT_DISPLAY_LABELS: dict[str, str] = {
    "itm": "In the Money",
    "listed": "Listed for Sale",
    "permit": "HELOC Intent",
    "investor": "Investor / Multi-Property",
    "equity": "Home Equity Candidate",
    "retention": "Retention Risk",
    "second_lien_itm": "Second-Lien Consolidation",
    "heloc_draw_to_payback": "HELOC Draw Ending",
    "home_equity_history": "Home Equity History",
    "refi_propensity": "Refi Propensity",
    "itm_on_related_property": "ITM on Related Property",
    "payoff_loss_leads": "Payoff Loss",
    "permit_activity": "Permit Activity",
}

ITM_RULE_LABEL = "In the Money rule"
ITM_RULE_DESCRIPTION = "Borrowers who are in the money (spread and equity thresholds)."

LEGACY_SEGMENT_NAMES: dict[str, str] = {"Prime Refi Candidates": "itm"}

# The analytics segment overview's per-code one-liners. These are the
# analytics surface's own descriptions (not the gold meta ones); only the
# itm line reads from the canonical rule description.
_OVERVIEW_DESCRIPTIONS: dict[str, str] = {
    "itm": ITM_RULE_DESCRIPTION,
    "equity": "Borrowers with clean equity capacity.",
    "investor": "Borrowers linked to investor or multi-property signals.",
    "retention": "Current-customer retention opportunities.",
    "listed": "Current active or under-contract Cotality MLS listing.",
    "permit": "High Cotality HELOC propensity; filed permit source remains pending.",
}


def labels_for(codes: Iterable[str]) -> dict[str, str]:
    """The display labels for ``codes``, in the order given (KeyError on an unknown code)."""
    return {code: SEGMENT_DISPLAY_LABELS[code] for code in codes}


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def segment_values_sql(codes: Iterable[str], *, indent: str) -> str:
    """``(code, label, description)`` VALUES rows for the analytics overview, comma-joined."""
    return ", ".join(
        f"{indent}({_sql_literal(code)}, {_sql_literal(SEGMENT_DISPLAY_LABELS[code])}, "
        f"{_sql_literal(_OVERVIEW_DESCRIPTIONS[code])})"
        for code in codes
    )


def segment_case_sql(codes: Iterable[str], *, indent: str) -> str:
    """The ``WHEN segment_code = ... THEN label`` arms, each line prefixed by ``indent``."""
    return "".join(
        f"{indent}WHEN segment_code = {_sql_literal(code)} THEN "
        f"{_sql_literal(SEGMENT_DISPLAY_LABELS[code])} "
        for code in codes
    )


__all__ = [
    "ITM_RULE_DESCRIPTION",
    "ITM_RULE_LABEL",
    "LEGACY_SEGMENT_NAMES",
    "SEGMENT_DISPLAY_LABELS",
    "labels_for",
    "segment_case_sql",
    "segment_values_sql",
]

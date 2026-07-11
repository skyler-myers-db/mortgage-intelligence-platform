"""Pinned constants + pure bin math for the economics scatter (S7).

`mip.gold.equity_spread_points` precomputes one density-bin coordinate per
borrower so the Analytics -> Economics overview is a GROUP BY, not a raw
point stream. This module is the Python parity mirror of the SQL bin
formulas in ``sql/transformations/gold_equity_spread_points.sql``:

    equity_bin_pct = FLOOR(equity_pct / 5) * 5
    spread_bin_bps = FLOOR(rate_spread_bps / 25) * 25

Python floor division matches Spark FLOOR on negatives (``-1 // 25 == -1``
=> bin edge ``-25``), and the golden fixtures in
``tests/fixtures/equity_spread_bins_golden.json`` pin both layers so the
formulas cannot drift apart silently. Change the SQL and this module (and
the fixtures) together or not at all.

The plot domain constants mirror the WHERE clause the economics surface has
always applied to the rate-spread histogram and the equity x spread scatter
(equity 0..100 pct, spread -100..400 bps).
"""
from __future__ import annotations

EQUITY_BIN_PCT: int = 5
SPREAD_BIN_BPS: int = 25

EQUITY_DOMAIN_MIN: int = 0
EQUITY_DOMAIN_MAX: int = 100
SPREAD_DOMAIN_MIN: int = -100
SPREAD_DOMAIN_MAX: int = 400

# Server-side cap on real borrower points returned for a zoomed viewport.
# The honest "showing N of M" payload reports the pre-cap total alongside
# the capped page, so the UI never implies the page is the population.
MAX_SCATTER_POINT_ROWS: int = 5000

# UC source cited by the scatter evidence affordance and returned in every
# scatter payload so the UI lineage story matches the warehouse.
EQUITY_SPREAD_SOURCE_TABLE: str = "mip.gold.equity_spread_points"

# Display labels for the dot's primary segment code. Mirrors the label CASE
# used across the analytics SQL surfaces (segment overview / by-state).
SEGMENT_DISPLAY_LABELS: dict[str, str] = {
    "itm": "Prime Refi Candidates",
    "equity": "Home Equity Candidate",
    "investor": "Investor / Multi-Property",
    "listed": "Listed for Sale",
    "permit": "HELOC Intent",
    "retention": "Retention Risk",
}
UNSEGMENTED_LABEL: str = "None / Unsegmented"


def equity_bin_pct(equity_pct: int) -> int:
    """Bin lower edge for the equity axis. Parity: FLOOR(equity_pct/5)*5."""
    return (equity_pct // EQUITY_BIN_PCT) * EQUITY_BIN_PCT


def spread_bin_bps(rate_spread_bps: int) -> int:
    """Bin lower edge for the spread axis. Parity: FLOOR(spread/25)*25."""
    return (rate_spread_bps // SPREAD_BIN_BPS) * SPREAD_BIN_BPS


def segment_display_label(primary_segment_code: str | None) -> str:
    """Map a stored primary segment code onto its product display label."""
    if not primary_segment_code:
        return UNSEGMENTED_LABEL
    return SEGMENT_DISPLAY_LABELS.get(primary_segment_code, UNSEGMENTED_LABEL)


__all__ = [
    "EQUITY_BIN_PCT",
    "EQUITY_DOMAIN_MAX",
    "EQUITY_DOMAIN_MIN",
    "EQUITY_SPREAD_SOURCE_TABLE",
    "MAX_SCATTER_POINT_ROWS",
    "SEGMENT_DISPLAY_LABELS",
    "SPREAD_BIN_BPS",
    "SPREAD_DOMAIN_MAX",
    "SPREAD_DOMAIN_MIN",
    "UNSEGMENTED_LABEL",
    "equity_bin_pct",
    "segment_display_label",
    "spread_bin_bps",
]

"""WhyPanel schema — explainability payload for Borrower 360.

Embeds deterministic rate-spread and in-the-money outputs alongside the
thresholds that were applied and the Unity Catalog sources that would back
the same computation in production. Preserves the Module 0 anchor that every
recommendation traces to a Cotality signal.
"""

from __future__ import annotations

from pydantic import BaseModel


class WhyPanelSource(BaseModel):
    """Parallel business-friendly label for a WhyPanel source.

    Added 2026-04-22 so Borrower 360's rationale chips render
    ``"In-the-money rule"`` instead of ``mip.gold.fn_in_the_money``.
    ``name`` stays the authoritative UC FQN so the evidence drawer
    lineage link still works.
    """

    name: str
    display_label: str


class WhyPanel(BaseModel):
    rate_spread_bps: int
    market_rate: float
    equity_pct: int
    in_the_money: bool
    in_the_money_reason: str
    min_spread_bps: int
    min_equity_pct: int
    sources: list[str]
    # 2026-04-22 additive field; same index alignment as `sources`.
    source_labels: list[WhyPanelSource] = []

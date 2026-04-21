"""WhyPanel schema — explainability payload for Borrower 360.

Embeds the deterministic output of ``backend.services.scoring.rate_spread_bps``
and ``in_the_money`` alongside the thresholds that were applied and the
Unity Catalog sources that would back the same computation in production.
Preserves the Module 0 anchor that every recommendation traces to a
Cotality signal.
"""

from __future__ import annotations

from pydantic import BaseModel


class WhyPanel(BaseModel):
    rate_spread_bps: int
    market_rate: float
    equity_pct: int
    in_the_money: bool
    in_the_money_reason: str
    min_spread_bps: int
    min_equity_pct: int
    sources: list[str]

"""Rate-window analytics schemas (``GET /api/v1/analytics/rate-window``).

The "why now" series behind the Analytics Executive tab: the weekly FRED
MORTGAGE30US print against the CURRENT fixed-rate book's note-rate band, and
the count of liens in the money at each week's rate. Every row is read from
``mip.gold.rate_window_weekly``, which the gold refresh job builds once per
refresh with the canonical ``fn_rate_spread`` / ``fn_in_the_money``
primitives; nothing here is computed per request.

Rates are in PERCENT form (6.30 == 6.30%), matching ``Borrower360.current_rate``
and the FRED series as published. The book distribution is as-of the refresh
anchor (``book_as_of``) and applied to every week -- the response says so in
its provenance block so no chart can pass the series off as a portfolio
history.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class RateWindowWeek(BaseModel):
    """One FRED week measured against the current book."""

    week: str = Field(description="Week-starting Monday, ISO date (YYYY-MM-DD).")
    market_rate_pct: float = Field(description="FRED 30-year fixed rate for the week, in percent.")
    book_median_pct: float | None = Field(
        default=None,
        description="Median note rate of the current fixed-rate book, in percent. None when the book is empty.",
    )
    book_p25_pct: float | None = Field(
        default=None,
        description="25th-percentile note rate of the current fixed-rate book, in percent.",
    )
    book_p75_pct: float | None = Field(
        default=None,
        description="75th-percentile note rate of the current fixed-rate book, in percent.",
    )
    itm_count: int = Field(
        ge=0,
        description=(
            "Liens in the money at this week's market rate: fn_in_the_money(fn_rate_spread(...)) "
            "summed over the current fixed-rate book with the governed thresholds."
        ),
    )
    is_latest: bool = Field(
        default=False,
        description="True on the most recent week (the current market print).",
    )


class RateWindowThresholds(BaseModel):
    """The governed in-the-money thresholds applied to every week.

    Both are ``None`` only when the fixed-rate book is empty (there was no
    row to carry them from); the UI then draws no reference line.
    """

    min_spread_bps: int | None = None
    min_equity_pct: int | None = None


class RateWindowProvenance(BaseModel):
    """Evidence manifest for the series: every table it was read from."""

    market_rate_source: str = Field(description="Weekly market rate source (FRED MORTGAGE30US).")
    book_source: str = Field(description="The book the note-rate band and itm_count were measured over.")
    gold_source: str = Field(description="The precomputed gold table the endpoint reads.")
    rule_source: str = Field(description="The UC functions that decide in the money.")
    book_as_of: str | None = Field(
        default=None,
        description="Refresh anchor of the book distribution; the same on every week.",
    )
    refreshed_at: str | None = None
    note: str


class RateWindowResponse(BaseModel):
    series_id: str = Field(description="FRED series code, MORTGAGE30US.")
    weeks: list[RateWindowWeek]
    book_lien_count: int = Field(
        ge=0,
        description="Active fixed-rate first liens in the current book (the same on every week).",
    )
    book_as_of: str | None = Field(
        default=None,
        description="Refresh anchor of the book distribution: today's book against the historical rate.",
    )
    thresholds: RateWindowThresholds
    provenance: RateWindowProvenance

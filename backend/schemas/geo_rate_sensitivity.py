"""Rate Lever schemas (``GET /api/v1/geo/rate-sensitivity``).

The Rate Lever (2026-09-21 UI/UX audit, wow-stage-1) recolours the geography
hero by a par-rate scenario: for each state and each step of a -100 .. +100 bps
grid, how many addressable borrowers would clear this refresh's refi screen if
the 30-year par rate moved by that step. It is a scenario, not a forecast.

The addressable grid is read from ``mip.gold.rate_sensitivity_rollup``, which
the gold refresh job builds by re-running ``fn_rate_spread`` /
``fn_in_the_money`` at ``par + step / 10000`` (never by shifting rounded spread
bins). The contactable subset cannot be precomputed (the eligibility predicate
reads the current time), so it is a live aggregate joined onto the gold rows
in the same statement and clamped so the subset never out-counts its superset.

Every per-state list is ALIGNED to ``steps_bps``: index ``i`` of
``in_the_money`` is the count at ``steps_bps[i]``. Rates are in PERCENT form
(6.30 == 6.30%) and are supplied by the server; the client does no rate
arithmetic. ``built`` is False until the gold refresh has built the table (the
deploy that introduces it promotes the App before the refresh runs), and a
state whose grid is incomplete is dropped rather than zero-filled.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class RateSensitivityThresholds(BaseModel):
    """The refresh's in-the-money thresholds the whole grid was evaluated with."""

    min_spread_bps: int | None = None
    min_equity_pct: int | None = None


class RateSensitivityState(BaseModel):
    """One state's scenario row, aligned to ``RateSensitivityResponse.steps_bps``."""

    state: str = Field(description="2-char USPS state code (uppercase).")
    addressable: int = Field(ge=0, description="Addressable borrowers in the state (the same at every step).")
    rate_movable: int = Field(
        ge=0,
        description=(
            "Addressable borrowers with a gated note rate (active lien, bounded rate strictly "
            "inside 1%..15%): the only ones a scenario can move. Never above addressable."
        ),
    )
    in_the_money: list[int] = Field(
        description="Borrowers clearing the refi screen at each step, aligned to steps_bps; each <= addressable.",
    )
    contactable_in_the_money: list[int] | None = Field(
        default=None,
        description=(
            "The contact-eligible subset of in_the_money at each step (live aggregate, clamped to "
            "in_the_money). None when not reported."
        ),
    )


class RateSensitivityProvenance(BaseModel):
    """Evidence manifest: every table and rule the grid was read or derived from."""

    gold_source: str = Field(description="The precomputed gold grid the endpoint reads.")
    book_source: str = Field(description="The book the grid was measured over.")
    rule_source: str = Field(description="The UC functions re-run at each scenario par rate.")
    contactable_source: str = Field(description="Where the live contactable subset comes from.")
    book_as_of: str | None = Field(default=None, description="Refresh anchor of the book.")
    refreshed_at: str | None = None
    note: str


class RateSensitivityResponse(BaseModel):
    built: bool = Field(
        description="False until the gold refresh has built mip.gold.rate_sensitivity_rollup.",
    )
    steps_bps: list[int] = Field(
        description="The scenario grid in basis points of par move (positive = par rises); empty when not built.",
    )
    scenario_market_rate_pct: list[float] = Field(
        description="The par rate each step describes, in percent, aligned to steps_bps.",
    )
    base_market_rate_pct: float | None = Field(
        default=None,
        description="The par rate this refresh scored with, in percent (step 0). None when not built.",
    )
    thresholds: RateSensitivityThresholds
    states: list[RateSensitivityState]
    provenance: RateSensitivityProvenance

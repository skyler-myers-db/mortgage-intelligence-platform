"""Databricks-backed Rate Lever repository (audit wow-stage-1).

Reads ``mip.gold.rate_sensitivity_rollup`` -- the (state, step_bps) grid the
gold refresh job precomputes by re-running ``fn_rate_spread`` /
``fn_in_the_money`` at ``par + step / 10000`` -- and, in the SAME statement,
LEFT JOINs a live contactable aggregate onto it. Contactability cannot be a
gold column: its predicate has one owner (``eligibility.eligible_sql_predicate``)
and reads the current time. The live CTE reads each borrower's gated note from
``mip.gold.rate_sensitivity_book`` -- built by the same refresh from the same
``NOTE_RATE_GATE_SQL`` text over the same lien join -- reuses the per-step
rule from ``rate_scenario`` verbatim, and takes its steps from the gold table
itself, so the two grids align by construction. The statement reads gold only:
``mip.silver`` is ETL-only (``docs/security/GRANTS.md`` section 5).

Projection rules (the endpoint's honesty contract):

* pivot to per-state lists aligned to ``RATE_SCENARIO_STEPS_BPS``;
* clamp ``contactable <= in_the_money <= addressable`` and
  ``rate_movable <= addressable`` at every step (a live subset joined to a
  precomputed superset can drift between refreshes; the UI states them as a
  relationship, the same reasoning as the geo repository's contactable clamp);
* a state whose grid is incomplete is DROPPED with an observability event,
  never zero-filled; no usable state means ``built=False``;
* a missing lane table -- the grid or the note book (the deploy that
  introduces one promotes the App before the refresh job builds it) -- is
  ``built=False``, never a 503 "warming".

Cache posture matches the geography rollups: ``GoldAggregateCache`` with a 60 s
soft TTL, single-flight and stale-if-error. A cold failure propagates so the
resilience layer answers 503 ``warming_up``. The read writes no audit row.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from backend.schemas.geo_rate_sensitivity import (
    RateSensitivityProvenance,
    RateSensitivityResponse,
    RateSensitivityState,
    RateSensitivityThresholds,
)
from backend.services.databricks_sql import DatabricksSqlClient
from backend.services.databricks_sql_helpers import qualify
from backend.services.eligibility import eligible_sql_predicate
from backend.services.gold_cache import AggregateCache, GoldAggregateCache
from backend.services.observability import emit
from backend.services.rate_scenario import (
    RATE_SCENARIO_STEPS_BPS,
    SCENARIO_ITM_SQL,
)

log = logging.getLogger("backend.services.repositories.databricks_repo")

_GOLD_SOURCE = qualify("gold", "rate_sensitivity_rollup")
_NOTE_BOOK = qualify("gold", "rate_sensitivity_book")
_BORROWER_360 = qualify("gold", "borrower_360")
# Provenance text only: the grid's book as the ETL refresh read it. Never
# interpolated into an executed statement (the App holds no silver grant).
_BOOK_SOURCE = f"{_BORROWER_360} + {qualify('silver', 'lien_current')}"
_RULE_SOURCE = f"{qualify('gold', 'fn_rate_spread')} + {qualify('gold', 'fn_in_the_money')}"
_CONTACTABLE_SOURCE = f"{_BORROWER_360} + {_NOTE_BOOK} (live eligibility predicate, per request)"
_MISSING_TABLE_MARKER = "TABLE_OR_VIEW_NOT_FOUND"
# The lane's own tables: either one missing is "not built yet".
_LANE_TABLES = ("rate_sensitivity_rollup", "rate_sensitivity_book")

_PROVENANCE_NOTE = (
    "A scenario, not a forecast: each step re-runs fn_rate_spread and fn_in_the_money "
    "at this refresh's par rate plus the step, over the book as of book_as_of. "
    "Borrowers without an active, in-bounds note rate never move. Rebuilt by the "
    "gold refresh job, not on a schedule; contactable counts are live."
)

# One statement: the precomputed addressable grid LEFT JOIN a live eligible
# aggregate at the gold table's own steps. The eligibility predicate is the
# unaliased text, embedded verbatim over borrower_360 before the note join.
#
# The note join is a LEFT JOIN on purpose. The book holds a row only for a
# borrower whose gated note is non-NULL, so a missing row reads as exactly the
# NULL note the rollup keeps for a gated borrower. Such a borrower scores the
# no-signal 0 bps at every step; it cannot clear a positive spread screen, but
# it DOES clear one at min_spread_bps <= 0 (a governed threshold the admin
# rules accept), where the rollup still counts it. An INNER JOIN would then
# drop it from the contactable subset only, so it is never used here.
RATE_SENSITIVITY_SQL = (
    "WITH eligible_book AS ( "
    "  SELECT "
    "    b.state, "
    "    nb.note_rate_fraction, "
    "    b.equity_pct, "
    "    b.min_spread_bps_applied, "
    "    b.min_equity_pct_applied, "
    "    b.market_rate_fraction "
    "  FROM ( "
    "    SELECT clip, state, equity_pct, min_spread_bps_applied, "
    "      min_equity_pct_applied, market_rate_fraction "
    f"    FROM {_BORROWER_360} "
    f"    WHERE {eligible_sql_predicate()} "
    "  ) AS b "
    f"  LEFT JOIN {_NOTE_BOOK} AS nb ON nb.clip = b.clip "
    "  WHERE b.state IS NOT NULL "
    "), eligible_cells AS ( "
    "  SELECT state, note_rate_fraction, equity_pct, min_spread_bps_applied, "
    "    min_equity_pct_applied, market_rate_fraction, COUNT(*) AS borrower_count "
    "  FROM eligible_book "
    "  GROUP BY state, note_rate_fraction, equity_pct, min_spread_bps_applied, "
    "    min_equity_pct_applied, market_rate_fraction "
    "), grid AS ( "
    f"  SELECT DISTINCT step_bps FROM {_GOLD_SOURCE} "
    "), contactable AS ( "
    "  SELECT k.state, g.step_bps, "
    f"    SUM(CASE WHEN {SCENARIO_ITM_SQL} THEN k.borrower_count ELSE 0 END) "
    "      AS contactable_in_the_money "
    "  FROM eligible_cells AS k "
    "  CROSS JOIN grid AS g "
    "  GROUP BY k.state, g.step_bps "
    ") "
    "SELECT "
    "  r.state, "
    "  CAST(r.step_bps AS INT) AS step_bps, "
    "  CAST(r.scenario_market_rate_pct AS DOUBLE) AS scenario_market_rate_pct, "
    "  CAST(r.addressable_borrowers AS BIGINT) AS addressable_borrowers, "
    "  CAST(r.rate_movable_borrowers AS BIGINT) AS rate_movable_borrowers, "
    "  CAST(r.in_the_money_borrowers AS BIGINT) AS in_the_money_borrowers, "
    "  r.min_spread_bps_applied, "
    "  r.min_equity_pct_applied, "
    "  r.book_as_of, "
    "  r.refreshed_at, "
    "  CAST(COALESCE(c.contactable_in_the_money, 0) AS BIGINT) AS contactable_in_the_money "
    f"FROM {_GOLD_SOURCE} AS r "
    "LEFT JOIN contactable AS c ON c.state = r.state AND c.step_bps = r.step_bps "
    "ORDER BY r.state, r.step_bps"
)


def _int_or_none(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    return int(float(str(value)))


def _float_or_none(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    return float(str(value))


def _timestamp_text(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    return str(value)


def _clamp(value: int | None, upper: int) -> int:
    return max(0, min(upper, value or 0))


def _is_missing_table(exc: BaseException) -> bool:
    """True when the failure is a Rate Lever table not existing yet.

    The resilient SQL client wraps the warehouse error in a
    ``DependencyDownError`` (``last_error``); the raw client raises it bare.
    Only the lane's own tables (the grid and the note book) missing is "not
    built": any other missing object is a real failure and keeps its 503.
    """
    seen: set[int] = set()
    node: BaseException | None = exc
    while node is not None and id(node) not in seen:
        seen.add(id(node))
        text = str(node)
        if _MISSING_TABLE_MARKER in text and any(table in text for table in _LANE_TABLES):
            return True
        next_node = getattr(node, "last_error", None)
        node = next_node if isinstance(next_node, BaseException) else node.__cause__
    return False


def _provenance(book_as_of: str | None, refreshed_at: str | None) -> RateSensitivityProvenance:
    return RateSensitivityProvenance(
        gold_source=_GOLD_SOURCE,
        book_source=_BOOK_SOURCE,
        rule_source=_RULE_SOURCE,
        contactable_source=_CONTACTABLE_SOURCE,
        book_as_of=book_as_of,
        refreshed_at=refreshed_at,
        note=_PROVENANCE_NOTE,
    )


def not_built_response() -> RateSensitivityResponse:
    """The honest answer before the gold refresh has built the grid."""
    return RateSensitivityResponse(
        built=False,
        steps_bps=[],
        scenario_market_rate_pct=[],
        base_market_rate_pct=None,
        thresholds=RateSensitivityThresholds(),
        states=[],
        provenance=_provenance(None, None),
    )


def _state_row(state: str, by_step: dict[int, dict[str, Any]]) -> RateSensitivityState:
    first = by_step[RATE_SCENARIO_STEPS_BPS[0]]
    addressable = max(0, _int_or_none(first.get("addressable_borrowers")) or 0)
    in_the_money = [
        _clamp(_int_or_none(by_step[step].get("in_the_money_borrowers")), addressable)
        for step in RATE_SCENARIO_STEPS_BPS
    ]
    contactable: list[int] | None = []
    for index, step in enumerate(RATE_SCENARIO_STEPS_BPS):
        raw = by_step[step].get("contactable_in_the_money")
        if raw is None or contactable is None:
            # Not reported (an older cached frame): say so, never a zero.
            contactable = None
            continue
        contactable.append(_clamp(_int_or_none(raw), in_the_money[index]))
    return RateSensitivityState(
        state=state,
        addressable=addressable,
        rate_movable=_clamp(_int_or_none(first.get("rate_movable_borrowers")), addressable),
        in_the_money=in_the_money,
        contactable_in_the_money=contactable,
    )


def project_rate_sensitivity(rows: list[dict[str, Any]]) -> RateSensitivityResponse:
    """Pivot the (state, step) rows into the aligned per-state response."""
    grid = set(RATE_SCENARIO_STEPS_BPS)
    by_state: dict[str, dict[int, dict[str, Any]]] = {}
    for row in rows:
        state = str(row.get("state") or "").strip().upper()
        step = _int_or_none(row.get("step_bps"))
        if not state or step is None or step not in grid:
            continue
        by_state.setdefault(state, {})[step] = row

    states: list[RateSensitivityState] = []
    template: dict[int, dict[str, Any]] | None = None
    for state in sorted(by_state):
        by_step = by_state[state]
        missing = [step for step in RATE_SCENARIO_STEPS_BPS if step not in by_step]
        if missing:
            emit(
                log,
                "rate_sensitivity_state_grid_incomplete",
                level=logging.WARNING,
                state=state,
                missing_steps=len(missing),
                outcome="dropped",
            )
            continue
        states.append(_state_row(state, by_step))
        template = template or by_step

    if template is None:
        return not_built_response()
    rates = [_float_or_none(template[step].get("scenario_market_rate_pct")) for step in RATE_SCENARIO_STEPS_BPS]
    if any(rate is None for rate in rates):
        # A grid without a par rate cannot state a scenario rate: the refresh
        # scored with no market print. Rebuilding after FRED lands fixes it.
        emit(log, "rate_sensitivity_par_rate_missing", level=logging.WARNING, outcome="not_built")
        return not_built_response()
    scenario_rates = [rate for rate in rates if rate is not None]
    head = template[0]
    book_as_of = _timestamp_text(head.get("book_as_of"))
    return RateSensitivityResponse(
        built=True,
        steps_bps=list(RATE_SCENARIO_STEPS_BPS),
        scenario_market_rate_pct=scenario_rates,
        base_market_rate_pct=scenario_rates[RATE_SCENARIO_STEPS_BPS.index(0)],
        thresholds=RateSensitivityThresholds(
            min_spread_bps=_int_or_none(head.get("min_spread_bps_applied")),
            min_equity_pct=_int_or_none(head.get("min_equity_pct_applied")),
        ),
        states=states,
        provenance=_provenance(book_as_of, _timestamp_text(head.get("refreshed_at"))),
    )


class DatabricksRateSensitivityRepository:
    """Typed read model over ``mip.gold.rate_sensitivity_rollup`` + live contactable.

    The live contactable subset reads ``mip.gold.rate_sensitivity_book`` for
    the gated notes; the statement touches gold only.
    """

    _SQL = RATE_SENSITIVITY_SQL
    _CACHE_KEY = "geo.rate_sensitivity"

    def __init__(
        self,
        client: DatabricksSqlClient,
        *,
        cache: AggregateCache | None = None,
        cache_ttl_s: float = 60.0,
    ) -> None:
        self._client = client
        self._cache: AggregateCache = cache if cache is not None else GoldAggregateCache()
        self._cache_ttl_s = cache_ttl_s

    def rate_sensitivity(self) -> RateSensitivityResponse:
        def build() -> RateSensitivityResponse:
            try:
                rows = self._client.execute(self._SQL) or []
            except Exception as exc:
                if not _is_missing_table(exc):
                    raise
                emit(log, "rate_sensitivity_table_missing", level=logging.INFO, outcome="not_built")
                return not_built_response()
            return project_rate_sensitivity(rows)

        result: RateSensitivityResponse = self._cache.get_or_set(
            self._CACHE_KEY,
            build,
            ttl_s=self._cache_ttl_s,
            stale_if_error=True,
        )
        return result

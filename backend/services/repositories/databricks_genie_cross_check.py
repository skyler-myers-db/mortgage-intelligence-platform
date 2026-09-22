"""Governed cross-check of a live Genie turn against the reviewed framing.

Single responsibility: decide whether a live Genie answer's own SQL disagrees
with the canonical statement the same question would have been answered with,
and report the divergence. This layer never replaces a live answer -- it
returns an outcome the adapter discloses. The canonical statements it compares
against live in ``databricks_genie_canonical``; the adapter that consumes the
outcome lives in ``databricks_genie``.
"""
from __future__ import annotations

from typing import Any, NamedTuple

from backend.services.databricks_sql import (
    DatabricksSqlClient,
    DatabricksSqlError,
)
from backend.services.repositories.databricks_genie_canonical import (
    _CANONICAL_ITM_COUNT_BY_CITY_SQL,
    _CANONICAL_ITM_COUNT_BY_STATE_SQL,
    _CANONICAL_ITM_COUNT_SQL,
    _CANONICAL_TOP_BORROWERS_ALL_SEGMENTS_SQL,
    _CANONICAL_TOP_BORROWERS_BY_STATE_INTENT_SQL,
    _CANONICAL_TOP_BORROWERS_BY_STATE_SQL,
    _CANONICAL_TOP_BORROWERS_GLOBAL_INTENT_SQL,
    _CANONICAL_TOP_BORROWERS_GLOBAL_SQL,
    _canonical_in_the_money_count_scope,
    _canonical_itm_city_scope,
    _canonical_specific_top_borrowers_global_scope,
    _canonical_specific_top_borrowers_state_scope,
    _canonical_top_borrowers_all_segments_scope,
    _canonical_top_borrowers_global_scope,
    _canonical_top_borrowers_state_scope,
)
from backend.services.repositories.databricks_genie_trace import GenieProcessTrace


class _CrossCheckOutcome(NamedTuple):
    gaps: list[str]
    # Canonical unique-borrower count when the live metric materially
    # diverges from the governed definition; None otherwise.
    count_reference: int | None


_CROSS_CHECK_CLEAN = _CrossCheckOutcome(gaps=[], count_reference=None)


def _governed_cross_check(
    question: str,
    rows: list[dict[str, Any]] | None,
    sql_client: DatabricksSqlClient | None,
    trace: GenieProcessTrace,
) -> _CrossCheckOutcome:
    """Verify a trusted LIVE result against the governed canonical framing.

    Verification, never replacement: Genie's own result remains the answer.
    Adds a verify step to the trace and returns disclosure notes only when the
    two governed framings materially diverge. Any warehouse error skips the
    check silently — a failed verification must never degrade a good answer.
    """

    if sql_client is None or not rows:
        return _CROSS_CHECK_CLEAN
    try:
        ranking_sql: str | None = None
        params: dict[str, object] | None = None
        if _canonical_top_borrowers_all_segments_scope(question):
            ranking_sql = _CANONICAL_TOP_BORROWERS_ALL_SEGMENTS_SQL
        else:
            spec_state = _canonical_specific_top_borrowers_state_scope(question)
            spec_global = _canonical_specific_top_borrowers_global_scope(question)
            state_scope = _canonical_top_borrowers_state_scope(question)
            if spec_state is not None:
                intent, _state_name, state_code = spec_state
                ranking_sql = _CANONICAL_TOP_BORROWERS_BY_STATE_INTENT_SQL[intent]
                params = {"state": state_code}
            elif spec_global is not None:
                ranking_sql = _CANONICAL_TOP_BORROWERS_GLOBAL_INTENT_SQL[spec_global]
            elif state_scope is not None:
                ranking_sql = _CANONICAL_TOP_BORROWERS_BY_STATE_SQL
                params = {"state": state_scope[1]}
            elif _canonical_top_borrowers_global_scope(question):
                ranking_sql = _CANONICAL_TOP_BORROWERS_GLOBAL_SQL
        if ranking_sql is not None:
            live_ids = [str(r.get("borrower_id") or "") for r in rows if r.get("borrower_id")]
            if not live_ids:
                return _CROSS_CHECK_CLEAN
            canonical_rows = sql_client.execute(ranking_sql, params) or []
            canonical_ids = [
                str(r.get("borrower_id") or "") for r in canonical_rows if r.get("borrower_id")
            ]
            if not canonical_ids:
                return _CROSS_CHECK_CLEAN
            top_n = min(len(live_ids), len(canonical_ids), 10)
            overlap = len(set(live_ids[:top_n]) & set(canonical_ids[:top_n]))
            trace.cross_check_ranking(overlap=overlap, total=top_n)
            if overlap * 2 < top_n:
                return _CrossCheckOutcome(
                    gaps=[
                        "Governed cross-check: this answer's framing overlaps "
                        f"{overlap} of {top_n} borrowers with the canonical "
                        "opportunity ranking; both are governed views — the "
                        "Lead Queue holds the operational list."
                    ],
                    count_reference=None,
                )
            return _CROSS_CHECK_CLEAN
        count_scope = _canonical_in_the_money_count_scope(question)
        city_scope = None if count_scope else _canonical_itm_city_scope(question)
        if count_scope or city_scope:
            if city_scope:
                count_sql = _CANONICAL_ITM_COUNT_BY_CITY_SQL
                count_params: dict[str, object] | None = {"city": city_scope}
            else:
                count_state = count_scope if isinstance(count_scope, tuple) else None
                count_sql = (
                    _CANONICAL_ITM_COUNT_BY_STATE_SQL if count_state else _CANONICAL_ITM_COUNT_SQL
                )
                count_params = {"state": count_state[1]} if count_state else None
            row = sql_client.execute_one(count_sql, count_params) or {}
            canonical_count = row.get("in_the_money_borrowers")
            if canonical_count is None:
                return _CROSS_CHECK_CLEAN
            canonical_f = float(canonical_count)
            live_values: list[float] = []
            for live_row in rows:
                for value in live_row.values():
                    try:
                        live_values.append(float(value))
                    except (TypeError, ValueError):
                        continue
            if not live_values:
                return _CROSS_CHECK_CLEAN
            tolerance = max(1.0, 0.01 * canonical_f)
            consistent = any(abs(value - canonical_f) <= tolerance for value in live_values)
            trace.cross_check_count(consistent=consistent)
            if not consistent:
                return _CrossCheckOutcome(
                    gaps=[
                        "Governed cross-check: the canonical unique-borrower "
                        f"recomputation returns {int(canonical_f):,} for this "
                        "metric; the live framing differs — compare both "
                        "governed queries in the proof."
                    ],
                    count_reference=int(canonical_f),
                )
    except DatabricksSqlError:
        return _CROSS_CHECK_CLEAN
    return _CROSS_CHECK_CLEAN

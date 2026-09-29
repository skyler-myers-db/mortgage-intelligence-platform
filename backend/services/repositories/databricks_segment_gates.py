"""Source-readiness gate helpers for segment rollups.

The ``gold.source_readiness`` snapshot sits in a ``GoldAggregateCache``
(audit ``delivery-06``): past its soft TTL it is served stale while one
background refresh re-reads it, and a failed refresh keeps last-good
(``stale_if_error``). The build no longer swallows a failure, so a failure is
never stored: a COLD failure logs ``segment_source_readiness_unavailable``
and gates nothing (every segment stays "connected") for that one call, and
the next call reads again. Before this it was negative-cached for the TTL;
with a missing table failing fast (one statement, no breaker failure) the
realistic cold failure is one quick statement. The factory reads only the
SQL client and never writes an audit row.
"""

from __future__ import annotations

import logging

from backend.schemas.lead import SegmentSummary
from backend.services.databricks_sql import DatabricksSqlClient
from backend.services.databricks_sql_helpers import qualify
from backend.services.gold_cache import AggregateCache
from backend.services.observability import emit

log = logging.getLogger("backend.services.repositories.databricks_repo")

# S1.3 three-state gating: driving gold.source_readiness source per
# entitleable segment. Core-spine segments (itm / investor / equity /
# retention) ride on silver.lien_current -- if that is down the whole
# app is degraded, so they are deliberately unmapped and always read
# "connected". The full entitlement matrix ships in S5.1 and replaces
# only the lookup, not this contract.
_SEGMENT_SOURCE_REQUIREMENTS: dict[str, str] = {
    "listed": "MLS Listings",
    "permit": "Cotality HELOC Propensity",
    "second_lien_itm": "Voluntary Lien",
    "heloc_draw_to_payback": "MMA Mortgage Analytics",
    "home_equity_history": "AVM",
    "refi_propensity": "Voluntary Lien",
    "itm_on_related_property": "Owner Link",
    "payoff_loss_leads": "MMA Mortgage Analytics",
    "permit_activity": "Building Permits",
}

_SOURCE_STATUS_SQL = (
    "SELECT source_name, status "
    f"FROM {qualify('gold', 'source_readiness')}"
)
_SOURCE_STATUS_CACHE_KEY = "segments.source_statuses"


def _three_state_status(status: str | None) -> str:
    """Collapse DataEstateStatus into the S1.3 three-state gate."""
    if status in ("live", "demo_synthetic", "configured_empty"):
        return "connected"
    if status == "permission_denied":
        return "not_licensed"
    return "not_connected"


def _source_statuses(
    client: DatabricksSqlClient,
    cache: AggregateCache,
    cache_ttl_s: float,
) -> dict[str, str]:
    """Return cached gold.source_readiness snapshot for presentational gates."""

    def build() -> dict[str, str]:
        rows = client.execute(_SOURCE_STATUS_SQL) or []
        return {
            str(r.get("source_name")): str(r.get("status") or "")
            for r in rows
            if r.get("source_name")
        }

    try:
        statuses: dict[str, str] = cache.get_or_set(
            _SOURCE_STATUS_CACHE_KEY,
            build,
            ttl_s=cache_ttl_s,
            stale_if_error=True,
        )
    except Exception as exc:  # noqa: BLE001 -- gating is presentational
        emit(
            log,
            "segment_source_readiness_unavailable",
            level=logging.WARNING,
            dependency="warehouse",
            outcome="degraded",
            exc_type=type(exc).__name__,
            exc_msg=str(exc)[:500],
        )
        return {}
    return statuses


def apply_source_gates(
    segments: list[SegmentSummary],
    *,
    client: DatabricksSqlClient,
    cache: AggregateCache,
    cache_ttl_s: float,
) -> list[SegmentSummary]:
    """Apply source readiness labels without suppressing real counts."""
    statuses = _source_statuses(client, cache, cache_ttl_s)
    if not statuses:
        return segments
    gated: list[SegmentSummary] = []
    for segment in segments:
        source_name = _SEGMENT_SOURCE_REQUIREMENTS.get(segment.code)
        if source_name is None:
            gated.append(segment)
            continue
        gated.append(
            segment.model_copy(
                update={
                    "source_status": _three_state_status(statuses.get(source_name)),
                    "source_name": source_name,
                }
            )
        )
    return gated

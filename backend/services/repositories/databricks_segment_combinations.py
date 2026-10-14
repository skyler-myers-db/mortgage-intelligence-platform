"""Databricks-backed signal stack repository (audit wow-stage-5).

Reads ``mip.gold.segment_combination_rollup`` -- one row per non-empty exact
set of the six core segment codes a borrower carries, precomputed by the gold
refresh job -- and, in the SAME statement, LEFT JOINs a live contactable
aggregate keyed the same way. Contactability cannot be a gold column: its
predicate has one owner (``eligibility.eligible_sql_predicate``, embedded
verbatim) and reads the current time, exactly as the segment list joins its
live contactable count (``databricks_geo_sql.SEGMENT_LIST_SQL``).

The combination key has ONE owner, ``COMBINATION_KEY_SQL`` below: the gold
CTAS (``sql/transformations/gold_segment_combination_rollup.sql``) carries the
fragment verbatim, so the gold rows and the live aggregate key alike by
construction (``tests/unit/test_segment_combination_sql_contract.py``).

Projection rules (the endpoint's honesty contract):

* codes come from the key, are re-ordered to the core order, and a row whose
  key is empty or names a code outside the core is DROPPED with an
  observability event, never coerced;
* ``contactable`` is clamped to ``0..addressable`` (a live subset joined to a
  precomputed superset can drift between refreshes) and stays None when not
  reported, never 0;
* rows are ordered largest addressable first, then by key;
* only THIS table missing (``TABLE_OR_VIEW_NOT_FOUND`` naming it) is
  ``built=False`` -- the deploy that introduces it promotes the App before the
  refresh builds it. Any other missing object (``borrower_360``, a schema)
  is a real failure and keeps its 503. An empty table is not built either;
* a table that HAS rows, every one of which was dropped, is a broken data
  contract, not "not built": ``SegmentCombinationContractError`` (with the
  per-reason counts, never a key) raised out of the cache factory, so
  stale-if-error keeps serving the last good projection and, without one,
  the route answers a non-retryable 503 (audit wow-stage-5 fold; a
  ``built=True`` empty body would claim nobody fires three signals).

Cache posture matches the geography rollups: ``GoldAggregateCache`` with a 60 s
soft TTL, single-flight and stale-if-error. A cold failure propagates so the
resilience layer answers 503 ``warming_up``. The read writes no audit row.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from backend.schemas.segment_combinations import (
    CORE_SEGMENT_CODES,
    SegmentCombination,
    SegmentCombinationProvenance,
    SegmentCombinationResponse,
)
from backend.services.databricks_sql import DatabricksSqlClient, DatabricksSqlObjectMissingError
from backend.services.databricks_sql_helpers import qualify
from backend.services.eligibility import eligible_sql_predicate
from backend.services.gold_cache import AggregateCache, GoldAggregateCache
from backend.services.observability import emit

log = logging.getLogger("backend.services.repositories.databricks_repo")

_GOLD_SOURCE = qualify("gold", "segment_combination_rollup")
_BORROWER_360 = qualify("gold", "borrower_360")
_CONTACTABLE_SOURCE = f"{_BORROWER_360} (live eligibility predicate, per request)"
# The table's own name as the warehouse quotes it (`mip`.`gold`.`...`) or
# plain: only an object-missing error naming THIS table is "not built".
_GOLD_SOURCE_RE = re.compile(
    r"`?" + r"`?\.`?".join(re.escape(part) for part in _GOLD_SOURCE.split(".")) + r"`?",
    re.IGNORECASE,
)

_PROVENANCE_NOTE = (
    "Exact combinations of the six core segments: each borrower counts once, under "
    "the set of core signals it carries; the S1.3 overlay segments are ignored. "
    "Rebuilt by the gold refresh job; contactable counts are live. Whole book, not "
    "narrowed by any filter."
)

#: The ONE combination key: the distinct core codes a row carries, sorted and
#: joined with '+'. Empty when the row carries no core code.
COMBINATION_KEY_SQL = (
    "array_join(array_sort(array_distinct(filter(segment_codes, c -> c IN "
    "('itm', 'listed', 'permit', 'investor', 'equity', 'retention')))), '+')"
)

# One statement: the gold exact rows LEFT JOIN a live eligible aggregate keyed
# by the same fragment. The eligibility predicate is the unaliased text.
SEGMENT_COMBINATIONS_SQL = (
    "WITH live AS ( "
    f"  SELECT {COMBINATION_KEY_SQL} AS combination_key, COUNT(*) AS contactable "
    f"  FROM {_BORROWER_360} "
    f"  WHERE {eligible_sql_predicate()} "
    "  GROUP BY 1 "
    ") "
    "SELECT "
    "  r.combination_key, "
    "  CAST(r.addressable_borrowers AS BIGINT) AS addressable_borrowers, "
    "  r.refreshed_at, "
    "  CAST(COALESCE(l.contactable, 0) AS BIGINT) AS contactable "
    f"FROM {_GOLD_SOURCE} AS r "
    "LEFT JOIN live AS l ON l.combination_key = r.combination_key "
    "ORDER BY r.addressable_borrowers DESC, r.combination_key"
)


def _int_or_none(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    return int(float(str(value)))


def _timestamp_text(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    return str(value)


def _is_missing_table(exc: BaseException) -> bool:
    """True when the failure is THIS table not existing yet.

    The resilient SQL client wraps the warehouse error in a
    ``DependencyDownError`` (``last_error``); the raw client raises it bare.
    Either way the typed ``DatabricksSqlObjectMissingError`` is on the chain,
    and its message must name ``mip.gold.segment_combination_rollup``: a
    missing schema or any other table is a real failure.
    """
    seen: set[int] = set()
    node: BaseException | None = exc
    while node is not None and id(node) not in seen:
        seen.add(id(node))
        if isinstance(node, DatabricksSqlObjectMissingError) and _GOLD_SOURCE_RE.search(str(node)):
            return True
        next_node = getattr(node, "last_error", None)
        node = next_node if isinstance(next_node, BaseException) else node.__cause__
    return False


def _provenance(refreshed_at: str | None) -> SegmentCombinationProvenance:
    return SegmentCombinationProvenance(
        source=_GOLD_SOURCE,
        contactable_source=_CONTACTABLE_SOURCE,
        refreshed_at=refreshed_at,
        note=_PROVENANCE_NOTE,
    )


def not_built_response() -> SegmentCombinationResponse:
    """The honest answer before the gold refresh has built the table."""
    return SegmentCombinationResponse(
        built=False,
        core_codes=list(CORE_SEGMENT_CODES),
        combinations=[],
        provenance=_provenance(None),
    )


class SegmentCombinationContractError(RuntimeError):
    """The gold table has rows and the projection dropped every one of them.

    Carries only counts: ``row_count`` and ``dropped`` (rows per drop
    reason). A combination key never leaves the repository.
    """

    def __init__(self, row_count: int, dropped: dict[str, int]) -> None:
        self.row_count = row_count
        self.dropped = dict(dropped)
        super().__init__(f"segment combination contract failure: all {row_count} rows dropped")


def _drop(reason: str, key: str, dropped: dict[str, int]) -> None:
    dropped[reason] = dropped.get(reason, 0) + 1
    emit(
        log,
        "segment_combination_row_dropped",
        level=logging.WARNING,
        reason=reason,
        key_length=len(key),
        outcome="dropped",
    )


def project_combinations(rows: list[dict[str, Any]]) -> SegmentCombinationResponse:
    """Validate and order the exact rows; drop (with an event) any row that is not a core set.

    Raises ``SegmentCombinationContractError`` when rows exist but none survives.
    """
    keyed: list[tuple[str, SegmentCombination]] = []
    refreshed_at: str | None = None
    dropped: dict[str, int] = {}
    for row in rows:
        key = str(row.get("combination_key") or "").strip()
        codes = key.split("+") if key else []
        if not codes:
            _drop("empty_key", key, dropped)
            continue
        if any(code not in CORE_SEGMENT_CODES for code in codes) or len(set(codes)) != len(codes):
            _drop("unknown_code", key, dropped)
            continue
        ordered = [code for code in CORE_SEGMENT_CODES if code in codes]
        addressable = max(0, _int_or_none(row.get("addressable_borrowers")) or 0)
        raw_contactable = _int_or_none(row.get("contactable"))
        contactable = None if raw_contactable is None else max(0, min(addressable, raw_contactable))
        keyed.append(
            (
                key,
                SegmentCombination(
                    segment_codes=ordered,
                    signal_count=len(ordered),
                    addressable=addressable,
                    contactable=contactable,
                ),
            )
        )
        refreshed_at = refreshed_at or _timestamp_text(row.get("refreshed_at"))
    if not rows:
        return not_built_response()
    if not keyed:
        emit(
            log,
            "segment_combination_contract_failure",
            level=logging.ERROR,
            row_count=len(rows),
            dropped_empty_key=dropped.get("empty_key", 0),
            dropped_unknown_code=dropped.get("unknown_code", 0),
            outcome="contract_failure",
        )
        raise SegmentCombinationContractError(len(rows), dropped)
    keyed.sort(key=lambda item: (-item[1].addressable, item[0]))
    return SegmentCombinationResponse(
        built=True,
        core_codes=list(CORE_SEGMENT_CODES),
        combinations=[combination for _, combination in keyed],
        provenance=_provenance(refreshed_at),
    )


class DatabricksSegmentCombinationRepository:
    """Typed read model over ``mip.gold.segment_combination_rollup`` + live contactable."""

    _SQL = SEGMENT_COMBINATIONS_SQL
    _CACHE_KEY = "segments.combinations"

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

    def combinations(self) -> SegmentCombinationResponse:
        def build() -> SegmentCombinationResponse:
            try:
                rows = self._client.execute(self._SQL) or []
            except Exception as exc:
                if not _is_missing_table(exc):
                    raise
                emit(log, "segment_combination_table_missing", level=logging.INFO, outcome="not_built")
                return not_built_response()
            return project_combinations(rows)

        result: SegmentCombinationResponse = self._cache.get_or_set(
            self._CACHE_KEY,
            build,
            ttl_s=self._cache_ttl_s,
            stale_if_error=True,
        )
        return result

"""Governed place-dimension values the output-policy scanner disagrees with.

The Genie result grid is scanned cell by cell by the same detectors that guard
model-authored outreach prose. Against a gold ``city`` column those detectors
produce pure false positives, and the block reaches the user as "The generated
response did not pass the governed output policy" with ``unsafe_field:
"table_rows"`` in the server log and nothing else to go on.

Measured on paychex 2026-08-12 against the 428 distinct ``city`` values in
``mip.gold.borrower_360``, exactly three collide:

* ``TACOMA`` — the tumor-suffix heuristic in the health-term bank matches the
  ``-oma`` tail. 17 borrowers.
* ``BLACK DIAMOND`` — matches ``black``. The place-name exemption in
  ``PROTECTED_CLASS_SAFE_CONTEXT_PATTERNS`` already lists
  ``(?:white|black)\\s+(?:...|diamond)`` and DOES clear ``Black Diamond``, but
  the protected-class scan runs against a space-joined superset that includes
  the ASCII-confusable fold (capital ``I`` -> lowercase ``l``). The exemption
  erases ``BLACK DIAMOND`` from that superset and cannot erase the fold's
  ``BLACK DLAMOND``, so a bare ``\\bblack\\b`` survives. Title case has no
  capital ``I`` to fold, which is why ``Black Diamond`` passes and the
  uppercase gold value does not. 3,678 borrowers.
* ``HAWAIIAN GARDENS`` — matches ``hawaiian``. No exemption exists. 2 borrowers.

This module resolves that set from the live governed dimension rather than
pinning three names. A static list would go stale the moment Cotality coverage
refreshes, and CLAUDE.md forbids pinning the product to a fixed geography.

Posture, in order:

1. Live distinct ``city`` values from the governed gold table, filtered to the
   values the structured-cell scan rejects, cached for
   ``_PLACE_DIMENSION_TTL_S``.
2. Last-known-good (stale cache) when the warehouse read fails.
3. Empty set. That is exactly today's behavior — the wide grid blocks — so the
   degraded path is fail-CLOSED, never a silent widening of the guard.

It never raises: a governed-output check must not turn a warehouse hiccup into
a failed request.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from threading import Lock

from backend.services.observability import emit

log = logging.getLogger(__name__)

# The dimension only changes when gold is rebuilt, so a longer TTL than the
# 300s geography footprint is both fresh enough and four times cheaper.
_PLACE_DIMENSION_TTL_S: float = 900.0
# Cache the degraded answer too, briefly. Without this every Genie response
# during a warehouse outage re-runs the read (and its retry/backoff) inside the
# output-policy check, turning a degraded dependency into a latency cliff on
# the surface that is supposed to survive it.
_PLACE_DIMENSION_FAILURE_TTL_S: float = 30.0
_PLACE_DIMENSION_CACHE_KEY: str = "mip.gold.borrower_360.city"

# Defensive bounds. The read is a governed DISTINCT over one low-cardinality
# column (428 values today); anything wildly outside that shape means the read
# returned something other than a city dimension, and the safe response is to
# exempt nothing rather than to trust it.
_MAX_DIMENSION_ROWS: int = 50_000
_MAX_CONFLICTING_VALUES: int = 256


def normalize_place_value(value: str) -> str:
    """Casefold and collapse whitespace for exact governed-value matching."""

    return " ".join(str(value).split()).casefold()


def _default_conflict_predicate(value: str) -> bool:
    """Does the structured-cell scan reject this governed dimension value?

    Imported lazily: the policy module resolves this dimension, so a module
    level import would close a cycle. ``genie_visible_text_unsafe`` never
    consults this resolver itself, so there is no recursion either way.
    """

    from backend.services.genie_message_policy import genie_visible_text_unsafe

    return genie_visible_text_unsafe(value, structured_value=True)


class GovernedPlaceDimensionResolver:
    """Resolve governed city values that the output-policy scanner rejects."""

    def __init__(
        self,
        *,
        ttl_s: float = _PLACE_DIMENSION_TTL_S,
        conflict_predicate: Callable[[str], bool] | None = None,
        dimension_reader: Callable[[], list[str]] | None = None,
    ) -> None:
        from backend.services.resilience import TTLCache

        self._cache: TTLCache = TTLCache(max_entries=4)
        self._ttl_s = ttl_s
        self._conflict_predicate = conflict_predicate or _default_conflict_predicate
        self._dimension_reader = dimension_reader or self._read_dimension
        self._load_lock = Lock()
        self._warned = False

    def _read_dimension(self) -> list[str]:
        """Read distinct governed city values. Raises on any dependency failure."""

        from backend.services.databricks_sql import get_sql_client
        from backend.services.databricks_sql_helpers import qualify

        rows = get_sql_client().execute(
            "SELECT DISTINCT city "
            f"FROM {qualify('gold', 'borrower_360')} "
            "WHERE city IS NOT NULL AND TRIM(city) <> '' "
            "ORDER BY city ASC"
        )
        return [str(row.get("city") or "") for row in (rows or [])[:_MAX_DIMENSION_ROWS]]

    def _load(self) -> frozenset[str]:
        values = self._dimension_reader()
        conflicting = {
            normalize_place_value(value)
            for value in values
            if value.strip() and self._conflict_predicate(value)
        }
        if len(conflicting) > _MAX_CONFLICTING_VALUES:
            # Not a city dimension, or the detectors changed shape. Exempting
            # hundreds of values on that evidence would be a guard rewrite by
            # accident, so exempt none and say so.
            emit(
                log,
                "genie_place_dimension_rejected",
                level=logging.WARNING,
                outcome="degraded",
                dimension_values=len(values),
                conflicting_values=len(conflicting),
                max_conflicting_values=_MAX_CONFLICTING_VALUES,
            )
            return frozenset()
        emit(
            log,
            "genie_place_dimension_loaded",
            level=logging.INFO,
            dimension_values=len(values),
            conflicting_values=len(conflicting),
        )
        return frozenset(conflicting)

    def conflicting_values(self) -> frozenset[str]:
        """Normalized governed city values the structured-cell scan rejects.

        Never raises. Returns an empty set when the dimension is unreachable
        and no last-known-good value is cached.
        """

        cached: frozenset[str] | None = self._cache.get(_PLACE_DIMENSION_CACHE_KEY)
        if cached is not None:
            return cached
        with self._load_lock:
            cached = self._cache.get(_PLACE_DIMENSION_CACHE_KEY)
            if cached is not None:
                return cached
            try:
                loaded = self._load()
            except Exception as exc:  # noqa: BLE001 — governed output must not 500
                if not self._warned:
                    emit(
                        log,
                        "genie_place_dimension_unavailable",
                        level=logging.WARNING,
                        dependency="warehouse",
                        outcome="degraded",
                        exc_type=type(exc).__name__,
                        exc_msg=str(exc)[:500],
                        posture="last_known_good_or_no_exemption",
                    )
                    self._warned = True
                stale = self._cache.get_stale(_PLACE_DIMENSION_CACHE_KEY)
                degraded: frozenset[str] = stale if stale is not None else frozenset()
                self._cache.set(
                    _PLACE_DIMENSION_CACHE_KEY, degraded, _PLACE_DIMENSION_FAILURE_TTL_S
                )
                return degraded
            self._warned = False
            self._cache.set(_PLACE_DIMENSION_CACHE_KEY, loaded, self._ttl_s)
            return loaded

    def invalidate(self) -> None:
        """Drop the cached dimension so the next call re-reads gold."""

        self._cache.invalidate(_PLACE_DIMENSION_CACHE_KEY)
        self._warned = False


_RESOLVER: GovernedPlaceDimensionResolver | None = None
_RESOLVER_LOCK = Lock()


def get_governed_place_dimension() -> GovernedPlaceDimensionResolver:
    global _RESOLVER
    if _RESOLVER is not None:
        return _RESOLVER
    with _RESOLVER_LOCK:
        if _RESOLVER is None:
            _RESOLVER = GovernedPlaceDimensionResolver()
        return _RESOLVER


def _reset_governed_place_dimension_for_tests(
    resolver: GovernedPlaceDimensionResolver | None = None,
) -> None:
    """Test helper: swap (or clear) the process-wide resolver."""

    global _RESOLVER
    with _RESOLVER_LOCK:
        _RESOLVER = resolver

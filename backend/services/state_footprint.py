"""State coverage resolver.

Single source of truth for the geography currently present in the refreshed
gold data. The authoritative data-bearing scope comes from
``mip.gold.county_rollup``. ``mip.ref.state_footprint`` is only label/default
metadata and an outage fallback; it must never keep stale states in scope when
the Cotality share expands or contracts.

Consumers:

- ``backend/api/config.py`` (``/api/config/footprint``) — frontend hydration.
- ``backend/services/repositories/databricks_repo.py`` — the ``"all N
  states"`` option reads from refreshed coverage rather than hardcoding a
  state list.
- Admin restart invalidates the cache (``invalidate()``), matching the
  manual-flush posture we use for ``LenderRefResolver``.

The generic fallback is not a license or data-coverage statement; it is only
an outage posture. Normal product behavior comes from live gold geography
rollups, with the ref table used only to improve labels/order when live rows
exist.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from threading import Lock

from backend.schemas._validators_tenant import set_state_footprint_provider
from backend.schemas.usps import US_STATE_NAME_BY_CODE
from backend.services.observability import emit

log = logging.getLogger(__name__)


def _emit_footprint_warning(event: str, exc: BaseException, *, posture: str) -> None:
    emit(
        log,
        event,
        level=logging.WARNING,
        dependency="warehouse",
        outcome="degraded",
        exc_type=type(exc).__name__,
        exc_msg=str(exc)[:500],
        posture=posture,
    )

# Generic fallback coverage metadata. This is stable geography metadata, not
# the current lender footprint and not the current Cotality share scope. It is
# used only when both live geography coverage and UC metadata are unreachable,
# so the app can show an explicit degraded state without pretending fallback
# rows are data-bearing scope.
_FOOTPRINT_FALLBACK: tuple[tuple[str, str, int, bool], ...] = tuple(
    (code, name, idx, False)
    for idx, (code, name) in enumerate(
        ((c, n) for c, n in US_STATE_NAME_BY_CODE.items() if c != "DC"),
        start=1,
    )
)

# 5-minute TTL per hole-finder #20 (300s). Admin restart invalidates; a
# full live-edit flow is a later slice.
_FOOTPRINT_TTL_S: float = 300.0
_FOOTPRINT_CACHE_KEY: str = "mip.ref.state_footprint"


@dataclass(frozen=True)
class FootprintState:
    """One row of current geography coverage or fallback metadata."""

    state_code: str
    state_name: str
    display_order: int
    is_default_state: bool


class StateFootprintResolver:
    """Resolve current geography coverage from ``mip.gold.county_rollup``.

    Behavior mirrors ``LenderRefResolver`` in
    ``backend.services.pii_redaction``:

    1. Cached live gold coverage (TTL 300s) if refreshed coverage exists.
    2. UC metadata rows only as degraded metadata when gold coverage is empty
       or unavailable.
    3. Generic ``_FOOTPRINT_FALLBACK`` if both UC sources are down. One
       WARNING per resolver lifetime so logs don't spam.

    Never raises. Always returns a non-empty list.
    """

    def __init__(
        self,
        *,
        ttl_s: float = _FOOTPRINT_TTL_S,
        fallback: tuple[tuple[str, str, int, bool], ...] | None = None,
    ) -> None:
        from backend.services.resilience import TTLCache

        self._cache: TTLCache = TTLCache()
        self._ttl_s = ttl_s
        self._fallback = fallback if fallback is not None else _FOOTPRINT_FALLBACK
        self._load_lock = Lock()
        self._warned_fallback = False
        self._source_status = "unknown"

    def _load_from_uc(self) -> list[FootprintState] | None:
        """Return refreshed Cotality coverage when it exists.

        ``mip.gold.county_rollup`` is authoritative for data-bearing scope.
        ``mip.ref.state_footprint`` supplies optional display metadata only.
        If coverage is unavailable but metadata exists, return metadata rows
        with ``_source_status = "metadata_only"`` so consumers can render a
        truthful degraded state without answering state-scoped data questions.
        """
        try:
            from backend.services.databricks_sql import (
                DatabricksSqlError,
                get_sql_client,
            )
            from backend.services.databricks_sql_helpers import qualify
            from backend.services.resilience import DependencyDownError

            client = get_sql_client()
        except (DependencyDownError, DatabricksSqlError, RuntimeError, OSError) as exc:
            if not self._warned_fallback:
                _emit_footprint_warning(
                    "state_footprint_uc_client_failed",
                    exc,
                    posture="fallback_footprint",
                )
                self._warned_fallback = True
            return None
        except Exception as exc:  # noqa: BLE001
            if not self._warned_fallback:
                _emit_footprint_warning(
                    "state_footprint_uc_client_unexpected",
                    exc,
                    posture="fallback_footprint",
                )
                self._warned_fallback = True
            return None

        try:
            rows = client.execute(
                "SELECT state_code, state_name, display_order, is_default_state "
                f"FROM {qualify('ref', 'state_footprint')} "
                "ORDER BY display_order ASC"
            )
        except (DependencyDownError, DatabricksSqlError, RuntimeError, OSError) as exc:
            if not self._warned_fallback:
                _emit_footprint_warning(
                    "state_footprint_metadata_failed",
                    exc,
                    posture="live_coverage_names",
                )
                self._warned_fallback = True
            rows = []
        except Exception as exc:  # noqa: BLE001
            if not self._warned_fallback:
                _emit_footprint_warning(
                    "state_footprint_metadata_unexpected",
                    exc,
                    posture="live_coverage_names",
                )
                self._warned_fallback = True
            rows = []

        try:
            coverage_rows = client.execute(
                "SELECT state, SUM(addressable_borrowers) AS addressable_borrowers "
                f"FROM {qualify('gold', 'county_rollup')} "
                f"WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM {qualify('gold', 'county_rollup')}) "
                "  AND state IS NOT NULL "
                "GROUP BY state "
                "ORDER BY addressable_borrowers DESC, state ASC"
            )
        except Exception as exc:  # noqa: BLE001
            if not self._warned_fallback:
                _emit_footprint_warning(
                    "state_footprint_coverage_failed",
                    exc,
                    posture="metadata_only_geography",
                )
                self._warned_fallback = True
            coverage_rows = []

        metadata: dict[str, FootprintState] = {}
        metadata_rows: list[FootprintState] = []
        for row in rows:
            code = row.get("state_code")
            name = row.get("state_name")
            order = row.get("display_order")
            default = row.get("is_default_state")
            if not code or not name:
                continue
            parsed = FootprintState(
                state_code=str(code).strip().upper(),
                state_name=str(name),
                display_order=int(order) if order is not None else 0,
                is_default_state=bool(default),
            )
            metadata[parsed.state_code] = parsed
            metadata_rows.append(parsed)

        coverage: list[FootprintState] = []
        seen: set[str] = set()
        for idx, row in enumerate(coverage_rows or [], start=1):
            code = str(row.get("state") or "").strip().upper()[:2]
            if len(code) != 2 or code in seen:
                continue
            meta = metadata.get(code)
            coverage.append(
                FootprintState(
                    state_code=code,
                    state_name=meta.state_name if meta else US_STATE_NAME_BY_CODE.get(code, code),
                    display_order=idx,
                    is_default_state=len(coverage) == 0,
                )
            )
            seen.add(code)
        if coverage:
            self._source_status = "live_coverage"
            return coverage
        if metadata_rows:
            self._source_status = "metadata_only"
            return metadata_rows
        return None

    def _footprint(self) -> list[FootprintState]:
        cached = self._cache.get(_FOOTPRINT_CACHE_KEY)
        if cached is not None:
            return cached  # type: ignore[no-any-return]
        with self._load_lock:
            cached = self._cache.get(_FOOTPRINT_CACHE_KEY)
            if cached is not None:
                return cached  # type: ignore[no-any-return]
            loaded = self._load_from_uc()
            if loaded is None:
                loaded = [
                    FootprintState(code, name, order, default)
                    for code, name, order, default in self._fallback
                ]
                self._source_status = "fallback"
            elif self._source_status == "unknown":
                self._source_status = "live_coverage"
            self._cache.set(_FOOTPRINT_CACHE_KEY, loaded, self._ttl_s)
            return loaded

    def list(self) -> list[FootprintState]:
        """Return current coverage rows, sorted by ``display_order``."""
        return list(self._footprint())

    def state_codes(self) -> list[str]:
        """Return just the USPS codes, sorted by ``display_order``."""
        return [s.state_code for s in self._footprint()]

    def state_name_to_codes(self) -> dict[str, list[str]]:
        """Return a lowercased ``state_name -> [state_code]`` map.

        Used by the portfolio builder preview predicate to translate
        frontend dropdown labels like "Florida" / "California" to the
        2-char USPS codes emitted into the WHERE clause. Keys are
        lowercased so the lookup is case-insensitive regardless of how
        the UI cases the label. Each value is a single-element list so
        callers can build SQL predicates without branching on shape.
        """
        return {s.state_name.lower(): [s.state_code] for s in self._footprint()}

    def default_state_code(self) -> str | None:
        """Return the USPS code of the row with ``is_default_state = TRUE``.

        If no row is flagged default, fall through to the first row by
        display order — but ONLY when the active list came from Unity
        Catalog (live gold coverage or the ``ref.state_footprint`` metadata
        table). The UI's broad default is still "All N states"; this is only
        anchor metadata for APIs that require a single state.

        Returns ``None`` on the generic outage fallback. That list is the
        alphabetical 50-state dictionary, so "first by display order" meant
        Alabama — a state with zero borrowers in the current share, named as
        THE default purely because it sorts first (2026-08-07 platform audit).
        There is no default state when there is no footprint, and callers
        that require a single state must handle its absence rather than
        receive a fabricated one. ``using_fallback()`` reports the same
        condition; this method just stops papering over it.
        """
        rows = self._footprint()
        for s in rows:
            if s.is_default_state:
                return s.state_code
        if self._source_status == "fallback":
            return None
        return rows[0].state_code

    def using_fallback(self) -> bool:
        """Return TRUE when the active list is metadata only.

        Metadata is useful for rendering degraded geography chrome during a
        dependency outage, but it is not a Cotality coverage contract.
        Data-bearing decisions, especially Genie out-of-footprint guards,
        should treat this as unavailable scope rather than broadening answers.
        """
        self._footprint()
        return self._source_status in {"fallback", "metadata_only"}

    def invalidate(self) -> None:
        """Drop the cached footprint so the next call re-fetches from UC."""
        self._cache.invalidate(_FOOTPRINT_CACHE_KEY)
        self._warned_fallback = False
        self._source_status = "unknown"


# Process-wide singleton, lazy.
_RESOLVER: StateFootprintResolver | None = None
_RESOLVER_LOCK = Lock()


def get_state_footprint_resolver() -> StateFootprintResolver:
    global _RESOLVER
    if _RESOLVER is not None:
        return _RESOLVER
    with _RESOLVER_LOCK:
        if _RESOLVER is None:
            _RESOLVER = StateFootprintResolver()
        return _RESOLVER


def _reset_state_footprint_resolver_for_tests(
    resolver: StateFootprintResolver | None = None,
) -> None:
    """Test helper: swap (or clear) the process-wide resolver."""
    global _RESOLVER
    with _RESOLVER_LOCK:
        _RESOLVER = resolver


def _schema_state_footprint_provider() -> tuple[tuple[tuple[str, str], ...], bool]:
    resolver = get_state_footprint_resolver()
    states = tuple((state.state_code, state.state_name) for state in resolver.list())
    return states, resolver.using_fallback()


set_state_footprint_provider(_schema_state_footprint_provider)

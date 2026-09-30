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
- ``backend/schemas/_validators_tenant.py`` (the schema validators) and the
  Genie footprint guards read ONE ``snapshot()`` per decision, so the codes
  and the degraded flag always come from the same load.

Cache posture (audit ``delivery-06``): a ``GoldAggregateCache`` holding one
snapshot (the rows AND their status, so a caller never mixes one refresh's
rows with another's flag). Past 80% of the TTL (240 s) it is served stale
while one background refresh re-reads UC; at the TTL (300 s) it is recomputed
inline, so the guards and validators never see coverage older than before.
There is no ``stale_if_error``: a degraded outcome is a VALUE, so a refresh
that degrades REPLACES the live snapshot and ``using_fallback()`` flips on
the next read.

The generic fallback is not a license or data-coverage statement; it is only
an outage posture. Normal product behavior comes from live gold geography
rollups, with the ref table used only to improve labels/order when live rows
exist.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from threading import Lock
from typing import TYPE_CHECKING

from backend.schemas._validators_tenant import set_state_footprint_provider
from backend.schemas.usps import US_STATE_NAME_BY_CODE
from backend.services.observability import emit

if TYPE_CHECKING:
    from backend.services.gold_cache import GoldAggregateCache

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
# Served stale-while-revalidate after this share of the TTL; recomputed
# inline at the TTL itself, so no reader sees older coverage than before.
_FOOTPRINT_SOFT_TTL_FRACTION: float = 0.8
_FOOTPRINT_CACHE_KEY: str = "mip.ref.state_footprint"
# Statuses that are not a Cotality coverage contract.
_DEGRADED_STATUSES = frozenset({"fallback", "metadata_only"})


@dataclass(frozen=True)
class FootprintState:
    """One row of current geography coverage or fallback metadata."""

    state_code: str
    state_name: str
    display_order: int
    is_default_state: bool


@dataclass(frozen=True)
class FootprintSnapshot:
    """One load's rows and the status they were loaded under, cached together.

    A decision that needs two footprint facts (the codes AND the degraded
    flag, say) reads both from ONE snapshot: a background refresh landing
    between two resolver reads could otherwise pair live rows with a
    fallback flag, which is how a guard fails open (audit ``delivery-06``,
    decision record e2).
    """

    rows: tuple[FootprintState, ...]
    status: str

    @property
    def degraded(self) -> bool:
        return self.status in _DEGRADED_STATUSES

    def codes(self) -> list[str]:
        """Return just the USPS codes, sorted by ``display_order``."""
        return [s.state_code for s in self.rows]

    def name_to_codes(self) -> dict[str, list[str]]:
        """Return a lowercased ``state_name -> [state_code]`` map.

        Used by the portfolio builder preview predicate to translate
        frontend dropdown labels like "Florida" / "California" to the
        2-char USPS codes emitted into the WHERE clause. Keys are
        lowercased so the lookup is case-insensitive regardless of how
        the UI cases the label. Each value is a single-element list so
        callers can build SQL predicates without branching on shape.
        """
        return {s.state_name.lower(): [s.state_code] for s in self.rows}

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
        receive a fabricated one. ``degraded`` reports the same condition;
        this method just stops papering over it.
        """
        for s in self.rows:
            if s.is_default_state:
                return s.state_code
        if self.status == "fallback":
            return None
        return self.rows[0].state_code


# Pre-2026-09-30 private name, kept so existing imports keep resolving.
_FootprintSnapshot = FootprintSnapshot


class StateFootprintResolver:
    """Resolve current geography coverage from ``mip.gold.county_rollup``.

    Behavior mirrors ``LenderRefResolver`` in
    ``backend.services.pii_redaction``:

    1. Cached live gold coverage (TTL 300s; stale-while-revalidate after
       240 s) if refreshed coverage exists.
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
        cache: GoldAggregateCache | None = None,
    ) -> None:
        from backend.services.gold_cache import GoldAggregateCache

        self._cache: GoldAggregateCache = cache if cache is not None else GoldAggregateCache()
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

    def snapshot(self) -> FootprintSnapshot:
        """The cached rows and their status, read together.

        A decision that needs two footprint facts must take ONE snapshot and
        read both from it; the wrappers below each take their own.
        """
        snapshot: FootprintSnapshot = self._cache.get_or_set(
            _FOOTPRINT_CACHE_KEY,
            self._load_snapshot,
            ttl_s=_FOOTPRINT_SOFT_TTL_FRACTION * self._ttl_s,
            stale_if_error=False,
            hard_ttl_s=self._ttl_s,
        )
        return snapshot

    def _load_snapshot(self) -> FootprintSnapshot:
        """Load once (inline or as the background refresh) under the load lock.

        ``_load_from_uc`` handles its own warehouse failures (it answers
        ``None`` or metadata rows), so a degraded outcome comes back as a
        snapshot VALUE that replaces the cached one.
        """
        with self._load_lock:
            self._source_status = "unknown"
            loaded = self._load_from_uc()
            if loaded is None:
                rows = tuple(
                    FootprintState(code, name, order, default)
                    for code, name, order, default in self._fallback
                )
                status = "fallback"
            else:
                rows = tuple(loaded)
                status = "live_coverage" if self._source_status == "unknown" else self._source_status
            self._source_status = status
            return FootprintSnapshot(rows=rows, status=status)

    # Single-read conveniences: each one takes its own snapshot(). A decision
    # that needs two facts must take snapshot() and read both from it
    # (tests/unit/test_footprint_single_read.py pins that).

    def list(self) -> list[FootprintState]:
        """Current coverage rows, sorted by ``display_order``.

        Single-read convenience; a decision that needs two facts must take
        snapshot().
        """
        return list(self.snapshot().rows)

    def state_codes(self) -> list[str]:
        """``FootprintSnapshot.codes()`` of a fresh snapshot.

        Single-read convenience; a decision that needs two facts must take
        snapshot().
        """
        return self.snapshot().codes()

    def state_name_to_codes(self) -> dict[str, list[str]]:
        """``FootprintSnapshot.name_to_codes()`` of a fresh snapshot.

        Single-read convenience; a decision that needs two facts must take
        snapshot().
        """
        return self.snapshot().name_to_codes()

    def default_state_code(self) -> str | None:
        """``FootprintSnapshot.default_state_code()`` of a fresh snapshot.

        Single-read convenience; a decision that needs two facts must take
        snapshot().
        """
        return self.snapshot().default_state_code()

    def using_fallback(self) -> bool:
        """TRUE when the active list is metadata only (``degraded``).

        Metadata is useful for rendering degraded geography chrome during a
        dependency outage, but it is not a Cotality coverage contract.
        Data-bearing decisions, especially Genie out-of-footprint guards,
        should treat this as unavailable scope rather than broadening answers.
        Single-read convenience; a decision that needs two facts must take
        snapshot().
        """
        return self.snapshot().degraded

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
    # ONE snapshot for both: a refresh landing between two reads could
    # otherwise pair live rows with a fallback flag (or the reverse).
    snapshot = get_state_footprint_resolver().snapshot()
    states = tuple((state.state_code, state.state_name) for state in snapshot.rows)
    return states, snapshot.degraded


set_state_footprint_provider(_schema_state_footprint_provider)

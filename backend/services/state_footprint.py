"""State footprint resolver.

Single source of truth for the tenant's operational footprint (the set of
US states the lender writes business in). Reads from
``mip.ref.state_footprint`` with a TTL-cached in-process copy, falling back
to the canonical 6-state Summit footprint on UC unavailability so the app
never ships an empty dropdown.

Consumers:

- ``backend/api/config.py`` (``/api/config/footprint``) — frontend hydration.
- ``backend/services/repositories/databricks_repo.py`` — the
  ``"all N states"`` option in ``_STATE_SETS`` reads from here rather than
  hardcoding the 6-state literal.
- Admin restart invalidates the cache (``invalidate()``), matching the
  manual-flush posture we use for ``LenderRefResolver``.

The fallback mirrors ``sql/ref/state_footprint_seed.sql`` byte-for-byte
so a UC outage on first boot still yields the correct behavior for the
default tenant. A tenant with a different footprint MUST re-run the seed
before the UC path activates.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from threading import Lock

log = logging.getLogger(__name__)


# Fallback footprint. Mirrors the rows seeded by
# ``sql/ref/state_footprint_seed.sql`` — keep the two in lockstep.
_FOOTPRINT_FALLBACK: tuple[tuple[str, str, int, bool], ...] = (
    ("IL", "Illinois",   1, True),
    ("CA", "California", 2, False),
    ("FL", "Florida",    3, False),
    ("TX", "Texas",      4, False),
    ("WA", "Washington", 5, False),
    ("CO", "Colorado",   6, False),
)

# 5-minute TTL per hole-finder #20 (300s). Admin restart invalidates; a
# full live-edit flow is a later slice.
_FOOTPRINT_TTL_S: float = 300.0
_FOOTPRINT_CACHE_KEY: str = "mip.ref.state_footprint"


@dataclass(frozen=True)
class FootprintState:
    """One row of the tenant footprint."""

    state_code: str
    state_name: str
    display_order: int
    is_default_state: bool


class StateFootprintResolver:
    """Resolve the tenant footprint from ``mip.ref.state_footprint``.

    Behavior mirrors ``LenderRefResolver`` in
    ``backend.services.pii_redaction``:

    1. Cached UC result (TTL 300s) if UC is reachable.
    2. ``_FOOTPRINT_FALLBACK`` tuple if UC is down. One WARNING per
       resolver lifetime so logs don't spam.

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

    def _load_from_uc(self) -> list[FootprintState] | None:
        """Return the list pulled from UC, or ``None`` on any failure."""
        try:
            from backend.services.databricks_sql import (
                DatabricksSqlError,
                get_sql_client,
            )
            from backend.services.databricks_sql_helpers import qualify
            from backend.services.resilience import DependencyDownError

            client = get_sql_client()
            rows = client.execute(
                "SELECT state_code, state_name, display_order, is_default_state "
                f"FROM {qualify('ref', 'state_footprint')} "
                "ORDER BY display_order ASC"
            )
        except (DependencyDownError, DatabricksSqlError, RuntimeError, OSError) as exc:
            if not self._warned_fallback:
                log.warning(
                    "StateFootprintResolver: UC load failed (%s: %s); "
                    "falling back to in-process _FOOTPRINT_FALLBACK.",
                    type(exc).__name__,
                    exc,
                )
                self._warned_fallback = True
            return None
        except Exception as exc:  # noqa: BLE001
            if not self._warned_fallback:
                log.warning(
                    "StateFootprintResolver: unexpected UC error (%s); "
                    "using fallback footprint.",
                    exc,
                )
                self._warned_fallback = True
            return None

        if not rows:
            return None
        out: list[FootprintState] = []
        for row in rows:
            code = row.get("state_code")
            name = row.get("state_name")
            order = row.get("display_order")
            default = row.get("is_default_state")
            if not code or not name:
                continue
            out.append(
                FootprintState(
                    state_code=str(code).strip().upper(),
                    state_name=str(name),
                    display_order=int(order) if order is not None else 0,
                    is_default_state=bool(default),
                )
            )
        return out or None

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
            self._cache.set(_FOOTPRINT_CACHE_KEY, loaded, self._ttl_s)
            return loaded

    def list(self) -> list[FootprintState]:
        """Return the current footprint, sorted by ``display_order``."""
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
        callers can treat this alongside the multi-state MSA combos
        without branching on shape.
        """
        return {s.state_name.lower(): [s.state_code] for s in self._footprint()}

    def default_state_code(self) -> str:
        """Return the USPS code of the row with ``is_default_state = TRUE``.

        If no row is flagged default (should not happen — the seed
        guarantees exactly one), return the first row by display order.
        """
        for s in self._footprint():
            if s.is_default_state:
                return s.state_code
        return self._footprint()[0].state_code

    def invalidate(self) -> None:
        """Drop the cached footprint so the next call re-fetches from UC."""
        self._cache.invalidate(_FOOTPRINT_CACHE_KEY)
        self._warned_fallback = False


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

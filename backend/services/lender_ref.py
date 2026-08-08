"""Lender-vocabulary resolution for the repository redaction boundary.

Split out of ``backend.services.pii_redaction`` on 2026-08-08: that module
crossed the 900-line monolith gate, and "which public alias does this raw
servicer string map to" is a separate concern from "which fields may leave a
repository". The redaction module re-exports every name below, so no caller
or test import path changes.

Competitor servicers are aliases, not public brand names, unless Cotality and
the lender approve raw lender display for an internal walkthrough. Resolution
is UC-backed with a deterministic in-process fallback, so a warehouse glitch
degrades the vocabulary but never breaks redaction.
"""
from __future__ import annotations

import logging
from threading import Lock

from backend.services.observability import emit

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lender vocabulary. Keys are the uppercase share strings; values are the
# public-demo-safe customer-facing labels. Competitor servicers are aliases,
# not public brand names, unless Cotality and the lender approve raw lender
# display for an internal walkthrough.
#
# Slice13-accuracy: the authoritative source of truth is the Unity Catalog
# table `mip.ref.lender_dictionary` (see `sql/ddl/004_ref_tables.sql` +
# `sql/ref/lender_dictionary_seed.sql`). `LenderRefResolver` loads from UC
# on first call and caches in-process for 15 minutes; `_LENDER_REF_MAP`
# below is the deterministic fallback used when:
#
#   (1) the UC warehouse is unreachable or the breaker is open, so the
#       app keeps redacting correctly during a SQL-plane glitch;
#   (2) local dev / unit tests where no DATABRICKS_* env vars are set;
#   (3) the first app boot before the `mip_ref_seed` job has completed.
#
# Keep this dict in sync with the seed SQL for (1) and (2) -- the unit
# test `test_lender_ref_map_matches_seed_sql` (future slice) will enforce
# that invariant mechanically. Today, the sync is a documentation
# contract: if you add a row to the seed SQL, add it here too.
# ---------------------------------------------------------------------------

_LENDER_REF_MAP: dict[str, str] = {
    "UNITED WHOLESALE MTG": "Competitor A",
    "WELLS FARGO BK NA": "Competitor B",
    "JPMORGAN CHASE BK NA": "Competitor C",
    "ROCKET MTG LLC": "Competitor D",
    "QUICKEN LNS": "Competitor E",
    "BANK OF AMERICA NA": "Competitor F",
    "GUARANTEED RATE INC": "Competitor G",
    "LOANDEPOT.COM LLC": "Competitor H",
    "CALIBER HM LOANS INC": "Competitor I",
    "FAIRWAY INDEPENDENT MTG CORP": "Competitor J",
    "SUMMIT MTG": "Summit Mortgage",
    "SUMMIT MORTGAGE": "Summit Mortgage",
    "SUMMIT MTG CORP": "Summit Mortgage",
    "SUMMIT MORTGAGE CORP": "Summit Mortgage",
}


# ---------------------------------------------------------------------------
# LenderRefResolver: UC-backed lookup with in-process TTL cache + fallback.
# ---------------------------------------------------------------------------

# Default TTL for the resolver's in-process cache. 15 minutes balances
# "analysts can land a seed-SQL edit and see it take effect within a
# dossier refresh" against "don't hammer the warehouse with a lookup for
# every evidence row." Overridable via constructor for tests.
_LENDER_RESOLVER_TTL_S: float = 15 * 60.0
_LENDER_RESOLVER_CACHE_KEY: str = "mip.ref.lender_dictionary"


class LenderRefResolver:
    """Resolve raw share lender strings to polished display labels.

    Load order:

    1. On first ``resolve()`` (or after TTL expiry), try to load the full
       dictionary from ``mip.ref.lender_dictionary`` via the process-wide
       Databricks SQL client. The result is cached in a ``TTLCache`` for
       ``ttl_s`` seconds (default 15 min).
    2. On UC unavailable (missing creds, ``DependencyDownError`` from the
       circuit breaker, any ``DatabricksSqlError``), fall back to the
       in-process ``_LENDER_REF_MAP`` constant and log a single WARNING
       so operators know the live vocabulary is stale.
    3. On an unknown raw string (not in UC result, not in fallback),
       return ``Competitor Other`` rather than title-casing the raw name.

    Thread safety: ``TTLCache`` guards concurrent get/set. The "am I
    loading right now" flag is advisory -- a concurrent second loader
    will just run the same query twice and overwrite the same cache
    entry, which is benign.
    """

    def __init__(
        self,
        *,
        ttl_s: float = _LENDER_RESOLVER_TTL_S,
        fallback: dict[str, str] | None = None,
    ) -> None:
        # Local import to avoid a hard coupling at module import time
        # (tests that never touch the resolver must not pay the
        # `resilience.TTLCache` construction cost).
        from backend.services.resilience import TTLCache

        self._cache: TTLCache = TTLCache()
        self._ttl_s = ttl_s
        self._fallback = fallback if fallback is not None else _LENDER_REF_MAP
        self._load_lock = Lock()
        # Sticky flag: TRUE once we've emitted the "falling back" warning
        # so we don't spam logs on every single ``resolve()`` call.
        self._warned_fallback = False

    def _load_from_uc(self) -> dict[str, str] | None:
        """Return the dict pulled from UC, or ``None`` on any failure.

        Never raises -- every exception is swallowed and callers fall
        back to ``_LENDER_REF_MAP``. A one-time WARNING is emitted on
        the first failure so ops know the vocabulary went stale.
        """
        try:
            from backend.services.databricks_sql import (
                DatabricksSqlError,
                get_sql_client,
            )
            from backend.services.databricks_sql_helpers import qualify
            from backend.services.resilience import DependencyDownError

            client = get_sql_client()
            rows = client.execute(
                f"SELECT raw_key, display_name FROM {qualify('ref', 'lender_dictionary')}"
            )
        except (DependencyDownError, DatabricksSqlError, RuntimeError, OSError) as exc:
            if not self._warned_fallback:
                emit(
                    log,
                    "lender_ref_dictionary_load_failed",
                    level=logging.WARNING,
                    dependency="warehouse",
                    outcome="degraded",
                    exc_type=type(exc).__name__,
                    exc_msg=str(exc)[:500],
                    posture="fallback_dictionary",
                )
                self._warned_fallback = True
            return None
        except Exception as exc:  # noqa: BLE001 -- paranoid fallback path
            if not self._warned_fallback:
                emit(
                    log,
                    "lender_ref_dictionary_unexpected_error",
                    level=logging.WARNING,
                    dependency="warehouse",
                    outcome="degraded",
                    exc_type=type(exc).__name__,
                    exc_msg=str(exc)[:500],
                    posture="fallback_dictionary",
                )
                self._warned_fallback = True
            return None

        if not rows:
            return None
        out: dict[str, str] = {}
        for row in rows:
            raw = row.get("raw_key")
            disp = row.get("display_name")
            if raw and disp:
                out[str(raw).strip().upper()] = str(disp)
        return out or None

    def _dictionary(self) -> dict[str, str]:
        """Return the active dict -- cached UC result OR the fallback."""
        cached = self._cache.get(_LENDER_RESOLVER_CACHE_KEY)
        if cached is not None:
            return cached
        with self._load_lock:
            # Double-checked: another thread may have just loaded.
            cached = self._cache.get(_LENDER_RESOLVER_CACHE_KEY)
            if cached is not None:
                return cached
            loaded = self._load_from_uc()
            active = loaded if loaded is not None else dict(self._fallback)
            self._cache.set(_LENDER_RESOLVER_CACHE_KEY, active, self._ttl_s)
            return active

    def resolve(self, raw: str | None) -> str | None:
        """Map ``raw`` to the polished label (None / empty pass through)."""
        if raw is None:
            return None
        key = raw.strip().upper()
        if not key:
            return None
        mapping = self._dictionary()
        if key in mapping:
            return mapping[key]
        return "Competitor Other"

    def invalidate(self) -> None:
        """Drop the cached dictionary so the next call re-fetches from UC.

        Test helper + the operator "force refresh" button when analysts
        land a new seed row and want it live immediately.
        """
        self._cache.invalidate(_LENDER_RESOLVER_CACHE_KEY)
        self._warned_fallback = False


# Process-wide singleton. Lazy so tests that never call ``generalize_lender``
# never construct a ``TTLCache``.
_RESOLVER: LenderRefResolver | None = None
_RESOLVER_LOCK = Lock()


def get_lender_resolver() -> LenderRefResolver:
    global _RESOLVER
    if _RESOLVER is not None:
        return _RESOLVER
    with _RESOLVER_LOCK:
        if _RESOLVER is None:
            _RESOLVER = LenderRefResolver()
        return _RESOLVER


def _reset_lender_resolver_for_tests(
    resolver: LenderRefResolver | None = None,
) -> None:
    """Test helper: swap the process-wide resolver (or clear it)."""
    global _RESOLVER
    with _RESOLVER_LOCK:
        _RESOLVER = resolver

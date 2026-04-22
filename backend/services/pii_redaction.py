"""Repository-boundary PII redaction for the Module 0 real-data path.

Every row that leaves a ``Databricks*Repository`` passes through one of
the functions below. The policy is spelled out in
``docs/governance-real-data-review.md`` §1 and mirrored in the gold DDL
(``sql/transformations/gold_borrower_360.sql``): raw names, mailing
addresses, street addresses, and competitively-sensitive lender strings
never reach an ``/api/*`` response. This module enforces that at the
Python boundary so a future edit to the gold view (or an accidental
``SELECT *``) cannot leak raw PII downstream.

Hard rules encoded here:

- ``display_name``: synthesized from the first 8 hex chars of
  ``owner_name_hash`` -- ``"Owner " + prefix``. Never the real name.
- ``subject_property``: ``"{city}, {state} {zip5}"`` only. No street,
  no unit, no parcel id.
- ``lat/lon``: block-level (the share's coarsest granularity); passed
  through without modification. Finer geo is not in gold.
- Lender names: generalized through ``_LENDER_REF_MAP``. Unknown
  lenders are title-cased. The raw uppercase servicer strings never
  cross the boundary.

Forbidden keys (enforced by ``tests/unit/test_pii_redaction.py`` and
``tests/integration/test_api_pii_boundary.py``): ``owner_1_full_name``,
``situs_street_address``, ``mailing_street_address``,
``owner_name_hash_raw``, ``trigger_timeline_json``.
"""
from __future__ import annotations

import logging
import re
from threading import Lock
from typing import Any

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lender vocabulary. Keys are the uppercase share strings; values are the
# polished customer-facing labels. Unknown lenders fall back to title-case.
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
    "UNITED WHOLESALE MTG": "United Wholesale Mortgage",
    "WELLS FARGO BK NA": "Wells Fargo Bank",
    "JPMORGAN CHASE BK NA": "JPMorgan Chase",
    "ROCKET MTG LLC": "Rocket Mortgage",
    "QUICKEN LNS": "Quicken Loans",
    "BANK OF AMERICA NA": "Bank of America",
    "GUARANTEED RATE INC": "Guaranteed Rate",
    "LOANDEPOT.COM LLC": "loanDepot",
    "CALIBER HM LOANS INC": "Caliber Home Loans",
    "FAIRWAY INDEPENDENT MTG CORP": "Fairway Independent Mortgage",
    "SUMMIT MTG": "Summit Mortgage",
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
       return title-cased raw (existing behavior).

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
            from backend.services.resilience import DependencyDownError

            client = get_sql_client()
            rows = client.execute(
                "SELECT raw_key, display_name FROM mip.ref.lender_dictionary"
            )
        except (DependencyDownError, DatabricksSqlError, RuntimeError, OSError) as exc:
            if not self._warned_fallback:
                log.warning(
                    "LenderRefResolver: UC load failed (%s: %s); "
                    "falling back to in-process _LENDER_REF_MAP.",
                    type(exc).__name__,
                    exc,
                )
                self._warned_fallback = True
            return None
        except Exception as exc:  # noqa: BLE001 -- paranoid fallback path
            if not self._warned_fallback:
                log.warning(
                    "LenderRefResolver: unexpected UC error (%s); "
                    "using fallback dictionary.",
                    exc,
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
            return cached  # type: ignore[no-any-return]
        with self._load_lock:
            # Double-checked: another thread may have just loaded.
            cached = self._cache.get(_LENDER_RESOLVER_CACHE_KEY)
            if cached is not None:
                return cached  # type: ignore[no-any-return]
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
        return raw.strip().title()

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


# Keys that must NEVER appear in the output dict. Enforced with an
# assertion below and re-asserted in tests.
_FORBIDDEN_OUTPUT_KEYS: frozenset[str] = frozenset(
    {
        "owner_1_full_name",
        "owner_full_name_raw",
        "situs_street_address",
        "situs_street_address_raw",
        "mailing_street_address",
        "mailing_street_raw",
        "mailing_city",
        "mailing_state",
        "owner_name_hash_raw",
        "trigger_timeline_json",   # raw JSON string; router gets the parsed struct
        "buyer_1_full_name",
        "buyer_full_name_raw",
    }
)


_STREET_NUMBER_PATTERN = re.compile(r"\d")


# ---------------------------------------------------------------------------
# Public helpers (exported)
# ---------------------------------------------------------------------------


def generalize_lender(raw: str | None) -> str | None:
    """Map a raw share lender string to the customer-facing label.

    ``None`` / empty strings pass through -- the caller owns whether to
    emit the evidence at all. The uppercase keying is intentional: the
    share mixes case occasionally, so we normalise before lookup.

    Slice13-accuracy: delegates to the process-wide ``LenderRefResolver``,
    which reads ``mip.ref.lender_dictionary`` from UC (cached 15 min) and
    falls back to the in-process ``_LENDER_REF_MAP`` when UC is
    unavailable. A UC glitch never breaks redaction.
    """
    return get_lender_resolver().resolve(raw)


def synthesize_display_name(owner_name_hash: str | None) -> str:
    """Build the ``display_name`` from the first 8 hex chars of the hash.

    The synthesized label is intentionally not reversible to a real
    name -- the hash is already one-way and 8 chars of 64 gives
    2^32-level collision space, plenty of visual variety without a
    plausible-name reconstruction vector.
    """
    if not owner_name_hash:
        return "Owner anon"
    # Be defensive: strip any non-hex (share ops hashed fields sometimes
    # come back with '0x' prefixes) before slicing.
    safe = "".join(c for c in owner_name_hash.strip().lower() if c in "0123456789abcdef")
    prefix = safe[:8] if safe else "anon"
    return f"Owner {prefix}"


def synthesize_subject_property(
    city: str | None,
    state: str | None,
    zip5: str | None,
) -> str:
    """Render the city/state/zip-only property label.

    Matches the gold-layer CTAS format ``"Synthetic property · {city},
    {state} {zip5}"``; the dossier header concatenates
    ``Synthetic property · `` upstream so we emit only the generalised
    tail here -- identical to ``mock_data.subject_property``.
    """
    safe_city = (city or "Unknown").strip()
    safe_state = (state or "??").strip()
    safe_zip = (zip5 or "00000").strip()[:5]
    return f"Synthetic property · {safe_city}, {safe_state} {safe_zip}"


# ---------------------------------------------------------------------------
# Row-level redactors. Each one takes a raw gold-row dict and returns a
# dict shaped for the corresponding Pydantic ``from_attributes``-style
# construction. Forbidden keys never appear in the output.
# ---------------------------------------------------------------------------


def _enforce_no_forbidden_keys(row: dict[str, Any]) -> None:
    """Defensive guard: assert that no forbidden key survived.

    This is called by every redactor's return path so a future change
    that forgets to drop a raw column fails loudly in tests rather than
    shipping to the UI.
    """
    leaks = _FORBIDDEN_OUTPUT_KEYS.intersection(row.keys())
    if leaks:
        raise ValueError(
            f"PII redaction bug: forbidden keys survived redaction: {sorted(leaks)}"
        )


def redact_borrower_row(row: dict[str, Any]) -> dict[str, Any]:
    """Project a ``gold.borrower_360`` row into the ``Borrower360`` shape.

    Field mapping (from ``docs/data-contract-module0.md`` §3.2 + §12):

    - ``clip``          -> ``clip_id``
    - ``delta_vs_prior`` handled on segment rows, not here
    - every other column passes through with PII-safe renames

    Raw-PII columns (``owner_name_hash``, ``trigger_timeline_json``)
    must be present on input -- we derive ``display_name`` from the
    hash and parse the timeline JSON -- but NEVER on output.
    """
    city = row.get("city")
    state = row.get("state")
    zip5 = row.get("zip")

    output: dict[str, Any] = {
        "borrower_id": row["borrower_id"],
        "display_name": synthesize_display_name(row.get("owner_name_hash")),
        "city": city,
        "state": state,
        "zip": zip5,
        "segment_codes": row.get("segment_codes") or [],
        "equity_estimate": int(row.get("equity_estimate") or 0),
        "rate_spread_bps": int(row.get("rate_spread_bps") or 0),
        "opportunity_score": int(row.get("opportunity_score") or 0),
        "confidence": int(row.get("confidence") or 0),
        "recommended_offer": row.get("recommended_offer") or "Nurture",
        "why_now": row.get("why_now") or "",
        "evidence_ids": row.get("evidence_ids") or [],
        "approval_status": row.get("approval_status") or "pending",
        # Borrower360 additions:
        "clip_id": row["clip"],  # <-- rename at boundary
        # Cotality owner_1_identifier arrives as BIGINT from silver; the
        # schema is STRING so we coerce at the boundary. Empty-string on
        # NULL keeps the Pydantic contract tight.
        "owner_link_id": str(row.get("owner_link_id") or ""),
        "subject_property": synthesize_subject_property(city, state, zip5),
        "avm_value": int(row.get("avm_value") or 0),
        "current_lien_balance": int(row.get("current_lien_balance") or 0),
        "current_rate": float(row.get("current_rate") or 0.0),
        "ltv": int(row.get("ltv") or 0),
        "related_property_count": int(row.get("related_property_count") or 1),
    }
    _enforce_no_forbidden_keys(output)
    return output


def redact_lead_row(row: dict[str, Any]) -> dict[str, Any]:
    """Project a ``gold.lead_population`` row into the ``LeadSummary`` shape.

    Same PII posture as the borrower redactor, but only the LeadSummary
    subset of columns. display_name comes from ``owner_name_hash`` if
    present; otherwise lead_population already carries the synthesized
    ``display_name`` column from gold.
    """
    city = row.get("city")
    state = row.get("state")
    zip5 = row.get("zip")
    display_name = (
        synthesize_display_name(row["owner_name_hash"])
        if row.get("owner_name_hash")
        else row.get("display_name") or "Owner anon"
    )
    output: dict[str, Any] = {
        "borrower_id": row["borrower_id"],
        "display_name": display_name,
        "city": city,
        "state": state,
        "zip": zip5,
        "segment_codes": row.get("segment_codes") or [],
        "equity_estimate": int(row.get("equity_estimate") or 0),
        "rate_spread_bps": int(row.get("rate_spread_bps") or 0),
        "opportunity_score": int(row.get("opportunity_score") or 0),
        "confidence": int(row.get("confidence") or 0),
        "recommended_offer": row.get("recommended_offer") or "Nurture",
        "why_now": row.get("why_now") or "",
        "evidence_ids": row.get("evidence_ids") or [],
        "approval_status": row.get("approval_status") or "pending",
    }
    _enforce_no_forbidden_keys(output)
    return output


def redact_evidence_row(row: dict[str, Any]) -> dict[str, Any]:
    """Project a ``gold.evidence_events`` row into ``EvidenceEvent`` shape.

    Evidence rows don't carry owner names or addresses by design -- the
    PII posture there is the lender-name generalization. If the
    ``signal_value`` looks like a raw lender string we run it through
    ``generalize_lender``; otherwise we pass it through.
    """
    signal_type = row.get("signal_type")
    signal_value = row.get("signal_value")
    # Only the competitor-lien / servicer-assigned signals carry lender
    # strings. Don't touch spread / equity / date formatted values.
    if signal_type in {"competitor_lien", "current_servicer"} and signal_value:
        signal_value = generalize_lender(signal_value) or signal_value

    output: dict[str, Any] = {
        "evidence_id": row["evidence_id"],
        "source_product": row["source_product"],
        "source_table": row["source_table"],
        "signal_type": signal_type,
        "signal_value": signal_value,
        "display_text": row.get("display_text") or "",
        "confidence": float(row.get("confidence") or 0.0),
        "timestamp": str(row.get("timestamp") or ""),
    }
    _enforce_no_forbidden_keys(output)
    return output


__all__ = [
    "LenderRefResolver",
    "generalize_lender",
    "get_lender_resolver",
    "redact_borrower_row",
    "redact_evidence_row",
    "redact_lead_row",
    "synthesize_display_name",
    "synthesize_subject_property",
    # Exposed for test assertions:
    "_FORBIDDEN_OUTPUT_KEYS",
    "_LENDER_REF_MAP",
    "_STREET_NUMBER_PATTERN",
    "_reset_lender_resolver_for_tests",
]

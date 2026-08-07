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
- Lender names: generalized through ``_LENDER_REF_MAP``. Public-demo
  competitor lenders use a controlled alias vocabulary. Unknown
  lenders collapse to ``Competitor Other``. The raw uppercase servicer strings never
  cross the boundary.

Forbidden keys (enforced by ``tests/unit/test_pii_redaction.py`` and
``tests/integration/test_api_pii_boundary.py``): ``owner_1_full_name``,
``situs_street_address``, ``mailing_street_address``,
``owner_name_hash_raw``, ``trigger_timeline_json``.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
from threading import Lock
from typing import Any

from backend.config.runtime_secret_policy import require_strong_runtime_secret
from backend.schemas._validators_tenant import is_public_lender_ref, normalize_public_lender_ref
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
        # Round-4 R4-22: bare `owner_name_hash` was missing. The hash is
        # a stable identifier per owner and is a cross-tenant correlation
        # vector — one refactor slip could leak it. Deny explicitly at
        # the boundary so the redactors strip it regardless of what the
        # SELECT projects.
        "owner_name_hash",
        "owner_name_hash_raw",
        "trigger_timeline_json",   # raw JSON string; router gets the parsed struct
        "buyer_1_full_name",
        "buyer_full_name_raw",
    }
)


_STREET_NUMBER_PATTERN = re.compile(r"\d")
_ID_MASK_NAMESPACE = "mip-cotality-id-mask-v1"
_ID_MASK_FALLBACK_APP_ENVS = frozenset({"local", "test"})
_ID_MASK_PLACEHOLDER_SECRETS = frozenset(
    {
        "redacted",
        "changeme",
        "change-me",
        "change_me",
        "placeholder",
        "example",
        "your-secret",
        "your_secret",
        "mip-cotality-id-mask-v1",
    }
)
_CONSENT_STATUS_VALUES: frozenset[str] = frozenset({"opt_in", "opt_out", "unknown"})


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


def _public_lender_ref(raw: Any) -> str | None:
    value = str(raw or "").strip()
    if not value:
        return None
    if is_public_lender_ref(value):
        return value
    return generalize_lender(value)


def _consent_status(raw: Any) -> str:
    value = str(raw or "").strip().lower()
    return value if value in _CONSENT_STATUS_VALUES else "unknown"


_ELIGIBILITY_SOURCE_PATTERN = re.compile(r"[a-z0-9][a-z0-9_.-]{0,63}")


def _eligibility_source(raw: Any) -> str:
    """Machine-safe consent-provenance slug; falls back to synthetic_seed.

    S1.4: gold defaults ``eligibility_source`` to ``synthetic_seed``; a
    connected CRM/CDP connector supplies its id (S4.1). Anything that is
    not a lowercase machine token fails back to the synthetic default so
    a bad feed value can never smuggle free text through the API.
    """
    value = str(raw or "").strip().lower()
    if value and _ELIGIBILITY_SOURCE_PATTERN.fullmatch(value):
        return value
    return "synthetic_seed"


# S1.1 owner-entity classification enum (silver.property_owners contract).
_OWNER_ENTITY_TYPE_VALUES = frozenset({"individual", "trust", "llc", "unresolved"})


def _owner_entity_type(raw: Any) -> str | None:
    value = str(raw or "").strip().lower()
    return value if value in _OWNER_ENTITY_TYPE_VALUES else None


def _optional_int(raw: Any) -> int | None:
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _optional_str(raw: Any) -> str | None:
    if raw is None:
        return None
    value = str(raw).strip()
    return value or None


def _id_mask_secret() -> str:
    configured = (os.environ.get("MIP_COTALITY_ID_MASK_SECRET") or "").strip()
    normalized = configured.lower()
    is_placeholder = (
        normalized in _ID_MASK_PLACEHOLDER_SECRETS
        or (normalized.startswith("<") and normalized.endswith(">"))
    )
    from backend.config.settings import settings

    app_env = (settings.app_env or "").strip().lower()
    if configured and not is_placeholder:
        if app_env not in _ID_MASK_FALLBACK_APP_ENVS:
            try:
                return require_strong_runtime_secret(
                    configured,
                    name="MIP_COTALITY_ID_MASK_SECRET",
                    extra_placeholders=_ID_MASK_PLACEHOLDER_SECRETS,
                )
            except ValueError as exc:
                raise RuntimeError(str(exc)) from exc
        return configured

    if app_env in _ID_MASK_FALLBACK_APP_ENVS:
        return _ID_MASK_NAMESPACE

    raise RuntimeError(
        "MIP_COTALITY_ID_MASK_SECRET is required outside local/test app environments"
    )


def mask_address_for_audit(normalized_address: str, zip5: str) -> str:
    """HMAC-derived audit token (64 hex) for a property-lookup address.

    The gold join key stays plain sha2 so the ETL SQL can compute it; the
    AUDIT ledger stores this tenant-secret token instead, so an insider
    holding the ledger cannot run an offline zip-scoped dictionary attack
    against a salt-free truncated hash (governance review FU-1,
    2026-07-07). Callers truncate: payload carries [:16], the miss-path
    entity id carries [:32] to satisfy PUBLIC_SERVER_ID_PATTERN. Repeated
    identical lookups share a token by design — the audit trail is meant
    to link re-probes of the same target.
    """
    secret = _id_mask_secret()
    message = f"addr:{normalized_address}|{zip5}".encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def mask_source_record_ref(source_system: str, source_record_ref: str) -> str:
    """Return a tenant-secret opaque token for an external outcome record id.

    CRM/LOS record identifiers are client-supplied and therefore cannot be
    trusted to remain free of borrower names or other PII.  The API hashes the
    value before any repository, response, or audit boundary sees it.  The
    source-system domain separator prevents the same upstream identifier from
    correlating across connectors while preserving deterministic idempotency
    within one connector.
    """

    secret = _id_mask_secret()
    source = str(source_system or "").strip().lower()
    record_ref = str(source_record_ref or "").strip()
    if not source or not record_ref:
        raise ValueError("source_system and source_record_ref are required")
    digest = hmac.new(
        secret.encode(),
        f"outcome-ref:{source}:{record_ref}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return f"auto-{digest[:32]}"


def mask_cotality_id(kind: str, raw: Any) -> str:
    """Return a stable display-safe surrogate for a Cotality identifier.

    ``kind`` is ``"clip"`` or ``"owner_link"``. Existing synthetic demo
    IDs and already-masked refs pass through so fixtures remain readable.
    Raw Cotality IDs are never exposed through app, CSV, or audit surfaces.
    Internal debugging that needs raw CLIP / Owner Link values must use
    governed Unity Catalog access, not an app-runtime escape hatch.
    """
    value = str(raw or "").strip()
    if not value:
        return ""

    if kind == "clip":
        if value.startswith(("clip_ref_", "clip_demo_")):
            return value
        prefix = "clip_ref"
    elif kind == "owner_link":
        if value.startswith(("owner_link_ref_", "ol_demo_")):
            return value
        prefix = "owner_link_ref"
    else:
        raise ValueError(f"unknown Cotality id kind: {kind!r}")

    secret = _id_mask_secret()
    digest = hmac.new(
        secret.encode("utf-8"),
        f"{kind}:{value}".encode(),
        hashlib.sha256,
    ).hexdigest()[:12]
    return f"{prefix}_{digest}"


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

    borrower_id = str(row["borrower_id"])
    clip_id = mask_cotality_id("clip", row.get("clip")) or f"clip_demo_{borrower_id}"
    owner_link_id = (
        mask_cotality_id("owner_link", row.get("owner_link_id"))
        or f"ol_demo_{borrower_id}"
    )

    current_lien_balance = int(row.get("current_lien_balance") or 0)
    current_lien_balance_low = (
        int(row["current_lien_balance_low"])
        if row.get("current_lien_balance_low") is not None
        else current_lien_balance
    )
    current_lien_balance_high = (
        int(row["current_lien_balance_high"])
        if row.get("current_lien_balance_high") is not None
        else current_lien_balance
    )

    output: dict[str, Any] = {
        "borrower_id": borrower_id,
        "display_name": synthesize_display_name(row.get("owner_name_hash")),
        "city": city,
        "state": state,
        "zip": zip5,
        "segment_codes": row.get("segment_codes") or [],
        "equity_estimate": int(row.get("equity_estimate") or 0),
        "rate_spread_bps": int(row.get("rate_spread_bps") or 0),
        "opportunity_score": int(row.get("opportunity_score") or 0),
        "confidence": int(row.get("confidence") or 0),
        "recommended_offer_code": row.get("recommended_offer_code") or "nurture",
        "recommended_offer": row.get("recommended_offer") or "Nurture",
        "why_now": row.get("why_now") or "",
        "evidence_ids": row.get("evidence_ids") or [],
        "approval_status": row.get("approval_status") or "pending",
        "outreach_status": row.get("outreach_status") or "none",
        "approved_at": row.get("approved_at"),
        "outreach_at": row.get("outreach_at"),
        # Borrower360 additions:
        "clip_id": clip_id,  # <-- rename + mask at boundary
        # Cotality owner_1_identifier arrives as BIGINT from silver; the
        # schema is STRING so we coerce at the boundary. Empty-string on
        # NULL keeps the Pydantic contract tight.
        "owner_link_id": owner_link_id,
        "subject_property": synthesize_subject_property(city, state, zip5),
        "avm_value": int(row.get("avm_value") or 0),
        "current_lien_balance": current_lien_balance,
        "current_lien_balance_low": current_lien_balance_low,
        "current_lien_balance_high": current_lien_balance_high,
        "current_rate": float(row.get("current_rate") or 0.0),
        "ltv": int(row.get("ltv") or 0),
        "related_property_count": int(row.get("related_property_count") or 1),
        # S1.1 multi-owner caveat fields. Display-only here: contact
        # suppression is enforced upstream (gold.borrower_360 fails
        # marketing_eligible closed on unresolved owners).
        "owner_count": int(row.get("owner_count") or 1),
        "has_unresolved_owner": bool(row.get("has_unresolved_owner") or False),
        "primary_owner_entity_type": _owner_entity_type(
            row.get("primary_owner_entity_type")
        ),
        "situs_cbsa_code": row.get("situs_cbsa_code") or None,
        "first_pos_loan_type": row.get("first_pos_loan_type") or None,
        "loan_product_type": _optional_str(row.get("loan_product_type")),
        "origination_channel": _optional_str(row.get("origination_channel")),
        "is_owner_occupied": bool(row.get("is_owner_occupied") or False),
        "is_absentee": bool(row.get("is_absentee") or False),
        "is_corporate_owner": bool(row.get("is_corporate_owner") or False),
        "is_investor": bool(row.get("is_investor") or False),
        "is_current_customer": bool(row.get("is_current_customer") or False),
        "is_former_customer": bool(row.get("is_former_customer") or False),
        "is_competitor_lien": bool(row.get("is_competitor_lien") or False),
        "second_pos_amount": int(row.get("second_pos_amount") or 0),
        "has_permit": bool(row.get("has_permit") or False),
        "listed_for_sale": bool(row.get("listed_for_sale") or False),
        "listing_status_category": _optional_str(row.get("listing_status_category")),
        "listing_status_description": _optional_str(row.get("listing_status_description")),
        "listing_date": _optional_str(row.get("listing_date")),
        "listing_status_date": _optional_str(row.get("listing_status_date")),
        "listing_price": _optional_int(row.get("listing_price")),
        "listing_days_on_market": _optional_int(row.get("listing_days_on_market")),
        "listing_service": _optional_str(row.get("listing_service")),
        "heloc_propensity_score": _optional_int(row.get("heloc_propensity_score")),
        "heloc_propensity_run_date": _optional_str(row.get("heloc_propensity_run_date")),
        "has_heloc_propensity_trigger": bool(
            row.get("has_heloc_propensity_trigger") or False
        ),
        "refi_propensity_score": _optional_int(row.get("refi_propensity_score")),
        "refi_propensity_run_date": _optional_str(row.get("refi_propensity_run_date")),
        "has_refi_propensity_trigger": bool(
            row.get("has_refi_propensity_trigger") or False
        ),
        "has_first_party_relationship": bool(
            row.get("has_first_party_relationship") or False
        ),
        "first_party_relationship_depth": int(
            row.get("first_party_relationship_depth") or 0
        ),
        "first_party_recent_interactions": int(
            row.get("first_party_recent_interactions") or 0
        ),
        "first_party_recent_application": bool(
            row.get("first_party_recent_application") or False
        ),
        "first_party_synthetic_demo": bool(row.get("first_party_synthetic_demo") or False),
        # Fail closed when the gold column is missing/null: marketing
        # workflows must prove eligibility rather than assume it.
        "marketing_eligible": bool(row.get("marketing_eligible") is True),
        "consent_status": _consent_status(row.get("consent_status")),
        "suppression_reason": row.get("suppression_reason") or None,
        "last_touch_at": row.get("last_touch_at"),
        "eligible_recontact_at": row.get("eligible_recontact_at"),
        # S1.4: dnc fails closed to the suppression-derived value so a
        # missing gold column can never un-flag a do-not-contact row.
        "dnc": bool(
            row.get("dnc") is True
            or (row.get("suppression_reason") or None) == "do_not_contact"
        ),
        "eligibility_source": _eligibility_source(row.get("eligibility_source")),
        "current_lender_ref": _public_lender_ref(row.get("current_lender_ref")),
        "source_refreshed_at": _optional_str(row.get("refreshed_at")),
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
    # Coerce None -> "" so the LeadSummary string contract holds even when
    # a gold.lead_population row has a null location (seen in the real UC
    # data 2026-04-22: rural / PO-box-only borrowers land with city=NULL,
    # zip=NULL). Previously this raised a Pydantic ValidationError and the
    # entire /api/leads call 500'd, which blocked the loan-officer flow on
    # Segment Intelligence + Lead Queue. Empty string degrades gracefully
    # in the UI (the location chip just hides).
    city = row.get("city") or ""
    state = row.get("state") or ""
    zip5 = row.get("zip") or ""
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
        # Display-safe Cotality property ref on the list row. Raw CLIP never
        # leaves governed data stores through app, CSV, or audit surfaces.
        "clip": mask_cotality_id("clip", row.get("clip")),
        "segment_codes": row.get("segment_codes") or [],
        "equity_estimate": int(row.get("equity_estimate") or 0),
        "rate_spread_bps": int(row.get("rate_spread_bps") or 0),
        "opportunity_score": int(row.get("opportunity_score") or 0),
        "confidence": int(row.get("confidence") or 0),
        "recommended_offer_code": row.get("recommended_offer_code") or "nurture",
        "recommended_offer": row.get("recommended_offer") or "Nurture",
        "why_now": row.get("why_now") or "",
        "evidence_ids": row.get("evidence_ids") or [],
        "approval_status": row.get("approval_status") or "pending",
        "outreach_status": row.get("outreach_status") or "none",
        "approved_at": row.get("approved_at"),
        "outreach_at": row.get("outreach_at"),
        # Secondary-filter fields (2026-04-23). Optional in the schema and
        # safely-defaulted here so older gold rows (pre-DDL-extension) and
        # the in-process test fixtures (which build LeadSummary from a
        # Borrower360 model_dump) both validate. Booleans go through the
        # same None-tolerant coercion used elsewhere in redaction.
        "is_owner_occupied": bool(row.get("is_owner_occupied") or False),
        "is_investor": bool(row.get("is_investor") or False),
        "is_current_customer": bool(row.get("is_current_customer") or False),
        "is_former_customer": bool(row.get("is_former_customer") or False),
        "is_competitor_lien": bool(row.get("is_competitor_lien") or False),
        "related_property_count": int(row.get("related_property_count") or 1),
        # S1.1 multi-owner caveat fields (display-only; suppression is
        # enforced upstream in gold.borrower_360.marketing_eligible).
        "owner_count": int(row.get("owner_count") or 1),
        "has_unresolved_owner": bool(row.get("has_unresolved_owner") or False),
        "primary_owner_entity_type": _owner_entity_type(
            row.get("primary_owner_entity_type")
        ),
        "current_lien_balance": int(row.get("current_lien_balance") or 0),
        "second_pos_amount": int(row.get("second_pos_amount") or 0),
        "has_permit": bool(row.get("has_permit") or False),
        "listed_for_sale": bool(row.get("listed_for_sale") or False),
        "listing_status_category": _optional_str(row.get("listing_status_category")),
        "listing_status_description": _optional_str(row.get("listing_status_description")),
        "listing_date": _optional_str(row.get("listing_date")),
        "listing_status_date": _optional_str(row.get("listing_status_date")),
        "listing_price": _optional_int(row.get("listing_price")),
        "listing_days_on_market": _optional_int(row.get("listing_days_on_market")),
        "listing_service": _optional_str(row.get("listing_service")),
        "heloc_propensity_score": _optional_int(row.get("heloc_propensity_score")),
        "heloc_propensity_run_date": _optional_str(row.get("heloc_propensity_run_date")),
        "has_heloc_propensity_trigger": bool(
            row.get("has_heloc_propensity_trigger") or False
        ),
        "refi_propensity_score": _optional_int(row.get("refi_propensity_score")),
        "refi_propensity_run_date": _optional_str(row.get("refi_propensity_run_date")),
        "has_refi_propensity_trigger": bool(
            row.get("has_refi_propensity_trigger") or False
        ),
        # S1.6 dimensions. None = unknown; the UI renders "Unknown" and the
        # filters treat NULL as its own reviewed bucket.
        "loan_product_type": _optional_str(row.get("loan_product_type")),
        "origination_channel": _optional_str(row.get("origination_channel")),
        # Fail closed when source columns are missing/null.
        "marketing_eligible": bool(row.get("marketing_eligible") is True),
        "consent_status": _consent_status(row.get("consent_status")),
        "suppression_reason": row.get("suppression_reason") or None,
        "last_touch_at": row.get("last_touch_at"),
        "eligible_recontact_at": row.get("eligible_recontact_at"),
        # S1.4: dnc fails closed to the suppression-derived value so a
        # missing gold column can never un-flag a do-not-contact row.
        "dnc": bool(
            row.get("dnc") is True
            or (row.get("suppression_reason") or None) == "do_not_contact"
        ),
        "eligibility_source": _eligibility_source(row.get("eligibility_source")),
        "current_lender_ref": _public_lender_ref(row.get("current_lender_ref")),
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


# Match a US SSN (strict): 3-2-4 digits with dashes.
_SSN_PATTERN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
# Match a US phone number in common separator forms. Keep conservative — we
# WILL capture a sales-line follow-up instruction ("call 1-800-XXX-XXXX") if
# the approver typed one, and that's fine; the draft should never contain a
# real individual borrower phone number either way. Covers +1, parens,
# dots, dashes, spaces.
_PHONE_PATTERN = re.compile(
    r"(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)\d{3}[-.\s]?\d{4}"
)
# Match an email address.
_EMAIL_PATTERN = re.compile(
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
)
# Match a street address like "123 Main St" or "4567 North Oak Ave".
_STREET_ADDRESS_PATTERN = re.compile(
    r"\b\d{1,6}\s+[A-Z][A-Za-z0-9.\s]{1,40}\b"
    r"(?:\s+(?:St|Street|Ave|Avenue|Rd|Road|Blvd|Boulevard|Dr|Drive|Ln|Lane|Way|Ct|Court|Place|Pl|Terrace|Ter|Circle|Cir|Hwy|Highway))\b",
    re.IGNORECASE,
)


def scrub_free_text(text: str) -> str:
    """Redact obvious PII markers from free-text drafts before they're
    persisted to the append-only audit ledger.

    Catches SSN, phone numbers, email addresses, and obvious US street
    addresses. Leaves the `[first name]` CRM placeholder + `1-800-XXX-XXXX`
    style tracking numbers intact (the X-string doesn't match the digits-
    only phone regex).

    This is a defence-in-depth pass, not a comprehensive DLP — the audit
    governance contract (CLAUDE.md §7) is that approvers don't paste PII
    into the draft in the first place. The intent of this scrub is to
    blunt an accidental paste; a determined leak still requires reviewer
    attention.

    Returns the redacted string. Token replacements:
        SSN:       [SSN-REDACTED]
        phone:     [PHONE-REDACTED]
        email:     [EMAIL-REDACTED]
        address:   [ADDRESS-REDACTED]
    """
    if not text:
        return text
    out = _SSN_PATTERN.sub("[SSN-REDACTED]", text)
    out = _PHONE_PATTERN.sub("[PHONE-REDACTED]", out)
    out = _EMAIL_PATTERN.sub("[EMAIL-REDACTED]", out)
    out = _STREET_ADDRESS_PATTERN.sub("[ADDRESS-REDACTED]", out)
    return out


__all__ = [
    "LenderRefResolver",
    "generalize_lender",
    "get_lender_resolver",
    "mask_cotality_id",
    "normalize_public_lender_ref",
    "redact_borrower_row",
    "redact_evidence_row",
    "redact_lead_row",
    "scrub_free_text",
    "synthesize_display_name",
    "synthesize_subject_property",
    # Exposed for test assertions:
    "_FORBIDDEN_OUTPUT_KEYS",
    "_LENDER_REF_MAP",
    "_STREET_NUMBER_PATTERN",
    "_reset_lender_resolver_for_tests",
]

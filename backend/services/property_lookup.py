"""Governed property loan lookup service (address -> CLIP -> loan).

Reusable spine consumed by the app UI, the Growth Agent dossier specialist, and
future org agents. Resolves a caller-supplied street address + ZIP to a masked
CLIP and its loan facts by hashing the canonicalized address and looking the
hash up in ``mip.gold.address_lookup`` (built at ETL refresh time).

Governance invariants (all enforced here + in tests):
  * The raw street address is NEVER stored, logged, echoed, or written to the
    audit ledger. The gold join key is the plain sha2; the ledger stores a
    tenant-secret HMAC token (payload [:16], miss entity [:32]) — never the
    plain hash at any truncation.
  * A REQUIRED audit row is written on BOTH hit and miss (governance §4:
    "who looked up which property"). The payload carries the HMAC audit
    token's first 16 hex (mask_address_for_audit),
    the 5-digit ZIP, a ``hit`` boolean, and -- on a hit only -- the masked CLIP
    ref. Never the raw address, never the full hash.
  * ``clip`` / ``owner_link_id`` are masked at egress via
    ``pii_redaction.mask_cotality_id``; the raw servicer string is generalized
    via ``pii_redaction.generalize_lender``.
  * v1 is EXACT-after-canonicalization against the refreshed share only -- no
    fuzzy matching, no CLIP mastering.

The lookup uses the process-wide ``ResilientSqlClient`` (breaker + retry) the
caller passes in, so warehouse flakiness surfaces as a ``DependencyDownError``
(translated to HTTP 503 by the app-wide handler), never a silent empty result.
"""
from __future__ import annotations

import logging
import re as _re
from typing import Any, Protocol

from backend.schemas.lookup import PropertyLoanLookupLoan, PropertyLoanLookupResponse
from backend.services.address_normalization import address_lookup_hash, normalize_address
from backend.services.audit_store import AuditStore
from backend.services.databricks_sql_helpers import qualify
from backend.services.observability import emit
from backend.services.pii_redaction import (
    generalize_lender,
    mask_address_for_audit,
    mask_cotality_id,
)

log = logging.getLogger(__name__)


class _SqlClient(Protocol):
    """The subset of the SQL client this service uses (execute_one)."""

    def execute_one(
        self, statement: str, parameters: dict[str, Any] | None = None
    ) -> dict[str, Any] | None: ...


# Address-spine lookup by hash. Parameterized -- the hash is a bound value, not
# interpolated. Returns at most one row (address_hash is the table PK).
_LOOKUP_SQL = (
    "SELECT "
    "  clip, owner_link_id, has_open_lien, current_lien_balance, ltv, "
    "  first_pos_lender_current, current_rate "
    f"FROM {qualify('gold', 'address_lookup')} "
    "WHERE address_hash = :address_hash "
    "LIMIT 1"
)

# Borrower linkage by CLIP -- picks up the masked public borrower_id + score +
# segments so a matched property deep-links to its governed dossier. Kept as a
# second query (cleaner than widening the address spine with score columns that
# would drift from gold.borrower_360 on every refresh).
_BORROWER_LINK_SQL = (
    "SELECT borrower_id, opportunity_score, segment_codes "
    f"FROM {qualify('gold', 'borrower_360')} "
    "WHERE clip = :clip "
    "LIMIT 1"
)


def lookup_property_loan(
    sql_client: _SqlClient,
    audit: AuditStore,
    *,
    actor: str,
    address_line: str,
    zip5: str,
    city: str | None = None,
    state: str | None = None,
) -> PropertyLoanLookupResponse:
    """Resolve ``(address_line, zip5)`` to a masked CLIP + loan facts.

    Writes a REQUIRED audit row (raises on audit failure so the governance
    record is never silently dropped) and returns a fully-masked response. The
    ``address_line`` is hashed and then discarded; ``city`` / ``state`` are
    accepted for parity with the request shape but are not part of the v1 hash
    key (the share-scoped key is address + ZIP only).
    """
    _ = (city, state)  # accepted for request parity; not part of the v1 hash key
    address_hash = address_lookup_hash(address_line, zip5)
    # ASCII digits only, byte-identical to the hash's ZIP normalization —
    # unicode digits (e.g. fullwidth) must not diverge between the join key
    # and the audit record (governance review FU-3, 2026-07-07).
    zip_digits = _re.sub(r"[^0-9]", "", zip5 or "")[:5]
    # Audit-side token is HMAC'd with the tenant mask secret; the plain sha2
    # never enters the ledger (governance review FU-1, 2026-07-07).
    audit_token = mask_address_for_audit(normalize_address(address_line), zip_digits)

    row = sql_client.execute_one(_LOOKUP_SQL, {"address_hash": address_hash})

    matched = row is not None
    clip_ref: str | None = None
    owner_link_ref: str | None = None
    borrower_id: str | None = None
    lead_score: int | None = None
    segment: list[str] | None = None
    loan: PropertyLoanLookupLoan | None = None
    dossier_path: str | None = None

    if row is not None:
        clip_ref = mask_cotality_id("clip", row.get("clip")) or None
        owner_link_ref = mask_cotality_id("owner_link", row.get("owner_link_id")) or None
        loan = PropertyLoanLookupLoan(
            lender_brand=generalize_lender(row.get("first_pos_lender_current")),
            current_rate=float(row.get("current_rate") or 0.0),
            current_lien_balance=int(row.get("current_lien_balance") or 0),
            has_open_lien=bool(row.get("has_open_lien")),
            ltv=int(row.get("ltv") or 0),
        )
        # Borrower linkage by CLIP: masked public borrower_id + score + segments.
        link = sql_client.execute_one(_BORROWER_LINK_SQL, {"clip": row.get("clip")})
        if link is not None and link.get("borrower_id"):
            borrower_id = str(link["borrower_id"])
            lead_score = int(link.get("opportunity_score") or 0)
            raw_segments = link.get("segment_codes") or []
            segment = [str(code) for code in raw_segments]
            dossier_path = f"/borrower-360/{borrower_id}"

    # REQUIRED audit write (hit AND miss). Payload carries the HMAC audit
    # token's first 16 hex, the ZIP, a hit boolean, and (on hit) the masked
    # CLIP ref. Never the raw address, never the plain sha2 at any length. The write returns the AuditEvent so the
    # response can echo its id; audit failures propagate to the caller (router
    # translates to 503) rather than dropping the governance record.
    payload: dict[str, Any] = {
        "address_hash": audit_token[:16],
        "hit": matched,
    }
    if _re.fullmatch(r"[0-9]{5}", zip_digits):
        # Defense-in-depth: the router already rejects non-5-ASCII-digit ZIPs;
        # if a caller bypasses it, the audit row records the lookup without a
        # malformed zip5 rather than tripping the value policy (FU-3).
        payload["zip5"] = zip_digits
    if clip_ref is not None:
        payload["source_record_ref"] = clip_ref
    event = audit.write(
        actor=actor,
        action="property_lookup",
        entity_type="property_lookup",
        entity_id=borrower_id if borrower_id is not None else f"auto-{audit_token[:32]}",
        payload_json=payload,
        event_type="PROPERTY_LOOKUP",
        subject_clip=row.get("clip") if row is not None else None,
    )

    emit(
        log,
        "property_lookup",
        dependency="warehouse",
        outcome="ok",
        hit=matched,
        matched_borrower=borrower_id is not None,
    )

    return PropertyLoanLookupResponse(
        matched=matched,
        clip_ref=clip_ref,
        owner_link_ref=owner_link_ref,
        borrower_id=borrower_id,
        lead_score=lead_score,
        segment=segment,  # type: ignore[arg-type]  # validated Literal at model boundary
        loan=loan,
        dossier_path=dossier_path,
        audit_event_id=event.event_id,
    )


__all__ = ["lookup_property_loan"]

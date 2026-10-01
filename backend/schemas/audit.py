"""Audit event wire schema.

Slice 5 adds ``subject_clip``, ``subject_segment``, ``request_id``, and
``event_type`` so the Lakebase-backed audit store can round-trip
governance §4 fields without dropping to JSONB. Existing callers that
use ``action`` / ``entity_type`` / ``entity_id`` continue to work --
``event_type`` defaults to the same string as ``action`` when omitted,
preserving the pre-Slice-5 contract.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from backend.schemas.common import (
    validate_public_audit_action,
    validate_public_audit_entity_type,
    validate_public_audit_event_type,
    validate_public_audit_identifier_or_none,
    validate_public_audit_subject_segment,
    validate_public_opaque_id,
)
from backend.schemas.lead_export import validate_export_filters, validate_sha256_hex


class AuditEvent(BaseModel):
    event_id: str
    actor: str
    action: str
    entity_type: str
    entity_id: str
    payload_json: dict[str, Any] = Field(default_factory=dict)
    evidence_ids: list[str] = Field(default_factory=list)
    created_at: str

    # Governance §4 additions -- populated by the Lakebase-backed writer.
    # Optional so existing tests that instantiate ``AuditEvent`` with
    # the original four-field constructor keep working.
    event_type: str | None = None
    subject_clip: str | None = None
    subject_segment: str | None = None
    request_id: str | None = None
    correlation_id: str | None = None
    # Internal monotonically increasing Lakebase watermark used for stable
    # cursor traversal. It is intentionally excluded from the wire response;
    # operators identify rows by ``event_id`` while pagination uses the
    # database insertion order rather than timestamps or random UUIDs.
    audit_sequence: int | None = Field(default=None, exclude=True, ge=1)
    # PostgreSQL MVCC snapshot captured by the page query. Signed cursors
    # carry it between requests so transactions that commit after page one
    # cannot enter a later page even if they reserved an earlier sequence.
    audit_snapshot: str | None = Field(default=None, exclude=True, max_length=256)


class AuditEventPage(BaseModel):
    """One snapshot-stable page of append-only audit events."""

    items: list[AuditEvent] = Field(default_factory=list)
    next_cursor: str | None = None


class AuditRollupResponse(BaseModel):
    bucket_start: str
    event_type: str | None = None
    group_by: Literal["event_type", "actor", "action"]
    group_key: str | None = None
    event_count: int


class AuditEventCreateRequest(BaseModel):
    actor: str
    action: str
    entity_type: str
    entity_id: str
    payload_json: dict[str, Any] = Field(default_factory=dict)
    evidence_ids: list[str] = Field(default_factory=list)
    event_type: str | None = None
    subject_clip: str | None = None
    subject_segment: str | None = None
    request_id: str | None = None

    @field_validator("action")
    @classmethod
    def _action_is_public_safe(cls, value: str) -> str:
        return validate_public_audit_action(value)

    @field_validator("entity_type")
    @classmethod
    def _entity_type_is_public_safe(cls, value: str) -> str:
        return validate_public_audit_entity_type(value)

    @field_validator("entity_id")
    @classmethod
    def _entity_id_is_public_safe(cls, value: str) -> str:
        normalized = validate_public_audit_identifier_or_none(value)
        if normalized is None:
            raise ValueError("entity_id must not be blank")
        return normalized

    @field_validator("event_type")
    @classmethod
    def _event_type_is_public_safe(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return validate_public_audit_event_type(value)

    @field_validator("subject_segment")
    @classmethod
    def _subject_segment_is_public_safe(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return validate_public_audit_subject_segment(value)

    @field_validator("request_id")
    @classmethod
    def _request_id_is_public_safe(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return validate_public_opaque_id(value)


# ---------------------------------------------------------------------------
# Audit explorer (audit tables-10): facets, count and the export receipt.
# ---------------------------------------------------------------------------

# The largest ledger slice one explorer CSV export may declare.
AUDIT_EXPORT_MAX_ROWS = 5000


class AuditFacetValue(BaseModel):
    """One distinct ledger value and how many rows carry it in the window."""

    value: str
    count: int


class AuditFacetsTruncated(BaseModel):
    """Per facet: true when more distinct values exist than were returned."""

    event_types: bool = False
    actions: bool = False
    actors: bool = False


class AuditFacetsResponse(BaseModel):
    """Distinct event types, actions and actors in the ledger window, most frequent first."""

    event_types: list[AuditFacetValue] = Field(default_factory=list)
    actions: list[AuditFacetValue] = Field(default_factory=list)
    actors: list[AuditFacetValue] = Field(default_factory=list)
    truncated: AuditFacetsTruncated = Field(default_factory=AuditFacetsTruncated)
    since: datetime
    until: datetime | None = None


class AuditCountResponse(BaseModel):
    """How many ledger rows match the explorer filters, counted up to ``cap``."""

    count: int
    capped: bool = Field(description="True when more rows match than the cap counts.")
    cap: int


class AuditExportReceiptRequest(BaseModel):
    """What the explorer declares about the ledger CSV it is about to download."""

    row_count: int = Field(ge=1, le=AUDIT_EXPORT_MAX_ROWS)
    csv_sha256: str = Field(
        description="SHA-256 (hex) of the exact CSV bytes handed to the download.",
    )
    event_ids: list[str] = Field(
        min_length=1,
        max_length=AUDIT_EXPORT_MAX_ROWS,
        description="Audit event ids in the order the file holds them.",
    )
    event_ids_sha256: str = Field(
        description=(
            "SHA-256 (hex) of the compact JSON array of event_ids, computed "
            "client-side; the server recomputes it and refuses the receipt on a "
            "mismatch. The ids themselves are not stored."
        ),
    )
    filters: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "The explorer query parameters the exported rows were read with. "
            "Only their fingerprint is written to the ledger."
        ),
    )

    @field_validator("csv_sha256")
    @classmethod
    def _csv_digest(cls, value: str) -> str:
        return validate_sha256_hex(value, "csv_sha256")

    @field_validator("event_ids_sha256")
    @classmethod
    def _ids_digest(cls, value: str) -> str:
        return validate_sha256_hex(value, "event_ids_sha256")

    @field_validator("event_ids")
    @classmethod
    def _unique_event_ids(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("event_ids must not repeat")
        return value

    @field_validator("filters")
    @classmethod
    def _bounded_filters(cls, value: dict[str, str]) -> dict[str, str]:
        return validate_export_filters(value)


class AuditExportReceipt(BaseModel):
    """The ``AUDIT_EXPORT`` ledger entry the explorer download waits for."""

    audit_event_id: str
    event_type: Literal["AUDIT_EXPORT"] = "AUDIT_EXPORT"
    actor: str
    row_count: int
    csv_sha256: str
    event_ids_sha256: str
    filter_fingerprint: str
    recorded_at: str

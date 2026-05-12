"""Audit event wire schema.

Slice 5 adds ``subject_clip``, ``subject_segment``, ``request_id``, and
``event_type`` so the Lakebase-backed audit store can round-trip
governance §4 fields without dropping to JSONB. Existing callers that
use ``action`` / ``entity_type`` / ``entity_id`` continue to work --
``event_type`` defaults to the same string as ``action`` when omitted,
preserving the pre-Slice-5 contract.
"""
from typing import Any

from pydantic import BaseModel, Field, field_validator

from backend.schemas.common import (
    validate_public_audit_action,
    validate_public_audit_entity_type,
    validate_public_audit_event_type,
    validate_public_audit_identifier_or_none,
    validate_public_audit_subject_segment,
    validate_public_opaque_id,
)


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

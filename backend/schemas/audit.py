"""Audit event wire schema.

Slice 5 adds ``subject_clip``, ``subject_segment``, ``request_id``, and
``event_type`` so the Lakebase-backed audit store can round-trip
governance §4 fields without dropping to JSONB. Existing callers that
use ``action`` / ``entity_type`` / ``entity_id`` continue to work --
``event_type`` defaults to the same string as ``action`` when omitted,
preserving the pre-Slice-5 contract.
"""
from typing import Any

from pydantic import BaseModel, Field


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

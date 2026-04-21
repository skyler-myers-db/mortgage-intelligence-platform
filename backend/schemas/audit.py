from typing import Any

from pydantic import BaseModel


class AuditEvent(BaseModel):
    event_id: str
    actor: str
    action: str
    entity_type: str
    entity_id: str
    payload_json: dict[str, Any] = {}
    evidence_ids: list[str] = []
    created_at: str


class AuditEventCreateRequest(BaseModel):
    actor: str
    action: str
    entity_type: str
    entity_id: str
    payload_json: dict[str, Any] = {}
    evidence_ids: list[str] = []

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from backend.schemas.audit import AuditEvent


class AuditStore:
    """In-memory audit store for mock mode. Lakebase adapter replaces this in prod."""

    def __init__(self) -> None:
        self._events: list[AuditEvent] = []

    def write(
        self,
        *,
        actor: str,
        action: str,
        entity_type: str,
        entity_id: str,
        payload_json: dict[str, Any] | None = None,
        evidence_ids: list[str] | None = None,
    ) -> AuditEvent:
        event = AuditEvent(
            event_id=f"evt-{uuid4().hex[:12]}",
            actor=actor,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            payload_json=payload_json or {},
            evidence_ids=evidence_ids or [],
            created_at=datetime.now(UTC).isoformat(),
        )
        self._events.append(event)
        return event

    def list(self, limit: int = 50) -> list[AuditEvent]:
        return list(reversed(self._events[-limit:]))


audit_store = AuditStore()

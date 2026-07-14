"""Test-only in-memory audit store implementation."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from backend.schemas.audit import AuditEvent
from backend.services.audit_store import (
    _assert_allowlisted,
    _assert_no_pii,
    _assert_public_safe_values,
    _coerce_event_type,
    _sanitize_metadata,
    _validate_top_level_audit_columns,
)
from backend.services.observability import get_correlation_id
from backend.services.pii_redaction import mask_cotality_id


class InMemoryAuditStore:
    """Deterministic in-memory audit ledger. Tests only."""

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
        event_type: str | None = None,
        subject_clip: str | None = None,
        subject_segment: str | None = None,
        request_id: str | None = None,
    ) -> AuditEvent:
        payload = payload_json or {}
        metadata = _sanitize_metadata({**payload, "action": action})
        payload = {k: v for k, v in metadata.items() if k != "action"}
        _assert_no_pii(metadata)
        _assert_allowlisted(metadata)
        _assert_public_safe_values(metadata)
        safe_event_type = _coerce_event_type(event_type, action)
        (
            safe_action,
            safe_entity_type,
            safe_entity_id,
            safe_event_type,
            safe_subject_segment,
            safe_request_id,
        ) = _validate_top_level_audit_columns(
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            event_type=safe_event_type,
            subject_segment=subject_segment,
            request_id=request_id,
        )
        event = AuditEvent(
            event_id=f"evt-{uuid4().hex[:12]}",
            actor=actor,
            action=safe_action,
            entity_type=safe_entity_type,
            entity_id=safe_entity_id,
            payload_json=payload,
            evidence_ids=evidence_ids or [],
            created_at=datetime.now(UTC).isoformat(),
            event_type=safe_event_type,
            subject_clip=(
                mask_cotality_id("clip", subject_clip)
                if subject_clip is not None
                else None
            ),
            subject_segment=safe_subject_segment,
            request_id=safe_request_id,
            correlation_id=get_correlation_id(),
        )
        self._events.append(event)
        return event

    def list(
        self,
        limit: int = 50,
        *,
        offset: int = 0,
        after_event_at: datetime | None = None,
        after_audit_id: str | None = None,
        snapshot_event_at: datetime | None = None,
        snapshot_audit_id: str | None = None,
        actor: str | None = None,
        action: str | None = None,
        entity_id: str | None = None,
        borrower_id: str | None = None,
        subject_clip: str | None = None,
        event_type: str | None = None,
        correlation_id: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[AuditEvent]:
        filtered = self._events
        if actor:
            filtered = [e for e in filtered if e.actor == actor]
        if action:
            filtered = [e for e in filtered if e.action == action]
        if entity_id:
            filtered = [e for e in filtered if e.entity_id == entity_id]
        if borrower_id:
            filtered = [
                e for e in filtered
                if e.entity_id == borrower_id
                or str((e.payload_json or {}).get("borrower_id") or "") == borrower_id
            ]
        if subject_clip:
            filtered = [e for e in filtered if e.subject_clip == subject_clip]
        if event_type:
            filtered = [e for e in filtered if e.event_type == event_type]
        if correlation_id:
            filtered = [e for e in filtered if e.correlation_id == correlation_id]
        if since:
            filtered = [
                e for e in filtered
                if datetime.fromisoformat(e.created_at.replace("Z", "+00:00")) >= since
            ]
        if until:
            filtered = [
                e for e in filtered
                if datetime.fromisoformat(e.created_at.replace("Z", "+00:00")) <= until
            ]
        def position(event: AuditEvent) -> tuple[datetime, str]:
            return (
                datetime.fromisoformat(event.created_at.replace("Z", "+00:00")),
                event.event_id,
            )

        newest_first = sorted(filtered, key=position, reverse=True)
        if snapshot_event_at is not None and snapshot_audit_id is not None:
            snapshot = (snapshot_event_at, snapshot_audit_id)
            newest_first = [event for event in newest_first if position(event) <= snapshot]
        if after_event_at is not None and after_audit_id is not None:
            after = (after_event_at, after_audit_id)
            newest_first = [event for event in newest_first if position(event) < after]
        return newest_first[offset:offset + limit]

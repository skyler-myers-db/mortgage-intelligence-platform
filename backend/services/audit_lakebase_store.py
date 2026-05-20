"""Lakebase-backed implementation of the append-only audit store."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from backend.schemas.audit import AuditEvent
from backend.services.audit_store import (
    _coerce_event_type,
    _validate_top_level_audit_columns,
    build_safe_audit_metadata,
)
from backend.services.lakebase import LakebaseClient, get_lakebase_client
from backend.services.observability import get_correlation_id
from backend.services.pii_redaction import mask_cotality_id

_INSERT_SQL = """
INSERT INTO mip_app.action_audit (
    event_type, actor_email, entity_type, entity_id,
    subject_clip, subject_segment, request_id,
    correlation_id, evidence_ids, metadata
) VALUES (
    %(event_type)s, %(actor_email)s, %(entity_type)s, %(entity_id)s,
    %(subject_clip)s, %(subject_segment)s, %(request_id)s,
    %(correlation_id)s, %(evidence_ids)s, %(metadata)s::jsonb
)
RETURNING audit_id, event_at
"""

_SELECT_SQL_TEMPLATE = """
SELECT audit_id, event_type, actor_email, entity_type, entity_id,
       subject_clip, subject_segment, request_id,
       correlation_id, evidence_ids, metadata, event_at
FROM mip_app.action_audit
{where_clause}
ORDER BY event_at DESC
LIMIT %(limit)s
"""


def _build_insert_params(
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
) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = payload_json or {}
    metadata = build_safe_audit_metadata(payload, action=action)
    payload = {k: v for k, v in metadata.items() if k != "action"}
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
    safe_subject_clip = (
        mask_cotality_id("clip", subject_clip)
        if subject_clip is not None
        else None
    )
    params: dict[str, Any] = {
        "event_type": safe_event_type,
        "actor_email": actor,
        "entity_type": safe_entity_type,
        "entity_id": safe_entity_id,
        "subject_clip": safe_subject_clip,
        "subject_segment": safe_subject_segment,
        "request_id": safe_request_id,
        "correlation_id": get_correlation_id(),
        "evidence_ids": list(evidence_ids or []),
        "metadata": json.dumps(metadata),
    }
    _ = safe_action
    return payload, params


def _audit_event_from_row(
    row: dict[str, Any],
    *,
    actor: str,
    action: str,
    entity_type: str,
    entity_id: str,
    payload_json: dict[str, Any],
    evidence_ids: list[str] | None = None,
    event_type: str | None = None,
    subject_clip: str | None = None,
    subject_segment: str | None = None,
    request_id: str | None = None,
    correlation_id: str | None = None,
) -> AuditEvent:
    event_at = row["event_at"]
    created_at = event_at.isoformat() if hasattr(event_at, "isoformat") else str(event_at)
    return AuditEvent(
        event_id=str(row["audit_id"]),
        actor=actor,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        payload_json=payload_json,
        evidence_ids=evidence_ids or [],
        created_at=created_at,
        event_type=_coerce_event_type(event_type, action),
        subject_clip=subject_clip,
        subject_segment=subject_segment,
        request_id=request_id,
        correlation_id=correlation_id,
    )


def write_audit_event_in_transaction(
    conn: Any,
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
    """Insert one audit row using an already-open Lakebase transaction."""

    payload, params = _build_insert_params(
        actor=actor,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        payload_json=payload_json,
        evidence_ids=evidence_ids,
        event_type=event_type,
        subject_clip=subject_clip,
        subject_segment=subject_segment,
        request_id=request_id,
    )
    row = conn.execute(_INSERT_SQL, params).fetchone()
    if row is None:
        raise RuntimeError("Lakebase INSERT returned no row")
    return _audit_event_from_row(
        dict(row),
        actor=actor,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        payload_json=payload,
        evidence_ids=evidence_ids,
        event_type=event_type,
        subject_clip=params["subject_clip"],
        subject_segment=subject_segment,
        request_id=request_id,
        correlation_id=params["correlation_id"],
    )


class LakebaseAuditStore:
    """Audit store backed by the Lakebase ``mip_app.action_audit`` table."""

    def __init__(self, client: LakebaseClient | None = None) -> None:
        self._client = client or get_lakebase_client()

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
        payload, params = _build_insert_params(
            actor=actor,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            payload_json=payload_json,
            evidence_ids=evidence_ids,
            event_type=event_type,
            subject_clip=subject_clip,
            subject_segment=subject_segment,
            request_id=request_id,
        )
        row = self._client.fetchone(_INSERT_SQL, params)
        if row is None:
            raise RuntimeError("Lakebase INSERT returned no row")
        return _audit_event_from_row(
            row,
            actor=actor,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            payload_json=payload,
            evidence_ids=list(evidence_ids or []),
            event_type=params["event_type"],
            subject_clip=params["subject_clip"],
            subject_segment=subject_segment,
            request_id=request_id,
            correlation_id=params["correlation_id"],
        )

    def list(
        self,
        limit: int = 50,
        *,
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
        clauses: list[str] = []
        params: dict[str, Any] = {"limit": limit}
        if actor:
            clauses.append("actor_email = %(actor)s")
            params["actor"] = actor
        if entity_id:
            clauses.append("entity_id = %(entity_id)s")
            params["entity_id"] = entity_id
        if borrower_id:
            clauses.append("(entity_id = %(borrower_id)s OR metadata->>'borrower_id' = %(borrower_id)s)")
            params["borrower_id"] = borrower_id
        if subject_clip:
            clauses.append("subject_clip = %(subject_clip)s")
            params["subject_clip"] = mask_cotality_id("clip", subject_clip)
        if event_type:
            clauses.append("event_type = %(event_type)s")
            params["event_type"] = event_type
        if correlation_id:
            clauses.append("correlation_id = %(correlation_id)s")
            params["correlation_id"] = correlation_id
        if action:
            clauses.append("metadata->>'action' = %(action)s")
            params["action"] = action
        if since:
            clauses.append("event_at >= %(since)s")
            params["since"] = since
        if until:
            clauses.append("event_at <= %(until)s")
            params["until"] = until
        where_clause = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = _SELECT_SQL_TEMPLATE.format(where_clause=where_clause)
        rows = self._client.fetchall(sql, params, limit=limit)
        out: list[AuditEvent] = []
        for row in rows:
            metadata = row.get("metadata") or {}
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except json.JSONDecodeError:
                    metadata = {}
            action = metadata.pop("action", row["event_type"])
            out.append(
                AuditEvent(
                    event_id=str(row["audit_id"]),
                    actor=row["actor_email"],
                    action=action,
                    entity_type=row.get("entity_type") or "",
                    entity_id=row.get("entity_id") or "",
                    payload_json=metadata,
                    evidence_ids=list(row.get("evidence_ids") or []),
                    created_at=row["event_at"].isoformat(),
                    event_type=row["event_type"],
                    subject_clip=(
                        mask_cotality_id("clip", row.get("subject_clip"))
                        if row.get("subject_clip") is not None
                        else None
                    ),
                    subject_segment=row.get("subject_segment"),
                    request_id=row.get("request_id"),
                    correlation_id=row.get("correlation_id"),
                )
            )
        return out

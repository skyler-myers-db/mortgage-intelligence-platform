"""Lakebase-backed saved workspace/inbox state.

This service owns the actor-scoped "saved leads" and "saved drafts"
surface. It deliberately stores only synthetic borrower ids, coarse
location, and offer metadata. Draft free text is scrubbed before Lakebase
storage, and every state-changing statement includes an action_audit
insert in the same SQL statement.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable
from uuid import uuid4

from backend.schemas.workspace import (
    SavedDraft,
    SavedDraftInput,
    SavedLead,
    SavedLeadInput,
    WorkspaceMutationResponse,
    WorkspaceState,
)
from backend.services.audit_store import build_safe_audit_metadata
from backend.services.lakebase import LakebaseClient, get_lakebase_client
from backend.services.observability import get_correlation_id
from backend.services.pii_redaction import scrub_free_text


@runtime_checkable
class WorkspaceStore(Protocol):
    def list(self, *, actor: str) -> WorkspaceState: ...

    def save_lead(self, *, actor: str, lead: SavedLeadInput) -> SavedLead: ...

    def save_leads_from_genie_action(
        self,
        *,
        actor: str,
        borrower_ids: list[str],
        request_id: str,
        entity_id: str,
        metadata: dict[str, Any],
    ) -> tuple[int, str | None]: ...

    def delete_lead(
        self, *, actor: str, borrower_id: str
    ) -> WorkspaceMutationResponse: ...

    def save_draft(self, *, actor: str, draft: SavedDraftInput) -> SavedDraft: ...

    def delete_draft(
        self, *, actor: str, borrower_id: str, channel: str = "email"
    ) -> WorkspaceMutationResponse: ...


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        dt = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return dt.isoformat()
    if value:
        return str(value)
    return datetime.now(UTC).isoformat()


def _audit_metadata(action: str, payload: dict[str, Any] | None = None) -> str:
    return json.dumps(build_safe_audit_metadata(payload, action=action))


def _lead_from_row(row: dict[str, Any]) -> SavedLead:
    return SavedLead(
        borrower_id=str(row["borrower_id"]),
        city=row.get("city"),
        state=row.get("state"),
        zip=row.get("zip"),
        recommended_offer=row.get("recommended_offer"),
        opportunity_score=row.get("opportunity_score"),
        confidence=row.get("confidence"),
        saved_at=_iso(row.get("saved_at")),
        updated_at=_iso(row.get("updated_at")),
    )


def _draft_from_row(row: dict[str, Any]) -> SavedDraft:
    return SavedDraft(
        borrower_id=str(row["borrower_id"]),
        offer_code=row.get("offer_code"),
        channel=row.get("channel") or "email",
        body=row.get("body") or "",
        saved_at=_iso(row.get("saved_at")),
        updated_at=_iso(row.get("updated_at")),
    )


_SAVE_LEAD_SQL = """
WITH upsert AS (
  INSERT INTO mip_app.saved_leads (
    actor_email, borrower_id, city, state, zip,
    recommended_offer, opportunity_score, confidence,
    saved_at, updated_at, deleted_at
  ) VALUES (
    %(actor_email)s, %(borrower_id)s, %(city)s, %(state)s, %(zip)s,
    %(recommended_offer)s, %(opportunity_score)s, %(confidence)s,
    now(), now(), NULL
  )
  ON CONFLICT (actor_email, borrower_id) DO UPDATE SET
    city = EXCLUDED.city,
    state = EXCLUDED.state,
    zip = EXCLUDED.zip,
    recommended_offer = EXCLUDED.recommended_offer,
    opportunity_score = EXCLUDED.opportunity_score,
    confidence = EXCLUDED.confidence,
    updated_at = now(),
    deleted_at = NULL
  RETURNING borrower_id, city, state, zip, recommended_offer,
            opportunity_score, confidence, saved_at, updated_at
),
audit AS (
  INSERT INTO mip_app.action_audit (
    event_type, actor_email, entity_type, entity_id,
    request_id, correlation_id, evidence_ids, metadata
  )
  SELECT
    'SAVE_LEAD', %(actor_email)s, 'saved_lead', borrower_id,
    %(request_id)s, %(correlation_id)s, ARRAY[]::TEXT[], %(metadata)s::jsonb
  FROM upsert
  RETURNING audit_id
)
SELECT upsert.*, audit.audit_id
FROM upsert CROSS JOIN audit
"""


_SAVE_LEADS_FROM_GENIE_ACTION_SQL = """
WITH inserted_audit AS (
  INSERT INTO mip_app.action_audit (
    event_type, actor_email, entity_type, entity_id,
    request_id, correlation_id, evidence_ids, metadata
  ) VALUES (
    'GENIE_ACTION_SAVE_BORROWERS',
    %(actor_email)s,
    'genie_action',
    %(entity_id)s,
    %(request_id)s,
    %(correlation_id)s,
    ARRAY[]::TEXT[],
    %(metadata)s::jsonb
  )
  ON CONFLICT (actor_email, request_id, event_type)
    WHERE request_id IS NOT NULL AND left(event_type, 13) = 'GENIE_ACTION_'
    DO NOTHING
  RETURNING audit_id, metadata
),
existing_audit AS (
  SELECT audit_id, metadata
  FROM mip_app.action_audit
  WHERE actor_email = %(actor_email)s
    AND request_id = %(request_id)s
    AND event_type = 'GENIE_ACTION_SAVE_BORROWERS'
    AND NOT EXISTS (SELECT 1 FROM inserted_audit)
  LIMIT 1
),
audit AS (
  SELECT audit_id, metadata, TRUE AS inserted FROM inserted_audit
  UNION ALL
  SELECT audit_id, metadata, FALSE AS inserted FROM existing_audit
),
input AS (
  SELECT DISTINCT unnest(%(borrower_ids)s::text[]) AS borrower_id
),
upsert AS (
  INSERT INTO mip_app.saved_leads (
    actor_email, borrower_id, city, state, zip,
    recommended_offer, opportunity_score, confidence,
    saved_at, updated_at, deleted_at
  )
  SELECT
    %(actor_email)s, borrower_id, NULL, NULL, NULL,
    NULL, NULL, NULL,
    now(), now(), NULL
  FROM input
  WHERE borrower_id ~ '^B-[A-Za-z0-9][A-Za-z0-9_-]{0,126}$'
    AND (SELECT inserted FROM audit)
  ON CONFLICT (actor_email, borrower_id) DO UPDATE SET
    updated_at = now(),
    deleted_at = NULL
  RETURNING borrower_id
)
SELECT
  CASE
    WHEN (SELECT inserted FROM audit) THEN COUNT(upsert.borrower_id)::int
    ELSE COALESCE(NULLIF((SELECT metadata ->> 'saved_count' FROM audit), '')::int, 0)
  END AS saved_count,
  (SELECT audit_id FROM audit) AS audit_id
FROM audit
LEFT JOIN upsert ON TRUE
GROUP BY audit.audit_id, audit.inserted, audit.metadata
"""


_DELETE_LEAD_SQL = """
WITH changed AS (
  UPDATE mip_app.saved_leads
  SET deleted_at = now(), updated_at = now()
  WHERE actor_email = %(actor_email)s
    AND borrower_id = %(borrower_id)s
    AND deleted_at IS NULL
  RETURNING borrower_id
),
audit AS (
  INSERT INTO mip_app.action_audit (
    event_type, actor_email, entity_type, entity_id,
    request_id, correlation_id, evidence_ids, metadata
  )
  SELECT
    'UNSAVE_LEAD', %(actor_email)s, 'saved_lead', borrower_id,
    %(request_id)s, %(correlation_id)s, ARRAY[]::TEXT[], %(metadata)s::jsonb
  FROM changed
  RETURNING audit_id
)
SELECT changed.borrower_id, audit.audit_id
FROM changed CROSS JOIN audit
"""


_SAVE_DRAFT_SQL = """
WITH upsert AS (
  INSERT INTO mip_app.outreach_drafts (
    actor_email, borrower_id, offer_code, channel, body,
    status, saved_at, updated_at, deleted_at
  ) VALUES (
    %(actor_email)s, %(borrower_id)s, %(offer_code)s, %(channel)s, %(body)s,
    'draft', now(), now(), NULL
  )
  ON CONFLICT (actor_email, borrower_id, channel) DO UPDATE SET
    offer_code = EXCLUDED.offer_code,
    body = EXCLUDED.body,
    status = 'draft',
    updated_at = now(),
    deleted_at = NULL
  RETURNING borrower_id, offer_code, channel, body, saved_at, updated_at
),
audit AS (
  INSERT INTO mip_app.action_audit (
    event_type, actor_email, entity_type, entity_id,
    request_id, correlation_id, evidence_ids, metadata
  )
  SELECT
    'SAVE_DRAFT', %(actor_email)s, 'outreach_draft', borrower_id,
    %(request_id)s, %(correlation_id)s, ARRAY[]::TEXT[], %(metadata)s::jsonb
  FROM upsert
  RETURNING audit_id
)
SELECT upsert.*, audit.audit_id
FROM upsert CROSS JOIN audit
"""


_DELETE_DRAFT_SQL = """
WITH changed AS (
  UPDATE mip_app.outreach_drafts
  SET deleted_at = now(), updated_at = now()
  WHERE actor_email = %(actor_email)s
    AND borrower_id = %(borrower_id)s
    AND channel = %(channel)s
    AND deleted_at IS NULL
  RETURNING borrower_id
),
audit AS (
  INSERT INTO mip_app.action_audit (
    event_type, actor_email, entity_type, entity_id,
    request_id, correlation_id, evidence_ids, metadata
  )
  SELECT
    'DELETE_DRAFT', %(actor_email)s, 'outreach_draft', borrower_id,
    %(request_id)s, %(correlation_id)s, ARRAY[]::TEXT[], %(metadata)s::jsonb
  FROM changed
  RETURNING audit_id
)
SELECT changed.borrower_id, audit.audit_id
FROM changed CROSS JOIN audit
"""


_LIST_LEADS_SQL = """
SELECT borrower_id, city, state, zip, recommended_offer,
       opportunity_score, confidence, saved_at, updated_at
FROM mip_app.saved_leads
WHERE actor_email = %(actor_email)s
  AND deleted_at IS NULL
ORDER BY updated_at DESC
LIMIT %(limit)s
"""


_LIST_DRAFTS_SQL = """
SELECT borrower_id, offer_code, channel, body, saved_at, updated_at
FROM mip_app.outreach_drafts
WHERE actor_email = %(actor_email)s
  AND deleted_at IS NULL
ORDER BY updated_at DESC
LIMIT %(limit)s
"""


class LakebaseWorkspaceStore:
    def __init__(self, client: LakebaseClient | None = None) -> None:
        self._client = client or get_lakebase_client()

    def list(self, *, actor: str) -> WorkspaceState:
        params = {"actor_email": actor, "limit": 100}
        lead_rows = self._client.fetchall(_LIST_LEADS_SQL, params, limit=100)
        draft_rows = self._client.fetchall(_LIST_DRAFTS_SQL, params, limit=100)
        return WorkspaceState(
            saved_leads=[_lead_from_row(row) for row in lead_rows],
            saved_drafts=[_draft_from_row(row) for row in draft_rows],
        )

    def save_lead(self, *, actor: str, lead: SavedLeadInput) -> SavedLead:
        request_id = str(uuid4())
        params: dict[str, Any] = {
            "actor_email": actor,
            "borrower_id": lead.borrower_id,
            "city": lead.city,
            "state": lead.state,
            "zip": lead.zip,
            "recommended_offer": lead.recommended_offer,
            "opportunity_score": lead.opportunity_score,
            "confidence": lead.confidence,
            "request_id": request_id,
            "correlation_id": get_correlation_id(),
            "metadata": _audit_metadata(
                "workspace.save_lead",
                {
                    "borrower_id": lead.borrower_id,
                    "recommended_offer": lead.recommended_offer,
                    "request_id": request_id,
                },
            ),
        }
        row = self._client.fetchone(_SAVE_LEAD_SQL, params)
        if row is None:
            raise RuntimeError("Lakebase saved_leads upsert returned no row")
        return _lead_from_row(row)

    def save_leads_from_genie_action(
        self,
        *,
        actor: str,
        borrower_ids: list[str],
        request_id: str,
        entity_id: str,
        metadata: dict[str, Any],
    ) -> tuple[int, str | None]:
        params = {
            "actor_email": actor,
            "borrower_ids": borrower_ids,
            "entity_id": entity_id,
            "request_id": request_id,
            "correlation_id": get_correlation_id(),
            "metadata": _audit_metadata("genie.save_borrowers", metadata),
        }
        row = self._client.fetchone(_SAVE_LEADS_FROM_GENIE_ACTION_SQL, params)
        if row is None:
            raise RuntimeError("Lakebase Genie save action returned no row")
        return int(row.get("saved_count") or 0), str(row["audit_id"]) if row.get("audit_id") else None

    def delete_lead(
        self, *, actor: str, borrower_id: str
    ) -> WorkspaceMutationResponse:
        request_id = str(uuid4())
        params = {
            "actor_email": actor,
            "borrower_id": borrower_id,
            "request_id": request_id,
            "correlation_id": get_correlation_id(),
            "metadata": _audit_metadata(
                "workspace.unsave_lead",
                {"borrower_id": borrower_id, "request_id": request_id},
            ),
        }
        row = self._client.fetchone(_DELETE_LEAD_SQL, params)
        return WorkspaceMutationResponse(
            ok=row is not None,
            borrower_id=borrower_id,
            audit_event_id=str(row["audit_id"]) if row and row.get("audit_id") else None,
        )

    def save_draft(self, *, actor: str, draft: SavedDraftInput) -> SavedDraft:
        request_id = str(uuid4())
        clean_body = scrub_free_text(draft.body)
        params: dict[str, Any] = {
            "actor_email": actor,
            "borrower_id": draft.borrower_id,
            "offer_code": draft.offer_code,
            "channel": draft.channel,
            "body": clean_body,
            "request_id": request_id,
            "correlation_id": get_correlation_id(),
            "metadata": _audit_metadata(
                "workspace.save_draft",
                {
                    "borrower_id": draft.borrower_id,
                    "workspace_offer_code": draft.offer_code,
                    "channel": draft.channel,
                    "request_id": request_id,
                },
            ),
        }
        row = self._client.fetchone(_SAVE_DRAFT_SQL, params)
        if row is None:
            raise RuntimeError("Lakebase outreach_drafts upsert returned no row")
        return _draft_from_row(row)

    def delete_draft(
        self, *, actor: str, borrower_id: str, channel: str = "email"
    ) -> WorkspaceMutationResponse:
        request_id = str(uuid4())
        params = {
            "actor_email": actor,
            "borrower_id": borrower_id,
            "channel": channel,
            "request_id": request_id,
            "correlation_id": get_correlation_id(),
            "metadata": _audit_metadata(
                "workspace.delete_draft",
                {
                    "borrower_id": borrower_id,
                    "channel": channel,
                    "request_id": request_id,
                },
            ),
        }
        row = self._client.fetchone(_DELETE_DRAFT_SQL, params)
        return WorkspaceMutationResponse(
            ok=row is not None,
            borrower_id=borrower_id,
            audit_event_id=str(row["audit_id"]) if row and row.get("audit_id") else None,
        )


_WORKSPACE_STORE: WorkspaceStore | None = None


def get_workspace_store() -> WorkspaceStore:
    global _WORKSPACE_STORE
    if _WORKSPACE_STORE is None:
        _WORKSPACE_STORE = LakebaseWorkspaceStore()
    return _WORKSPACE_STORE


def _reset_workspace_store_for_tests() -> None:
    global _WORKSPACE_STORE
    _WORKSPACE_STORE = None

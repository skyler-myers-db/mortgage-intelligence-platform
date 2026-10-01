"""Lakebase store for user-saved Lead Queue views (tables-09 phase 2, flow-08).

A separate Protocol from ``WorkspaceStore`` so every existing workspace test
double stays valid. Every statement filters ``actor_email`` -- a view is
visible to, and deletable by, its owner only -- and each write carries its
action_audit insert in the SAME statement: a save or delete the ledger never
saw cannot commit. The audit metadata names the view id, the filter
fingerprint and the request id; never the name or the params.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any, Literal, Protocol
from uuid import uuid4

from backend.schemas.saved_views import SavedView, SavedViewMutationResponse
from backend.services.audit_store import build_safe_audit_metadata
from backend.services.lakebase import LakebaseClient, get_lakebase_client
from backend.services.observability import emit, get_correlation_id
from backend.services.saved_view_params import canonical_saved_view_params

logger = logging.getLogger(__name__)

SAVED_VIEW_SURFACE = "lead_queue"
SAVED_VIEW_LIMIT = 25

SavedViewConflictKind = Literal["duplicate", "limit", "changed"]


class SavedViewConflict(Exception):
    """A save the store refused: a live duplicate name, or the per-actor limit."""

    def __init__(self, kind: SavedViewConflictKind) -> None:
        super().__init__(kind)
        self.kind: SavedViewConflictKind = kind


class SavedViewStore(Protocol):
    def list(self, *, actor: str) -> list[SavedView]: ...

    def create(self, *, actor: str, name: str, params: str, filter_fingerprint: str) -> SavedViewMutationResponse: ...

    def delete(self, *, actor: str, view_id: str) -> SavedViewMutationResponse | None: ...


_LIST_SQL = """
SELECT view_id, name, params, created_at, updated_at
FROM mip_app.saved_views
WHERE actor_email = %(actor_email)s
  AND surface = %(surface)s
  AND deleted_at IS NULL
ORDER BY updated_at DESC
LIMIT %(limit)s
"""

_CREATE_SQL = """
WITH live AS (
  SELECT count(*) AS n
  FROM mip_app.saved_views
  WHERE actor_email = %(actor_email)s
    AND surface = %(surface)s
    AND deleted_at IS NULL
),
inserted AS (
  INSERT INTO mip_app.saved_views (view_id, actor_email, surface, name, name_key, params)
  SELECT %(view_id)s::uuid, %(actor_email)s, %(surface)s, %(name)s, %(name_key)s, %(params)s
  FROM live
  WHERE live.n < %(limit)s
  ON CONFLICT (actor_email, surface, name_key) WHERE deleted_at IS NULL DO NOTHING
  RETURNING view_id
),
audit AS (
  INSERT INTO mip_app.action_audit (
    event_type, actor_email, entity_type, entity_id,
    request_id, correlation_id, evidence_ids, metadata
  )
  SELECT
    'SAVE_QUEUE_VIEW', %(actor_email)s, 'saved_view', view_id::text,
    %(request_id)s, %(correlation_id)s, ARRAY[]::TEXT[], %(metadata)s::jsonb
  FROM inserted
  RETURNING audit_id
)
SELECT inserted.view_id, audit.audit_id
FROM inserted CROSS JOIN audit
"""

_CLASSIFY_SQL = """
SELECT
  EXISTS (
    SELECT 1 FROM mip_app.saved_views
    WHERE actor_email = %(actor_email)s
      AND surface = %(surface)s
      AND name_key = %(name_key)s
      AND deleted_at IS NULL
  ) AS duplicate,
  (
    SELECT count(*) FROM mip_app.saved_views
    WHERE actor_email = %(actor_email)s
      AND surface = %(surface)s
      AND deleted_at IS NULL
  ) AS live
"""

_DELETE_SQL = """
WITH changed AS (
  UPDATE mip_app.saved_views
  SET deleted_at = now(), updated_at = now()
  WHERE actor_email = %(actor_email)s
    AND surface = %(surface)s
    AND view_id = %(view_id)s::uuid
    AND deleted_at IS NULL
  RETURNING view_id
),
audit AS (
  INSERT INTO mip_app.action_audit (
    event_type, actor_email, entity_type, entity_id,
    request_id, correlation_id, evidence_ids, metadata
  )
  SELECT
    'DELETE_QUEUE_VIEW', %(actor_email)s, 'saved_view', view_id::text,
    %(request_id)s, %(correlation_id)s, ARRAY[]::TEXT[], %(metadata)s::jsonb
  FROM changed
  RETURNING audit_id
)
SELECT changed.view_id, audit.audit_id
FROM changed CROSS JOIN audit
"""


def _iso(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _metadata(action: str, *, view_id: str, fingerprint: str | None, request_id: str) -> str:
    payload: dict[str, object] = {"saved_view_id": view_id, "request_id": request_id}
    if fingerprint is not None:
        payload["filter_fingerprint"] = fingerprint
    return json.dumps(build_safe_audit_metadata(payload, action=action))


def _view_from_row(row: dict[str, Any]) -> SavedView | None:
    """Re-check a stored row against today's grammar; quarantine a stale one."""

    try:
        params, _fingerprint = canonical_saved_view_params(str(row.get("params") or ""))
        return SavedView(
            view_id=str(row["view_id"]),
            name=str(row["name"]),
            params=params,
            created_at=_iso(row["created_at"]),
            updated_at=_iso(row["updated_at"]),
        )
    except (KeyError, ValueError):
        emit(logger, "saved_view_quarantined", level=logging.WARNING, outcome="quarantined")
        return None


class LakebaseSavedViewStore:
    def __init__(self, client: LakebaseClient | None = None) -> None:
        self._client = client or get_lakebase_client()

    def list(self, *, actor: str) -> list[SavedView]:
        rows = self._client.fetchall(
            _LIST_SQL,
            {"actor_email": actor, "surface": SAVED_VIEW_SURFACE, "limit": SAVED_VIEW_LIMIT},
            limit=SAVED_VIEW_LIMIT,
        )
        return [view for row in rows if (view := _view_from_row(row)) is not None]

    def create(self, *, actor: str, name: str, params: str, filter_fingerprint: str) -> SavedViewMutationResponse:
        view_id = str(uuid4())
        request_id = str(uuid4())
        name_key = name.casefold()
        row = self._client.fetchone(
            _CREATE_SQL,
            {
                "view_id": view_id,
                "actor_email": actor,
                "surface": SAVED_VIEW_SURFACE,
                "name": name,
                "name_key": name_key,
                "params": params,
                "limit": SAVED_VIEW_LIMIT,
                "request_id": request_id,
                "correlation_id": get_correlation_id(),
                "metadata": _metadata(
                    "workspace.save_view",
                    view_id=view_id,
                    fingerprint=filter_fingerprint,
                    request_id=request_id,
                ),
            },
        )
        if row is not None and row.get("audit_id"):
            return SavedViewMutationResponse(ok=True, view_id=view_id, audit_event_id=str(row["audit_id"]))
        verdict = self._client.fetchone(
            _CLASSIFY_SQL,
            {"actor_email": actor, "surface": SAVED_VIEW_SURFACE, "name_key": name_key},
        ) or {}
        if verdict.get("duplicate"):
            raise SavedViewConflict("duplicate")
        if int(verdict.get("live") or 0) >= SAVED_VIEW_LIMIT:
            raise SavedViewConflict("limit")
        raise SavedViewConflict("changed")

    def delete(self, *, actor: str, view_id: str) -> SavedViewMutationResponse | None:
        request_id = str(uuid4())
        row = self._client.fetchone(
            _DELETE_SQL,
            {
                "view_id": view_id,
                "actor_email": actor,
                "surface": SAVED_VIEW_SURFACE,
                "request_id": request_id,
                "correlation_id": get_correlation_id(),
                "metadata": _metadata(
                    "workspace.delete_view",
                    view_id=view_id,
                    fingerprint=None,
                    request_id=request_id,
                ),
            },
        )
        if row is None:
            return None
        audit_id = row.get("audit_id")
        return SavedViewMutationResponse(
            ok=True,
            view_id=view_id,
            audit_event_id=str(audit_id) if audit_id else None,
        )


_SAVED_VIEW_STORE: SavedViewStore | None = None


def get_saved_view_store() -> SavedViewStore:
    global _SAVED_VIEW_STORE
    if _SAVED_VIEW_STORE is None:
        _SAVED_VIEW_STORE = LakebaseSavedViewStore()
    return _SAVED_VIEW_STORE


def _reset_saved_view_store_for_tests() -> None:
    global _SAVED_VIEW_STORE
    _SAVED_VIEW_STORE = None

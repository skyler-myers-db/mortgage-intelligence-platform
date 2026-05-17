"""Test-only in-memory workspace store implementation."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from backend.schemas.workspace import (
    SavedDraft,
    SavedDraftInput,
    SavedLead,
    SavedLeadInput,
    WorkspaceMutationResponse,
    WorkspaceState,
)
from backend.services.pii_redaction import scrub_free_text


class InMemoryWorkspaceStore:
    """Test-only workspace store with actor scoping."""

    def __init__(self) -> None:
        self._leads: dict[tuple[str, str], SavedLead] = {}
        self._drafts: dict[tuple[str, str, str], SavedDraft] = {}

    def list(self, *, actor: str) -> WorkspaceState:
        leads = [
            row for (row_actor, _), row in self._leads.items()
            if row_actor == actor
        ]
        drafts = [
            row for (row_actor, _, _), row in self._drafts.items()
            if row_actor == actor
        ]
        leads.sort(key=lambda row: row.updated_at, reverse=True)
        drafts.sort(key=lambda row: row.updated_at, reverse=True)
        return WorkspaceState(saved_leads=leads, saved_drafts=drafts)

    def save_lead(self, *, actor: str, lead: SavedLeadInput) -> SavedLead:
        key = (actor, lead.borrower_id)
        now = datetime.now(UTC).isoformat()
        prior = self._leads.get(key)
        row = SavedLead(
            **lead.model_dump(),
            saved_at=prior.saved_at if prior else now,
            updated_at=now,
        )
        self._leads[key] = row
        return row

    def save_leads_from_genie_action(
        self,
        *,
        actor: str,
        borrower_ids: list[str],
        request_id: str,
        entity_id: str,
        metadata: dict[str, Any],
    ) -> tuple[int, str | None]:
        _ = (request_id, entity_id, metadata)
        saved = 0
        for borrower_id in borrower_ids:
            if not borrower_id.startswith("B-"):
                continue
            self.save_lead(actor=actor, lead=SavedLeadInput(borrower_id=borrower_id))
            saved += 1
        return saved, f"evt-{uuid4().hex[:12]}"

    def delete_lead(
        self, *, actor: str, borrower_id: str
    ) -> WorkspaceMutationResponse:
        existed = self._leads.pop((actor, borrower_id), None) is not None
        return WorkspaceMutationResponse(
            ok=existed,
            borrower_id=borrower_id,
            audit_event_id=f"evt-{uuid4().hex[:12]}" if existed else None,
        )

    def save_draft(self, *, actor: str, draft: SavedDraftInput) -> SavedDraft:
        clean = draft.model_copy(update={"body": scrub_free_text(draft.body)})
        key = (actor, clean.borrower_id, clean.channel)
        now = datetime.now(UTC).isoformat()
        prior = self._drafts.get(key)
        row = SavedDraft(
            **clean.model_dump(),
            saved_at=prior.saved_at if prior else now,
            updated_at=now,
        )
        self._drafts[key] = row
        return row

    def delete_draft(
        self, *, actor: str, borrower_id: str, channel: str = "email"
    ) -> WorkspaceMutationResponse:
        existed = self._drafts.pop((actor, borrower_id, channel), None) is not None
        return WorkspaceMutationResponse(
            ok=existed,
            borrower_id=borrower_id,
            audit_event_id=f"evt-{uuid4().hex[:12]}" if existed else None,
        )

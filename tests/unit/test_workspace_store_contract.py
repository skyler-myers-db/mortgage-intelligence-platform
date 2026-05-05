from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from backend.schemas.workspace import SavedDraftInput, SavedLeadInput
from backend.services.workspace_store import LakebaseWorkspaceStore


class _RecordingLakebase:
    def __init__(self) -> None:
        self.fetchones: list[tuple[str, dict[str, Any]]] = []
        self.fetchalls: list[tuple[str, dict[str, Any], int]] = []

    def fetchone(
        self,
        sql: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        p = params or {}
        self.fetchones.append((sql, p))
        now = datetime.now(UTC)
        if "mip_app.saved_leads" in sql:
            return {
                "borrower_id": p["borrower_id"],
                "city": p.get("city"),
                "state": p.get("state"),
                "zip": p.get("zip"),
                "recommended_offer": p.get("recommended_offer"),
                "opportunity_score": p.get("opportunity_score"),
                "confidence": p.get("confidence"),
                "saved_at": now,
                "updated_at": now,
                "audit_id": "audit-save-lead",
            }
        if "mip_app.outreach_drafts" in sql:
            return {
                "borrower_id": p["borrower_id"],
                "offer_code": p.get("offer_code"),
                "channel": p.get("channel"),
                "body": p.get("body"),
                "saved_at": now,
                "updated_at": now,
                "audit_id": "audit-save-draft",
            }
        return {"borrower_id": p["borrower_id"], "audit_id": "audit-delete"}

    def fetchall(
        self,
        sql: str,
        params: dict[str, Any] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        self.fetchalls.append((sql, params or {}, limit))
        return []


def test_save_lead_is_single_statement_with_audit_insert() -> None:
    client = _RecordingLakebase()
    store = LakebaseWorkspaceStore(client=client)  # type: ignore[arg-type]

    saved = store.save_lead(
        actor="lo@example.com",
        lead=SavedLeadInput(
            borrower_id="B-123",
            city="Seattle",
            state="WA",
            zip="98118",
            recommended_offer="Refinance + HELOC",
            opportunity_score=86,
            confidence=81,
        ),
    )

    sql, params = client.fetchones[0]
    assert saved.borrower_id == "B-123"
    assert "mip_app.saved_leads" in sql
    assert "mip_app.action_audit" in sql
    assert "'SAVE_LEAD'" in sql
    metadata = json.loads(params["metadata"])
    assert metadata == {
        "action": "workspace.save_lead",
        "borrower_id": "B-123",
        "offer_code": "Refinance + HELOC",
        "request_id": params["request_id"],
    }


def test_save_draft_scrubs_body_before_storage_and_audit_is_bodyless() -> None:
    client = _RecordingLakebase()
    store = LakebaseWorkspaceStore(client=client)  # type: ignore[arg-type]

    saved = store.save_draft(
        actor="lo@example.com",
        draft=SavedDraftInput(
            borrower_id="B-123",
            offer_code="OFFER-123",
            channel="email",
            body="Call 212-555-1212 at 123 Main St.",
        ),
    )

    sql, params = client.fetchones[0]
    assert "mip_app.outreach_drafts" in sql
    assert "mip_app.action_audit" in sql
    assert "'SAVE_DRAFT'" in sql
    assert "[PHONE-REDACTED]" in saved.body
    assert "[ADDRESS-REDACTED]" in saved.body
    metadata = json.loads(params["metadata"])
    assert metadata == {
        "action": "workspace.save_draft",
        "borrower_id": "B-123",
        "offer_code": "OFFER-123",
        "channel": "email",
        "request_id": params["request_id"],
    }
    assert "draft_body" not in metadata

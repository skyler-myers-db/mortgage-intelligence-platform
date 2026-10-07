"""The write-only free-text disposition note is gone (D-shell-deviations-g2).

flow-08 adjacent / critic-05: the optional 500-character Notes box wrote free
text about a consumer into two append-only ledgers (call_dispositions and the
CALL_DISPOSITION audit payload) and showed it nowhere. W5c retired it with
one tolerant release (absent, null or blank accepted); W5d w5-sales-loop
deletes the field. DispositionRequest is extra='forbid', so ANY `notes` key,
blank or null included, is now the app-wide 422 that writes nothing and
echoes no input. No read egresses a legacy note. The column, legacy rows, the
ledger's 'notes' metadata gate and the explorer are unchanged.
"""

from __future__ import annotations

import ast
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.main import app
from backend.schemas.sales import CallDisposition, DispositionRequest
from tests.fixtures import mock_population as mock_data
from tests.fixtures.reviewed_approval import reviewed_approval

ROOT = Path(__file__).resolve().parents[2]
client = TestClient(app)
client.headers.update({"X-Forwarded-Email": "skyler@entrada.ai"})
LO = "lo01@summit.example"


def _string_constants(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]


def test_no_backend_string_still_reads_or_writes_the_disposition_note() -> None:
    """Every SQL statement, payload and docstring under backend/: none names
    call_dispositions together with notes (a re-added SELECT, INSERT or
    payload column fails here)."""
    offenders = [
        f"{path.relative_to(ROOT)}: {text[:80]!r}"
        for path in sorted((ROOT / "backend").rglob("*.py"))
        for text in _string_constants(path)
        if "call_dispositions" in text and re.search(r"\bnotes\b", text)
    ]
    assert offenders == []


def test_the_schema_has_no_notes_field_and_refuses_any_notes_key() -> None:
    assert "notes" not in DispositionRequest.model_fields
    assert "notes" not in CallDisposition.model_fields
    assert DispositionRequest(lo_email="lo@entrada.ai", outcome="connected").model_dump().keys() >= {"lo_email"}
    for value in (None, "", "   ", "Discussed refinance options.", 5):
        with pytest.raises(ValidationError) as caught:
            DispositionRequest.model_validate({"lo_email": "lo@entrada.ai", "outcome": "connected", "notes": value})
        assert [error["type"] for error in caught.value.errors()] == ["extra_forbidden"]


def _approved_and_assigned() -> str:
    borrower_id = mock_data.BORROWERS[0].borrower_id
    # review_mode is required since w5-lead-queue-paging (W5c): the approve
    # certifies the generated draft (tests/fixtures/reviewed_approval.py).
    approved = client.post(
        "/api/outreach/approve",
        json=reviewed_approval(client, borrower_id, request_id=str(uuid4())),
    )
    assert approved.status_code == 200, approved.text
    assigned = client.post(
        f"/api/leads/{borrower_id}/assign",
        json={"assigned_to_email": LO, "strategy": "manual"},
    )
    assert assigned.status_code == 200
    return borrower_id


def _audit_payload(fake: Any, audit_event_id: str) -> dict[str, Any]:
    row = next(row for row in fake.audit_events if str(row["audit_id"]) == audit_event_id)
    return dict(json.loads(row["metadata"]))


def test_a_call_without_a_note_logs_and_stores_no_note(fake_lakebase_client: Any) -> None:
    borrower_id = _approved_and_assigned()

    response = client.post(
        f"/api/leads/{borrower_id}/disposition",
        json={"lo_email": LO, "outcome": "connected", "request_id": str(uuid4())},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert "notes" not in body["disposition"]
    row = next(r for r in fake_lakebase_client.dispositions if r["borrower_id"] == borrower_id)
    assert row["notes"] is None
    payload = _audit_payload(fake_lakebase_client, body["audit_event_id"])
    assert payload["outcome"] == "connected"
    assert "notes" not in payload


@pytest.mark.parametrize(
    "notes",
    [
        pytest.param(None, id="null"),
        pytest.param("", id="empty"),
        pytest.param("   ", id="blank"),
        pytest.param("Discussed refinance options with the borrower.", id="text"),
    ],
)
def test_any_notes_key_is_a_422_that_writes_nothing_and_echoes_no_text(
    fake_lakebase_client: Any, notes: str | None
) -> None:
    borrower_id = _approved_and_assigned()
    before = (len(fake_lakebase_client.dispositions), len(fake_lakebase_client.audit_events))

    response = client.post(
        f"/api/leads/{borrower_id}/disposition",
        json={"lo_email": LO, "outcome": "connected", "request_id": str(uuid4()), "notes": notes},
    )

    assert response.status_code == 422
    assert "Discussed refinance options" not in response.text
    assert (len(fake_lakebase_client.dispositions), len(fake_lakebase_client.audit_events)) == before


def test_the_lifecycle_read_never_egresses_a_legacy_note(fake_lakebase_client: Any) -> None:
    borrower_id = _approved_and_assigned()
    fake_lakebase_client.dispositions.append(
        {
            "disposition_id": uuid4(),
            "borrower_id": borrower_id,
            "lo_email": LO,
            "outcome": "connected",
            "attempt_number": 1,
            "occurred_at": datetime.now(UTC),
            "callback_at": None,
            "notes": "Legacy note about the borrower",
            "audit_event_id": None,
            "request_id": None,
            "created_at": datetime.now(UTC),
        }
    )

    lifecycle = client.get(f"/api/borrowers/{borrower_id}/lifecycle")

    assert lifecycle.status_code == 200, lifecycle.text
    latest = lifecycle.json()["latest_disposition"]
    assert latest["outcome"] == "connected"
    assert "notes" not in latest
    assert "Legacy note" not in lifecycle.text

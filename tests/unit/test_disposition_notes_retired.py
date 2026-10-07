"""The write-only free-text disposition note is retired (D-shell-deviations-g2).

flow-08 adjacent / critic-05: the optional 500-character Notes box wrote free
text about a consumer into two append-only ledgers (call_dispositions and the
CALL_DISPOSITION audit payload) and showed it nowhere. For one tolerant
release the request field survives so a tab loaded before the deploy still
logs a call: absent, null or blank is accepted and stores nothing; any other
value is a 422 carrying the retirement copy. No read egresses a legacy note.
The column, legacy rows, the ledger's 'notes' metadata gate and the explorer
are unchanged.
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

from backend.main import app
from backend.schemas.sales import DISPOSITION_NOTES_RETIRED, CallDisposition, DispositionRequest
from tests.fixtures import mock_population as mock_data

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


def test_the_schema_accepts_only_an_absent_null_or_blank_note() -> None:
    for blank in (None, "", "   ", "\n\t"):
        request = DispositionRequest(lo_email="lo@entrada.ai", outcome="connected", notes=blank)
        assert request.notes is None
    assert DispositionRequest(lo_email="lo@entrada.ai", outcome="connected").notes is None
    for value in ("Discussed refinance options.", "x", 5):
        with pytest.raises(ValueError, match="Free-text disposition notes are retired"):
            DispositionRequest(lo_email="lo@entrada.ai", outcome="connected", notes=value)
    assert "notes" not in CallDisposition.model_fields
    # extra='forbid' stays: an unknown key is still refused.
    with pytest.raises(ValueError):
        DispositionRequest.model_validate({"lo_email": "lo@entrada.ai", "outcome": "connected", "memo": "x"})


def _approved_and_assigned() -> str:
    borrower_id = mock_data.BORROWERS[0].borrower_id
    draft = client.post("/api/outreach/draft", json={"borrower_id": borrower_id, "channel": "email"})
    assert draft.status_code == 200
    approved = client.post(
        "/api/outreach/approve",
        json={
            "borrower_id": borrower_id,
            "offer_code": "refi_plus_heloc",
            "channel": "email",
            "draft_subject": draft.json()["subject"],
            "draft_body": draft.json()["body"],
            "request_id": str(uuid4()),
        },
    )
    assert approved.status_code == 200
    assigned = client.post(
        f"/api/leads/{borrower_id}/assign",
        json={"assigned_to_email": LO, "strategy": "manual"},
    )
    assert assigned.status_code == 200
    return borrower_id


def _audit_payload(fake: Any, audit_event_id: str) -> dict[str, Any]:
    row = next(row for row in fake.audit_events if str(row["audit_id"]) == audit_event_id)
    return dict(json.loads(row["metadata"]))


@pytest.mark.parametrize(
    "notes",
    [pytest.param({}, id="absent"), pytest.param({"notes": None}, id="null"), pytest.param({"notes": "   "}, id="blank")],
)
def test_an_absent_null_or_blank_note_logs_the_call_and_stores_no_note(
    fake_lakebase_client: Any, notes: dict[str, Any]
) -> None:
    borrower_id = _approved_and_assigned()

    response = client.post(
        f"/api/leads/{borrower_id}/disposition",
        json={"lo_email": LO, "outcome": "connected", "request_id": str(uuid4()), **notes},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert "notes" not in body["disposition"]
    row = next(r for r in fake_lakebase_client.dispositions if r["borrower_id"] == borrower_id)
    assert row["notes"] is None
    payload = _audit_payload(fake_lakebase_client, body["audit_event_id"])
    assert payload["outcome"] == "connected"
    assert "notes" not in payload


def test_a_note_is_a_422_with_the_retirement_copy_and_nothing_is_written(
    fake_lakebase_client: Any,
) -> None:
    borrower_id = _approved_and_assigned()
    before = (len(fake_lakebase_client.dispositions), len(fake_lakebase_client.audit_events))

    response = client.post(
        f"/api/leads/{borrower_id}/disposition",
        json={"lo_email": LO, "outcome": "connected", "notes": "Discussed refinance options."},
    )

    assert response.status_code == 422
    assert DISPOSITION_NOTES_RETIRED in response.text
    assert "Discussed refinance options." not in response.text
    assert (len(fake_lakebase_client.dispositions), len(fake_lakebase_client.audit_events)) == before


def test_a_replay_still_matches_without_a_note(fake_lakebase_client: Any) -> None:
    borrower_id = _approved_and_assigned()
    payload = {"lo_email": LO, "outcome": "connected", "request_id": str(uuid4())}

    first = client.post(f"/api/leads/{borrower_id}/disposition", json=payload)
    replay = client.post(f"/api/leads/{borrower_id}/disposition", json={**payload, "notes": "  "})

    assert first.status_code == 200 and replay.status_code == 200, replay.text
    assert replay.json()["disposition"]["disposition_id"] == first.json()["disposition"]["disposition_id"]
    assert len([r for r in fake_lakebase_client.dispositions if r.get("request_id") == payload["request_id"]]) == 1


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

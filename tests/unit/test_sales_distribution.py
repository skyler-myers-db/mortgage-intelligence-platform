"""Lead distribution records the allocation that actually ran (audit wow-power-5 step 1).

The store allocates ``borrower_ids[i] -> lo_emails[i % len(lo_emails)]`` for
every strategy: nothing score-orders the ids and capacity/region are not read.
The Lead Queue used to send ``score_balanced`` whenever several leads went to
one officer, so the LEAD_DISTRIBUTE audit row claimed a balancing that never
happened. The queue now sends ``manual`` for one officer and ``round_robin``
for two or more; these tests pin that both are recorded verbatim, on the
assignment rows AND on the audit payload, and that round-robin alternates in
request order.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient

from backend.main import app
from tests.fixtures import mock_population as mock_data

client = TestClient(app)
client.headers.update({"X-Forwarded-Email": "skyler@entrada.ai"})


def _approve_for_sales(borrower_id: str) -> None:
    draft = client.post(
        "/api/outreach/draft",
        json={"borrower_id": borrower_id, "channel": "email"},
    )
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


def _distribute_audit_payloads(fake_lakebase_client: Any) -> list[dict[str, Any]]:
    """The LEAD_DISTRIBUTE rows' stored metadata (the audit payload as written)."""
    payloads: list[dict[str, Any]] = []
    for row in fake_lakebase_client.audit_events:
        if row.get("event_type") != "LEAD_DISTRIBUTE":
            continue
        raw = row.get("metadata")
        payloads.append(json.loads(raw) if isinstance(raw, str) else dict(raw or {}))
    return payloads


def test_manual_distribution_to_one_officer_records_manual(fake_lakebase_client: Any) -> None:
    borrower_ids = [mock_data.BORROWERS[index].borrower_id for index in (10, 11, 12)]
    for borrower_id in borrower_ids:
        _approve_for_sales(borrower_id)

    response = client.post(
        "/api/sales/distribute",
        json={
            "borrower_ids": borrower_ids,
            "lo_emails": ["lo01@summit.example"],
            "strategy": "manual",
            "expires_in_hours": 24,
            "request_id": str(uuid4()),
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["strategy"] == "manual"
    assert body["assigned_count"] == 3
    assert [a["borrower_id"] for a in body["assignments"]] == borrower_ids
    assert {a["strategy"] for a in body["assignments"]} == {"manual"}
    assert {a["assigned_to_email"] for a in body["assignments"]} == {"lo01@summit.example"}
    payloads = _distribute_audit_payloads(fake_lakebase_client)
    assert len(payloads) == 1
    assert payloads[0]["strategy"] == "manual"
    assert payloads[0]["per_lo_counts"] == {"lo01@summit.example": 3}


def test_round_robin_alternates_in_request_order(fake_lakebase_client: Any) -> None:
    borrower_ids = [mock_data.BORROWERS[index].borrower_id for index in (10, 11, 12)]
    for borrower_id in borrower_ids:
        _approve_for_sales(borrower_id)
    lo_emails = ["lo02@summit.example", "lo01@summit.example"]

    response = client.post(
        "/api/sales/distribute",
        json={
            "borrower_ids": borrower_ids,
            "lo_emails": lo_emails,
            "strategy": "round_robin",
            "expires_in_hours": 24,
            "request_id": str(uuid4()),
        },
    )

    assert response.status_code == 200, response.text
    assignments = response.json()["assignments"]
    # Request order, not score order: lo02, lo01, lo02.
    assert [(a["borrower_id"], a["assigned_to_email"]) for a in assignments] == [
        (borrower_ids[0], "lo02@summit.example"),
        (borrower_ids[1], "lo01@summit.example"),
        (borrower_ids[2], "lo02@summit.example"),
    ]
    assert {a["strategy"] for a in assignments} == {"round_robin"}
    payloads = _distribute_audit_payloads(fake_lakebase_client)
    assert [payload["strategy"] for payload in payloads] == ["round_robin"]

"""Decision receipt contract: ``GET /api/audit/receipt/{audit_event_id}``.

The receipt is read back from the persisted audit row the approve / reject
route wrote (through the in-process audit ledger), never echoed from the
request. Pinned here:

* actor isolation -- the approver reads their own receipt, another actor
  gets 403, an admin reads any;
* 404 for an unknown, malformed or non-decision event id; 401 without an
  edge-authenticated identity;
* the field allowlist -- a closed key set, so a new audit metadata column
  (or the outreach body already stored on the row) cannot leak;
* the evidence-asset registry the receipt cites equals the Offer
  Orchestrator's for every offer branch;
* the Lakebase store filters by ``audit_id`` and never casts a non-UUID.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.api.offers import _sources_for as orchestrator_sources_for
from backend.config.settings import settings
from backend.main import app
from backend.schemas.audit import AuditEvent
from backend.schemas.audit_receipt import DecisionReceipt
from backend.services.audit_lakebase_store import LakebaseAuditStore
from backend.services.audit_store import get_audit_store
from backend.services.audit_store_receipt import (
    build_decision_receipt,
    decision_evidence_assets,
)
from backend.services.scoring import NBO_PRODUCT_LABELS
from tests.fixtures.in_memory_audit_store import InMemoryAuditStore

RECEIPT_ROUTE = "/api/audit/receipt/{audit_event_id}"
RECEIPT_ROUTE_V1 = "/api/v1/audit/receipt/{audit_event_id}"

ALICE = "alice.approver@summit.example"
BOB = "bob.approver@summit.example"
CAROL = "carol.admin@summit.example"
TEAM_MEMBER = "skyler@entrada.ai"

# The write needs approver admission; in local/test the admin group is the
# compatibility path. The reads below drop the group so the actor rule, not
# the admin rule, is what admits (or refuses) them.
ALICE_WRITE = {"X-Forwarded-Email": ALICE, "X-Forwarded-Groups": "mip-admin"}
ALICE_READ = {"X-Forwarded-Email": ALICE, "X-Forwarded-Groups": ""}
BOB_READ = {"X-Forwarded-Email": BOB, "X-Forwarded-Groups": ""}
CAROL_ADMIN = {"X-Forwarded-Email": CAROL, "X-Forwarded-Groups": "mip-admin"}
TEAM_WRITE = {"X-Forwarded-Email": TEAM_MEMBER, "X-Forwarded-Groups": "mip-admin"}

_DISCLOSURE = "Summit Mortgage, NMLS #123456. Equal Housing Lender. Reply unsubscribe to opt out."
_DRAFT_BODY = f"Contact a loan officer to review available mortgage options. {_DISCLOSURE}"
_DRAFT_SUBJECT = "Your mortgage review"

EXPECTED_RECEIPT_FIELDS = frozenset(
    {
        "audit_event_id",
        "event_type",
        "decision",
        "approval_id",
        "borrower_id",
        "offer_code",
        "offer_label",
        "campaign_id",
        "variant_name",
        "channel",
        "rationale_code",
        "copy_generation_id",
        "copy_hash",
        "approver",
        "request_id",
        "correlation_id",
        "created_at",
        "evidence_ids",
        "evidence_assets",
    }
)

client = TestClient(app)


@pytest.fixture(autouse=True)
def _trusted_test_edge(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "trust_forwarded_headers", True)


@pytest.fixture
def audit_store() -> InMemoryAuditStore:
    store = app.dependency_overrides[get_audit_store]()
    assert isinstance(store, InMemoryAuditStore)
    return store


def _approve(headers: dict[str, str], **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "borrower_id": "B-48291",
        "offer_code": "refi_plus_heloc",
        "channel": "email",
        "draft_subject": _DRAFT_SUBJECT,
        "draft_body": _DRAFT_BODY,
        "rationale": "Approved after reviewing the governed draft.",
        **overrides,
    }
    response = client.post("/api/outreach/approve", json=payload, headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["approved"] is True
    assert body["audit_event_id"]
    return body


def _reject(headers: dict[str, str]) -> dict[str, Any]:
    response = client.post(
        "/api/outreach/reject",
        json={
            "borrower_id": "B-48291",
            "offer_code": "refi_plus_heloc",
            "channel": "email",
            "rationale_code": "low_intent",
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["rejected"] is True
    return body


def _receipt(audit_event_id: str, headers: dict[str, str]):
    return client.get(RECEIPT_ROUTE_V1.format(audit_event_id=audit_event_id), headers=headers)


def test_actor_reads_back_the_ledger_row_their_approval_wrote() -> None:
    approved = _approve(ALICE_WRITE)

    response = _receipt(approved["audit_event_id"], ALICE_READ)

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "private, no-store"
    receipt = response.json()
    assert receipt["audit_event_id"] == approved["audit_event_id"]
    assert receipt["approval_id"] == approved["approval_id"]
    assert receipt["decision"] == "approved"
    assert receipt["event_type"] == "APPROVE"
    assert receipt["borrower_id"] == "B-48291"
    assert receipt["offer_code"] == "refi_plus_heloc"
    assert receipt["offer_label"] == "Refinance + home-equity review"
    assert receipt["channel"] == "email"
    assert receipt["approver"] == ALICE
    assert receipt["request_id"]
    assert receipt["created_at"]
    assert receipt["evidence_ids"], "the approval binds the borrower's evidence ids"
    assert "mip.gold.fn_next_best_offer" in receipt["evidence_assets"]
    assert "mip.gold.fn_lead_score" in receipt["evidence_assets"]


def test_another_actor_is_refused_with_403() -> None:
    approved = _approve(ALICE_WRITE)

    response = _receipt(approved["audit_event_id"], BOB_READ)

    assert response.status_code == 403
    assert response.json()["detail"] == "forbidden"


def test_admin_reads_any_receipt_and_sees_the_stored_approver() -> None:
    approved = _approve(ALICE_WRITE)

    response = _receipt(approved["audit_event_id"], CAROL_ADMIN)

    assert response.status_code == 200, response.text
    assert response.json()["approver"] == ALICE


@pytest.mark.parametrize(
    "audit_event_id",
    [str(uuid4()), "evt-000000000000", "not%20an%20id", "alice@summit.example"],
)
def test_unknown_or_malformed_ids_are_404(audit_event_id: str) -> None:
    response = _receipt(audit_event_id, CAROL_ADMIN)

    assert response.status_code == 404
    assert response.json()["detail"] == "audit event not found"


def test_non_decision_rows_have_no_receipt(audit_store: InMemoryAuditStore) -> None:
    event = audit_store.write(
        actor=ALICE,
        action="view_leads",
        entity_type="lead_list",
        entity_id="queue",
        event_type="VIEW_LEADS",
    )

    response = _receipt(event.event_id, ALICE_READ)

    assert response.status_code == 404
    assert response.json()["detail"] == "audit event is not a decision"


def test_receipt_requires_an_edge_authenticated_identity() -> None:
    approved = _approve(ALICE_WRITE)

    response = _receipt(
        approved["audit_event_id"],
        {"X-Forwarded-Email": "", "X-Forwarded-Groups": ""},
    )

    assert response.status_code == 401


def test_field_allowlist_is_closed_and_never_carries_the_copy() -> None:
    approved = _approve(
        ALICE_WRITE,
        assigned_to_email="lo01@summit.example",
        follow_up_in_days=5,
    )

    response = _receipt(approved["audit_event_id"], CAROL_ADMIN)

    assert response.status_code == 200, response.text
    receipt = response.json()
    assert set(receipt) == EXPECTED_RECEIPT_FIELDS
    assert set(DecisionReceipt.model_fields) == EXPECTED_RECEIPT_FIELDS
    text = response.text
    assert _DRAFT_BODY not in text
    assert _DRAFT_SUBJECT not in text
    assert "Approved after reviewing" not in text
    assert "lo01@summit.example" not in text, "other staff emails never cross the boundary"
    for key in ("draft_body", "draft_subject", "rationale", "assigned_to_email", "decision_inputs"):
        assert key not in receipt


def test_a_new_metadata_key_cannot_leak_through_the_projection() -> None:
    event = AuditEvent(
        event_id="evt-receipt-pin",
        actor=ALICE,
        action="outreach.approve",
        entity_type="approval",
        entity_id="11111111-1111-4111-8111-111111111111",
        payload_json={
            "borrower_id": "B-48291",
            "offer_code": "refi",
            "channel": "sms",
            "draft_generation_id": "22222222-2222-4222-8222-222222222222",
            "draft_response_hash": "f" * 64,
            "draft_body": "never on the receipt",
            "future_column": "never on the receipt either",
        },
        evidence_ids=["ev-1"],
        created_at=datetime.now(UTC).isoformat(),
        event_type="APPROVE",
        request_id="33333333-3333-4333-8333-333333333333",
        correlation_id="corr-1",
    )

    receipt = build_decision_receipt(event)

    assert receipt is not None
    dumped = receipt.model_dump()
    assert set(dumped) == EXPECTED_RECEIPT_FIELDS
    assert "never on the receipt" not in receipt.model_dump_json()
    assert dumped["copy_hash"] == "f" * 64
    assert dumped["copy_generation_id"] == "22222222-2222-4222-8222-222222222222"
    assert dumped["approval_id"] == "11111111-1111-4111-8111-111111111111"
    assert dumped["evidence_assets"] == decision_evidence_assets("refi")


def test_rejection_receipt_carries_the_closed_reason_code() -> None:
    rejected = _reject(ALICE_WRITE)

    response = _receipt(rejected["audit_event_id"], ALICE_READ)

    assert response.status_code == 200, response.text
    receipt = response.json()
    assert receipt["decision"] == "rejected"
    assert receipt["event_type"] == "OUTREACH_REJECT"
    assert receipt["rationale_code"] == "low_intent"
    assert receipt["copy_hash"] is None


def test_lifecycle_carries_the_audit_event_id_of_the_latest_decision() -> None:
    approved = _approve(TEAM_WRITE)

    response = client.get("/api/v1/borrowers/B-48291/lifecycle", headers=TEAM_WRITE)

    assert response.status_code == 200, response.text
    assert response.json()["audit_event_id"] == approved["audit_event_id"]


@pytest.mark.parametrize("has_heloc_propensity_trigger", [False, True])
@pytest.mark.parametrize("offer_code", sorted(set(NBO_PRODUCT_LABELS) | {"nurture", "recapture"}))
def test_receipt_evidence_assets_match_the_orchestrator_registry(
    offer_code: str,
    has_heloc_propensity_trigger: bool,
) -> None:
    assert decision_evidence_assets(
        offer_code, has_heloc_propensity_trigger=has_heloc_propensity_trigger
    ) == orchestrator_sources_for(
        offer_code, has_heloc_propensity_trigger=has_heloc_propensity_trigger
    )


def test_lakebase_store_filters_by_audit_id_and_never_casts_a_non_uuid() -> None:
    lakebase = MagicMock()
    lakebase.fetchall.return_value = []
    store = LakebaseAuditStore(client=lakebase)

    assert store.list(limit=1, event_id="evt-not-a-uuid") == []
    lakebase.fetchall.assert_not_called()

    wanted = str(uuid4())
    assert store.list(limit=1, event_id=wanted) == []
    sql, params = lakebase.fetchall.call_args.args[:2]
    assert "audit_id = %(event_id)s::uuid" in sql
    assert params["event_id"] == wanted
    assert params["limit"] == 1

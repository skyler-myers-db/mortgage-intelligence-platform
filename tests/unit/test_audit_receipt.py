"""Decision receipt contract: ``GET /api/audit/receipt/{audit_event_id}``.

The receipt is read back from the persisted audit row the approve / reject
route wrote (through the in-process audit ledger), never echoed from the
request. Pinned here:

* actor isolation -- the approver reads their own receipt, another actor
  gets 403, an admin or a configured read-only auditor reads any
  (D-audit-reads-c3);
* 404 for an unknown, malformed or non-decision event id; 401 without an
  edge-authenticated identity;
* the field allowlist -- a closed key set, so a new audit metadata column
  (or the outreach body already stored on the row) cannot leak;
* the evidence-asset registry the receipt cites equals the Offer
  Orchestrator's for every offer branch;
* the Lakebase store filters by ``audit_id`` and never casts a non-UUID;
* an own receipt read appends no audit row; a served read of ANOTHER
  actor's receipt appends exactly one ``VIEW_AUDIT_LEDGER`` row naming it.
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
        "review_mode",
        "bulk_id",
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


def _with_uuid_event_id(audit_store: InMemoryAuditStore, event_id: str) -> str:
    """Give a stored row the UUID id Lakebase issues (the store's own are
    ``evt-`` ids, which the opaque-id policy on ``read_audit_event_id`` refuses).
    """
    new_id = str(uuid4())
    for index, event in enumerate(audit_store._events):
        if event.event_id == event_id:
            audit_store._events[index] = event.model_copy(update={"event_id": new_id})
            return new_id
    raise AssertionError(f"no stored row {event_id}")


def _ledger_reads(audit_store: InMemoryAuditStore, *event_ids: str) -> list[AuditEvent]:
    """VIEW_AUDIT_LEDGER rows naming these ids (the session store is shared)."""
    return [
        row
        for row in audit_store.list(limit=100_000)
        if row.event_type == "VIEW_AUDIT_LEDGER" and row.entity_id in event_ids
    ]


def test_reading_your_own_receipt_writes_no_audit_row(audit_store: InMemoryAuditStore) -> None:
    # The actor's own receipt is the read the UI issues after every decision:
    # re-reading it must never append a row.
    approved = _approve(ALICE_WRITE)
    event_id = _with_uuid_event_id(audit_store, approved["audit_event_id"])
    rows_before = len(audit_store.list(limit=100_000))

    assert _receipt(event_id, ALICE_READ).status_code == 200
    assert _receipt(event_id, ALICE_READ).status_code == 200

    assert len(audit_store.list(limit=100_000)) == rows_before


def test_an_admin_reading_another_actors_receipt_writes_one_ledger_read(
    audit_store: InMemoryAuditStore,
) -> None:
    approved = _approve(ALICE_WRITE)
    event_id = _with_uuid_event_id(audit_store, approved["audit_event_id"])

    assert _receipt(event_id, CAROL_ADMIN).status_code == 200

    (row,) = _ledger_reads(audit_store, event_id)
    assert row.actor == CAROL
    assert (row.action, row.entity_type, row.entity_id) == ("view_audit_ledger", "audit_ledger", event_id)
    assert row.payload_json == {
        "ledger_surface": "receipt",
        "has_cursor": False,
        "returned_row_count": 1,
        "read_audit_event_id": event_id,
    }


def test_an_auditor_reads_another_actors_receipt_and_the_read_is_recorded(
    audit_store: InMemoryAuditStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    approved = _approve(ALICE_WRITE)
    event_id = _with_uuid_event_id(audit_store, approved["audit_event_id"])
    monkeypatch.setattr(settings, "auditor_emails", BOB)

    response = _receipt(event_id, BOB_READ)

    assert response.status_code == 200, response.text
    assert response.json()["approver"] == ALICE
    (row,) = _ledger_reads(audit_store, event_id)
    assert row.actor == BOB
    assert row.payload_json["read_audit_event_id"] == event_id


def test_a_cross_actor_receipt_read_records_the_stored_id_not_the_path_spelling(
    audit_store: InMemoryAuditStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Lakebase matches the path id through ``audit_id = %(event_id)s::uuid``,
    # so an upper-case path reads the same row; the accountability row must
    # name the canonical stored id, not whatever spelling the caller sent.
    approved = _approve(ALICE_WRITE)
    event_id = _with_uuid_event_id(audit_store, approved["audit_event_id"])
    stored_list = audit_store.list

    def _uuid_cast_list(*args: Any, event_id: str | None = None, **kwargs: Any) -> list[AuditEvent]:
        return stored_list(*args, event_id=event_id.lower() if event_id else event_id, **kwargs)

    monkeypatch.setattr(audit_store, "list", _uuid_cast_list)

    assert _receipt(event_id.upper(), CAROL_ADMIN).status_code == 200

    (row,) = _ledger_reads(audit_store, event_id)
    assert row.entity_id == event_id
    assert row.payload_json["read_audit_event_id"] == event_id


def test_a_refused_or_not_a_decision_receipt_writes_no_ledger_read(
    audit_store: InMemoryAuditStore,
) -> None:
    approved = _approve(ALICE_WRITE)
    event_id = _with_uuid_event_id(audit_store, approved["audit_event_id"])
    other = audit_store.write(
        actor=ALICE,
        action="view_leads",
        entity_type="lead_list",
        entity_id="queue",
        event_type="VIEW_LEADS",
    )
    other_id = _with_uuid_event_id(audit_store, other.event_id)

    assert _receipt(event_id, BOB_READ).status_code == 403
    assert _receipt(other_id, CAROL_ADMIN).status_code == 404

    assert _ledger_reads(audit_store, event_id, other_id) == []


# A fixed, well-formed UUID no write ever issued: a ``uuid4()`` here would
# give every pytest-xdist worker a different test id and abort collection
# under CI's ``-n auto``.
_UNKNOWN_AUDIT_EVENT_ID = "00000000-0000-4000-8000-000000000000"


@pytest.mark.parametrize(
    "audit_event_id",
    [_UNKNOWN_AUDIT_EVENT_ID, "evt-000000000000", "not%20an%20id", "alice@summit.example"],
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


def test_decision_writes_store_no_asset_list_so_the_receipt_derives_it(
    audit_store: InMemoryAuditStore,
) -> None:
    """The receipt UI labels its asset chips as the recorded offer branch's.

    That label is true only while no decision write stores an
    ``evidence_assets`` list and the receipt derives it from the stored
    offer code; if a writer starts storing one, this fails and the label
    (DecisionReceipt ``evidenceAssetsNote``) must be revisited.
    """
    approved = _approve(ALICE_WRITE)
    rejected = _reject(ALICE_WRITE)

    for decision in (approved, rejected):
        (row,) = audit_store.list(limit=10, event_id=decision["audit_event_id"])
        assert "evidence_assets" not in (row.payload_json or {})
        receipt = _receipt(decision["audit_event_id"], ALICE_READ).json()
        stored_inputs = (row.payload_json or {}).get("decision_inputs") or {}
        assert receipt["evidence_assets"] == decision_evidence_assets(
            (row.payload_json or {}).get("offer_code"),
            has_heloc_propensity_trigger=stored_inputs.get("has_heloc_propensity_trigger") is True,
        )


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


def test_explorer_pins_one_ledger_row_by_event_id_and_refuses_a_malformed_id() -> None:
    first = _approve(ALICE_WRITE)
    second = _reject(ALICE_WRITE)

    page = client.get(
        "/api/v1/audit/events/page",
        params={"event_id": first["audit_event_id"]},
        headers=CAROL_ADMIN,
    )
    assert page.status_code == 200, page.text
    assert [row["event_id"] for row in page.json()["items"]] == [first["audit_event_id"]]

    events = client.get(
        "/api/v1/audit/events",
        params={"event_id": second["audit_event_id"]},
        headers=CAROL_ADMIN,
    )
    assert events.status_code == 200, events.text
    assert [row["event_id"] for row in events.json()] == [second["audit_event_id"]]

    malformed = client.get(
        "/api/v1/audit/events/page",
        params={"event_id": "alice@summit.example"},
        headers=CAROL_ADMIN,
    )
    assert malformed.status_code == 422
    assert malformed.json()["detail"] == "invalid event_id"


# -- The review ledger on the receipt (audit flow-03, tables-07) -------------------------

_BULK_RUN_ID = "44444444-4444-4444-8444-444444444444"


def _decision_event(event_type: str, action: str, **payload: Any) -> AuditEvent:
    return AuditEvent(
        event_id=f"evt-{uuid4().hex[:12]}",
        actor=ALICE,
        action=action,
        entity_type="approval",
        entity_id=str(uuid4()),
        payload_json={"borrower_id": "B-48291", "offer_code": "heloc", **payload},
        evidence_ids=["ev-1"],
        created_at=datetime.now(UTC).isoformat(),
        event_type=event_type,
    )


@pytest.mark.parametrize(
    "mode", ["individual", "triage", "bulk_sample", "bulk_cohort", "undeclared"]
)
def test_approve_receipt_projects_the_review_mode_and_bulk_id(mode: str) -> None:
    receipt = build_decision_receipt(
        _decision_event("APPROVE", "outreach.approve", review_mode=mode, bulk_id=_BULK_RUN_ID)
    )

    assert receipt is not None
    assert receipt.review_mode == mode
    assert receipt.bulk_id == _BULK_RUN_ID


def test_reject_receipt_projects_its_bulk_id_and_no_review_mode() -> None:
    receipt = build_decision_receipt(
        _decision_event(
            "OUTREACH_REJECT",
            "outreach.reject",
            rationale_code="low_intent",
            bulk_id=_BULK_RUN_ID,
        )
    )

    assert receipt is not None
    assert receipt.decision == "rejected"
    assert receipt.bulk_id == _BULK_RUN_ID
    assert receipt.review_mode is None


@pytest.mark.parametrize(
    ("review_mode", "bulk_id"),
    [
        ("blind", "call 312-555-0100"),
        ("INDIVIDUAL", "alice.approver@summit.example"),
        (True, 42),
        ("", ""),
    ],
)
def test_a_foreign_review_mode_or_a_bad_bulk_id_is_not_projected(
    review_mode: object, bulk_id: object
) -> None:
    receipt = build_decision_receipt(
        _decision_event("APPROVE", "outreach.approve", review_mode=review_mode, bulk_id=bulk_id)
    )

    assert receipt is not None
    assert receipt.review_mode is None
    assert receipt.bulk_id is None
    assert "312-555-0100" not in receipt.model_dump_json()


def test_a_legacy_approve_reads_back_as_undeclared() -> None:
    approved = _approve(ALICE_WRITE)

    receipt = _receipt(approved["audit_event_id"], ALICE_READ).json()

    assert receipt["review_mode"] == "undeclared"
    assert receipt["bulk_id"] is None


def test_reject_receipts_derive_heloc_propensity_assets_from_decision_inputs(
    audit_store: InMemoryAuditStore,
) -> None:
    triggered = build_decision_receipt(
        _decision_event(
            "OUTREACH_REJECT",
            "outreach.reject",
            rationale_code="low_intent",
            decision_inputs={"has_heloc_propensity_trigger": True},
        )
    )
    assert triggered is not None
    assert triggered.evidence_assets == decision_evidence_assets(
        "heloc", has_heloc_propensity_trigger=True
    )
    assert triggered.evidence_assets != decision_evidence_assets("heloc")

    rejected = _reject(ALICE_WRITE)
    (row,) = audit_store.list(limit=10, event_id=rejected["audit_event_id"])
    assert (row.payload_json or {}).get("decision_inputs"), (
        "every OUTREACH_REJECT row now stores the decision inputs the receipt derives from"
    )

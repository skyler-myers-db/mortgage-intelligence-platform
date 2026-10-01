"""POST /api/outreach/revoke: an approver's correction path (audit flow-v2).

The revoke appends an approvals row (action 'revoke') and its OUTREACH_REVOKE
audit row in one transaction under the borrower decision lock; the approve
row is never touched. Allowed only for the borrower's current, finalized,
unbound approval while outreach is none or queued. The Lakebase tables are
the in-memory model in tests/fixtures/approval_ledger_fake.py; the real SQL
is pinned by tests/integration/test_approval_requests_postgres.py.
"""

from __future__ import annotations

import inspect
import json
import re
from collections.abc import Iterator
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.api import outreach_revoke as revoke_mod
from backend.main import app
from backend.services import approval_funnel, sales_state_reporting
from backend.services.lakebase import get_lakebase_client
from backend.services.outreach_decision_ordering import is_current_approval
from backend.services.repositories import get_lead_repository, get_outreach_repository
from jobs import sync_lifecycle_state
from tests.fixtures.approval_ledger_fake import FakeApprovalLedger

PAT = "pat.approver@summit.example"
APPROVER = {"X-Forwarded-Email": PAT, "X-Forwarded-Groups": "mip-admin"}
ANALYST = {"X-Forwarded-Email": "alice.analyst@summit.example", "X-Forwarded-Groups": ""}
BORROWER = "B-ARQTESTX00001"
RATIONALE = "Offer code was mis-keyed; needs a second review."
URL = "/api/outreach/revoke"


class _Borrowers:
    def find_borrower(self, borrower_id: str) -> Any:
        return SimpleNamespace(borrower_id=borrower_id, clip_id="clip_ref_test") if borrower_id == BORROWER else None


class _Leads:
    def list(self, segment: str | None, portfolio_id: str | None, limit: int | None = None, **kwargs: Any) -> list[Any]:
        return [
            SimpleNamespace(
                borrower_id=bid, marketing_eligible=True, dnc=False, consent_status="opt_in",
                suppression_reason=None, approval_status="approved",
            )
            for bid in kwargs.get("borrower_ids") or []
            if bid == BORROWER
        ]


@pytest.fixture
def ledger() -> FakeApprovalLedger:
    return FakeApprovalLedger()


@pytest.fixture
def side_effects(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[Any]]:
    calls: dict[str, list[Any]] = {"enqueue": [], "cleared": []}
    monkeypatch.setattr(
        revoke_mod, "enqueue_lifecycle_trigger", lambda background, *, reason: calls["enqueue"].append(reason)
    )
    monkeypatch.setattr(revoke_mod, "clear_sales_state_cache", lambda: calls["cleared"].append(True))
    return calls


@pytest.fixture
def client(ledger: FakeApprovalLedger, side_effects: dict[str, list[Any]]) -> Iterator[TestClient]:
    app.dependency_overrides[get_lakebase_client] = lambda: ledger
    app.dependency_overrides[get_outreach_repository] = lambda: _Borrowers()
    app.dependency_overrides[get_lead_repository] = lambda: _Leads()
    try:
        yield TestClient(app)
    finally:
        for dependency in (get_lakebase_client, get_outreach_repository, get_lead_repository):
            app.dependency_overrides.pop(dependency, None)


def _approve(ledger: FakeApprovalLedger, **extra: Any) -> str:
    return ledger.add_decision(BORROWER, "approve", decided_at=ledger.now - timedelta(hours=1), **extra)


def _body(approval_id: str, **updates: Any) -> dict[str, Any]:
    body = {"borrower_id": BORROWER, "approval_id": approval_id, "rationale": RATIONALE, "request_id": str(uuid4())}
    body.update(updates)
    return body


def test_a_revoke_appends_a_decision_and_its_audit_row_and_never_edits_the_approval(
    client: TestClient, ledger: FakeApprovalLedger, side_effects: dict[str, list[Any]]
) -> None:
    approval_id = _approve(ledger, offer_code="heloc", channel="sms")
    before = json.dumps(ledger.approvals[0], default=str, sort_keys=True)
    body = _body(approval_id)
    response = client.post(URL, json=body, headers=APPROVER)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["revoked"] is True and result["revoked_approval_id"] == approval_id
    assert result["released_assignment_id"] is None
    assert json.dumps(ledger.approvals[0], default=str, sort_keys=True) == before
    revoke = ledger.approvals[1]
    assert (revoke["action"], revoke["campaign_id"], revoke["variant_name"]) == ("revoke", None, None)
    assert (revoke["channel"], revoke["offer_code"]) == ("sms", "heloc")
    assert revoke["approval_id"] == result["approval_id"] and revoke["request_id"] == body["request_id"]
    assert json.loads(revoke["decision_intent"]) == {
        "action": "revoke", "actor": PAT, "borrower_id": BORROWER, "revoked_approval_id": approval_id,
        "rationale": RATIONALE, "channel": "sms", "offer_code": "heloc",
    }
    assert revoke["audit_event_id"] == result["audit_event_id"]
    [audit] = ledger.audits
    assert (audit["event_type"], audit["entity_type"], audit["entity_id"]) == (
        "OUTREACH_REVOKE", "approval", result["approval_id"],
    )
    assert audit["subject_clip"] == "clip_ref_test"
    assert {key: audit["metadata"][key] for key in ("approval_id", "revoked_approval_id", "rationale", "request_id")} == {
        "approval_id": result["approval_id"], "revoked_approval_id": approval_id,
        "rationale": RATIONALE, "request_id": body["request_id"],
    }
    assert ledger.statements[0] == "APPROVAL_BY_REQUEST_ID"  # the pre-read replay check
    assert ledger.statements[1:3] == ["DECISION_LOCK", "APPROVAL_BY_REQUEST_ID"]
    assert side_effects == {"enqueue": ["revocation"], "cleared": [True]}


def test_an_upper_case_approval_id_revokes_that_approval_in_the_ledger_spelling(
    client: TestClient, ledger: FakeApprovalLedger
) -> None:
    # The UUID pattern admits either case, but the current-decision check
    # compares the id as text with approval_id::text (lower-case): without
    # canonicalization the borrower's current approval read "no longer current".
    approval_id = _approve(ledger)
    response = client.post(URL, json=_body(approval_id.upper()), headers=APPROVER)
    assert response.status_code == 200, response.text
    assert response.json()["revoked_approval_id"] == approval_id
    revoke = ledger.approvals[1]
    assert json.loads(revoke["decision_intent"])["revoked_approval_id"] == approval_id
    assert ledger.audits[-1]["metadata"]["revoked_approval_id"] == approval_id


def test_only_an_approver_may_revoke(client: TestClient, ledger: FakeApprovalLedger) -> None:
    response = client.post(URL, json=_body(_approve(ledger)), headers=ANALYST)
    assert response.status_code == 403
    assert ledger.statements == []


@pytest.mark.parametrize(
    "rationale",
    ["Please ask Jane Smith to call them", "Prioritize borrowers who are not Hispanic", "   "],
    ids=["name-shaped", "protected-class", "blank"],
)
def test_a_refused_rationale_answers_422_before_any_read(
    client: TestClient, ledger: FakeApprovalLedger, rationale: str
) -> None:
    response = client.post(URL, json=_body(_approve(ledger), rationale=rationale), headers=APPROVER)
    assert response.status_code == 422
    assert ledger.statements == []


def test_the_request_id_and_rationale_are_required(client: TestClient, ledger: FakeApprovalLedger) -> None:
    approval_id = _approve(ledger)
    for missing in ("request_id", "rationale"):
        body = _body(approval_id)
        del body[missing]
        assert client.post(URL, json=body, headers=APPROVER).status_code == 422


@pytest.mark.parametrize("state", ["superseded", "not_latest", "unfinalized", "rejected", "unknown"])
def test_only_the_current_finalized_approval_can_be_revoked(
    client: TestClient, ledger: FakeApprovalLedger, state: str
) -> None:
    approval_id = _approve(ledger, finalized=state != "unfinalized")
    if state == "superseded":
        ledger.add_decision(BORROWER, "reject", decided_at=ledger.now - timedelta(minutes=5))
    elif state == "not_latest":
        ledger.add_decision(BORROWER, "approve", decided_at=ledger.now - timedelta(minutes=5))
    elif state == "rejected":
        approval_id = ledger.add_decision(BORROWER, "reject", decided_at=ledger.now - timedelta(minutes=5))
    elif state == "unknown":
        approval_id = str(uuid4())
    response = client.post(URL, json=_body(approval_id), headers=APPROVER)
    assert response.status_code == 409
    assert response.json()["detail"] == (
        "This approval is no longer the borrower's current decision; reload to see the latest."
    )
    assert ledger.audits == [] and len([row for row in ledger.approvals if row["action"] == "revoke"]) == 0


def test_a_campaign_bound_approval_cannot_be_revoked_in_v1(client: TestClient, ledger: FakeApprovalLedger) -> None:
    approval_id = _approve(ledger, campaign_id="11111111-1111-4111-8111-111111111111")
    response = client.post(URL, json=_body(approval_id), headers=APPROVER)
    assert response.status_code == 409
    assert response.json()["detail"] == "A campaign-bound approval cannot be revoked in this release."


@pytest.mark.parametrize(
    ("ledger_name", "row", "detail"),
    [
        ("dispositions", {"borrower_id": BORROWER, "occurred_at": "after"}, "A call has been logged"),
        ("outcomes", {"borrower_id": BORROWER, "occurred_at": "after"}, "A lead outcome has been recorded"),
        ("outbox", {"approval_id": "same", "status": "delivered"}, "outreach has been delivered"),
    ],
)
def test_outreach_past_queued_refuses_the_revoke(
    client: TestClient, ledger: FakeApprovalLedger, ledger_name: str, row: dict[str, Any], detail: str
) -> None:
    approval_id = _approve(ledger)
    concrete = dict(row)
    if concrete.get("occurred_at") == "after":
        concrete["occurred_at"] = ledger.now - timedelta(minutes=30)
    if concrete.get("approval_id") == "same":
        concrete["approval_id"] = approval_id
    getattr(ledger, ledger_name).append(concrete)
    response = client.post(URL, json=_body(approval_id), headers=APPROVER)
    assert response.status_code == 409
    assert detail in response.json()["detail"]
    assert ledger.audits == []


def test_earlier_outreach_and_undelivered_activation_do_not_block(client: TestClient, ledger: FakeApprovalLedger) -> None:
    approval_id = _approve(ledger)
    ledger.dispositions.append({"borrower_id": BORROWER, "occurred_at": ledger.now - timedelta(days=2)})
    ledger.outcomes.append({"borrower_id": BORROWER, "occurred_at": ledger.now - timedelta(days=2)})
    ledger.outbox.append({"approval_id": approval_id, "status": "staged"})
    assert client.post(URL, json=_body(approval_id), headers=APPROVER).status_code == 200


@pytest.mark.parametrize("worked_status", ["actioned", "outcome_recorded"])
def test_a_worked_assignment_refuses_and_an_unworked_one_is_released(
    client: TestClient, ledger: FakeApprovalLedger, worked_status: str
) -> None:
    approval_id = _approve(ledger)
    worked = ledger.add_assignment(BORROWER, status=worked_status)
    refused = client.post(URL, json=_body(approval_id), headers=APPROVER)
    assert refused.status_code == 409
    assert refused.json()["detail"] == "The loan officer has already worked this lead; it can no longer be revoked."
    assert ledger.assignments[worked]["released_at"] is None
    ledger.assignments[worked]["status"] = "assigned"
    released = client.post(URL, json=_body(approval_id), headers=APPROVER)
    assert released.status_code == 200
    assert released.json()["released_assignment_id"] == worked
    assert ledger.assignments[worked]["released_at"] == ledger.now
    assert ledger.audits[-1]["metadata"]["released_assignment_id"] == worked


def test_a_revoke_reopens_the_borrower_for_a_new_request(client: TestClient, ledger: FakeApprovalLedger) -> None:
    approval_id = _approve(ledger)
    batch_id = ledger.add_batch("alice.analyst@summit.example", [BORROWER], created_at=ledger.now - timedelta(hours=2))
    assert client.post(URL, json=_body(approval_id), headers=APPROVER).status_code == 200
    assert ledger.items[(batch_id, BORROWER)]["status"] == "expired"
    again = client.post(
        "/api/outreach/approval-requests",
        json={"borrower_ids": [BORROWER], "rationale": "Offer reconsidered; please review again.", "request_key": str(uuid4())},
        headers=ANALYST,
    )
    assert again.status_code == 200, again.text
    assert again.json()["requested"] == [BORROWER]


def test_a_replay_returns_the_stored_body_and_a_changed_payload_conflicts(
    client: TestClient, ledger: FakeApprovalLedger, side_effects: dict[str, list[Any]]
) -> None:
    approval_id = _approve(ledger)
    body = _body(approval_id)
    first = client.post(URL, json=body, headers=APPROVER)
    again = client.post(URL, json=body, headers=APPROVER)
    assert again.status_code == 200 and again.json() == first.json()
    assert len(ledger.audits) == 1
    assert side_effects["enqueue"] == ["revocation"]  # the replay enqueues nothing
    changed = client.post(URL, json={**body, "rationale": "A different reason entirely."}, headers=APPROVER)
    assert changed.status_code == 409


def test_an_unknown_borrower_is_404_and_an_outage_503(client: TestClient, ledger: FakeApprovalLedger) -> None:
    approval_id = _approve(ledger)
    missing = client.post(URL, json=_body(approval_id, borrower_id="B-ARQTESTX00009"), headers=APPROVER)
    assert missing.status_code == 404
    ledger.down = True
    assert client.post(URL, json=_body(approval_id), headers=APPROVER).status_code == 503


# -- a latest revoke reads pending everywhere, with no change there ----------------------


def test_a_latest_revoke_is_never_the_current_approval() -> None:
    approve_id = str(uuid4())
    assert is_current_approval({"approval_id": approve_id, "action": "approve"}, approval_id=approve_id)
    assert not is_current_approval({"approval_id": str(uuid4()), "action": "revoke"}, approval_id=approve_id)


def _case(sql: str, column: str) -> str:
    match = re.search(r"CASE(.*?)END\s+AS " + column, sql, flags=re.DOTALL)
    assert match is not None, column
    return " ".join(match.group(1).split())


@pytest.mark.parametrize(
    "sql",
    [
        sync_lifecycle_state._LAKEBASE_QUERY,
        inspect.getsource(sales_state_reporting._SalesStateReporting.lifecycle_for),
    ],
    ids=["lifecycle-sync", "lifecycle_for"],
)
def test_lifecycle_reads_map_any_other_latest_action_to_pending_and_none(sql: str) -> None:
    approval = _case(sql, "approval_status")
    outreach = _case(sql, "outreach_status")
    assert approval.endswith("ELSE 'pending'") and "revoke" not in approval
    assert "WHEN a.action = 'approve' THEN 'queued'" in outreach and outreach.endswith("ELSE 'none'")


def test_the_funnel_counts_a_revoked_approve_as_ever_approved_like_approve_then_reject() -> None:
    # Documented, unchanged: the 'approved' stage is "ever approved".
    union = " ".join(approval_funnel._APPROVED_UNION_SQL.split())
    assert "WHERE action = 'approve'" in union and "revoke" not in union

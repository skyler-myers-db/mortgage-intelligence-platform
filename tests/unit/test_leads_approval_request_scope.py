"""GET /leads?approval_request_batch=<id>: the open borrowers of one request.

D-approval-flow-c record item 12 (flow-02 / shell-06). Approvers may open any
request and the requester their own; an unknown request is 404 and anyone
else 403. The scope never combines with a cohort, a borrower list or a Growth
Agent handoff, and the audit-free aggregates (count, facets) refuse it, as
they refuse a borrower list (W5a ruling). A request with no open borrower
answers an empty list WITHOUT a VIEW_LEADS row and never widens to the whole
queue; otherwise the VIEW_LEADS row records the request id. Lakebase is
resolved only when the scope is named.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.api import leads as leads_mod
from backend.main import app
from backend.services.audit_store import get_audit_store
from backend.services.lakebase import LakebaseError
from backend.services.repositories.factory import get_lead_facet_repository
from backend.services.saved_view_params import canonical_saved_view_params
from tests.fixtures.approval_ledger_fake import FakeApprovalLedger
from tests.fixtures.in_memory_audit_store import InMemoryAuditStore

ALICE = "alice.analyst@summit.example"
REQUESTER = {"X-Forwarded-Email": ALICE, "X-Forwarded-Groups": ""}
STRANGER = {"X-Forwarded-Email": "bob.analyst@summit.example", "X-Forwarded-Groups": ""}
APPROVER = {"X-Forwarded-Email": "pat.approver@summit.example", "X-Forwarded-Groups": "mip-admin"}
OPEN_A, OPEN_B, DECIDED = "B-48291", "B-48294", "B-48295"


@pytest.fixture
def ledger(monkeypatch: pytest.MonkeyPatch) -> FakeApprovalLedger:
    fake = FakeApprovalLedger()
    monkeypatch.setattr(leads_mod, "get_lakebase_client", lambda: fake)
    return fake


@pytest.fixture
def audit() -> Iterator[InMemoryAuditStore]:
    store = InMemoryAuditStore()
    saved = app.dependency_overrides.get(get_audit_store)
    app.dependency_overrides[get_audit_store] = lambda: store
    try:
        yield store
    finally:
        if saved is None:
            app.dependency_overrides.pop(get_audit_store, None)
        else:
            app.dependency_overrides[get_audit_store] = saved


client = TestClient(app)


def _view_rows(audit: InMemoryAuditStore) -> list[dict[str, Any]]:
    return [dict(event.payload_json or {}) for event in audit.list(limit=50) if event.event_type == "VIEW_LEADS"]


def _batch(ledger: FakeApprovalLedger) -> str:
    batch_id = ledger.add_batch(ALICE, [OPEN_A, OPEN_B, DECIDED])
    ledger.add_decision(DECIDED, "approve", batch_id=batch_id)
    return batch_id


@pytest.mark.parametrize("who", [APPROVER, REQUESTER], ids=["approver", "requester"])
def test_the_scope_lists_only_the_requests_open_borrowers_and_is_audited(
    ledger: FakeApprovalLedger, audit: InMemoryAuditStore, who: dict[str, str]
) -> None:
    batch_id = _batch(ledger)
    response = client.get("/api/leads", params={"approval_request_batch": batch_id}, headers=who)
    assert response.status_code == 200, response.text
    assert sorted(lead["borrower_id"] for lead in response.json()) == [OPEN_A, OPEN_B]
    (row,) = _view_rows(audit)
    assert row["approval_request_batch_id"] == batch_id
    assert sorted(row["borrower_ids"]) == [OPEN_A, OPEN_B]


def test_a_request_with_no_open_borrower_is_empty_never_the_whole_queue(
    ledger: FakeApprovalLedger, audit: InMemoryAuditStore
) -> None:
    batch_id = ledger.add_batch(ALICE, [OPEN_A])
    ledger.items[(batch_id, OPEN_A)].update(status="withdrawn", closed_at=ledger.now)
    response = client.get("/api/leads", params={"approval_request_batch": batch_id}, headers=APPROVER)
    assert response.status_code == 200
    assert response.json() == []
    assert response.headers["X-Total-Matching"] == "0"
    assert response.headers["X-Returned-Rows"] == "0"
    assert _view_rows(audit) == []


def test_unknown_and_foreign_requests_are_refused(ledger: FakeApprovalLedger) -> None:
    batch_id = _batch(ledger)
    unknown = client.get("/api/leads", params={"approval_request_batch": str(uuid4())}, headers=APPROVER)
    assert unknown.status_code == 404
    foreign = client.get("/api/leads", params={"approval_request_batch": batch_id}, headers=STRANGER)
    assert foreign.status_code == 403


@pytest.mark.parametrize(
    "extra",
    [{"cohort_id": "genie-cohort-1"}, {"borrower_ids": OPEN_A}, {"growth_handoff": "proof"}],
    ids=["cohort", "borrower_ids", "growth_handoff"],
)
def test_the_scope_never_combines_with_a_cohort_list_or_handoff(
    ledger: FakeApprovalLedger, extra: dict[str, str]
) -> None:
    batch_id = _batch(ledger)
    response = client.get(
        "/api/leads", params={"approval_request_batch": batch_id, **extra}, headers=APPROVER
    )
    assert response.status_code == 422
    assert "approval_request_batch cannot be combined" in response.text
    assert ledger.statements == []


@pytest.mark.parametrize("path", ["/api/leads/count", "/api/leads/facets?dimension=state"])
def test_the_audit_free_aggregates_refuse_the_scope(
    ledger: FakeApprovalLedger, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    # The refusal precedes any read; the facet repository is never called.
    monkeypatch.setitem(app.dependency_overrides, get_lead_facet_repository, lambda: object())
    separator = "&" if "?" in path else "?"
    response = client.get(f"{path}{separator}approval_request_batch={_batch(ledger)}", headers=APPROVER)
    assert response.status_code == 422
    assert response.json()["detail"] == (
        "approval_request_batch applies to GET /leads only: a read of named borrowers is audited"
    )
    assert ledger.statements == []


def test_a_malformed_request_id_is_refused(ledger: FakeApprovalLedger) -> None:
    response = client.get("/api/leads", params={"approval_request_batch": "B-48291"}, headers=APPROVER)
    assert response.status_code == 422


def test_lakebase_is_resolved_only_for_the_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    def _unconfigured() -> Any:
        raise LakebaseError("Lakebase is not configured")

    monkeypatch.setattr(leads_mod, "get_lakebase_client", _unconfigured)
    assert client.get("/api/leads", headers=APPROVER).status_code == 200
    scoped = client.get("/api/leads", params={"approval_request_batch": str(uuid4())}, headers=APPROVER)
    assert scoped.status_code == 503


def test_saved_views_keep_refusing_the_request_scope() -> None:
    with pytest.raises(ValueError):
        canonical_saved_view_params(f"approval_request_batch={uuid4()}&segment=itm")

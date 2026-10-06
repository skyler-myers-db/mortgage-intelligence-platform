"""The borrower decision history (audit flow-04 phase 2 / tables-10, D-audit-reads-c2).

``GET /api/v1/borrowers/{borrower_id}/decisions`` shows the working team one
borrower's governed decisions. Pinned here (the SQL itself is proven against
PostgreSQL by tests/integration/test_borrower_decision_history_postgres.py
over the same seeds, tests/fixtures/borrower_decision_rows.py):

1. Server ownership: the legacy decision codes (HOLD, REJECT, OUTREACH_HOLD)
   and SUPPRESS_CONTACT are server-owned, so POST /audit/event refuses them.
2. The vocabulary: exactly 13 server-owned decision types; never a read, a
   Genie row, a refusal or a legacy code; the partial index predicate in
   lakebase/schema.sql is the service's literal list (never a bound ANY).
3. Real writers: what each governed writer actually emits is accepted by
   ``row_names_borrower`` for its borrower, and for no other borrower.
4. The projection: closed keys, no free text, a coarse rationale label,
   actor resolution, receipt availability, the belt's exclusions, truncation
   and at most five Lakebase statements.
5. The endpoint: admission (team member, approver, admin, auditor; 403
   otherwise), no audit row, no warehouse dependency, private no-store.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, get_args
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest
from fastapi.testclient import TestClient

from backend.api import audit as audit_api
from backend.config.settings import settings
from backend.main import app
from backend.schemas.borrower_decisions import BorrowerDecisionEvent, DecisionHistoryEventType
from backend.services import borrower_decision_history as history
from backend.services.audit_event_types import is_server_owned_audit_event_type
from backend.services.audit_store import get_audit_store
from backend.services.eligibility import GoldEligibilityService, write_suppression_audit
from backend.services.lakebase import LakebaseError, get_lakebase_client
from backend.services.repositories import (
    get_borrower_repository,
    get_lead_repository,
    get_offer_repository,
    get_outreach_repository,
)
from backend.services.sales_state import clear_sales_state_cache
from tests.fixtures import borrower_decision_rows as seeds
from tests.fixtures.in_memory_audit_store import InMemoryAuditStore

client = TestClient(app)

_FORGEABLE_BEFORE_C2 = ("HOLD", "REJECT", "OUTREACH_HOLD", "SUPPRESS_CONTACT")
THIRTEEN = {
    "ACTIVATION_STAGE", "APPROVAL_REQUESTED", "APPROVE", "CALL_DISPOSITION", "LEAD_ASSIGN",
    "LEAD_ASSIGNMENT_STATUS", "LEAD_DISTRIBUTE", "LEAD_OUTCOME", "LEAD_OUTCOME_RECORDED",
    "LEAD_UNASSIGN", "OUTREACH_REJECT", "OUTREACH_REVOKE", "SUPPRESS_CONTACT",
}
ROUTE = "/api/v1/borrowers/{borrower_id}/decisions"
COMPAT_ROUTE = "/api/borrowers/{borrower_id}/decisions"
SCHEMA_SQL = Path("lakebase/schema.sql").read_text(encoding="utf-8")


# -- 1. server ownership ----------------------------------------------------------------


@pytest.mark.parametrize("event_type", _FORGEABLE_BEFORE_C2)
def test_the_legacy_decision_codes_and_the_contact_block_are_server_owned(event_type: str) -> None:
    assert is_server_owned_audit_event_type(event_type)


@pytest.mark.parametrize("event_type", _FORGEABLE_BEFORE_C2)
def test_post_audit_event_refuses_a_forged_decision_or_contact_block(event_type: str) -> None:
    response = client.post(
        "/api/audit/event",
        json={
            "actor": "attacker@example.com",
            "action": "outreach.custom",
            "entity_type": "borrower",
            "entity_id": "B-0123456789ABC",
            "event_type": event_type,
        },
    )

    assert response.status_code == 400, event_type
    assert response.json()["detail"] == "event type is owned by a governed server route"


# -- 2. the vocabulary ------------------------------------------------------------------


def test_the_history_reads_exactly_the_thirteen_server_owned_decision_types() -> None:
    assert history.DECISION_HISTORY_EVENT_TYPES == THIRTEEN
    assert set(get_args(DecisionHistoryEventType)) == THIRTEEN
    assert all(is_server_owned_audit_event_type(code) for code in history.DECISION_HISTORY_EVENT_TYPES)
    # C8: never a read, a governed-text refusal (W5d) or a request refusal.
    assert not any(code.startswith("VIEW_") for code in history.DECISION_HISTORY_EVENT_TYPES)
    assert {"GOVERNED_TEXT_REFUSED", "APPROVAL_REQUEST_REFUSED", "RUN_GENIE", "RECOMMEND_OFFER"}.isdisjoint(
        history.DECISION_HISTORY_EVENT_TYPES
    )
    assert {"OUTREACH_APPROVE", "REJECT", "HOLD", "OUTREACH_HOLD"}.isdisjoint(history.DECISION_HISTORY_EVENT_TYPES)
    assert {"outreach_approve", "activation_stage"} == history.SUPPRESS_ROUTES


def _literal_list(sql_in_body: str) -> list[str]:
    return re.findall(r"'([A-Z_]+)'", sql_in_body)


def test_the_partial_index_predicate_is_the_service_literal_list() -> None:
    match = re.search(
        r"CREATE INDEX IF NOT EXISTS idx_action_audit_decision_entity\s+"
        r"ON mip_app\.action_audit \((?P<columns>[^)]*)\)\s+WHERE event_type IN \((?P<types>[^)]*)\);",
        SCHEMA_SQL,
    )
    assert match is not None
    # Plain columns only: no expression, jsonb path or GIN.
    assert match["columns"] == "entity_id, audit_sequence DESC"
    predicate = _literal_list(match["types"])
    assert predicate == sorted(THIRTEEN - {"LEAD_DISTRIBUTE"})
    assert not {"GOVERNED_TEXT_REFUSED", "APPROVAL_REQUEST_REFUSED"} & set(predicate)
    assert not any(code.startswith("VIEW_") for code in predicate)
    # The service query carries the IDENTICAL literal text.
    assert f"event_type IN ({match['types']})" in history.DECISION_HISTORY_SQL
    assert "'2026_10_02_borrower_decision_history_index'" in SCHEMA_SQL


def test_the_type_list_is_a_literal_never_a_bound_parameter() -> None:
    for sql in (history.DECISION_HISTORY_SQL, history.DISTRIBUTE_HISTORY_SQL):
        assert not re.search(r"event_type\s*=\s*ANY\s*\(", sql)
        assert "%(types)s" not in sql and "%s" not in sql
        assert "subject_clip" not in sql
    assert "COALESCE(metadata->>'borrower_id', entity_id) = %(bid)s" in history.DECISION_HISTORY_SQL


def test_audit_rollups_group_the_ten_workflow_types(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[str] = []

    class _Lakebase:
        def fetchall(self, sql: str, params: dict[str, Any] | None = None, limit: int = 100) -> list[dict[str, Any]]:
            captured.append(sql)
            return []

    monkeypatch.setattr(audit_api, "record_ledger_read", lambda *args, **kwargs: None)
    audit_api.audit_rollups(
        actor_id="admin@summit.example", lakebase=_Lakebase(), store=InMemoryAuditStore(),  # type: ignore[arg-type]
        background=SimpleNamespace(), period="week", group_by="event_type", since=None, until=None,  # type: ignore[arg-type]
    )
    (sql,) = captured
    listed = _literal_list(sql[sql.index("event_type IN (") : sql.index(")", sql.index("event_type IN ("))])
    assert sorted(listed) == sorted({
        "APPROVE", "OUTREACH_APPROVE", "OUTREACH_REJECT", "CALL_DISPOSITION", "LEAD_ASSIGN",
        "LEAD_DISTRIBUTE", "LEAD_OUTCOME", "OUTREACH_REVOKE", "APPROVAL_REQUESTED", "LEAD_UNASSIGN",
    })


# -- 3. real writers ----------------------------------------------------------------------


Captured = tuple[str, str, str, dict[str, Any], str]  # event_type, entity_type, entity_id, metadata, borrower

ACTOR = "skyler@entrada.ai"
HEADERS = {"X-Forwarded-Email": ACTOR}
LO_01 = "55555555-5555-4555-8555-555555555501"
STRANGER = "B-STRANGER00001"


@pytest.fixture
def writer_audit(monkeypatch: pytest.MonkeyPatch) -> Iterator[InMemoryAuditStore]:
    monkeypatch.setattr(settings, "approver_identities", ACTOR)
    monkeypatch.setattr(settings, "trust_forwarded_headers", True)
    store = InMemoryAuditStore()
    app.dependency_overrides[get_audit_store] = lambda: store
    clear_sales_state_cache()
    yield store
    clear_sales_state_cache()


def _store_rows(store: InMemoryAuditStore, borrower_id: str) -> list[Captured]:
    return [
        (str(e.event_type), e.entity_type, e.entity_id, dict(e.payload_json or {}), borrower_id)
        for e in store.list(limit=500)
    ]


def _fake_rows(fake: Any, borrower_id: str) -> list[Captured]:
    return [
        (row["event_type"], row["entity_type"], row["entity_id"], json.loads(row["metadata"]), borrower_id)
        for row in fake.audit_events
    ]


def _approved(borrower_id: str, *, review_mode: str = "individual") -> dict[str, Any]:
    draft = client.post("/api/outreach/draft", json={"borrower_id": borrower_id, "channel": "email"}, headers=HEADERS)
    assert draft.status_code == 200, draft.text
    body = draft.json()
    # C3: review_mode and the reviewed draft proof travel on every approve.
    response = client.post(
        "/api/outreach/approve",
        json={
            "borrower_id": borrower_id, "channel": "email", "offer_code": body["offer_code"],
            "draft_subject": body["subject"], "draft_body": body["body"],
            "draft_generation_id": body["generation_id"], "draft_response_hash": body["response_hash"],
            "draft_source_refreshed_at": body["source_refreshed_at"], "review_mode": review_mode,
            "request_id": str(uuid4()),
        },
        headers=HEADERS,
    )
    assert response.status_code == 200, response.text
    return response.json()


def _outreach_writers(store: InMemoryAuditStore, borrower_id: str) -> list[Captured]:
    _approved(borrower_id)
    rejected = client.post(
        "/api/outreach/reject",
        json={"borrower_id": borrower_id, "channel": "email", "rationale_code": "fair_lending_review", "request_id": str(uuid4())},
        headers=HEADERS,
    )
    assert rejected.status_code == 200, rejected.text
    return [row for row in _store_rows(store, borrower_id) if row[0] in {"APPROVE", "OUTREACH_REJECT"}]


def _sales_writers(fake: Any, borrower_id: str) -> list[Captured]:
    fake.activation_destinations[0]["status"] = "connected"  # the outcome feed
    _approved(borrower_id)
    steps = [
        (f"/api/leads/{borrower_id}/assign", {"assigned_to_email": "lo01@summit.example", "strategy": "manual"}),
        (f"/api/leads/{borrower_id}/disposition", {"lo_email": "lo01@summit.example", "outcome": "connected"}),
        (f"/api/leads/{borrower_id}/outcome", {
            "outcome_type": "closed_funded", "source_system": "salesforce", "source_record_ref": "sf_case_77",
            "assigned_to_email": "lo01@summit.example", "request_id": str(uuid4()),
        }),
        ("/api/sales/distribute", {
            "borrower_ids": [borrower_id], "lo_emails": ["lo02@summit.example"], "strategy": "round_robin",
            "request_id": str(uuid4()),
        }),
    ]
    for path, body in steps:
        response = client.post(path, json=body, headers=HEADERS)
        assert response.status_code == 200, (path, response.text)
    return _fake_rows(fake, borrower_id)


def _loan_officer_writers(fake: Any, borrower_id: str) -> list[Captured]:
    assigned = client.post(
        "/api/loan-officers/assignments",
        json={"borrower_id": borrower_id, "loan_officer_id": LO_01, "request_id": str(uuid4())},
        headers=HEADERS,
    )
    assert assigned.status_code == 200, assigned.text
    assignment_id = assigned.json()["assignment"]["assignment_id"]
    for status in ("contact_drafted", "approved", "actioned"):
        moved = client.patch(f"/api/loan-officers/assignments/{assignment_id}/status", json={"status": status}, headers=HEADERS)
        assert moved.status_code == 200, moved.text
    recorded = client.post(
        f"/api/loan-officers/assignments/{assignment_id}/outcome", json={"outcome": "success"}, headers=HEADERS
    )
    assert recorded.status_code == 200, recorded.text
    return _fake_rows(fake, borrower_id)


def _activation_writer(borrower_id: str) -> list[Captured]:
    from backend.schemas.activation import ActivationStageRequest
    from backend.services.activation_state import ActivationStateStore
    from tests.fixtures import mock_population
    from tests.unit.test_activation_state import _approved_decision, _Client, _destination

    borrower = next(b for b in mock_population.BORROWERS if b.borrower_id == borrower_id)
    fake = _Client()
    approval_id = str(uuid4())
    ActivationStateStore(client=fake).stage_borrower(  # type: ignore[arg-type]
        borrower=borrower,
        destination=_destination(),
        payload=ActivationStageRequest(
            borrower_id=borrower_id, destination_key="salesforce_crm", offer_code="refi", channel="email",
            approval_id=approval_id, request_id=str(uuid4()),
        ),
        approved_decision=_approved_decision(approval_id, borrower_id),
        actor=ACTOR,
    )
    params = fake.conn.audit_params
    assert params is not None
    return [(params["event_type"], params["entity_type"], params["entity_id"], json.loads(params["metadata"]), borrower_id)]


def _ledger_writers(monkeypatch: pytest.MonkeyPatch) -> list[Captured]:
    from backend.api import outreach_revoke as revoke_mod
    from tests.fixtures.approval_ledger_fake import FakeApprovalLedger

    borrower_id = "B-ARQTESTX00001"
    ledger = FakeApprovalLedger()
    approval_id = ledger.add_decision(borrower_id, "approve", decided_at=ledger.now - timedelta(hours=1))
    monkeypatch.setattr(revoke_mod, "enqueue_lifecycle_trigger", lambda background, *, reason: None)
    monkeypatch.setattr(revoke_mod, "clear_sales_state_cache", lambda: None)
    contactable = SimpleNamespace(
        borrower_id=borrower_id, marketing_eligible=True, dnc=False, consent_status="opt_in",
        suppression_reason=None, approval_status="pending",
    )
    leads = SimpleNamespace(list=lambda *args, **kwargs: [contactable] if borrower_id in (kwargs.get("borrower_ids") or []) else [])
    outreach = SimpleNamespace(find_borrower=lambda bid: SimpleNamespace(borrower_id=bid, clip_id="clip_ref_test"))
    app.dependency_overrides[get_lakebase_client] = lambda: ledger
    app.dependency_overrides[get_lead_repository] = lambda: leads
    app.dependency_overrides[get_outreach_repository] = lambda: outreach
    revoked = client.post(
        "/api/outreach/revoke",
        json={"borrower_id": borrower_id, "approval_id": approval_id, "rationale": "Mis-keyed offer code.", "request_id": str(uuid4())},
        headers={"X-Forwarded-Email": "pat.approver@summit.example", "X-Forwarded-Groups": "mip-admin"},
    )
    assert revoked.status_code == 200, revoked.text
    requested_id = "B-ARQTESTX00002"
    leads_requested = SimpleNamespace(
        list=lambda *args, **kwargs: [
            SimpleNamespace(**{**vars(contactable), "borrower_id": bid}) for bid in kwargs.get("borrower_ids") or []
        ]
    )
    app.dependency_overrides[get_lead_repository] = lambda: leads_requested
    requested = client.post(
        "/api/outreach/approval-requests",
        json={"borrower_ids": [requested_id], "rationale": "Rate-sensitive refinance candidates.", "request_key": str(uuid4())},
        headers={"X-Forwarded-Email": "alice.analyst@summit.example", "X-Forwarded-Groups": ""},
    )
    assert requested.status_code == 200, requested.text
    owner = {"OUTREACH_REVOKE": borrower_id, "APPROVAL_REQUESTED": requested_id}
    return [
        (row["event_type"], row["entity_type"], row["entity_id"], dict(row["metadata"]), owner[row["event_type"]])
        for row in ledger.audits
    ]


def _suppression_writers(borrower_id: str) -> tuple[list[Captured], list[Captured]]:
    store = InMemoryAuditStore()
    borrower = SimpleNamespace(marketing_eligible=True, consent_status="opt_out", suppression_reason=None, dnc=False)
    decision = GoldEligibilityService().evaluate(borrower)
    for surface in ("outreach_approve", "activation_stage", "outreach_draft"):
        write_suppression_audit(store, actor=ACTOR, borrower_id=borrower_id, decision=decision, surface=surface)
    rows = _store_rows(store, borrower_id)
    return (
        [row for row in rows if row[3]["route"] != "outreach_draft"],
        [row for row in rows if row[3]["route"] == "outreach_draft"],
    )


def _assert_found_for_its_borrower_only(rows: list[Captured], expected_types: set[str]) -> None:
    assert {row[0] for row in rows} >= expected_types, rows
    for event_type, entity_type, entity_id, metadata, borrower_id in rows:
        if event_type not in expected_types:
            continue
        assert row_found(event_type, entity_type, entity_id, metadata, borrower_id), (event_type, entity_type)
        assert not history.row_names_borrower(event_type, entity_id, metadata, STRANGER), event_type


def row_found(event_type: str, entity_type: str, entity_id: str, metadata: dict[str, Any], borrower_id: str) -> bool:
    """Accepted by the belt, on an entity shape the resolution step reaches."""
    resolvable = {"borrower", "approval", "activation", "approval_request_batch", "lead_queue"}
    if entity_type not in resolvable or (entity_type == "borrower" and entity_id != borrower_id):
        return False
    return history.row_names_borrower(event_type, entity_id, metadata, borrower_id)


def test_the_outreach_approve_and_reject_writers_are_found(writer_audit: InMemoryAuditStore) -> None:
    from tests.fixtures import mock_population

    borrower_id = mock_population.BORROWERS[0].borrower_id
    _assert_found_for_its_borrower_only(_outreach_writers(writer_audit, borrower_id), {"APPROVE", "OUTREACH_REJECT"})


def test_the_sales_state_writers_are_found(writer_audit: InMemoryAuditStore, fake_lakebase_client: Any) -> None:
    from tests.fixtures import mock_population

    borrower_id = mock_population.BORROWERS[1].borrower_id
    rows = _sales_writers(fake_lakebase_client, borrower_id)
    _assert_found_for_its_borrower_only(rows, {"LEAD_ASSIGN", "CALL_DISPOSITION", "LEAD_OUTCOME", "LEAD_DISTRIBUTE"})


def test_the_loan_officer_writers_are_found(writer_audit: InMemoryAuditStore, fake_lakebase_client: Any) -> None:
    from tests.fixtures import mock_population

    borrower_id = mock_population.BORROWERS[2].borrower_id
    rows = _loan_officer_writers(fake_lakebase_client, borrower_id)
    _assert_found_for_its_borrower_only(rows, {"LEAD_ASSIGN", "LEAD_ASSIGNMENT_STATUS", "LEAD_OUTCOME_RECORDED"})


def test_the_activation_writer_is_found() -> None:
    from tests.fixtures import mock_population

    rows = _activation_writer(mock_population.BORROWERS[0].borrower_id)
    _assert_found_for_its_borrower_only(rows, {"ACTIVATION_STAGE"})


def test_the_revoke_and_approval_request_writers_are_found(
    writer_audit: InMemoryAuditStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    _assert_found_for_its_borrower_only(_ledger_writers(monkeypatch), {"OUTREACH_REVOKE", "APPROVAL_REQUESTED"})


def test_a_blocked_approve_or_activation_is_found_and_a_blocked_draft_is_not() -> None:
    found, dropped = _suppression_writers("B-SUPPRESS00001")
    _assert_found_for_its_borrower_only(found, {"SUPPRESS_CONTACT"})
    assert len(found) == 2
    [(event_type, _entity_type, entity_id, metadata, borrower_id)] = dropped
    assert not history.row_names_borrower(event_type, entity_id, metadata, borrower_id)


def test_a_lead_unassign_row_on_the_borrower_entity_is_found() -> None:
    # No LEAD_UNASSIGN writer exists before W5d; its contract is a borrower
    # entity with metadata.borrower_id, like every other lead writer.
    row = next(row for row in seeds.AUDIT_ROWS if row.key == "unassign")
    assert row_found(row.event_type, row.entity_type, row.entity_id, row.metadata, seeds.BORROWER)


# -- 4. the projection, over an in-memory Lakebase ----------------------------------------


class _HistoryLakebase:
    """Answers the five service statements by identity from the shared seeds.

    The history statements return every row of the right type on a resolved
    entity WITHOUT the borrower re-check or the route filter, so the Python
    belt is what these tests prove; PostgreSQL proves the SQL itself.
    """

    def __init__(self, rows: tuple[seeds.SeedAudit, ...] = seeds.AUDIT_ROWS) -> None:
        self.rows = [
            {
                "audit_id": str(uuid5(NAMESPACE_URL, f"mip-decision/{row.key}")), "audit_sequence": index + 1,
                "event_type": row.event_type, "actor_email": row.actor_email, "entity_type": row.entity_type,
                "entity_id": row.entity_id, "request_id": row.request_id, "metadata": dict(row.metadata),
                "event_at": seeds.event_at(index), "key": row.key,
            }
            for index, row in enumerate(rows)
        ]
        self.statements: list[str] = []
        self.fail = False

    def _run(self, sql: str) -> None:
        self.statements.append(sql)
        if self.fail:
            raise LakebaseError("down")

    def fetchall(self, sql: str, params: dict[str, Any] | None = None, limit: int = 100) -> list[dict[str, Any]]:
        self._run(sql)
        params = params or {}
        if sql == history.ENTITY_RESOLUTION_SQL:
            bid = params["bid"]
            ids = [a["approval_id"] for a in seeds.APPROVALS if a["borrower_id"] == bid]
            ids += [a["activation_id"] for a in seeds.ACTIVATIONS if a["borrower_id"] == bid]
            ids += [i["batch_id"] for i in seeds.BATCH_ITEMS if i["borrower_id"] == bid]
            return [{"entity_id": entity_id} for entity_id in ids][:limit]
        if sql == history.DECISION_HISTORY_SQL:
            types = history.DECISION_HISTORY_EVENT_TYPES - {"LEAD_DISTRIBUTE"}
            hits = [r for r in self.rows if r["event_type"] in types and r["entity_id"] in params["entity_ids"]]
            return sorted(hits, key=lambda r: r["audit_sequence"], reverse=True)[:limit]
        if sql == history.DISTRIBUTE_HISTORY_SQL:
            hits = [r for r in self.rows if r["event_type"] == "LEAD_DISTRIBUTE"]
            return sorted(hits, key=lambda r: (r["event_at"], r["audit_sequence"]), reverse=True)[:limit]
        if sql == history.DISTRIBUTION_ASSIGNEE_SQL:
            return [
                {"request_id": a["request_id"], "assigned_to_email": a["assigned_to_email"]}
                for a in seeds.DISTRIBUTION_ASSIGNMENTS
                if a["borrower_id"] == params["bid"] and a["request_id"] in params["rids"]
            ]
        if sql == history.ACTOR_TEAM_SQL:
            return [dict(m) for m in seeds.TEAM if m["email"] in params["emails"]]
        raise AssertionError(f"unexpected statement: {sql[:80]}")

    def fetchone(self, sql: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        self._run(sql)
        if "FROM mip_app.sales_team" in sql and "active = true" in sql:
            email = (params or {}).get("email")
            member = next((m for m in seeds.TEAM if m["email"] == email), None)
            return None if member is None else {**member, "region": None, "manager_email": None, "capacity_per_day": 35}
        raise AssertionError(f"unexpected statement: {sql[:80]}")

    def key_of(self, audit_event_id: str) -> str:
        return next(r["key"] for r in self.rows if r["audit_id"] == audit_event_id)


def _list(lakebase: _HistoryLakebase, borrower_id: str = seeds.BORROWER, *, viewer: str = seeds.OWN, privileged: bool = False):
    return history.list_borrower_decisions(
        lakebase,  # type: ignore[arg-type]
        borrower_id,
        viewer=viewer,
        privileged=privileged,
        automation_identities=frozenset({seeds.AUTOMATION}),
    )


@pytest.mark.parametrize("borrower_id", [seeds.BORROWER, seeds.OTHER, seeds.PHONE_SHAPED])
def test_each_history_is_exactly_its_borrower_s_decisions_newest_first(borrower_id: str) -> None:
    lakebase = _HistoryLakebase()
    result = _list(lakebase, borrower_id)

    assert [lakebase.key_of(item.audit_event_id) for item in result.items] == seeds.expected_keys(borrower_id)
    assert result.borrower_id == borrower_id and result.truncated is False
    assert len(lakebase.statements) <= 5


def test_the_belt_drops_every_negative_control() -> None:
    lakebase = _HistoryLakebase()
    keys = {lakebase.key_of(item.audit_event_id) for item in _list(lakebase).items}

    for dropped in (
        "blocked_draft", "legacy_outreach_approve", "legacy_reject", "legacy_hold", "legacy_outreach_hold",
        "view", "genie", "recommend", "draft", "governed_text_refused", "request_refused",
        "approval_naming_other", "requested_without_borrower", "other_assign_same_clip", "other_approve",
        "other_distribute",
    ):
        assert dropped not in keys, dropped
    assert "approval_naming_other" in {r["key"] for r in lakebase.rows if r["entity_id"] == seeds.APPROVAL}


def test_the_projection_is_the_closed_allowlist_and_carries_no_free_text() -> None:
    result = _list(_HistoryLakebase(), privileged=True)
    payload = result.model_dump(mode="json")
    raw = json.dumps(payload)

    assert set(payload) == {"borrower_id", "items", "truncated"}
    for item in payload["items"]:
        assert set(item) == set(BorrowerDecisionEvent.model_fields)
    for forbidden in (
        seeds.SECRET_TEXT, "fair", "lending", "f" * 64, seeds.APPROVAL, seeds.BATCH, seeds.ACTIVATION,
        seeds.DISTRIBUTION_REQUEST, seeds.SHARED_CLIP, seeds.OTHER, seeds.PHONE_SHAPED, "consent_not_opt_in",
        "opt_out", "rationale_code", "per_lo_counts", "lo_emails", "notes", '"draft_', "review_mode", "individual",
    ):
        assert forbidden not in raw, forbidden


def test_each_row_projects_its_closed_qualifiers() -> None:
    lakebase = _HistoryLakebase()
    by_key = {lakebase.key_of(item.audit_event_id): item for item in _list(lakebase).items}

    assert by_key["reject"].rationale_label == "Compliance review"
    assert (by_key["reject"].offer_code, by_key["reject"].channel, by_key["reject"].outcome) == ("heloc", "sms", "rejected")
    assert by_key["approve"].assigned_to_display == "Summit LO 01 (Loan officer)"
    assert by_key["status"].from_status == "assigned" and by_key["status"].to_status == "contact_drafted"
    assert by_key["disposition"].disposition_outcome == "connected"
    assert by_key["lead_outcome"].lead_outcome_type == "closed_funded"
    assert by_key["activation"].activation_status == "dry_run"
    assert by_key["blocked_approve"].contact_block_label == "No marketing consent"
    assert by_key["blocked_activation"].contact_block_label == "Contacted within 30 days"
    assert by_key["distribute"].assigned_to_display == "Summit LO 01 (Loan officer)"
    assert by_key["unassign"].outcome == "unassigned"
    assert by_key["revoke"].outcome == "revoked" and by_key["requested"].outcome == "requested"
    # Out-of-vocabulary metadata projects to null (fail closed).
    assert by_key["requested"].offer_code is None and by_key["assign"].rationale_label is None


def test_actors_read_as_team_label_staff_email_automation_or_unverified() -> None:
    lakebase = _HistoryLakebase()
    by_key = {lakebase.key_of(item.audit_event_id): item for item in _list(lakebase).items}

    assert (by_key["assign"].actor_display, by_key["assign"].actor_kind) == ("Summit Sales Manager (Sales manager)", "staff")
    assert (by_key["revoke"].actor_display, by_key["revoke"].actor_kind) == ("pat.approver@summit.example", "staff")
    assert (by_key["activation"].actor_display, by_key["activation"].actor_kind) == ("Automation", "automation")
    assert (by_key["blocked_activation"].actor_display, by_key["blocked_activation"].actor_kind) == (
        "Unverified identity", "unverified",
    )


def test_a_receipt_is_offered_on_own_decisions_or_to_a_privileged_viewer_only() -> None:
    lakebase = _HistoryLakebase()
    as_member = {lakebase.key_of(i.audit_event_id): i for i in _list(lakebase, viewer=seeds.OWN).items}
    as_auditor = {lakebase.key_of(i.audit_event_id): i for i in _list(lakebase, viewer="aud@summit.example", privileged=True).items}

    assert as_member["approve"].is_own and as_member["approve"].receipt_available
    assert not as_member["reject"].is_own and not as_member["reject"].receipt_available
    assert not as_member["revoke"].receipt_available
    assert as_auditor["reject"].receipt_available and as_auditor["revoke"].receipt_available
    assert not as_auditor["approve"].is_own
    # Only the three decisions the receipt endpoint reads offer one.
    offered = {key for key, item in as_auditor.items() if item.receipt_available}
    assert offered == {"approve", "reject", "revoke"}


def test_more_than_fifty_decisions_keep_the_latest_fifty_and_say_so() -> None:
    template = next(row for row in seeds.AUDIT_ROWS if row.key == "disposition")
    many = tuple(
        seeds.SeedAudit(f"d{n}", template.event_type, template.actor_email, template.entity_type, template.entity_id,
                        dict(template.metadata), template.shown_to)
        for n in range(53)
    )
    lakebase = _HistoryLakebase(many)
    result = _list(lakebase)

    assert len(result.items) == 50 and result.truncated is True
    assert lakebase.key_of(result.items[0].audit_event_id) == "d52"
    exactly = _list(_HistoryLakebase(many[:50]))
    assert len(exactly.items) == 50 and exactly.truncated is False


# -- 5. the endpoint -----------------------------------------------------------------------


OUTSIDER = "outsider@summit.example"
APPROVER = "approver.person@summit.example"
ADMIN = "admin.person@summit.example"
AUDITOR = "auditor.person@summit.example"


@pytest.fixture
def endpoint(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[_HistoryLakebase, InMemoryAuditStore]]:
    lakebase = _HistoryLakebase()
    audit = InMemoryAuditStore()
    monkeypatch.setattr(settings, "trust_forwarded_headers", True)
    monkeypatch.setattr(settings, "approver_emails", APPROVER)
    monkeypatch.setattr(settings, "admin_emails", ADMIN)
    monkeypatch.setattr(settings, "auditor_emails", AUDITOR)

    def _no_warehouse() -> Any:
        raise AssertionError("the decision history must never construct a warehouse repository")

    app.dependency_overrides[get_lakebase_client] = lambda: lakebase
    app.dependency_overrides[get_audit_store] = lambda: audit
    for factory in (get_borrower_repository, get_lead_repository, get_offer_repository, get_outreach_repository):
        app.dependency_overrides[factory] = _no_warehouse
    clear_sales_state_cache()
    yield lakebase, audit
    clear_sales_state_cache()


def _get(viewer: str, borrower_id: str = seeds.BORROWER):
    return client.get(ROUTE.format(borrower_id=borrower_id), headers={"X-Forwarded-Email": viewer, "X-Forwarded-Groups": ""})


def test_a_plain_user_outside_the_team_is_refused(endpoint: tuple[_HistoryLakebase, InMemoryAuditStore]) -> None:
    lakebase, audit = endpoint
    response = _get(OUTSIDER)

    assert response.status_code == 403
    assert response.json()["detail"] == "forbidden"
    # Refused before any history statement ran.
    assert history.DECISION_HISTORY_SQL not in lakebase.statements
    assert audit.list(limit=50) == []


@pytest.mark.parametrize(("viewer", "privileged"), [
    (seeds.OWN, False), (APPROVER, False), (ADMIN, True), (AUDITOR, True),
])
def test_the_working_team_reads_it_with_no_audit_row_and_no_store(
    endpoint: tuple[_HistoryLakebase, InMemoryAuditStore], viewer: str, privileged: bool
) -> None:
    lakebase, audit = endpoint
    response = _get(viewer)

    assert response.status_code == 200, response.text
    assert response.headers["Cache-Control"] == "private, no-store"
    items = response.json()["items"]
    assert [lakebase.key_of(item["audit_event_id"]) for item in items] == seeds.expected_keys(seeds.BORROWER)
    reject = next(item for item in items if lakebase.key_of(item["audit_event_id"]) == "reject")
    assert reject["receipt_available"] is privileged
    assert audit.list(limit=50) == []
    # Only the roster member needed the team check.
    team_checks = [sql for sql in lakebase.statements if "active = true" in sql]
    assert len(team_checks) == (1 if viewer == seeds.OWN else 0)


def test_a_malformed_id_is_a_fixed_422_and_an_outage_a_503(endpoint: tuple[_HistoryLakebase, InMemoryAuditStore]) -> None:
    lakebase, _audit = endpoint
    malformed = _get(ADMIN, borrower_id="not-a-borrower")
    assert malformed.status_code == 422
    assert malformed.json()["detail"] == "invalid borrower_id"

    lakebase.fail = True
    down = _get(ADMIN)
    assert down.status_code == 503
    assert "down" not in down.text


def test_an_unauthenticated_caller_gets_401(endpoint: tuple[_HistoryLakebase, InMemoryAuditStore]) -> None:
    response = client.get(ROUTE.format(borrower_id=seeds.BORROWER))
    assert response.status_code == 401


def test_the_compat_alias_serves_the_same_route(endpoint: tuple[_HistoryLakebase, InMemoryAuditStore]) -> None:
    response = client.get(COMPAT_ROUTE.format(borrower_id=seeds.BORROWER), headers={"X-Forwarded-Email": ADMIN})
    assert response.status_code == 200
    assert response.json()["borrower_id"] == seeds.BORROWER


def test_the_seeds_are_internally_consistent() -> None:
    assert len({row.key for row in seeds.AUDIT_ROWS}) == len(seeds.AUDIT_ROWS)
    assert all(re.fullmatch(r"B-[0-9A-Z]{13}", bid) for bid in (seeds.BORROWER, seeds.OTHER, seeds.PHONE_SHAPED))
    # Non-vacuity: every borrower has a history and every control is negative somewhere.
    assert all(seeds.expected_keys(bid) for bid in (seeds.BORROWER, seeds.OTHER, seeds.PHONE_SHAPED))
    assert sum(1 for row in seeds.AUDIT_ROWS if not row.shown_to) >= 10

"""Verify the Slice-5 audit-emission wiring on the API surface.

Uses the FastAPI ``TestClient`` with the test overrides from
``tests/conftest.py`` (an in-memory ``AuditStore`` + a fake
``LakebaseClient``). We assert that hitting each endpoint produces
the right audit row(s) in the in-memory store, and that
``/api/outreach/approve`` ALSO issues an INSERT against the fake
Lakebase client's ``approvals`` table.

These are integration-tier tests because they exercise the full
router -> factory -> store seam, not a single unit. They do not
require any live network.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from backend.main import app
from backend.services.audit_store import InMemoryAuditStore, get_audit_store
from backend.services.lakebase import get_lakebase_client

client = TestClient(app)


def _installed_audit_store() -> InMemoryAuditStore:
    # conftest installed a session-scoped InMemoryAuditStore; retrieve it
    # via the dependency override callable so we're asserting on the same
    # instance the routers write to.
    store = app.dependency_overrides[get_audit_store]()
    assert isinstance(store, InMemoryAuditStore)
    return store


def _installed_lakebase():
    return app.dependency_overrides[get_lakebase_client]()


def _clear_store() -> InMemoryAuditStore:
    store = _installed_audit_store()
    store._events.clear()
    return store


def test_get_borrower_emits_view_borrower_audit_row() -> None:
    store = _clear_store()

    r = client.get("/api/borrowers/B-48291")
    assert r.status_code == 200

    events = store.list(limit=10)
    assert len(events) == 1
    e = events[0]
    assert e.event_type == "VIEW_BORROWER"
    assert e.entity_type == "borrower"
    assert e.entity_id == "B-48291"
    # payload captures score components for governance §4 replay.
    assert "opportunity_score" in e.payload_json
    # subject_clip is the already-redacted clip_id (synthetic
    # ``clip_demo_*`` for the pinned trio in fixtures, or the 12-char
    # ``clip_ref`` hash for real rows).
    # Either way, the raw Cotality CLIP never lands here -- the repository
    # boundary enforces that per docs/governance-real-data-review.md §1.
    assert e.subject_clip is not None


def test_list_leads_emits_view_leads_audit_row() -> None:
    store = _clear_store()

    r = client.get("/api/leads?portfolio_id=p1&segment=itm")
    assert r.status_code == 200

    events = store.list(limit=10)
    view_leads = [e for e in events if e.event_type == "VIEW_LEADS"]
    assert len(view_leads) == 1
    e = view_leads[0]
    assert e.subject_segment == "itm"
    assert "rendered_borrower_ids" in e.payload_json
    assert isinstance(e.payload_json["rendered_borrower_ids"], list)


def test_recommend_offer_emits_recommend_offer_audit_row() -> None:
    store = _clear_store()

    r = client.post("/api/offers/recommend", json={"borrower_id": "B-48291"})
    assert r.status_code == 200

    events = store.list(limit=10)
    rec = [e for e in events if e.event_type == "RECOMMEND_OFFER"]
    assert len(rec) == 1
    e = rec[0]
    assert e.entity_id == "B-48291"
    assert "offer_code" in e.payload_json
    assert "thresholds_applied" in e.payload_json


def test_draft_outreach_emits_draft_outreach_audit_row() -> None:
    store = _clear_store()

    r = client.post(
        "/api/outreach/draft",
        json={"borrower_id": "B-48291", "channel": "email"},
    )
    assert r.status_code == 200

    events = store.list(limit=10)
    drafts = [e for e in events if e.event_type == "DRAFT_OUTREACH"]
    assert len(drafts) == 1
    e = drafts[0]
    assert e.entity_type == "outreach_draft"
    assert e.payload_json.get("channel") == "email"


def test_approve_outreach_writes_approvals_row_and_audit_event() -> None:
    store = _clear_store()
    lakebase = _installed_lakebase()
    lakebase.executes.clear()

    r = client.post(
        "/api/outreach/approve",
        json={
            "borrower_id": "B-48291",
            "offer_code": "refi",
            "actor": "anonymous",
        },
    )
    assert r.status_code == 200

    # Approvals row -- INSERT INTO mip_app.approvals with named params.
    approval_inserts = [
        (sql, params)
        for sql, params in lakebase.executes
        if "INSERT INTO mip_app.approvals" in sql
    ]
    assert len(approval_inserts) == 1
    _sql, params = approval_inserts[0]
    assert params["borrower_id"] == "B-48291"
    assert params["offer_code"] == "refi"
    assert params["action"] == "approve"
    assert params["actor_email"]  # resolved to default_actor or header

    # Audit event.
    events = store.list(limit=10)
    approves = [e for e in events if e.event_type == "APPROVE"]
    assert len(approves) == 1
    e = approves[0]
    assert e.entity_type == "approval"
    assert e.payload_json.get("borrower_id") == "B-48291"
    assert e.payload_json.get("offer_code") == "refi"

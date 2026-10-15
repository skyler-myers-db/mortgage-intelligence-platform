"""POST /api/leads/{borrower_id}/snooze (wow-power-3 phase 2).

Over a stateful fake Lakebase (the real snooze transaction SQL runs against
PostgreSQL in tests/integration/test_lead_snoozes_postgres.py): exactly one
LEAD_SNOOZE row with no text on success; the frequency-cap clamp; 409
not_contactable with exactly one LEAD_SNOOZE_REFUSED row and no snooze row;
409 no_open_follow_up with no row at all; request_id replay and mismatch;
supersede and timer close inside the one transaction; 401 / 403 / 422 / 503;
the actor's summary cache invalidated.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.config.settings import settings
from backend.main import app
from backend.services import lead_snooze, sales_followups
from backend.services.databricks_sql import DatabricksSqlError
from backend.services.lakebase import LakebaseError
from backend.services.lead_snooze import LeadSnoozeService, get_lead_snooze_service
from backend.services.repositories import get_borrower_repository
from backend.services.sales_followups import FollowupService, get_followup_service
from backend.services.sales_followups_sql import BORROWER_ITEM_SQL
from backend.services.sales_state_mappers import _SALES_AUDIT_INSERT_SQL, _SALES_STATE_CACHE

LO = "lo01@summit.example"
APPROVER = "approver@summit-mortgage.example"
STRANGER = "someone@summit-mortgage.example"
BORROWER = "B-48291"
# Route literal for the architecture manifest: /api/leads/{borrower_id}/snooze
PATH = f"/api/v1/leads/{BORROWER}/snooze"
RECENT_TOUCH = datetime.now(UTC) - timedelta(days=2)


class _Cursor:
    def __init__(self, lakebase: _Lakebase) -> None:
        self.lakebase = lakebase
        self._next: dict[str, Any] | None = None

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def execute(self, sql: str, params: dict[str, Any] | None = None) -> None:
        params = dict(params or {})
        lb = self.lakebase
        lb.statements.append(sql)
        self._next = None
        if sql == _SALES_AUDIT_INSERT_SQL:
            audit_id = str(uuid4())
            lb.pending_audit.append({**params, "audit_id": audit_id, "metadata": json.loads(params["metadata"])})
            self._next = {"audit_id": audit_id}
        elif sql == lead_snooze._CLOSE_EXPIRED_SQL:
            for row in lb.pending_rows():
                if row["woken_at"] is None and row["wake_at"] <= params["now"]:
                    row.update(woken_at=row["wake_at"], wake_reason="timer")
        elif sql == lead_snooze._SUPERSEDE_SQL:
            for row in lb.pending_rows():
                if row["woken_at"] is None and row["wake_at"] > params["now"]:
                    row.update(woken_at=params["now"], wake_reason="superseded")
        elif sql == lead_snooze._INSERT_SQL:
            if lb.lose_insert:
                return
            lb.pending_snoozes.append({
                "snooze_id": params["snooze_id"], "actor_email": params["actor"],
                "borrower_id": params["borrower_id"], "snoozed_at": params["now"],
                "wake_at": params["wake_at"], "wake_clamped": params["wake_clamped"],
                "request_id": params["request_id"], "audit_event_id": params["audit_event_id"],
                "woken_at": None, "wake_reason": None,
            })
            self._next = {"snooze_id": params["snooze_id"]}
        else:
            raise AssertionError(f"unexpected statement: {sql[:60]}")

    def fetchone(self) -> dict[str, Any] | None:
        return self._next


class _Conn:
    def __init__(self, lakebase: _Lakebase) -> None:
        self.lakebase = lakebase

    def cursor(self) -> _Cursor:
        return _Cursor(self.lakebase)


class _Lakebase:
    def __init__(self, *, kind: str | None = "callback") -> None:
        self.kind = kind
        self.snoozes: list[dict[str, Any]] = []
        self.audit: list[dict[str, Any]] = []
        self.pending_snoozes: list[dict[str, Any]] = []
        self.pending_audit: list[dict[str, Any]] = []
        self.statements: list[str] = []
        self.lose_insert = False
        self.error: BaseException | None = None

    def pending_rows(self) -> list[dict[str, Any]]:
        return self.snoozes

    @contextmanager
    def transaction(self) -> Iterator[_Conn]:
        if self.error is not None:
            raise self.error
        self.pending_snoozes, self.pending_audit = [], []
        before = [dict(row) for row in self.snoozes]
        try:
            yield _Conn(self)
        except Exception:
            self.snoozes[:] = before  # rolled back
            self.pending_snoozes, self.pending_audit = [], []
            raise
        self.snoozes.extend(self.pending_snoozes)
        self.audit.extend(self.pending_audit)

    def fetchone(self, sql: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        if self.error is not None:
            raise self.error
        params = dict(params or {})
        if "FROM mip_app.sales_team" in sql:
            if params.get("email") != LO:
                return None
            return {"email": LO, "display_label": "Summit LO 01", "role": "loan_officer", "region": "IL",
                    "manager_email": None, "capacity_per_day": 35, "active": True}
        if sql == lead_snooze._REPLAY_SQL:
            row = next((r for r in self.snoozes if r["request_id"] == params["request_id"]), None)
            if row is None:
                return None
            audit = next((a for a in self.audit if a["audit_id"] == row["audit_event_id"]), {"metadata": {}})
            return {**row, "snooze_days": str(audit["metadata"].get("snooze_days"))}
        if sql == BORROWER_ITEM_SQL:
            if params["actor"] != LO or params["borrower_id"] != BORROWER or self.kind is None:
                return None
            return {"kind": self.kind, "since_at": datetime.now(UTC)}
        raise AssertionError(f"unexpected read: {sql[:60]}")

    def fetchall(self, sql: str, params: dict[str, Any] | None = None, limit: int = 100) -> list[dict[str, Any]]:
        raise AssertionError("the snooze never lists")

    def execute(self, sql: str, params: dict[str, Any] | None = None) -> None:
        raise AssertionError("every snooze write runs in one transaction")


@pytest.fixture(autouse=True)
def _settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "trust_forwarded_headers", True)
    monkeypatch.setattr(settings, "app_env", "sandbox")
    monkeypatch.setattr(settings, "admin_emails", "")
    monkeypatch.setattr(settings, "admin_identities", "")
    monkeypatch.setattr(settings, "approver_emails", APPROVER)
    monkeypatch.setattr(settings, "approver_identities", "")
    _SALES_STATE_CACHE.clear()


@pytest.fixture
def repo() -> Iterator[Any]:
    borrower_repo = app.dependency_overrides[get_borrower_repository]()
    borrower_repo.contactability_overrides = {}
    yield borrower_repo
    borrower_repo.contactability_overrides = {}


@pytest.fixture
def lakebase(repo: Any) -> _Lakebase:
    fake = _Lakebase()
    app.dependency_overrides[get_followup_service] = lambda: FollowupService(lambda: fake)
    app.dependency_overrides[get_lead_snooze_service] = lambda: LeadSnoozeService(lambda: fake)
    return fake


def _post(body: dict[str, Any], *, actor: str | None = LO, path: str = PATH) -> Any:
    headers = {"X-Forwarded-Groups": "", "Content-Type": "application/json"}
    if actor is not None:
        headers["X-Forwarded-Email"] = actor
    return TestClient(app).post(path, headers=headers, content=json.dumps(body))


def _body(days: int = 3, request_id: str | None = None) -> dict[str, Any]:
    return {"snooze_days": days, "request_id": request_id or str(uuid4())}


def _events(lakebase: _Lakebase, event_type: str) -> list[dict[str, Any]]:
    return [row for row in lakebase.audit if row["event_type"] == event_type]


def test_a_snooze_writes_one_lead_snooze_row_with_no_text(lakebase: _Lakebase) -> None:
    started = datetime.now(UTC)

    response = _post(_body(3))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["borrower_id"] == BORROWER and body["wake_clamped"] is False
    wake = datetime.fromisoformat(body["wake_at"])
    assert started + timedelta(days=3) <= wake <= datetime.now(UTC) + timedelta(days=3)
    [audit] = _events(lakebase, "LEAD_SNOOZE")
    assert audit["entity_type"] == "borrower" and audit["entity_id"] == BORROWER
    assert set(audit["metadata"]) == {
        "action", "borrower_id", "snooze_id", "snooze_days", "wake_at", "wake_clamped", "followup_kind",
    }
    assert audit["metadata"]["snooze_days"] == 3 and audit["metadata"]["followup_kind"] == "callback"
    [snooze] = lakebase.snoozes
    assert snooze["audit_event_id"] == audit["audit_id"] == body["audit_event_id"]
    assert snooze["snooze_id"] == audit["metadata"]["snooze_id"]
    assert len(lakebase.audit) == 1


def test_a_frequency_capped_item_wakes_no_earlier_than_the_cap(lakebase: _Lakebase, repo: Any) -> None:
    cap_ends = datetime.now(UTC) + timedelta(days=20)
    repo.contactability_overrides = {BORROWER: {"eligible_recontact_at": cap_ends}}

    response = _post(_body(1))

    assert response.status_code == 200
    body = response.json()
    assert body["wake_clamped"] is True
    assert datetime.fromisoformat(body["wake_at"]) == cap_ends
    assert len(_events(lakebase, "LEAD_SNOOZE")) == 1


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"suppression_reason": "bankruptcy"}, "suppressed"),
        ({"consent_status": "opt_out"}, "consent_not_opt_in"),
        ({"contactable_sql": False}, "eligibility_not_proven"),
        # evaluate() reports the cap first; a cap never masks the block behind it
        # (a borrower who said stop during a call is both recently touched and DNC).
        ({"suppression_reason": "do_not_contact", "dnc": True, "last_touch_at": RECENT_TOUCH}, "suppressed"),
        ({"marketing_eligible": False, "last_touch_at": RECENT_TOUCH}, "eligibility_not_proven"),
    ],
    ids=["suppressed", "consent", "predicate", "dnc-behind-cap", "unproven-behind-cap"],
)
def test_a_withheld_item_is_refused_with_exactly_one_refusal_row(
    lakebase: _Lakebase, repo: Any, overrides: dict[str, Any], reason: str
) -> None:
    repo.contactability_overrides = {BORROWER: overrides}

    response = _post(_body())

    assert response.status_code == 409 and response.json()["detail"] == "not_contactable"
    [refused] = lakebase.audit
    assert refused["event_type"] == "LEAD_SNOOZE_REFUSED"
    assert {k: v for k, v in refused["metadata"].items() if k != "action"} == {
        "borrower_id": BORROWER, "contact_block_reason": reason,
    }
    assert lakebase.snoozes == []


def test_an_id_out_of_coverage_is_refused_as_not_in_coverage(lakebase: _Lakebase, monkeypatch: pytest.MonkeyPatch,
                                                            repo: Any) -> None:
    monkeypatch.setattr(repo, "contactability", lambda _ids: {})

    response = _post(_body())

    assert response.status_code == 409
    [refused] = lakebase.audit
    assert refused["metadata"]["contact_block_reason"] == "not_in_coverage"


def test_no_open_item_is_an_unaudited_409(lakebase: _Lakebase) -> None:
    lakebase.kind = None

    response = _post(_body())

    assert response.status_code == 409 and response.json()["detail"] == "no_open_follow_up"
    assert lakebase.audit == [] and lakebase.snoozes == []


def test_a_replayed_request_id_answers_the_stored_snooze(lakebase: _Lakebase) -> None:
    body = _body(7)
    first = _post(body)
    second = _post(body)

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert len(lakebase.snoozes) == 1 and len(lakebase.audit) == 1


@pytest.mark.parametrize("days", [1, 3])
def test_a_request_id_reused_for_a_different_snooze_is_a_409(lakebase: _Lakebase, days: int) -> None:
    request_id = str(uuid4())
    assert _post(_body(7, request_id)).status_code == 200

    response = _post(_body(days, request_id))

    assert response.status_code == 409
    assert response.json()["detail"] == "request_id already belongs to a different snooze"
    assert len(lakebase.snoozes) == 1


def test_a_new_snooze_supersedes_the_open_one_and_closes_an_expired_one(lakebase: _Lakebase) -> None:
    now = datetime.now(UTC)
    lakebase.snoozes.append({
        "snooze_id": str(uuid4()), "actor_email": LO, "borrower_id": BORROWER, "snoozed_at": now - timedelta(days=5),
        "wake_at": now - timedelta(days=2), "wake_clamped": False, "request_id": str(uuid4()),
        "audit_event_id": str(uuid4()), "woken_at": None, "wake_reason": None,
    })
    assert _post(_body(1)).status_code == 200

    assert _post(_body(3)).status_code == 200

    reasons = [row["wake_reason"] for row in lakebase.snoozes]
    assert reasons == ["timer", "superseded", None]
    assert [s for s in lakebase.statements if "lead_snoozes" in s][:3] == [
        lead_snooze._CLOSE_EXPIRED_SQL, lead_snooze._SUPERSEDE_SQL, lead_snooze._INSERT_SQL,
    ]


def test_a_lost_insert_race_is_a_snooze_in_progress_409_and_rolls_the_audit_back(lakebase: _Lakebase) -> None:
    lakebase.lose_insert = True

    response = _post(_body())

    assert response.status_code == 409 and response.json()["detail"] == "snooze_in_progress"
    assert lakebase.audit == [] and lakebase.snoozes == []


def test_the_actor_summary_cache_is_invalidated(lakebase: _Lakebase) -> None:
    key = sales_followups.summary_cache_key(LO)
    sales_followups._SUMMARY_CACHE.set(key, object(), 30)

    assert _post(_body()).status_code == 200

    assert sales_followups._SUMMARY_CACHE.get(key) is None


def test_auth_and_input_gates(lakebase: _Lakebase) -> None:
    assert _post(_body(), actor=None).status_code == 401
    assert _post(_body(), actor=STRANGER).status_code == 403
    assert _post(_body(2)).status_code == 422, "only 1, 3 or 7 days"
    assert _post({"snooze_days": 1, "request_id": "not-a-uuid"}).status_code == 422
    assert _post({**_body(), "note": "call him Tuesday"}).status_code == 422, "no free text"
    assert _post(_body(), path="/api/v1/leads/12345/snooze").status_code == 422
    assert _post(_body(), actor=APPROVER).status_code == 409, "an approver reaches the ownership check"
    assert lakebase.audit == [] and lakebase.snoozes == []


def test_dependency_failures_are_safe_503s_and_write_nothing(
    lakebase: _Lakebase, repo: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _fail(_ids: Any) -> Any:
        raise DatabricksSqlError("statement_id=abc")

    monkeypatch.setattr(repo, "contactability", _fail)
    warehouse = _post(_body())
    assert warehouse.status_code == 503
    assert warehouse.json()["detail"] == "warehouse is temporarily unavailable"
    assert lakebase.audit == [] and lakebase.snoozes == []

    lakebase.error = LakebaseError("connection to 10.0.0.1 failed")
    down = _post(_body())
    assert down.status_code == 503 and "10.0.0.1" not in down.text

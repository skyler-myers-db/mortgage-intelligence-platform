"""O1 over HTTP: approve and reject audit a governed-text refusal, nothing else.

A rationale the governed text screen refuses answers the SAME 422 detail as
before the ruling plus exactly one GOVERNED_TEXT_REFUSED row: no approvals
row, no draft or borrower read, no other audit row. A missing review_mode and
a non-approver's 403 are answered before the screen and write nothing.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.services.audit_store import get_audit_store
from backend.services.lakebase import get_lakebase_client
from backend.services.repositories import get_lead_repository, get_outreach_repository
from tests.fixtures.approval_ledger_fake import FakeApprovalLedger
from tests.fixtures.in_memory_audit_store import InMemoryAuditStore
from tests.fixtures.reviewed_approval import synthetic_review_proof

APPROVER = {"X-Forwarded-Email": "pat.approver@summit.example", "X-Forwarded-Groups": "mip-admin"}
ANALYST = {"X-Forwarded-Email": "alice.analyst@summit.example", "X-Forwarded-Groups": ""}
BORROWER = "B-ARQTESTX00001"
FAIR_LENDING = "Prioritize borrowers who are not Hispanic"
PII = "Call them even if they opted out."


class _NoReads:
    """A repository that fails the test on any read."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)

        def _record(*_args: Any, **_kwargs: Any) -> Any:
            self.calls.append(name)
            raise AssertionError(f"unexpected read: {name}")

        return _record


def _provider(value: Any) -> Any:
    # A closure with no parameters: FastAPI reads an override's signature, so
    # a defaulted argument would become a request parameter.
    def _provide() -> Any:
        return value

    return _provide


@pytest.fixture
def world() -> Iterator[dict[str, Any]]:
    state: dict[str, Any] = {
        "ledger": FakeApprovalLedger(),
        "repo": _NoReads(),
        "leads": _NoReads(),
        "audit": InMemoryAuditStore(),
    }
    deps = {
        get_lakebase_client: state["ledger"],
        get_outreach_repository: state["repo"],
        get_lead_repository: state["leads"],
        get_audit_store: state["audit"],
    }
    saved = {dep: app.dependency_overrides.get(dep) for dep in deps}
    for dep, value in deps.items():
        app.dependency_overrides[dep] = _provider(value)
    try:
        yield state
    finally:
        for dep, original in saved.items():
            if original is None:
                app.dependency_overrides.pop(dep, None)
            else:
                app.dependency_overrides[dep] = original


def _approve(rationale: str | None = None, **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "borrower_id": BORROWER,
        "draft_body": "Reviewed copy.",
        "request_id": str(uuid4()),
        **synthetic_review_proof(review_mode="individual"),
    }
    if rationale is not None:
        body["rationale"] = rationale
    body.update(extra)
    return body


def _reject(rationale: str) -> dict[str, Any]:
    return {
        "borrower_id": BORROWER,
        "rationale_code": "low_intent",
        "rationale": rationale,
        "request_id": str(uuid4()),
    }


def _assert_only_the_refusal_row(world: dict[str, Any], *, surface: str, screen: str) -> None:
    ledger: FakeApprovalLedger = world["ledger"]
    assert ledger.audit_types() == ["GOVERNED_TEXT_REFUSED"]
    metadata = ledger.audits[0]["metadata"]
    assert {key: metadata[key] for key in ("surface", "screen", "item_count")} == {
        "surface": surface,
        "screen": screen,
        "item_count": 1,
    }
    assert ledger.statements == ["INSERT_AUDIT"]
    assert ledger.approvals == []
    assert world["repo"].calls == [] and world["leads"].calls == []
    assert world["audit"]._events == []


@pytest.mark.parametrize(
    ("field", "text", "screen"),
    [
        ("rationale", FAIR_LENDING, "fair_lending"),
        ("rationale", PII, "pii"),
        ("bulk_rationale", FAIR_LENDING, "fair_lending"),
    ],
)
def test_a_refused_approve_rationale_answers_the_same_422_and_writes_one_row(
    world: dict[str, Any], field: str, text: str, screen: str
) -> None:
    body = _approve(**{field: text}) if field == "bulk_rationale" else _approve(text)
    if field == "bulk_rationale":
        body.update(bulk_id="11111111-1111-4111-8111-111111111111", review_mode="bulk_sample")

    response = TestClient(app).post("/api/outreach/approve", json=body, headers=APPROVER)

    assert response.status_code == 422, response.text
    assert response.json()["detail"] == f"{field} failed the governed text policy"
    assert text not in response.text
    _assert_only_the_refusal_row(world, surface="approve", screen=screen)


@pytest.mark.parametrize(("text", "screen"), [(FAIR_LENDING, "fair_lending"), (PII, "pii")])
def test_a_refused_reject_rationale_answers_the_same_422_and_writes_one_row(
    world: dict[str, Any], text: str, screen: str
) -> None:
    response = TestClient(app).post("/api/outreach/reject", json=_reject(text), headers=APPROVER)

    assert response.status_code == 422, response.text
    assert response.json()["detail"] == "rationale failed the governed text policy"
    assert text not in response.text
    _assert_only_the_refusal_row(world, surface="reject", screen=screen)


def test_a_missing_review_mode_is_refused_first_and_writes_nothing(world: dict[str, Any]) -> None:
    body = _approve(FAIR_LENDING)
    del body["review_mode"]

    response = TestClient(app).post("/api/outreach/approve", json=body, headers=APPROVER)

    assert response.status_code == 422
    assert response.json()["detail"] != "rationale failed the governed text policy"
    assert world["ledger"].statements == [] and world["ledger"].audits == []


@pytest.mark.parametrize("route", ["approve", "reject"])
def test_a_non_approver_is_403_and_writes_nothing(world: dict[str, Any], route: str) -> None:
    body = _approve(FAIR_LENDING) if route == "approve" else _reject(FAIR_LENDING)

    response = TestClient(app).post(f"/api/outreach/{route}", json=body, headers=ANALYST)

    assert response.status_code == 403
    assert world["ledger"].statements == [] and world["ledger"].audits == []


def test_a_refusal_whose_row_cannot_be_written_is_a_503(world: dict[str, Any]) -> None:
    world["ledger"].down = True

    response = TestClient(app).post("/api/outreach/reject", json=_reject(FAIR_LENDING), headers=APPROVER)

    assert response.status_code == 503
    assert "governed text policy" not in response.text
    assert world["ledger"].audits == [] and world["audit"]._events == []

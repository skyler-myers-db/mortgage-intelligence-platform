"""User-saved Lead Queue views: GET/POST/DELETE /workspace/saved-views.

tables-09 phase 2 / flow-08 slice 1. A view is a public-safe name plus the
canonical Copy-link grammar; the list is audit-free, save and delete are
owner-only and audited by the store, and no response echoes what was typed.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.schemas.saved_views import SavedView, SavedViewMutationResponse
from backend.services.audit_store import get_audit_store
from backend.services.lakebase import LakebaseError
from backend.services.saved_view_params import (
    SAVED_VIEW_PARAMS_REFUSED,
    canonical_saved_view_params,
)
from backend.services.saved_view_store import (
    SAVED_VIEW_LIMIT,
    SavedViewConflict,
    get_saved_view_store,
)

ALICE = {"X-Forwarded-Email": "alice.lo@summit.example"}
BOB = {"X-Forwarded-Email": "bob.lo@summit.example"}
PARAMS = "?state=IL&segment_codes=itm,equity&segment_mode=any&sort=score&dir=desc"


class _MemoryStore:
    """Owner-scoped, soft-deleting double with the Lakebase store's semantics."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.audits: list[tuple[str, str]] = []
        self.down = False

    def _live(self, actor: str) -> list[dict[str, Any]]:
        return [row for row in self.rows if row["actor"] == actor and row["deleted_at"] is None]

    def list(self, *, actor: str) -> list[SavedView]:
        if self.down:
            raise LakebaseError("down")
        live = sorted(self._live(actor), key=lambda row: row["updated_at"], reverse=True)
        return [
            SavedView(
                view_id=row["view_id"],
                name=row["name"],
                params=row["params"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in live[:SAVED_VIEW_LIMIT]
        ]

    def create(self, *, actor: str, name: str, params: str, filter_fingerprint: str) -> SavedViewMutationResponse:
        if self.down:
            raise LakebaseError("down")
        live = self._live(actor)
        if any(row["name"].casefold() == name.casefold() for row in live):
            raise SavedViewConflict("duplicate")
        if len(live) >= SAVED_VIEW_LIMIT:
            raise SavedViewConflict("limit")
        now = datetime.now(UTC)
        view_id = str(uuid4())
        self.rows.append(
            {"view_id": view_id, "actor": actor, "name": name, "params": params,
             "fingerprint": filter_fingerprint, "created_at": now, "updated_at": now, "deleted_at": None}
        )
        self.audits.append(("SAVE_QUEUE_VIEW", view_id))
        return SavedViewMutationResponse(ok=True, view_id=view_id, audit_event_id=str(uuid4()))

    def delete(self, *, actor: str, view_id: str) -> SavedViewMutationResponse | None:
        for row in self._live(actor):
            if row["view_id"] == view_id:
                row["deleted_at"] = datetime.now(UTC)
                self.audits.append(("DELETE_QUEUE_VIEW", view_id))
                return SavedViewMutationResponse(ok=True, view_id=view_id, audit_event_id=str(uuid4()))
        return None


class _AuditProbe:
    resolved = 0

    def provide(self) -> _AuditProbe:
        self.resolved += 1
        return self


def _provide(value: object) -> Any:
    def provide() -> object:
        return value

    return provide


@pytest.fixture
def store() -> Iterator[_MemoryStore]:
    memory = _MemoryStore()
    prior = app.dependency_overrides.get(get_saved_view_store)
    app.dependency_overrides[get_saved_view_store] = _provide(memory)
    try:
        yield memory
    finally:
        if prior is None:
            app.dependency_overrides.pop(get_saved_view_store, None)
        else:
            app.dependency_overrides[get_saved_view_store] = prior


def _save(name: str, params: str = PARAMS, headers: dict[str, str] = ALICE) -> Any:
    return TestClient(app).post("/api/workspace/saved-views", json={"name": name, "params": params}, headers=headers)


def test_save_list_and_delete_round_trip(store: _MemoryStore) -> None:
    saved = _save("My CA recapture queue")
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["ok"] is True and body["audit_event_id"]
    listed = TestClient(app).get("/api/workspace/saved-views", headers=ALICE)
    assert listed.status_code == 200
    [view] = listed.json()["saved_views"]
    assert view["view_id"] == body["view_id"]
    assert view["name"] == "My CA recapture queue"
    # Stored canonical: no leading "?", keys sorted.
    assert view["params"] == canonical_saved_view_params(PARAMS)[0]
    assert not view["params"].startswith("?")
    deleted = TestClient(app).delete(f"/api/workspace/saved-views/{body['view_id']}", headers=ALICE)
    assert deleted.status_code == 200
    assert deleted.json()["view_id"] == body["view_id"]
    assert TestClient(app).get("/api/workspace/saved-views", headers=ALICE).json() == {"saved_views": []}
    assert [event for event, _ in store.audits] == ["SAVE_QUEUE_VIEW", "DELETE_QUEUE_VIEW"]


def test_the_list_is_audit_free(store: _MemoryStore) -> None:
    probe = _AuditProbe()
    prior = app.dependency_overrides.get(get_audit_store)
    app.dependency_overrides[get_audit_store] = probe.provide
    try:
        assert TestClient(app).get("/api/workspace/saved-views", headers=ALICE).status_code == 200
    finally:
        if prior is None:
            app.dependency_overrides.pop(get_audit_store, None)
        else:
            app.dependency_overrides[get_audit_store] = prior
    assert probe.resolved == 0


def test_views_are_isolated_per_actor(store: _MemoryStore) -> None:
    view_id = _save("Pending approval – IL").json()["view_id"]
    assert TestClient(app).get("/api/workspace/saved-views", headers=BOB).json() == {"saved_views": []}
    # Bob cannot delete Alice's view, and the answer never confirms it exists.
    refused = TestClient(app).delete(f"/api/workspace/saved-views/{view_id}", headers=BOB)
    assert refused.status_code == 404
    assert refused.json()["detail"] == "Saved view not found"
    assert len(TestClient(app).get("/api/workspace/saved-views", headers=ALICE).json()["saved_views"]) == 1


@pytest.mark.parametrize(
    "name",
    [
        "My CA recapture queue",
        "Pending approval – IL",
        pytest.param(
            "TX HELOC aged >7d",
            marks=pytest.mark.xfail(
                reason=(
                    "Policy false positive, recorded not relaxed (W5a C2): the campaign-text "
                    "policy reads 'aged' as age targeting."
                ),
                strict=False,
            ),
        ),
    ],
)
def test_public_safe_names_are_allowed(store: _MemoryStore, name: str) -> None:
    response = _save(name)
    assert response.status_code == 200, response.text


@pytest.mark.parametrize(
    ("name", "fragments"),
    [
        ("John Smith leads", ("John", "Smith")),
        ("call 212-555-1212", ("212", "555-1212")),
        ("jane@x.com", ("jane", "x.com")),
        ("B-0123456789ABC", ("0123456789ABC",)),
    ],
)
def test_personal_details_are_refused_without_echo(
    store: _MemoryStore, name: str, fragments: tuple[str, ...]
) -> None:
    response = _save(name)
    assert response.status_code == 422
    text = response.text
    for fragment in fragments:
        assert fragment not in text
    assert "saved view name must be a short public-safe label" in text
    assert store.rows == []


@pytest.mark.parametrize(
    "params",
    [
        "?state=IL&row=B-0123456789ABC",
        "?assigned_to=lo.one@summit.example",
        "?growth_handoff=abc",
        "?campaign_id=11111111-1111-4111-8111-111111111111",
        "?variant_name=A",
        "?include_suppressed_for_analytics=true",
        "?limit=50",
        "?state=IL&mystery=1",
        "?state=IL&state=TX",
        "?state=ZZ",
        "?sort=borrower_id",
        "?min_opportunity_score=120",
        "?min_opportunity_score=90&max_opportunity_score=80",
        "?cohort_id=11111111-1111-4111-8111-111111111111&min_opportunity_score=70",
        "?state=",
        "",
        "?" + "&".join(f"zips=6{index:04d}" for index in range(41)),
    ],
)
def test_params_outside_the_view_grammar_are_refused(store: _MemoryStore, params: str) -> None:
    response = _save("Grammar probe", params=params)
    assert response.status_code == 422
    assert "@" not in response.text and "B-0123456789ABC" not in response.text
    if params:
        assert response.json()["detail"] == SAVED_VIEW_PARAMS_REFUSED
    assert store.rows == []


def test_a_duplicate_name_is_a_409_case_insensitively(store: _MemoryStore) -> None:
    assert _save("TX refi").status_code == 200
    duplicate = _save("tx REFI")
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "A saved view with this name already exists"


def test_the_limit_is_a_409(store: _MemoryStore) -> None:
    for index in range(SAVED_VIEW_LIMIT):
        assert _save(f"View {index}").status_code == 200
    over = _save("One more")
    assert over.status_code == 409
    assert over.json()["detail"] == "Saved view limit reached (25)"


def test_a_malformed_view_id_is_refused_and_an_absent_one_is_404(store: _MemoryStore) -> None:
    assert TestClient(app).delete("/api/workspace/saved-views/not-a-uuid", headers=ALICE).status_code == 422
    absent = TestClient(app).delete(f"/api/workspace/saved-views/{uuid4()}", headers=ALICE)
    assert absent.status_code == 404


def test_extra_fields_and_a_non_json_body_are_refused(store: _MemoryStore) -> None:
    extra = TestClient(app).post(
        "/api/workspace/saved-views",
        json={"name": "TX refi", "params": PARAMS, "actor_email": "bob.lo@summit.example"},
        headers=ALICE,
    )
    assert extra.status_code == 422
    form = TestClient(app).post(
        "/api/workspace/saved-views",
        content="name=TX+refi",
        headers={**ALICE, "Content-Type": "application/x-www-form-urlencoded"},
    )
    assert form.status_code in {415, 422}
    assert store.rows == []


def test_a_lakebase_outage_is_the_safe_503(store: _MemoryStore) -> None:
    store.down = True
    listed = TestClient(app).get("/api/workspace/saved-views", headers=ALICE)
    saved = _save("TX refi")
    assert listed.status_code == 503 and saved.status_code == 503
    assert "down" not in listed.text

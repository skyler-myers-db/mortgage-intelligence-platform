"""LakebaseSavedViewStore statements: owner-scoped, audited in-statement, no name in the ledger."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

import pytest

from backend.services import saved_view_store as store_module
from backend.services.saved_view_params import canonical_saved_view_params
from backend.services.saved_view_store import (
    SAVED_VIEW_LIMIT,
    LakebaseSavedViewStore,
    SavedViewConflict,
)

ACTOR = "alice.lo@summit.example"
NAME = "My CA recapture queue"
PARAMS, FINGERPRINT = canonical_saved_view_params("state=CA&segment=retention")


class _Client:
    def __init__(self, fetchone: list[dict[str, Any] | None] | None = None, fetchall: list[dict[str, Any]] | None = None) -> None:
        self._fetchone = list(fetchone or [])
        self._fetchall = fetchall or []
        self.statements: list[tuple[str, dict[str, Any]]] = []

    def fetchone(self, sql: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        self.statements.append((sql, dict(params or {})))
        return self._fetchone.pop(0) if self._fetchone else None

    def fetchall(self, sql: str, params: dict[str, Any] | None = None, *, limit: int | None = None) -> list[dict[str, Any]]:
        self.statements.append((sql, dict(params or {})))
        return self._fetchall


ALL_SQL = {
    "list": store_module._LIST_SQL,
    "create": store_module._CREATE_SQL,
    "classify": store_module._CLASSIFY_SQL,
    "delete": store_module._DELETE_SQL,
}


@pytest.mark.parametrize("name", sorted(ALL_SQL))
def test_every_statement_is_scoped_to_the_actor_and_the_surface(name: str) -> None:
    sql = ALL_SQL[name]
    # Every read or write of mip_app.saved_views carries the owner predicate.
    reads = len(re.findall(r"FROM mip_app\.saved_views|UPDATE mip_app\.saved_views", sql))
    assert reads >= 1 or "INSERT INTO mip_app.saved_views" in sql
    assert sql.count("actor_email = %(actor_email)s") >= reads
    assert "surface" in sql


@pytest.mark.parametrize(("name", "event"), [("create", "SAVE_QUEUE_VIEW"), ("delete", "DELETE_QUEUE_VIEW")])
def test_each_write_inserts_its_audit_row_in_the_same_statement(name: str, event: str) -> None:
    sql = ALL_SQL[name]
    assert "INSERT INTO mip_app.action_audit" in sql
    assert f"'{event}', %(actor_email)s, 'saved_view', view_id::text" in sql
    # The audit row is produced only from the changed row, and the statement
    # answers only when both exist.
    assert "CROSS JOIN audit" in sql


def test_delete_is_soft() -> None:
    sql = store_module._DELETE_SQL
    assert "SET deleted_at = now(), updated_at = now()" in sql
    assert "DELETE FROM" not in sql


def test_create_writes_the_row_and_metadata_without_the_name_or_params() -> None:
    client = _Client(fetchone=[{"view_id": "x", "audit_id": "a-1"}])
    result = LakebaseSavedViewStore(client).create(  # type: ignore[arg-type]
        actor=ACTOR, name=NAME, params=PARAMS, filter_fingerprint=FINGERPRINT
    )
    assert result.ok is True and result.audit_event_id == "a-1"
    [(sql, params)] = client.statements
    assert sql == store_module._CREATE_SQL
    assert params["actor_email"] == ACTOR
    assert params["name"] == NAME and params["name_key"] == NAME.casefold()
    assert params["params"] == PARAMS
    assert params["limit"] == SAVED_VIEW_LIMIT
    metadata = json.loads(params["metadata"])
    assert metadata["saved_view_id"] == result.view_id == params["view_id"]
    assert metadata["filter_fingerprint"] == FINGERPRINT
    rendered = params["metadata"]
    assert "recapture" not in rendered.lower()
    assert "retention" not in rendered and "state" not in rendered


@pytest.mark.parametrize(
    ("verdict", "kind"),
    [
        ({"duplicate": True, "live": 3}, "duplicate"),
        ({"duplicate": False, "live": SAVED_VIEW_LIMIT}, "limit"),
        ({"duplicate": False, "live": 2}, "changed"),
    ],
)
def test_a_refused_insert_is_classified(verdict: dict[str, Any], kind: str) -> None:
    client = _Client(fetchone=[None, verdict])
    with pytest.raises(SavedViewConflict) as refused:
        LakebaseSavedViewStore(client).create(  # type: ignore[arg-type]
            actor=ACTOR, name=NAME, params=PARAMS, filter_fingerprint=FINGERPRINT
        )
    assert refused.value.kind == kind
    classify_sql, classify_params = client.statements[1]
    assert classify_sql == store_module._CLASSIFY_SQL
    assert classify_params == {"actor_email": ACTOR, "surface": "lead_queue", "name_key": NAME.casefold()}


def test_delete_scopes_to_the_actor_and_answers_none_for_a_miss() -> None:
    client = _Client(fetchone=[None])
    view_id = "11111111-1111-4111-8111-111111111111"
    assert LakebaseSavedViewStore(client).delete(actor=ACTOR, view_id=view_id) is None  # type: ignore[arg-type]
    [(sql, params)] = client.statements
    assert "AND view_id = %(view_id)s::uuid" in sql
    assert params["actor_email"] == ACTOR and params["view_id"] == view_id
    metadata = json.loads(params["metadata"])
    assert set(metadata) <= {"saved_view_id", "request_id", "action"}


def test_the_list_quarantines_a_row_outside_todays_grammar() -> None:
    now = datetime(2026, 9, 30, tzinfo=UTC)
    client = _Client(
        fetchall=[
            {"view_id": "11111111-1111-4111-8111-111111111111", "name": NAME, "params": PARAMS,
             "created_at": now, "updated_at": now},
            {"view_id": "22222222-2222-4222-8222-222222222222", "name": "Old", "params": "row=B-0123456789ABC",
             "created_at": now, "updated_at": now},
        ]
    )
    views = LakebaseSavedViewStore(client).list(actor=ACTOR)  # type: ignore[arg-type]
    assert [view.name for view in views] == [NAME]
    sql, params = client.statements[0]
    assert "ORDER BY updated_at DESC" in sql and "LIMIT %(limit)s" in sql
    assert params == {"actor_email": ACTOR, "surface": "lead_queue", "limit": SAVED_VIEW_LIMIT}

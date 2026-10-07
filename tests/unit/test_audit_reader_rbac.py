"""The read-only Auditor role and accountable ledger reads (D-audit-reads-c3).

An auditor -- an exact identity in ``MIP_AUDITOR_EMAILS`` /
``MIP_AUDITOR_IDENTITIES`` -- reads the full audit ledger (events, page,
rollups, facets, count and another actor's decision receipt) and nothing
else: every administrator route and every approver decision stays closed to
them. Deployed ``X-Forwarded-Groups`` grant nothing; the ``mip-auditor``
group is a local/test compatibility path only, like the admin group.

Every served cross-actor ledger read writes exactly ONE background, fail-open
``VIEW_AUDIT_LEDGER`` row naming its surface, with a closed metadata shape
(no ledger row contents, no actor filter in clear).
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from pydantic import SecretStr

from backend.api.audit import DEFAULT_AUDIT_LIMIT
from backend.config.settings import settings
from backend.main import app
from backend.services import audit_ledger_reads, rbac
from backend.services.activation_state import get_activation_state_store
from backend.services.audit_fingerprint import (
    AUDIT_LEDGER_FINGERPRINT_DOMAIN,
    keyed_filter_fingerprint,
)
from backend.services.audit_metadata_policy import LEDGER_SURFACES
from backend.services.audit_pagination import audit_filter_fingerprint
from backend.services.audit_store import (
    AuditMetadataValueViolation,
    build_safe_audit_metadata,
    get_audit_store,
)
from backend.services.lakebase import get_lakebase_client
from tests.fixtures.in_memory_audit_store import InMemoryAuditStore

client = TestClient(app)

AUDITOR = "auditor.one@summit-mortgage.example"
ADMIN = "ops@summit-mortgage.example"
PLAIN = "lo@summit-mortgage.example"
AUDITOR_HEADERS = {"X-Forwarded-Email": AUDITOR, "X-Forwarded-Groups": ""}
PLAIN_HEADERS = {"X-Forwarded-Email": PLAIN, "X-Forwarded-Groups": ""}
ADMIN_HEADERS = {"X-Forwarded-Email": ADMIN, "X-Forwarded-Groups": "mip-admin"}

LEDGER_READS = {
    "events": "/api/v1/audit/events",
    "events_page": "/api/v1/audit/events/page",
    "rollups": "/api/v1/audit/rollups",
    "facets": "/api/v1/audit/facets",
    "count": "/api/v1/audit/count",
}
# Outreach decisions call ``require_approver`` inside the handler, after the
# body is validated, so they are probed with a well-formed decision body.
_DECISION_BODIES: dict[str, dict[str, Any]] = {
    "/api/v1/outreach/approve": {
        "borrower_id": "B-48291",
        "offer_code": "refi_plus_heloc",
        "channel": "email",
        "draft_subject": "Your mortgage review",
        "draft_body": "Contact a loan officer to review available mortgage options.",
        "rationale": "Approved after reviewing the governed draft.",
    },
    "/api/v1/outreach/reject": {
        "borrower_id": "B-48291",
        "offer_code": "refi_plus_heloc",
        "channel": "email",
        "rationale_code": "low_intent",
    },
}
ROW_KEYS = {"ledger_surface", "has_cursor", "returned_row_count", "filter_fingerprint", "read_audit_event_id"}


class _LedgerLakebase:
    """Answers the rollup / facet / count reads; refuses any write."""

    def fetchone(self, sql: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return {"event_count": 3}

    def fetchall(
        self, sql: str, params: dict[str, Any] | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        if "DATE_TRUNC" in sql:
            return [{"bucket_start": "2026-09-28", "group_key": "APPROVE", "event_count": 4}]
        return [{"value": "APPROVE", "event_count": 2}]

    def execute(self, sql: str, params: dict[str, Any] | None = None) -> None:
        raise AssertionError("a ledger read must not write through the ledger client")


@pytest.fixture(autouse=True)
def _auditor_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """A deployed posture: no compat groups, one exact auditor, no other roles."""

    monkeypatch.setattr(settings, "trust_forwarded_headers", True)
    monkeypatch.setattr(settings, "app_env", "sandbox")
    monkeypatch.setattr(settings, "auditor_emails", AUDITOR)
    monkeypatch.setattr(settings, "auditor_identities", "")
    monkeypatch.setattr(settings, "admin_emails", ADMIN)
    monkeypatch.setattr(settings, "admin_identities", "")
    monkeypatch.setattr(settings, "approver_emails", "")
    monkeypatch.setattr(settings, "approver_identities", "")


@pytest.fixture
def audit_store() -> Iterator[InMemoryAuditStore]:
    prior = app.dependency_overrides.get(get_audit_store)
    store = InMemoryAuditStore()
    app.dependency_overrides[get_audit_store] = lambda: store
    try:
        yield store
    finally:
        if prior is None:
            app.dependency_overrides.pop(get_audit_store, None)
        else:
            app.dependency_overrides[get_audit_store] = prior


@pytest.fixture
def lakebase() -> Iterator[_LedgerLakebase]:
    prior = app.dependency_overrides.get(get_lakebase_client)
    fake = _LedgerLakebase()
    app.dependency_overrides[get_lakebase_client] = lambda: fake
    try:
        yield fake
    finally:
        if prior is None:
            app.dependency_overrides.pop(get_lakebase_client, None)
        else:
            app.dependency_overrides[get_lakebase_client] = prior


def _ledger_rows(store: InMemoryAuditStore) -> list[Any]:
    return [row for row in store.list(limit=1000) if row.event_type == "VIEW_AUDIT_LEDGER"]


def _seed_events(store: InMemoryAuditStore, count: int = 3) -> None:
    for index in range(count):
        store.write(
            actor="approver@summit-mortgage.example",
            action="view_leads",
            entity_type="lead_list",
            entity_id=f"queue-{index}",
            event_type="VIEW_LEADS",
        )


# ---------------------------------------------------------------------------
# Admission: the auditor reads the ledger and nothing else.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("surface", sorted(LEDGER_READS))
def test_auditor_gets_200_on_every_ledger_read(
    surface: str, audit_store: InMemoryAuditStore, lakebase: _LedgerLakebase
) -> None:
    response = client.get(LEDGER_READS[surface], headers=AUDITOR_HEADERS)

    assert response.status_code == 200, response.text


def test_auditor_page_read_writes_exactly_one_row(
    audit_store: InMemoryAuditStore, lakebase: _LedgerLakebase, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The deployed posture keys the stored fingerprint with the server secret
    # (W5c); without one the value is omitted, never stored plain.
    monkeypatch.setattr(
        settings, "mip_genie_action_secret_current", SecretStr("ledger-cursor-key-0123456789abcdef")
    )
    _seed_events(audit_store, 2)

    response = client.get(LEDGER_READS["events_page"], headers=AUDITOR_HEADERS)

    assert response.status_code == 200, response.text
    rows = _ledger_rows(audit_store)
    assert len(rows) == 1
    row = rows[0]
    assert row.actor == AUDITOR
    assert (row.action, row.entity_type, row.entity_id) == (
        "view_audit_ledger",
        "audit_ledger",
        "events_page",
    )
    assert row.payload_json["ledger_surface"] == "events_page"
    assert row.payload_json["has_cursor"] is False
    assert row.payload_json["returned_row_count"] == 2
    assert re.fullmatch(r"[0-9a-f]{64}", row.payload_json["filter_fingerprint"])
    assert set(row.payload_json) <= ROW_KEYS - {"read_audit_event_id"}


def test_the_stored_ledger_fingerprint_is_keyed_never_the_plain_digest(
    audit_store: InMemoryAuditStore, lakebase: _LedgerLakebase, monkeypatch: pytest.MonkeyPatch
) -> None:
    """W5c (12.3): a VIEW_AUDIT_LEDGER row stores the HMAC of the filter digest,
    so a dictionary of candidate actor filters never reproduces it."""

    monkeypatch.setattr(
        settings, "mip_genie_action_secret_current", SecretStr("ledger-fingerprint-key-0123456789ab")
    )
    _seed_events(audit_store, 1)
    actor_filter = "approver@summit-mortgage.example"

    response = client.get(LEDGER_READS["events"], params={"actor": actor_filter}, headers=AUDITOR_HEADERS)

    assert response.status_code == 200, response.text
    (row,) = _ledger_rows(audit_store)
    stored = row.payload_json["filter_fingerprint"]
    plain = audit_filter_fingerprint({"limit": DEFAULT_AUDIT_LIMIT, "offset": 0, "actor": actor_filter})
    assert re.fullmatch(r"[0-9a-f]{64}", stored)
    assert stored != plain
    assert stored == keyed_filter_fingerprint(plain, domain=AUDIT_LEDGER_FINGERPRINT_DOMAIN)


def _new_ledger_row(store: InMemoryAuditStore, seen: list[Any]) -> Any:
    """The one VIEW_AUDIT_LEDGER row written since ``seen`` was taken."""

    known = {row.event_id for row in seen}
    (row,) = [row for row in _ledger_rows(store) if row.event_id not in known]
    return row


def test_a_cursor_page_turn_is_recorded_with_has_cursor(
    audit_store: InMemoryAuditStore, lakebase: _LedgerLakebase, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first page records has_cursor False; the page turned with its
    next_cursor records has_cursor True (a page turn is itself accountable)."""

    # The deployed posture signs cursors with the configured server secret.
    monkeypatch.setattr(
        settings, "mip_genie_action_secret_current", SecretStr("ledger-cursor-key-0123456789abcdef")
    )
    _seed_events(audit_store, 3)

    first = client.get(LEDGER_READS["events_page"], params={"limit": 1}, headers=AUDITOR_HEADERS)
    assert first.status_code == 200, first.text
    next_cursor = first.json()["next_cursor"]
    assert next_cursor, "three seeded rows and limit=1 leave a next page"
    (first_row,) = _ledger_rows(audit_store)
    assert first_row.payload_json["ledger_surface"] == "events_page"
    assert first_row.payload_json["has_cursor"] is False
    assert first_row.payload_json["returned_row_count"] == 1

    seen = _ledger_rows(audit_store)
    second = client.get(
        LEDGER_READS["events_page"],
        params={"limit": 1, "cursor": next_cursor},
        headers=AUDITOR_HEADERS,
    )
    assert second.status_code == 200, second.text
    assert len(second.json()["items"]) == 1
    turned = _new_ledger_row(audit_store, seen)
    assert turned.payload_json["ledger_surface"] == "events_page"
    assert turned.payload_json["has_cursor"] is True
    assert turned.payload_json["returned_row_count"] == 1
    assert turned.payload_json["filter_fingerprint"] == first_row.payload_json["filter_fingerprint"]


def test_an_offset_events_read_is_recorded_with_has_cursor(
    audit_store: InMemoryAuditStore, lakebase: _LedgerLakebase
) -> None:
    """GET /events at offset 0 records has_cursor False; offset 1 records True."""

    _seed_events(audit_store, 3)

    assert client.get(
        LEDGER_READS["events"], params={"limit": 1, "offset": 0}, headers=AUDITOR_HEADERS
    ).status_code == 200
    (start_row,) = _ledger_rows(audit_store)
    assert start_row.payload_json["ledger_surface"] == "events"
    assert start_row.payload_json["has_cursor"] is False

    seen = _ledger_rows(audit_store)
    later = client.get(
        LEDGER_READS["events"], params={"limit": 1, "offset": 1}, headers=AUDITOR_HEADERS
    )
    assert later.status_code == 200, later.text
    offset_row = _new_ledger_row(audit_store, seen)
    assert offset_row.payload_json["ledger_surface"] == "events"
    assert offset_row.payload_json["has_cursor"] is True
    assert offset_row.payload_json["returned_row_count"] == 1


@pytest.mark.parametrize(
    "path",
    [
        ("PUT", "/api/v1/admin/rules"),
        ("GET", "/api/v1/admin/operations"),
        ("POST", "/api/v1/admin/operations/run"),
        ("POST", "/api/v1/admin/force-degraded"),
        ("GET", "/api/v1/admin/settings"),
        ("GET", "/api/v1/admin/assets/gold.lead_population/metadata"),
        ("POST", "/api/v1/audit/event"),
        ("POST", "/api/v1/audit/export-receipt"),
        ("POST", "/api/v1/outreach/approve"),
        ("POST", "/api/v1/outreach/reject"),
        ("GET", "/api/v1/leads?include_suppressed_for_analytics=true"),
        ("GET", "/api/v1/leads?marketing_eligibility=All"),
    ],
    ids=lambda value: f"{value[0]} {value[1]}",
)
def test_auditor_is_refused_every_admin_and_approver_power(
    path: tuple[str, str], audit_store: InMemoryAuditStore
) -> None:
    method, url = path
    response = client.request(method, url, json=_DECISION_BODIES.get(url, {}), headers=AUDITOR_HEADERS)

    assert response.status_code == 403, response.text
    assert response.json() == {"detail": "forbidden"}
    assert audit_store.list(limit=10) == []


def _dependency_calls(dependant: Any) -> set[Any]:
    calls: set[Any] = set()
    for sub in dependant.dependencies:
        if sub.call is not None:
            calls.add(sub.call)
        calls |= _dependency_calls(sub)
    return calls


def _gated_routes() -> list[tuple[str, str]]:
    gates = {rbac.require_admin, rbac.require_approver}
    found: list[tuple[str, str]] = []
    for route in app.routes:
        if not isinstance(route, APIRoute) or not (_dependency_calls(route.dependant) & gates):
            continue
        path = route.path_format
        for name, convertor in route.param_convertors.items():
            value = "1" if type(convertor).__name__ == "IntegerConvertor" else "sweep-1"
            path = path.replace("{" + name + "}", value)
        found.extend((method, path) for method in sorted(route.methods or ()) if method != "HEAD")
    return sorted(set(found))


# Non-vacuity floor: the dependency-gated admin and approver routes registered
# at W5b (28: the versioned and deprecated alias mounts both count). A drop
# means the sweep stopped finding the gates, not that the API lost them. Gates
# called inside a handler (outreach decisions, the /leads overrides) are not in
# a dependant tree; the explicit list above covers them.
_GATED_ROUTE_FLOOR = 28


def test_the_gated_route_sweep_is_not_vacuous() -> None:
    assert len(_gated_routes()) >= _GATED_ROUTE_FLOOR


@pytest.mark.parametrize("route", _gated_routes(), ids=lambda value: f"{value[0]} {value[1]}")
def test_auditor_is_refused_on_every_admin_or_approver_gated_route(
    route: tuple[str, str], audit_store: InMemoryAuditStore
) -> None:
    method, path = route
    # The activation store resolves before the approver gate; a stub keeps the
    # sweep off a live Lakebase so the gate is what answers.
    prior = app.dependency_overrides.get(get_activation_state_store)
    app.dependency_overrides[get_activation_state_store] = MagicMock
    try:
        response = client.request(
            method,
            path,
            json={},
            headers={**AUDITOR_HEADERS, "Content-Type": "application/json"},
        )
    finally:
        if prior is None:
            app.dependency_overrides.pop(get_activation_state_store, None)
        else:
            app.dependency_overrides[get_activation_state_store] = prior

    assert response.status_code == 403, f"{method} {path}: {response.status_code} {response.text}"
    assert audit_store.list(limit=10) == []


@pytest.mark.parametrize("surface", sorted(LEDGER_READS))
def test_an_unconfigured_identity_is_refused_every_ledger_read(
    surface: str, audit_store: InMemoryAuditStore, lakebase: _LedgerLakebase
) -> None:
    response = client.get(LEDGER_READS[surface], headers=PLAIN_HEADERS)

    assert response.status_code == 403
    assert response.json() == {"detail": "forbidden"}
    assert audit_store.list(limit=10) == []


def test_a_deployed_auditor_group_header_grants_nothing(
    monkeypatch: pytest.MonkeyPatch, audit_store: InMemoryAuditStore, lakebase: _LedgerLakebase
) -> None:
    monkeypatch.setattr(settings, "app_env", "production")
    headers = {"X-Forwarded-Email": PLAIN, "X-Forwarded-Groups": "mip-auditor"}

    assert client.get(LEDGER_READS["events_page"], headers=headers).status_code == 403
    session = client.get("/api/v1/session", headers=headers).json()
    assert session["can_read_audit"] is False
    assert "Auditor" not in session["role_labels"]

    monkeypatch.setattr(settings, "app_env", "test")
    assert client.get(LEDGER_READS["events_page"], headers=headers).status_code == 200
    session = client.get("/api/v1/session", headers=headers).json()
    assert session["can_read_audit"] is True
    assert session["role_labels"] == ["Auditor"]


def test_empty_auditor_lists_admit_nobody(
    monkeypatch: pytest.MonkeyPatch, audit_store: InMemoryAuditStore, lakebase: _LedgerLakebase
) -> None:
    monkeypatch.setattr(settings, "auditor_emails", "")

    assert client.get(LEDGER_READS["events_page"], headers=AUDITOR_HEADERS).status_code == 403


def test_auditor_identities_admit_an_exact_automation_identity(
    monkeypatch: pytest.MonkeyPatch, audit_store: InMemoryAuditStore, lakebase: _LedgerLakebase
) -> None:
    monkeypatch.setattr(settings, "auditor_emails", "")
    monkeypatch.setattr(settings, "auditor_identities", "Audit-Client-01")

    headers = {"X-Forwarded-User": "audit-client-01", "X-Forwarded-Groups": ""}
    assert client.get(LEDGER_READS["events_page"], headers=headers).status_code == 200


# ---------------------------------------------------------------------------
# Session: one decision for the UI and for enforcement.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("email", "extra_auditor", "labels", "can_read"),
    [
        (ADMIN, False, ["Administrator", "Approver"], True),
        (AUDITOR, False, ["Auditor"], True),
        (ADMIN, True, ["Administrator", "Approver", "Auditor"], True),
        (PLAIN, False, ["Workspace user"], False),
    ],
)
def test_session_labels_and_can_read_audit_match_the_ledger_gate(
    email: str,
    extra_auditor: bool,
    labels: list[str],
    can_read: bool,
    monkeypatch: pytest.MonkeyPatch,
    audit_store: InMemoryAuditStore,
    lakebase: _LedgerLakebase,
) -> None:
    if extra_auditor:
        monkeypatch.setattr(settings, "auditor_emails", f"{AUDITOR},{ADMIN}")
    headers = {"X-Forwarded-Email": email, "X-Forwarded-Groups": ""}

    body = client.get("/api/v1/session", headers=headers).json()
    gate = client.get(LEDGER_READS["count"], headers=headers)

    assert body["role_labels"] == labels
    assert body["can_read_audit"] is can_read
    assert (gate.status_code == 200) is body["can_read_audit"], "one decision for UI and enforcement"
    assert body["can_access_admin"] is (email == ADMIN)
    assert body["can_approve"] is (email == ADMIN)


def test_auditor_role_overlap_count_counts_only_and_ignores_case(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "auditor_emails", f"{AUDITOR},Ops@Summit-Mortgage.example,x@y.example")
    monkeypatch.setattr(settings, "auditor_identities", "approver-bot")
    monkeypatch.setattr(settings, "approver_identities", "APPROVER-BOT")

    assert rbac.auditor_role_overlap_count() == 2
    monkeypatch.setattr(settings, "auditor_emails", "")
    monkeypatch.setattr(settings, "auditor_identities", "")
    assert rbac.auditor_role_overlap_count() == 0


# ---------------------------------------------------------------------------
# Accountability: one VIEW_AUDIT_LEDGER row per served read.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("surface", sorted(LEDGER_READS))
def test_each_ledger_surface_writes_exactly_one_row_of_its_surface(
    surface: str, audit_store: InMemoryAuditStore, lakebase: _LedgerLakebase
) -> None:
    response = client.get(LEDGER_READS[surface], headers=AUDITOR_HEADERS)

    assert response.status_code == 200, response.text
    rows = _ledger_rows(audit_store)
    assert len(rows) == 1
    payload = rows[0].payload_json
    assert payload["ledger_surface"] == surface
    assert payload["has_cursor"] is False
    assert isinstance(payload["returned_row_count"], int)
    assert set(payload) <= ROW_KEYS - {"read_audit_event_id"}
    assert AUDITOR not in str(payload), "no identity rides on the row's metadata"


def test_an_actor_filter_never_lands_in_clear(
    audit_store: InMemoryAuditStore, lakebase: _LedgerLakebase
) -> None:
    response = client.get(
        LEDGER_READS["events_page"],
        params={"actor": "approver@summit-mortgage.example"},
        headers=AUDITOR_HEADERS,
    )

    assert response.status_code == 200, response.text
    (row,) = _ledger_rows(audit_store)
    assert "approver@summit-mortgage.example" not in str(row.payload_json)


def test_refused_and_invalid_reads_write_nothing(
    audit_store: InMemoryAuditStore, lakebase: _LedgerLakebase
) -> None:
    assert client.get(LEDGER_READS["events_page"], headers=PLAIN_HEADERS).status_code == 403
    assert (
        client.get(
            LEDGER_READS["events_page"], params={"borrower_id": "nope"}, headers=AUDITOR_HEADERS
        ).status_code
        == 422
    )

    assert _ledger_rows(audit_store) == []


def test_my_events_stays_audit_free(audit_store: InMemoryAuditStore) -> None:
    response = client.get("/api/v1/audit/my-events", headers=AUDITOR_HEADERS)

    assert response.status_code == 200, response.text
    assert audit_store.list(limit=10) == []


class _RaisingWriteStore(InMemoryAuditStore):
    def write(self, **kwargs: Any) -> Any:  # type: ignore[override]
        raise RuntimeError("lakebase down: ops@summit-mortgage.example")


@pytest.mark.parametrize("surface", sorted(LEDGER_READS))
def test_a_failing_ledger_write_never_fails_the_read(
    surface: str, monkeypatch: pytest.MonkeyPatch, lakebase: _LedgerLakebase
) -> None:
    emitted: list[tuple[str, dict[str, Any]]] = []

    def _record(_log: Any, event: str, **fields: Any) -> None:
        emitted.append((event, fields))

    monkeypatch.setattr(audit_ledger_reads, "emit", _record)
    store = _RaisingWriteStore()
    prior = app.dependency_overrides.get(get_audit_store)
    app.dependency_overrides[get_audit_store] = lambda: store
    try:
        response = client.get(LEDGER_READS[surface], headers=AUDITOR_HEADERS)
    finally:
        if prior is None:
            app.dependency_overrides.pop(get_audit_store, None)
        else:
            app.dependency_overrides[get_audit_store] = prior

    assert response.status_code == 200, response.text
    dropped = [fields for event, fields in emitted if event == "audit.dropped"]
    assert dropped == [
        {
            "dependency": "lakebase",
            "exc_type": "RuntimeError",
            "event_type": "VIEW_AUDIT_LEDGER",
            "outcome": "error",
        }
    ]


# ---------------------------------------------------------------------------
# The metadata value policy for the new keys.
# ---------------------------------------------------------------------------


def _metadata(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ledger_surface": "events_page",
        "has_cursor": False,
        "returned_row_count": 3,
        "filter_fingerprint": "a" * 64,
        **overrides,
    }
    return build_safe_audit_metadata(payload, action="view_audit_ledger")


def test_the_reviewed_shape_passes_the_value_policy() -> None:
    assert _metadata(read_audit_event_id="0f8fad5b-d9cb-469f-a165-70867728950e")["has_cursor"] is False
    for surface in LEDGER_SURFACES:
        assert _metadata(ledger_surface=surface)["ledger_surface"] == surface


@pytest.mark.parametrize(
    "overrides",
    [
        {"ledger_surface": "route"},
        {"has_cursor": "true"},
        {"returned_row_count": -1},
        {"returned_row_count": True},
        {"read_audit_event_id": "alice@x"},
        {"filter_fingerprint": "not-a-digest"},
    ],
    ids=lambda value: next(iter(value)),
)
def test_the_value_policy_refuses_an_unreviewed_value(overrides: dict[str, Any]) -> None:
    with pytest.raises(AuditMetadataValueViolation):
        _metadata(**overrides)


def test_record_ledger_read_refuses_an_unknown_surface() -> None:
    with pytest.raises(ValueError, match="unknown ledger surface"):
        audit_ledger_reads.record_ledger_read(
            None,  # type: ignore[arg-type]
            InMemoryAuditStore(),
            actor=AUDITOR,
            surface="route",
            filter_fingerprint=None,
            has_cursor=False,
            returned_row_count=0,
        )

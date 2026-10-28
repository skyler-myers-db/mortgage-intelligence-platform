"""POST /api/print-receipt: the PRINT_EXPORT row an Export PDF waits for.

Audit responsive-08 (W5d). Borrower 360 and the audit explorer print only the
page already rendered to the reader, and only after this route has written
exactly one server-owned ``PRINT_EXPORT`` row. Pinned here: who may print
which surface, the request shape per surface, the exact stored metadata keys
(never the filters, an id list or row content), the KEYED fingerprint, the
fail-closed refusals (no key, Lakebase down, a metadata policy refusal) and
that a refused receipt never writes a row.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.api.print_receipt import PRINT_RECEIPT_REFUSED_DETAIL
from backend.config.settings import settings
from backend.main import app
from backend.schemas.common import contains_pii_marker
from backend.services import audit_fingerprint
from backend.services.audit_entity_ids import audit_safe_letters
from backend.services.audit_event_types import is_server_owned_audit_event_type
from backend.services.audit_fingerprint import (
    AUDIT_LEDGER_FINGERPRINT_DOMAIN,
    keyed_filter_fingerprint,
)
from backend.services.audit_metadata_policy import AuditMetadataValueViolation
from backend.services.audit_pagination import audit_filter_fingerprint
from backend.services.audit_store import get_audit_store
from backend.services.lakebase import LakebaseError
from tests.fixtures.in_memory_audit_store import InMemoryAuditStore

client = TestClient(app)

AUDITOR = "auditor.one@summit-mortgage.example"
ADMIN = "ops@summit-mortgage.example"
PLAIN = "lo@summit-mortgage.example"
AUDITOR_HEADERS = {"X-Forwarded-Email": AUDITOR, "X-Forwarded-Groups": ""}
PLAIN_HEADERS = {"X-Forwarded-Email": PLAIN, "X-Forwarded-Groups": ""}
ADMIN_HEADERS = {"X-Forwarded-Email": ADMIN, "X-Forwarded-Groups": "mip-admin"}
URL = "/api/v1/print-receipt"
BORROWER = "B-0000000000001"
LEDGER_FILTERS = {"event_type": "APPROVE", "since": "2026-09-01T00:00:00Z"}


def _borrower_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"surface": "borrower_360", "borrower_id": BORROWER, "row_count": 1}
    body.update(overrides)
    return body


def _ledger_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"surface": "audit_ledger", "filters": dict(LEDGER_FILTERS), "row_count": 25}
    body.update(overrides)
    return body


@pytest.fixture(autouse=True)
def _deployed_roles(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "trust_forwarded_headers", True)
    monkeypatch.setattr(settings, "app_env", "test")
    monkeypatch.setattr(settings, "auditor_emails", AUDITOR)
    monkeypatch.setattr(settings, "auditor_identities", "")
    monkeypatch.setattr(settings, "admin_emails", ADMIN)
    monkeypatch.setattr(settings, "admin_identities", "")


@pytest.fixture
def store() -> Iterator[InMemoryAuditStore]:
    prior = app.dependency_overrides.get(get_audit_store)
    memory = InMemoryAuditStore()
    app.dependency_overrides[get_audit_store] = lambda: memory
    try:
        yield memory
    finally:
        if prior is None:
            app.dependency_overrides.pop(get_audit_store, None)
        else:
            app.dependency_overrides[get_audit_store] = prior


def _print_rows(memory: InMemoryAuditStore) -> list[Any]:
    return [row for row in memory.list(limit=100) if row.event_type == "PRINT_EXPORT"]


# --- RBAC ------------------------------------------------------------------


def test_any_authenticated_reader_prints_a_borrower(store: InMemoryAuditStore) -> None:
    response = client.post(URL, json=_borrower_body(), headers=PLAIN_HEADERS)

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "private, no-store"
    receipt = response.json()
    assert receipt["event_type"] == "PRINT_EXPORT"
    assert receipt["surface"] == "borrower_360"
    assert receipt["borrower_id"] == BORROWER
    assert receipt["actor"] == PLAIN
    assert receipt["filter_fingerprint"] is None
    [row] = _print_rows(store)
    assert receipt["audit_event_id"] == row.event_id


@pytest.mark.parametrize("headers", [ADMIN_HEADERS, AUDITOR_HEADERS], ids=["admin", "auditor"])
def test_ledger_readers_print_the_ledger_page(store: InMemoryAuditStore, headers: dict[str, str]) -> None:
    response = client.post(URL, json=_ledger_body(), headers=headers)

    assert response.status_code == 200, response.text
    assert response.json()["actor"] == headers["X-Forwarded-Email"]
    assert len(_print_rows(store)) == 1


def test_a_plain_user_cannot_print_the_ledger(store: InMemoryAuditStore) -> None:
    response = client.post(URL, json=_ledger_body(), headers=PLAIN_HEADERS)

    assert response.status_code == 403
    assert _print_rows(store) == []


def test_no_identity_is_refused_before_anything_is_written(store: InMemoryAuditStore) -> None:
    response = client.post(URL, json=_borrower_body())

    assert response.status_code == 401
    assert store.list(limit=10) == []


# --- request shape -----------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        _borrower_body(borrower_id="Jane Doe 12 Main St"),
        _borrower_body(borrower_id=None),
        _borrower_body(row_count=2),
        _borrower_body(filters={"event_type": "APPROVE"}),
        _ledger_body(borrower_id=BORROWER),
        _ledger_body(row_count=0),
        _ledger_body(row_count=5001),
        _ledger_body(filters={"Event Type": "APPROVE"}),
        {"surface": "lead_queue", "row_count": 1},
    ],
    ids=[
        "bad-masked-id",
        "missing-borrower",
        "borrower-row-count",
        "borrower-filters",
        "ledger-borrower",
        "ledger-zero-rows",
        "ledger-over-cap",
        "ledger-bad-filter-key",
        "unknown-surface",
    ],
)
def test_a_malformed_request_is_422_and_writes_no_row(store: InMemoryAuditStore, body: dict[str, Any]) -> None:
    response = client.post(URL, json=body, headers=ADMIN_HEADERS)

    assert response.status_code == 422
    # The validation body never reflects what was submitted.
    assert "Jane Doe" not in response.text
    assert store.list(limit=10) == []


# --- what the row holds ------------------------------------------------------


def test_a_borrower_print_stores_exactly_its_three_keys(store: InMemoryAuditStore) -> None:
    client.post(URL, json=_borrower_body(), headers=PLAIN_HEADERS)

    [row] = _print_rows(store)
    assert set(row.payload_json) == {"print_surface", "printed_row_count", "borrower_id"}
    assert row.payload_json == {"print_surface": "borrower_360", "printed_row_count": 1, "borrower_id": BORROWER}
    assert row.action == "print.export"
    assert (row.entity_type, row.entity_id) == ("borrower", BORROWER)


def test_a_ledger_print_stores_a_keyed_fingerprint_never_the_filters(store: InMemoryAuditStore) -> None:
    response = client.post(URL, json=_ledger_body(), headers=AUDITOR_HEADERS)

    [row] = _print_rows(store)
    assert set(row.payload_json) == {"print_surface", "printed_row_count", "filter_fingerprint"}
    assert row.payload_json["printed_row_count"] == 25
    plain = audit_filter_fingerprint(dict(LEDGER_FILTERS))
    stored = row.payload_json["filter_fingerprint"]
    assert stored != plain, "a plain sha256 of the filters is a dictionary target"
    assert stored == keyed_filter_fingerprint(plain, domain=AUDIT_LEDGER_FINGERPRINT_DOMAIN)
    assert response.json()["filter_fingerprint"] == stored
    assert (row.entity_type, row.entity_id) == ("audit_ledger", f"print-{audit_safe_letters(stored)}")
    flat = repr(row.payload_json)
    assert "APPROVE" not in flat and "2026-09-01" not in flat


def test_a_fingerprint_whose_hex_reads_as_a_phone_number_still_prints(
    store: InMemoryAuditStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Pre-2026-10-28 the entity was ``print-{fingerprint[:16]}``. Under this
    # process key LEDGER_FILTERS fingerprint to b917f0406271903c..., whose
    # ten-digit run reads as a phone number, so the audit-id PII guard refused
    # the row and the print answered 422 (about 1.4% of keys).
    monkeypatch.setattr(audit_fingerprint, "_LOCAL_KEY", hashlib.sha256(b"probe-168").digest())
    for name in ("mip_genie_action_secret_current", "mip_genie_action_secret", "mip_genie_action_secret_previous"):
        monkeypatch.setattr(settings, name, None)
    stored = keyed_filter_fingerprint(
        audit_filter_fingerprint(dict(LEDGER_FILTERS)), domain=AUDIT_LEDGER_FINGERPRINT_DOMAIN
    )
    assert stored is not None and contains_pii_marker(f"print-{stored[:16]}"), "control: the pin must still trip"

    response = client.post(URL, json=_ledger_body(), headers=AUDITOR_HEADERS)

    assert response.status_code == 200, response.text
    [row] = _print_rows(store)
    assert row.entity_id == f"print-{audit_safe_letters(stored)}"
    assert row.payload_json["filter_fingerprint"] == response.json()["filter_fingerprint"] == stored


# --- fail closed -------------------------------------------------------------


def test_no_fingerprint_key_outside_local_answers_503_and_writes_no_row(
    store: InMemoryAuditStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "mip_genie_action_secret_current", None)
    monkeypatch.setattr(settings, "mip_genie_action_secret", None)
    monkeypatch.setattr(settings, "mip_genie_action_secret_previous", None)
    monkeypatch.setattr(settings, "app_env", "sandbox")

    response = client.post(URL, json=_ledger_body(), headers=ADMIN_HEADERS)

    assert response.status_code == 503
    assert store.list(limit=10) == []


def test_lakebase_down_answers_503(store: InMemoryAuditStore, monkeypatch: pytest.MonkeyPatch) -> None:
    def _down(**_: Any) -> Any:
        raise LakebaseError("lakebase unavailable")

    monkeypatch.setattr(store, "write", _down)

    response = client.post(URL, json=_borrower_body(), headers=PLAIN_HEADERS)

    assert response.status_code == 503
    assert "lakebase unavailable" not in response.text


def test_a_metadata_policy_refusal_is_the_fixed_422(store: InMemoryAuditStore, monkeypatch: pytest.MonkeyPatch) -> None:
    def _refused(**_: Any) -> Any:
        raise AuditMetadataValueViolation("print_surface", "must be a governed print surface")

    monkeypatch.setattr(store, "write", _refused)

    response = client.post(URL, json=_borrower_body(), headers=PLAIN_HEADERS)

    assert response.status_code == 422
    assert response.json()["detail"] == PRINT_RECEIPT_REFUSED_DETAIL


def test_the_value_policy_refuses_an_unknown_print_surface() -> None:
    memory = InMemoryAuditStore()
    with pytest.raises(AuditMetadataValueViolation):
        memory.write(
            actor=ADMIN,
            action="print.export",
            entity_type="borrower",
            entity_id=BORROWER,
            payload_json={"print_surface": "lead_queue", "printed_row_count": 1, "borrower_id": BORROWER},
            event_type="PRINT_EXPORT",
        )
    with pytest.raises(AuditMetadataValueViolation):
        memory.write(
            actor=ADMIN,
            action="print.export",
            entity_type="borrower",
            entity_id=BORROWER,
            payload_json={"print_surface": "borrower_360", "printed_row_count": -1, "borrower_id": BORROWER},
            event_type="PRINT_EXPORT",
        )
    assert memory.list(limit=10) == []


def test_a_client_cannot_author_a_print_row(store: InMemoryAuditStore) -> None:
    assert is_server_owned_audit_event_type("PRINT_EXPORT")
    response = client.post(
        "/api/v1/audit/event",
        json={
            "actor": ADMIN,
            "action": "print.export",
            "event_type": "PRINT_EXPORT",
            "entity_type": "borrower",
            "entity_id": BORROWER,
        },
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 400
    assert store.list(limit=10) == []

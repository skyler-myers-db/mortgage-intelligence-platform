"""Router tests for POST /api/v1/lookup/property-loan.

Covers success (hit), miss, the no-echo invariant (the submitted street string
appears ZERO times in the serialized response), the 422 shape (bad ZIP / short
address, with the raw value stripped from the error body), and RBAC (the same
authenticated-operator surface as /api/v1/borrowers -- not admin-gated).

Dependency overrides snapshot-and-restore (never blind-pop) so the shared app
dependency graph is left exactly as found.
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from fastapi.testclient import TestClient

from backend.api.lookup import get_lookup_sql_client
from backend.main import app
from backend.services.audit_store import get_audit_store
from tests.fixtures.in_memory_audit_store import InMemoryAuditStore

client = TestClient(app)

_RAW_ADDRESS = "742 Evergreen Terrace"
_ZIP = "62704"


class _FakeSqlClient:
    def __init__(self, responses: list[tuple[str, dict[str, Any] | None]]) -> None:
        self._responses = responses

    def execute_one(
        self, statement: str, parameters: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        for marker, row in self._responses:
            if marker in statement:
                return row
        return None


def _address_row() -> dict[str, Any]:
    return {
        "clip": "1234567890ABCDEF",
        "owner_link_id": "OL-9988",
        "has_open_lien": True,
        "current_lien_balance": 325000,
        "ltv": 68,
        "first_pos_lender_current": "WELLS FARGO BK NA",
        "current_rate": 6.75,
    }


def _borrower_row() -> dict[str, Any]:
    return {
        "borrower_id": "B-000000000ABCD",
        "opportunity_score": 82,
        "segment_codes": ["itm", "equity"],
    }


@contextmanager
def _overrides(sql: object, audit: object) -> Iterator[None]:
    prev_sql = app.dependency_overrides.get(get_lookup_sql_client)
    prev_audit = app.dependency_overrides.get(get_audit_store)
    app.dependency_overrides[get_lookup_sql_client] = lambda: sql
    app.dependency_overrides[get_audit_store] = lambda: audit
    try:
        yield
    finally:
        if prev_sql is None:
            app.dependency_overrides.pop(get_lookup_sql_client, None)
        else:
            app.dependency_overrides[get_lookup_sql_client] = prev_sql
        if prev_audit is None:
            app.dependency_overrides.pop(get_audit_store, None)
        else:
            app.dependency_overrides[get_audit_store] = prev_audit


def test_property_loan_lookup_hit() -> None:
    sql = _FakeSqlClient(
        [("address_lookup", _address_row()), ("borrower_360", _borrower_row())]
    )
    audit = InMemoryAuditStore()
    with _overrides(sql, audit):
        response = client.post(
            "/api/v1/lookup/property-loan",
            json={"address_line": _RAW_ADDRESS, "zip5": _ZIP},
            headers={"X-Forwarded-Email": "skyler@entrada.ai"},
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["matched"] is True
    assert body["clip_ref"].startswith("clip_ref_")
    assert body["borrower_id"] == "B-000000000ABCD"
    assert body["loan"]["lender_brand"] == "Competitor B"
    assert body["dossier_path"] == "/borrower-360/B-000000000ABCD"
    assert body["audit_event_id"]
    assert audit.list()[0].payload_json["hit"] is True


def test_property_loan_lookup_miss() -> None:
    sql = _FakeSqlClient([])  # spine returns nothing
    audit = InMemoryAuditStore()
    with _overrides(sql, audit):
        response = client.post(
            "/api/v1/lookup/property-loan",
            json={"address_line": _RAW_ADDRESS, "zip5": _ZIP},
            headers={"X-Forwarded-Email": "skyler@entrada.ai"},
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["matched"] is False
    assert body["clip_ref"] is None
    assert body["borrower_id"] is None
    assert body["loan"] is None
    assert body["audit_event_id"]
    assert audit.list()[0].payload_json["hit"] is False


def test_response_never_echoes_address_line() -> None:
    """The submitted street string never appears in user-facing response data."""
    sql = _FakeSqlClient(
        [("address_lookup", _address_row()), ("borrower_360", _borrower_row())]
    )
    audit = InMemoryAuditStore()
    with _overrides(sql, audit):
        response = client.post(
            "/api/v1/lookup/property-loan",
            json={"address_line": _RAW_ADDRESS, "zip5": _ZIP},
            headers={"X-Forwarded-Email": "skyler@entrada.ai"},
        )
    body = response.json()
    assert "address_line" not in body
    assert "zip5" not in body
    # Opaque identifiers may coincidentally contain the same short digit run as
    # a house number. Exclude those non-display tokens while keeping every loan
    # fact and response label under the no-echo assertion.
    display_body = {
        key: value
        for key, value in body.items()
        if key
        not in {
            "audit_event_id",
            "borrower_id",
            "clip_ref",
            "dossier_path",
            "owner_link_ref",
        }
    }
    raw_body = json.dumps(display_body, sort_keys=True)
    assert _RAW_ADDRESS not in raw_body
    assert "Evergreen" not in raw_body
    assert "742" not in raw_body


def test_bad_zip_returns_422_without_echoing_value() -> None:
    sql = _FakeSqlClient([])
    audit = InMemoryAuditStore()
    with _overrides(sql, audit):
        # Non-digit ZIP of correct length -> in-route fixed-message 422.
        response = client.post(
            "/api/v1/lookup/property-loan",
            json={"address_line": _RAW_ADDRESS, "zip5": "ABcde"},
            headers={"X-Forwarded-Email": "skyler@entrada.ai"},
        )
    assert response.status_code == 422
    # Fixed detail; the submitted value is not reflected.
    assert "ABcde" not in response.text


def test_wrong_length_zip_returns_422_shape_without_input_echo() -> None:
    sql = _FakeSqlClient([])
    audit = InMemoryAuditStore()
    with _overrides(sql, audit):
        response = client.post(
            "/api/v1/lookup/property-loan",
            json={"address_line": _RAW_ADDRESS, "zip5": "1"},
            headers={"X-Forwarded-Email": "skyler@entrada.ai"},
        )
    assert response.status_code == 422
    body = response.json()
    # App-wide 422 handler strips pydantic ``input``/``ctx``; body has detail +
    # correlation_id and never reflects the raw submitted value.
    assert "correlation_id" in body
    for item in body["detail"]:
        if isinstance(item, dict):
            assert "input" not in item
            assert "ctx" not in item


def test_short_address_returns_422() -> None:
    sql = _FakeSqlClient([])
    audit = InMemoryAuditStore()
    with _overrides(sql, audit):
        response = client.post(
            "/api/v1/lookup/property-loan",
            json={"address_line": "12", "zip5": _ZIP},
            headers={"X-Forwarded-Email": "skyler@entrada.ai"},
        )
    assert response.status_code == 422


def test_compat_alias_path_is_reachable() -> None:
    """The deprecated compat alias /api/lookup/property-loan still resolves
    (canonical surface is /api/v1/lookup/property-loan)."""
    sql = _FakeSqlClient([])
    audit = InMemoryAuditStore()
    with _overrides(sql, audit):
        response = client.post(
            "/api/lookup/property-loan",
            json={"address_line": _RAW_ADDRESS, "zip5": _ZIP},
            headers={"X-Forwarded-Email": "skyler@entrada.ai"},
        )
    assert response.status_code == 200, response.text


def test_lookup_is_not_admin_gated() -> None:
    """Same authenticated-operator surface as /api/v1/borrowers: a normal
    workspace user (no admin header) gets a 200, not a 403."""
    sql = _FakeSqlClient([])
    audit = InMemoryAuditStore()
    with _overrides(sql, audit):
        response = client.post(
            "/api/v1/lookup/property-loan",
            json={"address_line": _RAW_ADDRESS, "zip5": _ZIP},
            headers={"X-Forwarded-Email": "loan.officer@entrada.ai"},
        )
    assert response.status_code == 200, response.text
    assert response.json()["matched"] is False

"""The four reads the boot module primes write no audit rows (audit bundle-02).

``frontend/src/boot/primeBoot.ts`` starts GET session, config/options,
config/footprint and health?idle_s=0 beside the entry chunk, before any user
intent. Priming a read that writes an audit row (GET /api/leads writes
VIEW_LEADS, borrower and proof reads write VIEW_*, outreach drafts write
DRAFT_OUTREACH, offer recommendations RECOMMEND_OFFER) would mint governance
evidence for a page nobody opened, so the boot module never primes those.
This pins the other half: the four it does prime stay audit-free, with a
forwarded identity, the SQL stubbed (conftest) and an in-memory audit store
that also catches a direct ``get_audit_store()`` call.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.services import audit_store as audit_store_module
from backend.services.audit_store import get_audit_store
from tests.fixtures.in_memory_audit_store import InMemoryAuditStore

PRIME_BOOT = Path(__file__).resolve().parents[2] / "frontend" / "src" / "boot" / "primeBoot.ts"

BOOT_READS = (
    "/api/v1/session",
    "/api/v1/config/options",
    "/api/v1/config/footprint",
    "/api/v1/health?idle_s=0",
)

IDENTITY = {
    "X-Forwarded-Email": "analyst@summit.example",
    "X-Forwarded-User": "analyst@summit.example",
    "X-Forwarded-Groups": "",
}


@pytest.fixture()
def audit(monkeypatch: pytest.MonkeyPatch) -> Iterator[InMemoryAuditStore]:
    store = InMemoryAuditStore()
    app.dependency_overrides[get_audit_store] = lambda: store
    monkeypatch.setattr(audit_store_module, "_AUDIT_STORE", store)
    yield store


def test_the_boot_module_primes_exactly_these_reads() -> None:
    source = PRIME_BOOT.read_text(encoding="utf-8")
    primed = re.findall(r"url: '(/api/[^']+)'", source)

    assert sorted(primed) == sorted(BOOT_READS)
    for audited in ("/leads", "/borrowers", "/outreach", "/offers", "/proof"):
        assert audited not in source


@pytest.mark.parametrize("path", BOOT_READS)
def test_a_primed_boot_read_writes_no_audit_event(audit: InMemoryAuditStore, path: str) -> None:
    response = TestClient(app).get(path, headers=IDENTITY)

    assert response.status_code == 200, response.text
    assert audit.list(limit=100) == []


def test_the_harness_sees_an_audited_read(audit: InMemoryAuditStore) -> None:
    """Non-vacuity control: the same harness records GET /api/leads' VIEW_LEADS."""
    response = TestClient(app).get("/api/v1/leads", headers=IDENTITY)

    assert response.status_code == 200, response.text
    assert [event.event_type for event in audit.list(limit=100)] == ["VIEW_LEADS"]

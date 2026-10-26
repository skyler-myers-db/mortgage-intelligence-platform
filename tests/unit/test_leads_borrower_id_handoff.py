"""A 50-id Lead Queue hand-off is one audited VIEW_LEADS page (W5e, item 12).

The Lead Queue parses at most ``MAX_QUEUE_BORROWER_IDS`` (50) masked ids from
``?borrower_ids=`` (frontend/src/lib/queueBorrowerIds.ts), and the Follow-ups
tray hands the queue at most that many. Driven through the REAL route and the
REAL Databricks repository over the in-memory SQLite gold copy, with the
in-memory audit store running the real metadata validation: the hand-off reads
one page, writes exactly one VIEW_LEADS row, and that row's ``borrower_ids``
list (the filter the actor named) holds all 50 ids. ``audit_metadata_validation``
is not edited; the server parse (``lead_query_helpers.parse_borrower_ids``)
has no count cap of its own.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from backend.config.settings import settings
from backend.main import app
from backend.services.audit_store import get_audit_store
from backend.services.repositories import get_lead_repository
from backend.services.repositories.databricks_leads import DatabricksLeadRepository
from tests.fixtures.in_memory_audit_store import InMemoryAuditStore
from tests.fixtures.sqlite_lead_warehouse import SqliteLeadWarehouse, gold_lead_row, lead_id

HEADERS = {"X-Forwarded-Email": "lo.one@summit-mortgage.example", "X-Forwarded-Groups": ""}
QUEUE_HANDOFF_IDS = 50
client = TestClient(app)


@pytest.fixture(autouse=True)
def _keyed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "mip_genie_action_secret_current", SecretStr("test-secret-0123456789abcdef"))
    monkeypatch.setattr(settings, "mip_genie_action_secret_previous", None)


@pytest.fixture
def audit() -> Iterator[InMemoryAuditStore]:
    store = InMemoryAuditStore()
    prior_audit = app.dependency_overrides.get(get_audit_store)
    prior_repo = app.dependency_overrides.get(get_lead_repository)
    warehouse = SqliteLeadWarehouse([gold_lead_row(index) for index in range(80)])
    repo = DatabricksLeadRepository(warehouse, cache_ttl_s=0.0)  # type: ignore[arg-type]
    app.dependency_overrides[get_audit_store] = lambda: store
    app.dependency_overrides[get_lead_repository] = lambda: repo
    yield store
    for dep, prior in ((get_audit_store, prior_audit), (get_lead_repository, prior_repo)):
        if prior is None:
            app.dependency_overrides.pop(dep, None)
        else:
            app.dependency_overrides[dep] = prior


def _view_rows(audit: InMemoryAuditStore) -> list[dict[str, Any]]:
    return [dict(event.payload_json or {}) for event in audit.list(limit=100) if event.event_type == "VIEW_LEADS"]


def test_a_fifty_id_handoff_reads_one_page_with_one_view_leads_row_naming_all_fifty(
    audit: InMemoryAuditStore,
) -> None:
    ids = [lead_id(index) for index in range(QUEUE_HANDOFF_IDS)]

    response = client.get("/api/leads", params={"borrower_ids": ",".join(ids)}, headers=HEADERS)

    assert response.status_code == 200, response.text
    assert sorted(lead["borrower_id"] for lead in response.json()) == sorted(ids)
    (row,) = _view_rows(audit)
    assert row["borrower_ids"] == ids
    assert len(row["borrower_ids"]) == QUEUE_HANDOFF_IDS
    assert sorted(row["rendered_borrower_ids"]) == sorted(ids)

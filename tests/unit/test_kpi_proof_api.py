"""GET /api/kpi-proof: reproduce SQL for any authenticated role, audit-free (audit flow-06).

Decision D-audit-reads-c1's flow-06 ruling: server-emitted, parameter-bound,
gold-only reproduce SQL for a KPI may be shown to every authenticated user.
The route executes no SQL, writes no audit row, answers 422 for a key outside
the closed vocabulary and 401 without a forwarded identity.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.config.settings import settings
from backend.main import app
from backend.services import audit_store as audit_store_module
from backend.services import databricks_sql, kpi_proof_sql
from backend.services.audit_store import get_audit_store
from backend.services.kpi_proof_sql import KpiStatement
from tests.fixtures.in_memory_audit_store import InMemoryAuditStore

LOAN_OFFICER = {"X-Forwarded-Email": "lo.alpha@summit.example", "X-Forwarded-Groups": ""}
AUDITOR = {"X-Forwarded-Email": "auditor@summit.example", "X-Forwarded-Groups": ""}
ADMIN = {"X-Forwarded-Email": "admin@summit.example"}


@pytest.fixture()
def audit(monkeypatch: pytest.MonkeyPatch) -> Iterator[InMemoryAuditStore]:
    store = InMemoryAuditStore()
    app.dependency_overrides[get_audit_store] = lambda: store
    monkeypatch.setattr(audit_store_module, "_AUDIT_STORE", store)
    yield store


@pytest.fixture()
def no_sql(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("the KPI proof route must execute no SQL")

    monkeypatch.setattr(databricks_sql, "get_sql_client", refuse)


@pytest.mark.parametrize("headers", [LOAN_OFFICER, AUDITOR, ADMIN], ids=["loan-officer", "auditor", "admin"])
def test_any_authenticated_role_reads_the_proof(
    monkeypatch: pytest.MonkeyPatch,
    audit: InMemoryAuditStore,
    no_sql: None,
    headers: dict[str, str],
) -> None:
    monkeypatch.setattr(settings, "auditor_emails", "auditor@summit.example")

    response = TestClient(app).get("/api/v1/kpi-proof?kpi=home.in_the_money", headers=headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["kpi"] == "home.in_the_money"
    assert body["measure_column"] == "high_intent_leads"
    assert body["predicates"] == ["in_the_money = TRUE"]
    assert body["sql"].startswith("WITH preview_population AS (")
    assert body["params"] == []
    assert set(body["relations"]) == {
        "mip.semantics.portfolio_headline_metric_view",
        "mip.gold.borrower_360",
    }
    assert response.headers["cache-control"] == "private, no-store"
    assert audit.list(limit=100) == []


def test_the_compat_prefix_answers_too(no_sql: None) -> None:
    response = TestClient(app).get("/api/kpi-proof?kpi=funnel.population", headers=LOAN_OFFICER)

    assert response.status_code == 200
    assert response.json()["measure_column"] == "population"


def test_no_forwarded_identity_is_a_401(no_sql: None) -> None:
    response = TestClient(app).get("/api/v1/kpi-proof?kpi=home.in_the_money")

    assert response.status_code == 401


@pytest.mark.parametrize("query", ["kpi=home.anything", "kpi=", ""])
def test_a_key_outside_the_closed_vocabulary_is_a_422(no_sql: None, query: str) -> None:
    response = TestClient(app).get(f"/api/v1/kpi-proof?{query}", headers=LOAN_OFFICER)

    assert response.status_code == 422


def test_a_refused_statement_is_never_emitted(monkeypatch: pytest.MonkeyPatch, no_sql: None) -> None:
    monkeypatch.setitem(
        kpi_proof_sql.KPI_STATEMENTS,
        "funnel.population",
        lambda: KpiStatement("SELECT COUNT(*) AS population FROM mip.silver.lien_current", {}, "population", None, ""),
    )

    response = TestClient(app).get("/api/v1/kpi-proof?kpi=funnel.population", headers=LOAN_OFFICER)

    assert response.status_code == 500
    assert "silver" not in response.text
    assert "SELECT" not in response.text

"""S1.4 contact-eligibility contract.

Pins the single-interface enforcement gate:

* ``GoldEligibilityService.evaluate`` is fail-closed for opt-out,
  do-not-contact, frequency-capped, unproven, and unconfigured rows.
* ``eligibility_source`` provenance defaults to ``synthetic_seed`` and
  passes a connected CRM/CDP connector id through untouched.
* Every blocked campaign/queue/export decision writes a
  ``SUPPRESS_CONTACT`` Lakebase audit row (allowlist-validated).
* Set-based predicates (lead queue, campaign preview, growth-agent
  workflows) read the same canonical predicate helpers.
* The gold DDL/transformations declare the ``dnc`` +
  ``eligibility_source`` columns with the synthetic_seed default.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient

from backend.main import app
from backend.schemas.activation import ActivationDestination
from backend.services.activation_state import get_activation_state_store
from backend.services.audit_store import get_audit_store
from backend.services.eligibility import (
    DEFAULT_ELIGIBILITY_SOURCE,
    REASON_CONSENT_NOT_OPT_IN,
    REASON_FREQUENCY_CAP,
    REASON_NOT_CONFIGURED,
    REASON_SUPPRESSED,
    SUPPRESS_ACTION,
    SUPPRESS_EVENT_TYPE,
    GoldEligibilityService,
    eligible_sql_predicate,
    get_eligibility_service,
    suppressed_sql_predicate,
    write_suppression_audit,
)
from backend.services.repositories import (
    get_borrower_repository,
    get_outreach_repository,
)
from backend.services.sales_state import get_sales_state_store
from tests.fixtures import mock_population
from tests.fixtures.in_memory_audit_store import InMemoryAuditStore

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICE = GoldEligibilityService()


# ---------------------------------------------------------------------------
# Row-level decisions.
# ---------------------------------------------------------------------------


def test_evaluate_eligible_happy_path_defaults_to_synthetic_seed_source() -> None:
    decision = SERVICE.evaluate(mock_population.BORROWERS[0])
    assert decision.eligible is True
    assert decision.reason_code is None
    assert decision.source == DEFAULT_ELIGIBILITY_SOURCE
    assert decision.dnc is False


def test_evaluate_blocks_opt_out_before_anything_else() -> None:
    borrower = mock_population.BORROWERS[0].model_copy(
        update={
            "marketing_eligible": False,
            "consent_status": "opt_out",
            "suppression_reason": "do_not_contact",
        },
    )
    decision = SERVICE.evaluate(borrower)
    assert decision.eligible is False
    assert decision.reason_code == REASON_CONSENT_NOT_OPT_IN
    assert decision.dnc is True


def test_evaluate_flags_explicit_dnc_field_as_suppressed() -> None:
    borrower = mock_population.BORROWERS[0].model_copy(update={"dnc": True})
    decision = SERVICE.evaluate(borrower)
    assert decision.eligible is False
    assert decision.reason_code == REASON_SUPPRESSED
    assert decision.dnc is True


def test_evaluate_frequency_cap_from_recent_touch() -> None:
    touched = datetime.now(UTC) - timedelta(days=3)
    borrower = mock_population.BORROWERS[0].model_copy(
        update={"last_touch_at": touched},
    )
    decision = SERVICE.evaluate(borrower)
    assert decision.eligible is False
    assert decision.reason_code == REASON_FREQUENCY_CAP
    assert decision.earliest_recontact_at == touched + timedelta(days=30)


def test_evaluate_unconfigured_row_fails_closed() -> None:
    decision = SERVICE.evaluate(SimpleNamespace(borrower_id="B-0000000000000"))
    assert decision.eligible is False
    assert decision.reason_code == REASON_NOT_CONFIGURED


def test_evaluate_passes_connected_crm_source_through() -> None:
    borrower = mock_population.BORROWERS[0].model_copy(
        update={"eligibility_source": "customer_crm_connector"},
    )
    decision = SERVICE.evaluate(borrower)
    assert decision.source == "customer_crm_connector"


def test_get_eligibility_service_returns_singleton_gold_impl() -> None:
    service = get_eligibility_service()
    assert isinstance(service, GoldEligibilityService)
    assert get_eligibility_service() is service


# ---------------------------------------------------------------------------
# Set-based predicates read the single interface.
# ---------------------------------------------------------------------------


def test_sql_predicates_are_canonical_and_fail_closed() -> None:
    assert eligible_sql_predicate() == "marketing_eligible = TRUE"
    assert eligible_sql_predicate("b") == "b.marketing_eligible = TRUE"
    assert suppressed_sql_predicate() == "marketing_eligible = FALSE"


def test_growth_agent_workflow_predicates_read_single_interface() -> None:
    from backend.services.growth_agent_workflows import WORKFLOWS

    workflow = WORKFLOWS["branch_capacity_review"]
    assert workflow.broad_predicate == eligible_sql_predicate("b")
    assert workflow.actionable_predicate == eligible_sql_predicate("b")


def test_campaign_preview_predicates_read_single_interface() -> None:
    from backend.schemas.portfolio import PortfolioCriteria
    from backend.services.repositories.databricks_portfolio import build_preview_predicates

    where, _ = build_preview_predicates(
        PortfolioCriteria(marketing_eligibility="Eligible only"),
        state_sets={},
    )
    assert eligible_sql_predicate() in where

    where_suppressed, _ = build_preview_predicates(
        PortfolioCriteria(marketing_eligibility="Suppressed only"),
        state_sets={},
    )
    assert suppressed_sql_predicate() in where_suppressed


# ---------------------------------------------------------------------------
# Suppression audit rows.
# ---------------------------------------------------------------------------


def test_write_suppression_audit_row_passes_metadata_allowlist() -> None:
    store = InMemoryAuditStore()
    borrower = mock_population.BORROWERS[0].model_copy(
        update={
            "marketing_eligible": False,
            "consent_status": "opt_out",
            "suppression_reason": "do_not_contact",
        },
    )
    decision = SERVICE.evaluate(borrower)
    event = write_suppression_audit(
        store,
        actor="skyler@entrada.ai",
        borrower_id=borrower.borrower_id,
        decision=decision,
        surface="outreach_draft",
    )
    assert event.event_type == SUPPRESS_EVENT_TYPE
    assert event.action == SUPPRESS_ACTION
    assert event.entity_id == borrower.borrower_id
    payload = event.payload_json
    assert payload["dnc"] is True
    assert payload["eligibility_source"] == DEFAULT_ELIGIBILITY_SOURCE
    assert payload["consent_status"] == "opt_out"
    assert payload["route"] == "outreach_draft"


@contextmanager
def _audit_override() -> Iterator[InMemoryAuditStore]:
    store = InMemoryAuditStore()
    prior = app.dependency_overrides.get(get_audit_store)
    app.dependency_overrides[get_audit_store] = lambda: store
    try:
        yield store
    finally:
        if prior is None:
            app.dependency_overrides.pop(get_audit_store, None)
        else:
            app.dependency_overrides[get_audit_store] = prior


class _SingleBorrowerOutreachRepo:
    def __init__(self, borrower: Any) -> None:
        self.borrower = borrower

    def find_borrower(self, borrower_id: str) -> Any | None:
        if borrower_id == self.borrower.borrower_id:
            return self.borrower
        return None


def test_outreach_draft_block_writes_suppress_contact_audit_row() -> None:
    borrower = mock_population.BORROWERS[0].model_copy(
        update={
            "marketing_eligible": False,
            "consent_status": "opt_out",
            "suppression_reason": "do_not_contact",
        },
    )
    prior_repo = app.dependency_overrides.get(get_outreach_repository)
    app.dependency_overrides[get_outreach_repository] = (
        lambda: _SingleBorrowerOutreachRepo(borrower)
    )
    try:
        with _audit_override() as store:
            response = TestClient(app).post(
                "/api/outreach/draft",
                json={"borrower_id": borrower.borrower_id, "channel": "email"},
            )
            events = store.list(event_type=SUPPRESS_EVENT_TYPE)
    finally:
        if prior_repo is None:
            app.dependency_overrides.pop(get_outreach_repository, None)
        else:
            app.dependency_overrides[get_outreach_repository] = prior_repo

    assert response.status_code == 422
    assert len(events) == 1
    assert events[0].entity_id == borrower.borrower_id
    assert events[0].payload_json["route"] == "outreach_draft"
    assert events[0].payload_json["eligibility_source"] == DEFAULT_ELIGIBILITY_SOURCE


class _StubActivationStore:
    def __init__(self) -> None:
        self.stage_calls = 0

    def get_destination(self, destination_key: str) -> ActivationDestination:
        return ActivationDestination(
            destination_key=destination_key,
            destination_type="salesforce",
            display_name="Salesforce CRM",
            status="not_configured",
            allowed_actions=["stage_lead"],
            updated_at="2026-06-01T00:00:00Z",
        )


class _StubBorrowerRepo:
    def __init__(self, borrower: Any) -> None:
        self.borrower = borrower

    def get(self, borrower_id: str) -> Any | None:
        if borrower_id == self.borrower.borrower_id:
            return self.borrower
        return None


class _ApprovedSalesState:
    def __init__(self, approval_id: str) -> None:
        self.approval_id = approval_id

    def lifecycle_for(self, borrower_id: str) -> dict[str, object]:
        _ = borrower_id
        return {"approval_status": "approved", "approval_id": self.approval_id}


def test_activation_stage_block_writes_suppress_contact_audit_row() -> None:
    borrower = mock_population.BORROWERS[0].model_copy(
        update={"suppression_reason": "do_not_contact"},
    )
    approval_id = str(uuid4())
    deps: dict[Any, Any] = {
        get_activation_state_store: lambda: _StubActivationStore(),
        get_borrower_repository: lambda: _StubBorrowerRepo(borrower),
        get_sales_state_store: lambda: _ApprovedSalesState(approval_id),
    }
    previous = {dep: app.dependency_overrides.get(dep) for dep in deps}
    app.dependency_overrides.update(deps)
    try:
        with _audit_override() as store:
            response = TestClient(app).post(
                "/api/activation/stage",
                json={
                    "borrower_id": borrower.borrower_id,
                    "destination_key": "salesforce_crm",
                    "approval_id": approval_id,
                    "request_id": str(uuid4()),
                },
            )
            events = store.list(event_type=SUPPRESS_EVENT_TYPE)
    finally:
        for dep, original in previous.items():
            if original is None:
                app.dependency_overrides.pop(dep, None)
            else:
                app.dependency_overrides[dep] = original

    assert response.status_code == 409
    assert response.json()["detail"] == "lead is suppressed"
    assert len(events) == 1
    assert events[0].payload_json["route"] == "activation_stage"
    assert events[0].payload_json["dnc"] is True


# ---------------------------------------------------------------------------
# Gold provenance columns are declared end-to-end.
# ---------------------------------------------------------------------------


def test_gold_sql_declares_dnc_and_eligibility_source_with_synthetic_default() -> None:
    b360 = (REPO_ROOT / "sql/transformations/gold_borrower_360.sql").read_text()
    lead_pop = (REPO_ROOT / "sql/transformations/gold_lead_population.sql").read_text()
    ddl_b360 = (REPO_ROOT / "sql/ddl/gold_borrower_360.sql").read_text()
    ddl_lead_pop = (REPO_ROOT / "sql/ddl/gold_lead_population.sql").read_text()

    assert "'synthetic_seed'" in b360
    assert "AS eligibility_source" in b360
    assert "AS dnc" in b360
    for text in (lead_pop, ddl_b360, ddl_lead_pop):
        assert "eligibility_source" in text
        assert "dnc" in text
    # Provenance comment documents the synthetic-seed default in the DDL.
    assert "synthetic_seed" in ddl_b360
    assert "synthetic_seed" in ddl_lead_pop


def test_lead_summary_schema_carries_provenance_fields() -> None:
    lead = mock_population.BORROWERS[0]
    assert lead.dnc is False
    assert lead.eligibility_source == DEFAULT_ELIGIBILITY_SOURCE

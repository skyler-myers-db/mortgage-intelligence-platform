"""Live Lakebase proof for the S2 loan-officer assignment slice.

SKIPPED unless ``LAKEBASE_INTEGRATION=1`` plus credentials are present
(same opt-in contract as ``test_lakebase_round_trip.py``). When it runs:

1. Applies ``lakebase/schema.sql`` + ``lakebase/seed_campaigns.sql``
   TWICE -- re-runs must be no-ops (idempotent migration proof).
2. Assigns a synthetic borrower to a seeded loan officer and asserts the
   ``LEAD_ASSIGN`` audit row landed in ``mip_app.action_audit``.
3. Advances the lifecycle one step and asserts the
   ``LEAD_ASSIGNMENT_STATUS`` audit row landed.
4. Cleans up its lead_assignments rows; audit rows stay (append-only).
"""

from __future__ import annotations

import contextlib
import os
import string
from pathlib import Path
from random import SystemRandom
from uuid import uuid4

import pytest

from backend.services.audit_lakebase_store import LakebaseAuditStore
from backend.services.lakebase import (
    LakebaseClient,
    _reset_client_for_tests,
    get_lakebase_client,
)
from backend.services.loan_officer_state import LoanOfficerStateStore

_HAS_STATIC_CREDS = all(
    os.environ.get(k)
    for k in ("LAKEBASE_HOST", "LAKEBASE_USER", "LAKEBASE_PASSWORD")
)
_HAS_WORKSPACE_CREDS = all(
    os.environ.get(k)
    for k in ("DATABRICKS_HOST", "DATABRICKS_TOKEN")
)
_HAS_CREDS = os.environ.get("LAKEBASE_INTEGRATION") == "1" and (
    _HAS_STATIC_CREDS or _HAS_WORKSPACE_CREDS
)

pytestmark = pytest.mark.skipif(
    not _HAS_CREDS,
    reason="Set LAKEBASE_INTEGRATION=1 + LAKEBASE_HOST/USER/PASSWORD to run",
)

_SEEDED_LO_01 = "55555555-5555-4555-8555-555555555501"
_ADMIN_ACTOR = "skyler@entrada.ai"


def _client_from_env() -> LakebaseClient:
    if not _HAS_STATIC_CREDS:
        _reset_client_for_tests()
        return get_lakebase_client()
    return LakebaseClient(
        host=os.environ["LAKEBASE_HOST"],
        port=int(os.environ.get("LAKEBASE_PORT", "5432")),
        database=os.environ.get("LAKEBASE_DATABASE") or "mip_app_state",
        user=os.environ["LAKEBASE_USER"],
        password=os.environ["LAKEBASE_PASSWORD"],
        sslmode=os.environ.get("LAKEBASE_SSLMODE", "require"),
    )


def _apply_schema_and_seed(client: LakebaseClient) -> None:
    root = Path(__file__).resolve().parents[2] / "lakebase"
    client.execute((root / "schema.sql").read_text(encoding="utf-8"))
    client.execute((root / "seed_campaigns.sql").read_text(encoding="utf-8"))


def _synthetic_borrower_id() -> str:
    rng = SystemRandom()
    alphabet = string.ascii_uppercase + string.digits
    return "B-" + "".join(rng.choice(alphabet) for _ in range(13))


def test_loan_officer_assignment_audit_round_trip() -> None:
    client = _client_from_env()
    # Idempotency proof: two consecutive applies must both succeed.
    _apply_schema_and_seed(client)
    _apply_schema_and_seed(client)

    store = LoanOfficerStateStore(client)
    officers = store.list_officers()
    assert any(o.loan_officer_id == _SEEDED_LO_01 for o in officers), (
        "seeded loan officer roster missing lo01"
    )
    lo01 = next(o for o in officers if o.loan_officer_id == _SEEDED_LO_01)
    assert lo01.coverage_states and lo01.coverage_counties

    borrower_id = _synthetic_borrower_id()
    request_id = str(uuid4())
    audit = LakebaseAuditStore(client=client)
    try:
        assignment, assign_audit_id = store.assign_lead(
            borrower_id=borrower_id,
            loan_officer_id=_SEEDED_LO_01,
            assigned_by=_ADMIN_ACTOR,
            request_id=request_id,
        )
        assert assignment.status == "assigned"
        assert assign_audit_id

        assign_events = [
            e
            for e in audit.list(limit=50, event_type="LEAD_ASSIGN")
            if e.request_id == request_id
        ]
        assert len(assign_events) == 1, "assign must write exactly one audit row"
        assert assign_events[0].event_id == assign_audit_id
        assert assign_events[0].entity_id == borrower_id

        advanced, status_audit_id = store.transition_status(
            assignment_id=assignment.assignment_id,
            to_status="contact_drafted",
            actor=_ADMIN_ACTOR,
        )
        assert advanced.status == "contact_drafted"
        assert status_audit_id

        status_events = [
            e
            for e in audit.list(limit=50, event_type="LEAD_ASSIGNMENT_STATUS", borrower_id=borrower_id)
        ]
        assert len(status_events) == 1
        assert status_events[0].payload_json.get("from_status") == "assigned"
        assert status_events[0].payload_json.get("to_status") == "contact_drafted"
    finally:
        # Workflow-state cleanup only; mip_app.action_audit is append-only
        # by trigger, so audit rows intentionally persist.
        with contextlib.suppress(Exception):
            client.execute(
                "DELETE FROM mip_app.lead_assignments WHERE borrower_id = %(b)s",
                {"b": borrower_id},
            )

"""Live Lakebase proof for the S6 approval-funnel slice.

SKIPPED unless ``LAKEBASE_INTEGRATION=1`` plus credentials are present
(same opt-in contract as ``test_lakebase_round_trip.py``). When it runs:

1. Applies ``lakebase/schema.sql`` + ``lakebase/seed_campaigns.sql``
   TWICE -- the S6 feedback-outcome DDL appendix must be idempotent.
2. Proves the acceptance contract: an approval moves the funnel counts
   within one cache TTL. The funnel reads are cached (sales-state TTL
   knob); every approval-workflow write clears that cache, so the very
   next read must reflect the change while the TTL window is still open.
3. Records an outcome and asserts the feedback row, the in-transaction
   LEAD_OUTCOME_RECORDED audit row, and the per-LO drill counts.
4. Cleans up its workflow rows; audit rows stay (append-only).
"""

from __future__ import annotations

import contextlib
import os
import string
from pathlib import Path
from random import SystemRandom
from uuid import uuid4

import pytest

from backend.config.settings import settings
from backend.schemas.funnel import FunnelPopulation
from backend.services.approval_funnel import (
    ApprovalFunnelStore,
    clear_approval_funnel_cache,
)
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


def _workflow_counts(store: ApprovalFunnelStore) -> dict[str, int]:
    stages = store.workflow_stages(FunnelPopulation())
    return {stage.stage: stage.borrower_count for stage in stages}


def test_approval_moves_funnel_within_one_cache_ttl_and_per_lo_drill() -> None:
    client = _client_from_env()
    # Idempotency proof for the S6 DDL appendix: two applies, both succeed.
    _apply_schema_and_seed(client)
    _apply_schema_and_seed(client)

    assert settings.mip_sales_state_cache_ttl_s > 0, (
        "the cache-TTL acceptance is only meaningful with the funnel cache on"
    )

    lifecycle = LoanOfficerStateStore(client)
    funnel = ApprovalFunnelStore(client)
    audit = LakebaseAuditStore(client=client)
    borrower_id = _synthetic_borrower_id()
    request_id = str(uuid4())
    outcome_request_id = str(uuid4())

    try:
        # Prime the cached funnel read BEFORE any write so the later reads
        # can only reflect the change through invalidation (the TTL window
        # is still open for the whole test run).
        before = _workflow_counts(funnel)
        officer_before = funnel.officer_row(_SEEDED_LO_01)
        assert officer_before is not None, "seeded loan officer missing"
        outcomes_before = funnel.outcome_counts(_SEEDED_LO_01)

        assignment, _ = lifecycle.assign_lead(
            borrower_id=borrower_id,
            loan_officer_id=_SEEDED_LO_01,
            assigned_by=_ADMIN_ACTOR,
            request_id=request_id,
        )
        for status in ("contact_drafted", "approved"):
            assignment, _ = lifecycle.transition_status(
                assignment_id=assignment.assignment_id,
                to_status=status,  # type: ignore[arg-type]
                actor=_ADMIN_ACTOR,
            )

        # ACCEPTANCE: the approval is visible on the immediately-following
        # funnel read -- within one cache TTL of the write.
        after_approve = _workflow_counts(funnel)
        assert after_approve["approved"] == before["approved"] + 1

        assignment, _ = lifecycle.transition_status(
            assignment_id=assignment.assignment_id,
            to_status="actioned",
            actor=_ADMIN_ACTOR,
        )
        recorded, feedback_id, outcome_audit_id = lifecycle.record_outcome(
            assignment_id=assignment.assignment_id,
            outcome="success",
            actor=_ADMIN_ACTOR,
            request_id=outcome_request_id,
        )
        assert recorded.status == "outcome_recorded"
        assert feedback_id

        # Feedback row (the existing feedback-table pattern) landed and is
        # linked to the in-transaction audit event.
        feedback_row = client.fetchone(
            """
            SELECT event_type, borrower_id, audit_event_id
            FROM mip_app.feedback
            WHERE feedback_id = %(feedback_id)s
            """,
            {"feedback_id": feedback_id},
        )
        assert feedback_row is not None
        assert feedback_row["event_type"] == "assignment_outcome_success"
        assert feedback_row["borrower_id"] == borrower_id
        assert str(feedback_row["audit_event_id"]) == outcome_audit_id

        outcome_events = [
            e
            for e in audit.list(limit=50, event_type="LEAD_OUTCOME_RECORDED", borrower_id=borrower_id)
        ]
        assert len(outcome_events) == 1
        assert outcome_events[0].payload_json.get("assignment_outcome") == "success"

        after_outcome = _workflow_counts(funnel)
        assert after_outcome["outcome_recorded"] == before["outcome_recorded"] + 1
        assert after_outcome["actioned"] == before["actioned"] + 1

        # Per-LO drill renders real per-officer counts.
        officer_after = funnel.officer_row(_SEEDED_LO_01)
        assert officer_after is not None
        assert officer_after.outcome_recorded == officer_before.outcome_recorded + 1
        assert officer_after.total_active == officer_before.total_active + 1
        outcomes_after = funnel.outcome_counts(_SEEDED_LO_01)
        assert outcomes_after.success == outcomes_before.success + 1

        # The approvals-ledger entry point (POST /outreach/approve) also
        # moves the funnel: its write path calls clear_sales_state_cache,
        # emulated here by the ledger insert + the same cache hook.
        ledger_borrower = _synthetic_borrower_id()
        client.execute(
            """
            INSERT INTO mip_app.approvals (borrower_id, offer_code, action, actor_email, request_id)
            VALUES (%(borrower_id)s, 'refi', 'approve', %(actor)s, %(request_id)s)
            """,
            {
                "borrower_id": ledger_borrower,
                "actor": _ADMIN_ACTOR,
                "request_id": str(uuid4()),
            },
        )
        clear_approval_funnel_cache()
        try:
            after_ledger = _workflow_counts(funnel)
            assert after_ledger["approved"] == after_outcome["approved"] + 1
            approvers = {row.actor_email for row in funnel.recent_approvals(limit=50)}
            assert _ADMIN_ACTOR in approvers
        finally:
            with contextlib.suppress(Exception):
                client.execute(
                    "DELETE FROM mip_app.approvals WHERE borrower_id = %(b)s",
                    {"b": ledger_borrower},
                )
    finally:
        # Workflow-state cleanup only; mip_app.action_audit is append-only
        # by trigger, so audit rows intentionally persist.
        with contextlib.suppress(Exception):
            client.execute(
                "DELETE FROM mip_app.feedback WHERE borrower_id = %(b)s",
                {"b": borrower_id},
            )
        with contextlib.suppress(Exception):
            client.execute(
                "DELETE FROM mip_app.lead_assignments WHERE borrower_id = %(b)s",
                {"b": borrower_id},
            )
        clear_approval_funnel_cache()

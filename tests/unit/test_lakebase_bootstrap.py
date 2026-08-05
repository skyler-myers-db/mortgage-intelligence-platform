"""R6-02: ``ensure_approval_idempotency_column`` retries on failure.

The prior implementation flipped the per-process
``_APPROVAL_REQUEST_ID_BOOTSTRAPPED`` flag on BOTH success and failure,
which meant a Lakebase brownout during the first approve/reject would
permanently convince the process that the R5-01 DDL had succeeded. Every
subsequent INSERT would then silently fail on an index that didn't
exist.

These tests pin the R6-02 fix:

* a failed bootstrap call leaves the flag False;
* a successful subsequent call flips it to True;
* a subsequent already-True call is a no-op (no extra execute calls).
"""
from __future__ import annotations

from typing import Any

import pytest

from backend.services import lakebase_bootstrap
from backend.services.lakebase import LakebaseError
from backend.services.lakebase_bootstrap import (
    _bootstrap_state_for_tests,
    _reset_bootstrap_for_tests,
    ensure_approval_followup_columns,
    ensure_approval_idempotency_column,
    ensure_assignment_outcome_schema,
    ensure_sales_workflow_request_id_columns,
)


class _FakeClient:
    """Minimal stand-in for ``LakebaseClient``.

    ``raise_on_call`` is a list of booleans consumed left-to-right: the
    Nth ``execute`` call raises ``LakebaseError`` when the Nth entry is
    True, succeeds otherwise. The bootstrap issues 2 statements per
    invocation (an ALTER + a CREATE INDEX); the list must cover them.
    """

    def __init__(self, raise_on_call: list[bool] | None = None) -> None:
        self.raise_on_call = raise_on_call or []
        self.calls: list[str] = []

    def execute(self, stmt: str, params: Any = None) -> None:
        _ = params
        idx = len(self.calls)
        self.calls.append(stmt)
        if idx < len(self.raise_on_call) and self.raise_on_call[idx]:
            raise LakebaseError("simulated Lakebase brownout")


@pytest.fixture(autouse=True)
def _reset_flag() -> Any:
    """Ensure every test starts with the flag cleared."""
    _reset_bootstrap_for_tests()
    yield
    _reset_bootstrap_for_tests()


def test_bootstrap_flag_stays_false_on_lakebase_error() -> None:
    """R6-02: a LakebaseError during the DDL must leave the flag False.

    The prior behaviour set it to True so the second attempt was a
    no-op, which permanently hid the outage -- the bootstrap never
    ran again in the process lifetime even after Lakebase recovered.
    """
    client = _FakeClient(raise_on_call=[True, True])

    ensure_approval_idempotency_column(client)  # type: ignore[arg-type]

    state = _bootstrap_state_for_tests()
    assert state["request_id_bootstrapped"] is False
    # The first statement was attempted; the second was not because the
    # first raised. Either way, the flag must be False.
    assert len(client.calls) == 1


def test_bootstrap_flag_stays_false_on_unexpected_error() -> None:
    """Any non-LakebaseError exception (schema drift, OS-level) must
    also leave the flag False so the next request retries."""

    class _Boom(_FakeClient):
        def execute(self, stmt: str, params: Any = None) -> None:
            _ = params
            self.calls.append(stmt)
            raise RuntimeError("unexpected")

    client = _Boom()
    ensure_approval_idempotency_column(client)  # type: ignore[arg-type]

    state = _bootstrap_state_for_tests()
    assert state["request_id_bootstrapped"] is False


def test_bootstrap_flag_flips_on_successful_retry_after_failure() -> None:
    """R6-02 core contract: a failed call followed by a successful call
    leaves the flag True. DDL is idempotent so the retry is safe."""
    client = _FakeClient(raise_on_call=[True])

    ensure_approval_idempotency_column(client)  # type: ignore[arg-type]
    assert _bootstrap_state_for_tests()["request_id_bootstrapped"] is False

    # Next call: clean client, bootstrap succeeds, flag flips True.
    clean = _FakeClient()
    ensure_approval_idempotency_column(clean)  # type: ignore[arg-type]
    assert _bootstrap_state_for_tests()["request_id_bootstrapped"] is True
    assert len(clean.calls) == len(lakebase_bootstrap._APPROVAL_REQUEST_ID_DDL) + 2


def test_bootstrap_successful_call_flips_flag_and_runs_all_statements() -> None:
    """Happy path: the clean first call applies every statement and
    marks the process bootstrapped."""
    client = _FakeClient()

    ensure_approval_idempotency_column(client)  # type: ignore[arg-type]

    assert _bootstrap_state_for_tests()["request_id_bootstrapped"] is True
    # R6-04: call count is DDL tuple length + 2 (advisory lock + unlock
    # bracket the DDL block). Assert the DDL statements still ran by
    # matching the sub-sequence rather than an exact count.
    ddl_calls = [c for c in client.calls if "pg_advisory" not in c]
    assert len(ddl_calls) == len(lakebase_bootstrap._APPROVAL_REQUEST_ID_DDL)


def test_bootstrap_preflight_latches_when_schema_already_exists() -> None:
    """Runtime app principal should not issue owner-only DDL after deploy.

    The deploy-time migration creates the column/index. On a fresh app process,
    a read-only preflight can prove that and latch the bootstrap flag without
    trying ALTER TABLE as the app service principal.
    """

    class _AlreadyApplied(_FakeClient):
        def fetchone(self, stmt: str, params: Any = None) -> dict[str, bool]:
            _ = params
            self.calls.append(stmt)
            return {
                "has_request_id_column": True,
                "has_decision_intent_column": True,
                "has_decision_payload_hash_column": True,
                "has_decision_response_column": True,
                "has_audit_event_id_column": True,
                "has_request_id_index": True,
            }

    client = _AlreadyApplied()
    ensure_approval_idempotency_column(client)  # type: ignore[arg-type]

    assert _bootstrap_state_for_tests()["request_id_bootstrapped"] is True
    assert len(client.calls) == 1
    assert "information_schema.columns" in client.calls[0]
    assert not any("ALTER TABLE" in call for call in client.calls)


def test_bootstrap_second_call_after_success_is_noop() -> None:
    """A successful bootstrap should not re-execute statements on the
    second invocation -- that's the memoisation contract."""
    first = _FakeClient()
    ensure_approval_idempotency_column(first)  # type: ignore[arg-type]
    assert _bootstrap_state_for_tests()["request_id_bootstrapped"] is True

    second = _FakeClient()
    ensure_approval_idempotency_column(second)  # type: ignore[arg-type]
    # Flag still True, but the second client never saw an execute call.
    assert _bootstrap_state_for_tests()["request_id_bootstrapped"] is True
    assert second.calls == []


# ---------------------------------------------------------------------------
# R6-04: advisory-lock serialisation across the app bootstrap + migrate job
# ---------------------------------------------------------------------------


def test_bootstrap_acquires_and_releases_advisory_lock_on_success() -> None:
    """R6-04: the happy-path bootstrap wraps the idempotent DDL in a
    ``pg_advisory_lock(hashtext(...))`` / ``pg_advisory_unlock(...)``
    pair so a racing ``mip_lakebase_migrate`` job serialises behind it.

    We observe the order of execute calls: first SELECT is the lock
    acquire, last is the matching unlock, and the DDL statements live
    between them.
    """
    client = _FakeClient()
    ensure_approval_idempotency_column(client)  # type: ignore[arg-type]

    assert _bootstrap_state_for_tests()["request_id_bootstrapped"] is True
    assert len(client.calls) == len(lakebase_bootstrap._APPROVAL_REQUEST_ID_DDL) + 2
    assert "pg_advisory_lock" in client.calls[0]
    assert "pg_advisory_unlock" in client.calls[-1]
    # Every DDL statement must sit inside the locked region.
    assert "ALTER TABLE" in client.calls[1]
    assert "CREATE UNIQUE INDEX" in client.calls[-2]


def test_bootstrap_releases_advisory_lock_on_ddl_failure() -> None:
    """R6-04: when a DDL statement raises between the lock and unlock,
    the ``finally``-style release path must still run. Otherwise a
    stuck advisory lock would block the next call (and the migrate
    job) until the Lakebase session ends.
    """
    # Sequence: acquire lock OK, first DDL fails. Release must still be
    # attempted (the fake records a 3rd call to pg_advisory_unlock).
    client = _FakeClient(raise_on_call=[False, True])
    ensure_approval_idempotency_column(client)  # type: ignore[arg-type]

    # Flag stays False (R6-02: failed bootstrap re-runs on next call).
    assert _bootstrap_state_for_tests()["request_id_bootstrapped"] is False
    # 1 lock + 1 failed DDL + 1 unlock attempt = 3 calls
    assert len(client.calls) == 3
    assert "pg_advisory_lock" in client.calls[0]
    assert "pg_advisory_unlock" in client.calls[-1]


# ---------------------------------------------------------------------------
# Sales workflow request-id bootstrap
# ---------------------------------------------------------------------------


def test_sales_workflow_bootstrap_runs_expected_ddl_under_lock() -> None:
    """Sales assignment/disposition idempotency DDL must be applied atomically.

    This pins the exact migration shape used by the runtime bootstrap: the old
    assignment request-id-only index is dropped, assignments receive the
    borrower-aware request-id index, and dispositions receive their own
    request-id uniqueness guard.
    """

    client = _FakeClient()

    ensure_sales_workflow_request_id_columns(client)  # type: ignore[arg-type]

    assert _bootstrap_state_for_tests()["sales_workflow_request_id_bootstrapped"] is True
    assert len(client.calls) == len(lakebase_bootstrap._SALES_WORKFLOW_REQUEST_ID_DDL) + 2
    assert "pg_advisory_lock" in client.calls[0]
    assert "pg_advisory_unlock" in client.calls[-1]
    assert client.calls[1:-1] == list(lakebase_bootstrap._SALES_WORKFLOW_REQUEST_ID_DDL)
    ddl_blob = "\n".join(client.calls[1:-1])
    assert "ALTER TABLE mip_app.lead_assignments ADD COLUMN IF NOT EXISTS request_id TEXT" in ddl_blob
    assert "ALTER TABLE mip_app.lead_assignments ADD COLUMN IF NOT EXISTS assignment_scope TEXT" in ddl_blob
    assert "DROP INDEX IF EXISTS mip_app.idx_lead_assignments_request_id" in ddl_blob
    assert "idx_lead_assignments_request_borrower" in ddl_blob
    assert "ON mip_app.lead_assignments (request_id, borrower_id)" in ddl_blob
    assert "idx_lead_assignments_single_request_id" in ddl_blob
    assert "assignment_scope = 'single'" in ddl_blob
    assert "ALTER TABLE mip_app.call_dispositions ADD COLUMN IF NOT EXISTS request_id TEXT" in ddl_blob
    assert "idx_call_dispositions_request_id" in ddl_blob


def test_sales_workflow_bootstrap_preflight_latches_when_schema_already_exists() -> None:
    """Runtime app principal should not issue sales-workflow owner-only DDL.

    The deploy-time Lakebase migration owns the table shape. Once the
    request-id columns and indexes exist, the first sales route hit must
    latch success from read-only metadata instead of trying ALTER/DROP/CREATE
    as the Databricks App principal.
    """

    class _AlreadyApplied(_FakeClient):
        def fetchone(self, stmt: str, params: Any = None) -> dict[str, bool]:
            _ = params
            self.calls.append(stmt)
            return {
                "has_assignment_request_id_column": True,
                "has_assignment_scope_column": True,
                "has_disposition_request_id_column": True,
                "has_assignment_request_id_index": True,
                "has_assignment_single_request_id_index": True,
                "has_disposition_request_id_index": True,
            }

    client = _AlreadyApplied()
    ensure_sales_workflow_request_id_columns(client)  # type: ignore[arg-type]

    assert _bootstrap_state_for_tests()["sales_workflow_request_id_bootstrapped"] is True
    assert len(client.calls) == 1
    assert "information_schema.columns" in client.calls[0]
    assert not any("ALTER TABLE" in call for call in client.calls)
    assert not any("DROP INDEX" in call for call in client.calls)
    assert not any("CREATE UNIQUE INDEX" in call for call in client.calls)


def test_sales_workflow_bootstrap_retries_after_lakebase_error() -> None:
    """A Sales DDL failure must not latch success; the next request retries."""

    failing = _FakeClient(raise_on_call=[False, False, True])

    ensure_sales_workflow_request_id_columns(failing)  # type: ignore[arg-type]

    assert _bootstrap_state_for_tests()["sales_workflow_request_id_bootstrapped"] is False
    assert "pg_advisory_unlock" in failing.calls[-1]

    clean = _FakeClient()
    ensure_sales_workflow_request_id_columns(clean)  # type: ignore[arg-type]

    assert _bootstrap_state_for_tests()["sales_workflow_request_id_bootstrapped"] is True
    assert len(clean.calls) == len(lakebase_bootstrap._SALES_WORKFLOW_REQUEST_ID_DDL) + 2


def test_sales_workflow_bootstrap_second_call_after_success_is_noop() -> None:
    """Successful Sales bootstrap should not re-run DDL on later requests."""

    first = _FakeClient()
    ensure_sales_workflow_request_id_columns(first)  # type: ignore[arg-type]
    assert _bootstrap_state_for_tests()["sales_workflow_request_id_bootstrapped"] is True

    second = _FakeClient()
    ensure_sales_workflow_request_id_columns(second)  # type: ignore[arg-type]

    assert _bootstrap_state_for_tests()["sales_workflow_request_id_bootstrapped"] is True
    assert second.calls == []


# ---------------------------------------------------------------------------
# Approval assignment/follow-up bootstrap
# ---------------------------------------------------------------------------


def test_approval_followup_bootstrap_runs_expected_ddl_under_lock() -> None:
    """Local/owned Lakebase runtimes still get the idempotent DDL backstop."""

    client = _FakeClient()

    ensure_approval_followup_columns(client)  # type: ignore[arg-type]

    assert _bootstrap_state_for_tests()["approval_followup_bootstrapped"] is True
    assert len(client.calls) == len(lakebase_bootstrap._APPROVAL_FOLLOWUP_DDL) + 2
    assert "pg_advisory_lock" in client.calls[0]
    assert "pg_advisory_unlock" in client.calls[-1]
    assert client.calls[1:-1] == list(lakebase_bootstrap._APPROVAL_FOLLOWUP_DDL)


def test_approval_followup_bootstrap_preflight_latches_when_schema_already_exists() -> None:
    """Do not ALTER ``mip_app.approvals`` when deploy migration already ran.

    This pins the live smoke failure: the Databricks App principal can insert
    into ``approvals`` but is not table owner, so owner-only DDL must be skipped
    when the follow-up columns are present.
    """

    class _AlreadyApplied(_FakeClient):
        def fetchone(self, stmt: str, params: Any = None) -> dict[str, bool]:
            _ = params
            self.calls.append(stmt)
            return {
                "has_assigned_to_email_column": True,
                "has_follow_up_at_column": True,
            }

    client = _AlreadyApplied()

    ensure_approval_followup_columns(client)  # type: ignore[arg-type]

    assert _bootstrap_state_for_tests()["approval_followup_bootstrapped"] is True
    assert len(client.calls) == 1
    assert "information_schema.columns" in client.calls[0]
    assert not any("ALTER TABLE" in call for call in client.calls)


def test_approval_followup_bootstrap_retries_after_lakebase_error() -> None:
    """A follow-up DDL failure must not latch success; the next request retries."""

    failing = _FakeClient(raise_on_call=[False, True])

    ensure_approval_followup_columns(failing)  # type: ignore[arg-type]

    assert _bootstrap_state_for_tests()["approval_followup_bootstrapped"] is False
    assert "pg_advisory_unlock" in failing.calls[-1]

    clean = _FakeClient()
    ensure_approval_followup_columns(clean)  # type: ignore[arg-type]

    assert _bootstrap_state_for_tests()["approval_followup_bootstrapped"] is True
    assert len(clean.calls) == len(lakebase_bootstrap._APPROVAL_FOLLOWUP_DDL) + 2


def test_approval_followup_bootstrap_second_call_after_success_is_noop() -> None:
    """Successful follow-up bootstrap should not re-run DDL."""

    first = _FakeClient()
    ensure_approval_followup_columns(first)  # type: ignore[arg-type]
    assert _bootstrap_state_for_tests()["approval_followup_bootstrapped"] is True

    second = _FakeClient()
    ensure_approval_followup_columns(second)  # type: ignore[arg-type]

    assert _bootstrap_state_for_tests()["approval_followup_bootstrapped"] is True
    assert second.calls == []


def test_assignment_outcome_bootstrap_runs_expected_ddl_under_lock() -> None:
    """S6: local/owned Lakebase runtimes get the idempotent feedback DDL."""

    client = _FakeClient()

    ensure_assignment_outcome_schema(client)  # type: ignore[arg-type]

    assert _bootstrap_state_for_tests()["assignment_outcome_bootstrapped"] is True
    assert len(client.calls) == len(lakebase_bootstrap._ASSIGNMENT_OUTCOME_DDL) + 2
    assert "pg_advisory_lock" in client.calls[0]
    assert "pg_advisory_unlock" in client.calls[-1]
    assert client.calls[1:-1] == list(lakebase_bootstrap._ASSIGNMENT_OUTCOME_DDL)


def test_assignment_outcome_bootstrap_preflight_latches_when_schema_already_exists() -> None:
    """Skip owner-only DDL when the migrate job already applied the S6 columns."""

    class _AlreadyApplied(_FakeClient):
        def fetchone(self, stmt: str, params: Any = None) -> dict[str, bool]:
            _ = params
            self.calls.append(stmt)
            return {
                "has_assignment_id_column": True,
                "has_request_id_index": True,
                "has_assignment_outcome_index": True,
            }

    client = _AlreadyApplied()

    ensure_assignment_outcome_schema(client)  # type: ignore[arg-type]

    assert _bootstrap_state_for_tests()["assignment_outcome_bootstrapped"] is True
    assert len(client.calls) == 1
    assert "information_schema.columns" in client.calls[0]
    assert not any("ALTER TABLE" in call for call in client.calls)


def test_assignment_outcome_bootstrap_retries_after_lakebase_error() -> None:
    """A failed S6 bootstrap must not latch success; next request retries."""

    failing = _FakeClient(raise_on_call=[False, True])

    ensure_assignment_outcome_schema(failing)  # type: ignore[arg-type]

    assert _bootstrap_state_for_tests()["assignment_outcome_bootstrapped"] is False
    assert "pg_advisory_unlock" in failing.calls[-1]

    clean = _FakeClient()
    ensure_assignment_outcome_schema(clean)  # type: ignore[arg-type]

    assert _bootstrap_state_for_tests()["assignment_outcome_bootstrapped"] is True
    assert len(clean.calls) == len(lakebase_bootstrap._ASSIGNMENT_OUTCOME_DDL) + 2


def test_assignment_outcome_bootstrap_second_call_after_success_is_noop() -> None:
    """Successful S6 bootstrap should not re-run DDL."""

    first = _FakeClient()
    ensure_assignment_outcome_schema(first)  # type: ignore[arg-type]
    assert _bootstrap_state_for_tests()["assignment_outcome_bootstrapped"] is True

    second = _FakeClient()
    ensure_assignment_outcome_schema(second)  # type: ignore[arg-type]

    assert _bootstrap_state_for_tests()["assignment_outcome_bootstrapped"] is True
    assert second.calls == []

"""Real-Postgres proof: the funnel's recent approvals leave out a revoked approve.

flow-v2 (W5e w5-lead-table-compile): ``_RECENT_APPROVALS_SQL`` excludes an
approve that has a later finalized revoke for the same borrower (NOT EXISTS),
a re-approve after that revoke is newer than it and lists again, and the
"ever approved" stage count is unchanged (a revoke appends, it never edits
history). The revoke runs through the real ``revoke_approval`` transaction,
so the row the funnel reads is the one production writes. No Lakebase block
changes in this lane, so there is no twice-applied migration here: the schema
is applied once per test from a dropped ``mip_app``. Skipped unless
``MIP_TEST_POSTGRES_DSN`` names a disposable database.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from backend.schemas.funnel import FunnelPopulation
from backend.schemas.outreach_revoke import OutreachRevokeRequest
from backend.services.approval_funnel import ApprovalFunnelStore, clear_approval_funnel_cache
from backend.services.outreach_revoke import revoke_approval
from jobs import lakebase_migrate
from tests.fixtures.lakebase_contract_prefix import contract_as_of

pytestmark = pytest.mark.integration

_SCHEMA = Path("lakebase/schema.sql").read_text(encoding="utf-8")
_SEED = Path("lakebase/seed_campaigns.sql").read_text(encoding="utf-8")
APPROVER = "pat.approver@summit.example"
BORROWER_A = "B-0000000000FR1"
BORROWER_B = "B-0000000000FR2"


@pytest.fixture
def conn_kwargs() -> Iterator[dict[str, str]]:
    dsn = os.environ.get("MIP_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("MIP_TEST_POSTGRES_DSN is not configured")
    kwargs = {"conninfo": dsn}
    with psycopg.connect(**kwargs, autocommit=True) as conn:
        conn.execute("DROP SCHEMA IF EXISTS mip_app CASCADE")
    clear_approval_funnel_cache()
    try:
        yield kwargs
    finally:
        clear_approval_funnel_cache()
        with psycopg.connect(**kwargs, autocommit=True) as conn:
            conn.execute("DROP SCHEMA IF EXISTS mip_app CASCADE")


class _PgLakebase:
    """The LakebaseClient surface the services use, one real connection per call."""

    _supports_atomic_transactions = True

    def __init__(self, conn_kwargs: dict[str, str]) -> None:
        self._kwargs = conn_kwargs

    def execute(self, statement: str, params: dict[str, Any] | None = None) -> None:
        with psycopg.connect(**self._kwargs) as conn:
            conn.execute(statement, params)

    def fetchone(self, statement: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        with psycopg.connect(**self._kwargs, row_factory=dict_row) as conn:
            return conn.execute(statement, params).fetchone()

    def fetchall(
        self, statement: str, params: dict[str, Any] | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        with psycopg.connect(**self._kwargs, row_factory=dict_row) as conn:
            return conn.execute(statement, params).fetchall()[:limit]

    @contextmanager
    def transaction(self) -> Iterator[psycopg.Connection[dict[str, Any]]]:
        with psycopg.connect(**self._kwargs, row_factory=dict_row) as conn:
            yield conn


def _schema(conn_kwargs: dict[str, str]) -> _PgLakebase:
    pre_seed, post_seed = lakebase_migrate._split_schema_sql(_SCHEMA)
    with contract_as_of(_SCHEMA):
        lakebase_migrate._run_transaction(
            (pre_seed, _SEED, post_seed),
            conn_kwargs,
            app_role="lakebase-schema-upgrade-test-role",
            verify_outreach_integrity=True,
            allow_absent_managed_event_triggers=True,
            allow_absent_provider_schema=True,
        )
    return _PgLakebase(conn_kwargs)


def _approve(conn_kwargs: dict[str, str], borrower_id: str) -> str:
    """A finalized approve row, its intent canonical like _canonical_intent writes it."""
    intent = json.dumps(
        {"action": "approve", "actor": APPROVER, "borrower_id": borrower_id},
        sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    )
    with psycopg.connect(**conn_kwargs, autocommit=True) as conn:
        row = conn.execute(
            "INSERT INTO mip_app.approvals (borrower_id, action, actor_email, channel, offer_code, "
            "decision_intent, decided_at) VALUES (%s, 'approve', %s, 'email', 'refi', %s, clock_timestamp()) "
            "RETURNING approval_id::text",
            (borrower_id, APPROVER, intent),
        ).fetchone()
        assert row is not None
        approval_id = str(row[0])
        audit = conn.execute(
            "INSERT INTO mip_app.action_audit (event_type, actor_email, entity_type, entity_id) "
            "VALUES ('APPROVE', %s, 'approval', %s) RETURNING audit_id::text",
            (APPROVER, approval_id),
        ).fetchone()
        assert audit is not None
        conn.execute(
            "UPDATE mip_app.approvals SET decision_response = '{}'::jsonb, audit_event_id = %s "
            "WHERE approval_id = %s",
            (str(audit[0]), approval_id),
        )
    return approval_id


def _revoke(lakebase: _PgLakebase, borrower_id: str, approval_id: str) -> str:
    payload = OutreachRevokeRequest(
        borrower_id=borrower_id,
        approval_id=approval_id,
        rationale="Offer code was mis-keyed; needs a second review.",
        request_id=str(uuid4()),
    )
    response, created = revoke_approval(
        lakebase,  # type: ignore[arg-type]
        actor=APPROVER, payload=payload, rationale=payload.rationale, subject_clip=None,
    )
    assert created and response.audit_event_id
    return response.approval_id


def _recent(lakebase: _PgLakebase) -> list[str]:
    """This test's borrowers' rows in the recent list, newest first (the seed carries its own approvals)."""
    clear_approval_funnel_cache()
    rows = ApprovalFunnelStore(lakebase).recent_approvals(limit=50)  # type: ignore[arg-type]
    return [row.approval_id for row in rows if row.borrower_id in {BORROWER_A, BORROWER_B}]


def _ever_approved(lakebase: _PgLakebase) -> int:
    clear_approval_funnel_cache()
    stages = ApprovalFunnelStore(lakebase).workflow_stages(FunnelPopulation())  # type: ignore[arg-type]
    return next(stage.borrower_count for stage in stages if stage.stage == "approved")


def test_a_revoked_approve_leaves_the_recent_list_and_a_re_approve_lists(conn_kwargs: dict[str, str]) -> None:
    lakebase = _schema(conn_kwargs)
    first = _approve(conn_kwargs, BORROWER_A)
    other = _approve(conn_kwargs, BORROWER_B)
    assert set(_recent(lakebase)) == {first, other}
    approved = _ever_approved(lakebase)

    _revoke(lakebase, BORROWER_A, first)
    assert _recent(lakebase) == [other], "the revoked approve left the recent list; the other borrower's stays"
    assert _ever_approved(lakebase) == approved, "'ever approved' is unchanged by a revoke"

    again = _approve(conn_kwargs, BORROWER_A)
    assert _recent(lakebase) == [again, other], "a re-approve after the revoke is newer, so it lists"


def test_a_revoke_of_one_borrower_never_hides_another_borrowers_approve(conn_kwargs: dict[str, str]) -> None:
    lakebase = _schema(conn_kwargs)
    older = _approve(conn_kwargs, BORROWER_B)
    revoked = _approve(conn_kwargs, BORROWER_A)
    _revoke(lakebase, BORROWER_A, revoked)
    # BORROWER_B's approve is older than the revoke, but it is another borrower's.
    assert _recent(lakebase) == [older]

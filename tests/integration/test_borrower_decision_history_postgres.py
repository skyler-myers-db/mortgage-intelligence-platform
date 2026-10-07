"""Real-PostgreSQL contract of the borrower decision history (audit flow-04 phase 2).

D-audit-reads-c2. Every test starts from the schema as it stood before the
``idx_action_audit_decision_entity`` block, then applies the governed
migration TWICE through ``lakebase_migrate._run_transaction`` inside
``contract_as_of`` (the executable-hook preflight inventories the new index
predicate only on the re-run). The shared seeds
(tests/fixtures/borrower_decision_rows.py) are then inserted by raw SQL, the
phone-shaped masked id included (C7), and the EXACT service statements must
return every positive and exclude every negative control, before the Python
belt sees a row. Under a forced generic plan the history statement must use
the partial index. Skipped unless ``MIP_TEST_POSTGRES_DSN`` names a
disposable database.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import psycopg
import pytest
from psycopg import sql as psql
from psycopg.rows import dict_row

from backend.services import borrower_decision_history as history
from jobs import lakebase_migrate
from tests.fixtures import borrower_decision_rows as seeds
from tests.fixtures.lakebase_contract_prefix import contract_as_of

pytestmark = pytest.mark.integration

_SCHEMA = Path("lakebase/schema.sql").read_text(encoding="utf-8")
_SEED = Path("lakebase/seed_campaigns.sql").read_text(encoding="utf-8")
_MARKER = "-- Borrower decision history index ---"
# The integrator re-dates this placeholder after 2026_10_01_genie_job_sections (C6).
_VERSION = "2026_10_07_borrower_decision_history_index"


@pytest.fixture
def conn_kwargs() -> Iterator[dict[str, str]]:
    dsn = os.environ.get("MIP_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("MIP_TEST_POSTGRES_DSN is not configured")
    kwargs = {"conninfo": dsn}
    with psycopg.connect(**kwargs, autocommit=True) as conn:
        conn.execute("DROP SCHEMA IF EXISTS mip_app CASCADE")
    try:
        yield kwargs
    finally:
        with psycopg.connect(**kwargs, autocommit=True) as conn:
            conn.execute("DROP SCHEMA IF EXISTS mip_app CASCADE")


def _apply(conn_kwargs: dict[str, str], schema_sql: str) -> None:
    pre_seed, post_seed = lakebase_migrate._split_schema_sql(schema_sql)
    with contract_as_of(schema_sql):
        lakebase_migrate._run_transaction(
            (pre_seed, _SEED, post_seed),
            conn_kwargs,
            app_role="lakebase-schema-upgrade-test-role",
            verify_outreach_integrity=True,
            allow_absent_managed_event_triggers=True,
            allow_absent_provider_schema=True,
        )


def _migrated(conn_kwargs: dict[str, str]) -> None:
    _apply(conn_kwargs, _SCHEMA[: _SCHEMA.index(_MARKER)])
    _apply(conn_kwargs, _SCHEMA)
    # The executable-hook preflight inventories the new index predicate here.
    _apply(conn_kwargs, _SCHEMA)


def _audit_id(key: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"mip-decision/{key}"))


def _seeded(conn_kwargs: dict[str, str]) -> dict[str, str]:
    """Migrate, insert the shared seeds; return audit_id -> seed key."""
    _migrated(conn_kwargs)
    with psycopg.connect(**conn_kwargs) as conn:
        for approval in seeds.APPROVALS:
            conn.execute(
                "INSERT INTO mip_app.approvals (approval_id, borrower_id, action, actor_email, offer_code, channel) "
                "VALUES (%(approval_id)s, %(borrower_id)s, %(action)s, %(actor_email)s, %(offer_code)s, %(channel)s)",
                approval,
            )
        for activation in seeds.ACTIVATIONS:
            conn.execute(
                "INSERT INTO mip_app.activation_outbox (activation_id, destination_key, entity_type, entity_id, "
                "borrower_id, approval_id, channel, status, request_id, created_by) VALUES (%(activation_id)s, "
                "'salesforce_crm', 'borrower', %(borrower_id)s, %(borrower_id)s, %(approval_id)s, 'email', "
                "'dry_run', 'act-req-0001', 'lo01@summit.example')",
                activation,
            )
        conn.execute(
            "INSERT INTO mip_app.approval_request_batches (batch_id, requested_by, request_key, "
            "request_intent_hash, note) VALUES (%s, %s, 'req-key-0001', %s, 'Seeded batch.')",
            (seeds.BATCH, seeds.MANAGER, "a" * 64),
        )
        for item in seeds.BATCH_ITEMS:
            conn.execute(
                "INSERT INTO mip_app.approval_request_items (batch_id, borrower_id) VALUES (%(batch_id)s, %(borrower_id)s)",
                item,
            )
        for assignment in seeds.DISTRIBUTION_ASSIGNMENTS:
            conn.execute(
                "INSERT INTO mip_app.lead_assignments (borrower_id, assigned_to_email, assigned_by, strategy, "
                "request_id, assignment_scope, released_at) VALUES (%(borrower_id)s, %(assigned_to_email)s, "
                "'sam.manager@summit.example', 'round_robin', %(request_id)s, 'distribution', now())",
                assignment,
            )
        for index, row in enumerate(seeds.AUDIT_ROWS):
            conn.execute(
                "INSERT INTO mip_app.action_audit (audit_id, event_type, actor_email, entity_type, entity_id, "
                "subject_clip, request_id, metadata, event_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)",
                (
                    _audit_id(row.key), row.event_type, row.actor_email, row.entity_type, row.entity_id,
                    row.subject_clip, row.request_id, json.dumps(row.metadata), seeds.event_at(index),
                ),
            )
    return {_audit_id(row.key): row.key for row in seeds.AUDIT_ROWS}


class _PgClient:
    """The two LakebaseClient read methods over one psycopg connection, dict rows."""

    def __init__(self, conn_kwargs: dict[str, str]) -> None:
        self.conn_kwargs = conn_kwargs
        self.statements: list[str] = []

    def fetchall(self, sql: str, params: dict[str, Any] | None = None, limit: int = 100) -> list[dict[str, Any]]:
        self.statements.append(sql)
        with psycopg.connect(**self.conn_kwargs, row_factory=dict_row) as conn:
            return [dict(row) for row in conn.execute(sql, params or {}).fetchmany(limit)]


def _sql_keys(conn_kwargs: dict[str, str], sql: str, params: dict[str, Any], keys: dict[str, str]) -> list[str]:
    with psycopg.connect(**conn_kwargs, row_factory=dict_row) as conn:
        return [keys[str(row["audit_id"])] for row in conn.execute(sql, params).fetchall()]


def test_the_index_applies_twice_and_is_recorded_once(conn_kwargs: dict[str, str]) -> None:
    _migrated(conn_kwargs)
    with psycopg.connect(**conn_kwargs, row_factory=dict_row) as conn:
        version = conn.execute(
            "SELECT count(*) AS n FROM mip_app.schema_migrations WHERE version = %s", (_VERSION,)
        ).fetchone()
        index = conn.execute(
            "SELECT indexdef FROM pg_indexes WHERE indexname = 'idx_action_audit_decision_entity'"
        ).fetchone()
    assert version is not None and version["n"] == 1
    assert index is not None
    definition = str(index["indexdef"])
    assert "(entity_id, audit_sequence DESC)" in definition
    for code in history.DECISION_HISTORY_EVENT_TYPES - {"LEAD_DISTRIBUTE"}:
        assert f"'{code}'" in definition
    assert "LEAD_DISTRIBUTE" not in definition and "->" not in definition


@pytest.mark.parametrize("borrower_id", [seeds.BORROWER, seeds.OTHER, seeds.PHONE_SHAPED])
def test_the_exact_service_sql_returns_the_positives_and_excludes_every_negative(
    conn_kwargs: dict[str, str], borrower_id: str
) -> None:
    keys = _seeded(conn_kwargs)
    expected = seeds.expected_keys(borrower_id)
    distributed = {row.key for row in seeds.AUDIT_ROWS if row.event_type == "LEAD_DISTRIBUTE"}

    client = _PgClient(conn_kwargs)
    resolved = client.fetchall(history.ENTITY_RESOLUTION_SQL, {"bid": borrower_id}, limit=600)
    entity_ids = [borrower_id, *(str(row["entity_id"]) for row in resolved)]
    # The SQL alone, before the Python belt sees a row.
    main = _sql_keys(conn_kwargs, history.DECISION_HISTORY_SQL, {"bid": borrower_id, "entity_ids": entity_ids}, keys)
    assert main == [key for key in expected if key not in distributed]
    runs = _sql_keys(conn_kwargs, history.DISTRIBUTE_HISTORY_SQL, {"bid": borrower_id}, keys)
    assert runs == [key for key in expected if key in distributed]

    result = history.list_borrower_decisions(
        client,  # type: ignore[arg-type]
        borrower_id,
        viewer=seeds.OWN,
        privileged=False,
        automation_identities=frozenset({seeds.AUTOMATION}),
    )
    assert [keys[item.audit_event_id] for item in result.items] == expected
    assert len(client.statements) <= 5 + 1  # the five service statements (+ the resolution read above)
    if borrower_id == seeds.BORROWER:
        by_key = {keys[item.audit_event_id]: item for item in result.items}
        assert by_key["distribute"].assigned_to_display == "Summit LO 01 (Loan officer)"
        assert by_key["assign"].actor_display == "Summit Sales Manager (Sales manager)"


def test_the_history_read_uses_the_partial_index_under_a_generic_plan(conn_kwargs: dict[str, str]) -> None:
    _seeded(conn_kwargs)
    prepared = (
        history.DECISION_HISTORY_SQL.replace("%(bid)s", "$1").replace("%(entity_ids)s", "$2")
    )
    assert "%(" not in prepared
    with psycopg.connect(**conn_kwargs, autocommit=True) as conn:
        # Bulk the table with decision rows for other borrowers and reads for
        # this one, so the plan is chosen on real selectivity.
        conn.execute(
            "INSERT INTO mip_app.action_audit (event_type, actor_email, entity_type, entity_id, metadata) "
            "SELECT (ARRAY['APPROVE','LEAD_ASSIGN','CALL_DISPOSITION'])[1 + g % 3], 'lo03@summit.example', "
            "'borrower', 'B-FILLER' || lpad(g::text, 7, '0'), '{}'::jsonb FROM generate_series(1, 4000) AS g"
        )
        conn.execute(
            "INSERT INTO mip_app.action_audit (event_type, actor_email, entity_type, entity_id, metadata) "
            "SELECT 'VIEW_BORROWER', 'lo03@summit.example', 'borrower', %s, '{}'::jsonb "
            "FROM generate_series(1, 2000)",
            (seeds.BORROWER,),
        )
        conn.execute("ANALYZE mip_app.action_audit")
        conn.execute("SET plan_cache_mode = force_generic_plan")
        conn.execute("SET enable_seqscan = off")
        conn.execute(f"PREPARE decision_history(text, text[]) AS {prepared}")
        plan = "\n".join(
            str(row[0])
            for row in conn.execute(
                psql.SQL("EXPLAIN EXECUTE decision_history({}, {})").format(
                    psql.Literal(seeds.BORROWER),
                    psql.Literal([seeds.BORROWER, seeds.APPROVAL, seeds.ACTIVATION, seeds.BATCH]),
                )
            ).fetchall()
        )
    assert "idx_action_audit_decision_entity" in plan, plan

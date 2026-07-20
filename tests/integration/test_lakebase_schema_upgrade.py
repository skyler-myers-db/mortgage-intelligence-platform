"""Real-Postgres contract for Lakebase fresh install, upgrade, and reapply."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql as psql

from jobs import lakebase_migrate

pytestmark = pytest.mark.integration

_SCHEMA = Path("lakebase/schema.sql").read_text(encoding="utf-8")
_SEED = Path("lakebase/seed_campaigns.sql").read_text(encoding="utf-8")
_CAMPAIGN_JSON_SHAPE_CONSTRAINTS = (
    "campaigns_criteria_reviewed_shape_chk",
    "campaigns_suppression_policy_reviewed_shape_chk",
    "campaigns_channel_cascade_reviewed_shape_chk",
    "campaigns_send_window_reviewed_shape_chk",
    "campaigns_holdout_reviewed_shape_chk",
    "campaigns_roi_assumptions_reviewed_shape_chk",
)


@pytest.fixture
def postgres_kwargs() -> Iterator[dict[str, str]]:
    dsn = os.environ.get("MIP_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("MIP_TEST_POSTGRES_DSN is not configured")

    kwargs = {"conninfo": dsn}
    with psycopg.connect(**kwargs, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DROP SCHEMA IF EXISTS mip_app CASCADE")
    try:
        yield kwargs
    finally:
        with psycopg.connect(**kwargs, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute("DROP SCHEMA IF EXISTS mip_app CASCADE")


def _apply_migration(conn_kwargs: dict[str, str]) -> None:
    pre_seed, post_seed = lakebase_migrate._split_schema_sql(_SCHEMA)
    lakebase_migrate._run_transaction(
        (pre_seed, _SEED, post_seed),
        conn_kwargs,
        app_role="lakebase-schema-upgrade-test-role",
        verify_outreach_integrity=True,
        allow_absent_managed_event_triggers=True,
        allow_absent_provider_schema=True,
    )


def _reviewed_trigger_keys(conn_kwargs: dict[str, str]) -> set[tuple[str, str, str]]:
    with psycopg.connect(**conn_kwargs) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT n.nspname, c.relname, t.tgname
            FROM pg_trigger t
            JOIN pg_class c ON c.oid = t.tgrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE NOT t.tgisinternal
              AND n.nspname = 'mip_app'
            ORDER BY n.nspname, c.relname, t.tgname
            """
        )
        return {(str(schema), str(table), str(trigger)) for schema, table, trigger in cur}


def _proof_rows(conn_kwargs: dict[str, str]) -> tuple[list[tuple[Any, ...]], ...]:
    with psycopg.connect(**conn_kwargs) as conn, conn.cursor() as cur:
        tables: list[list[tuple[Any, ...]]] = []
        for query in (
            """
            SELECT approval_id::text, borrower_id, campaign_id::text,
                   variant_name, channel, action, actor_email, rationale,
                   decision_response::text, audit_event_id::text
            FROM mip_app.approvals
            ORDER BY approval_id
            """,
            """
            SELECT generation_id::text, audit_event_id::text, borrower_id,
                   campaign_id::text, variant_name, channel, response_hash,
                   response_json::text
            FROM mip_app.generated_outreach_drafts
            ORDER BY generation_id
            """,
            """
            SELECT activation_id::text, approval_id::text, borrower_id,
                   campaign_id::text, channel, status, request_id
            FROM mip_app.activation_outbox
            ORDER BY activation_id
            """,
            """
            SELECT outcome_id::text, borrower_id, outcome_type, source_system,
                   source_record_ref, request_id, audit_event_id::text
            FROM mip_app.lead_outcomes
            ORDER BY outcome_id
            """,
        ):
            cur.execute(query)
            tables.append(cur.fetchall())
    return tuple(tables)


def _simulate_legacy_proof(conn_kwargs: dict[str, str], *, borrower_id: str) -> None:
    with psycopg.connect(**conn_kwargs) as conn, conn.cursor() as cur:
        cur.execute("DROP TRIGGER trg_approvals_finalize_only ON mip_app.approvals")
        cur.execute("DROP TRIGGER trg_approvals_no_remove ON mip_app.approvals")
        cur.execute(
            "DROP TRIGGER trg_generated_outreach_drafts_immutable "
            "ON mip_app.generated_outreach_drafts"
        )
        for constraint in (
            "approvals_borrower_id_format_chk",
            "approvals_channel_chk",
            "approvals_channel_required_chk",
            "approvals_campaign_variant_pair_chk",
            "approvals_campaign_variant_channel_fkey",
        ):
            cur.execute(f"ALTER TABLE mip_app.approvals DROP CONSTRAINT {constraint}")
        for constraint in (
            "generated_outreach_campaign_variant_pair_chk",
            "generated_outreach_campaign_variant_channel_fkey",
        ):
            cur.execute(
                "ALTER TABLE mip_app.generated_outreach_drafts " f"DROP CONSTRAINT {constraint}"
            )
        cur.execute(
            "ALTER TABLE mip_app.lead_outcomes "
            "DROP CONSTRAINT ck_lead_outcomes_source_record_ref"
        )
        cur.execute(
            "ALTER TABLE mip_app.generated_outreach_drafts "
            "ALTER COLUMN campaign_id TYPE TEXT USING campaign_id::text"
        )
        cur.execute(
            """
            UPDATE mip_app.approvals
            SET borrower_id = %s, variant_name = NULL, channel = NULL
            WHERE approval_id = '44444444-4444-4444-8444-444444444441'
            """,
            (borrower_id,),
        )
        cur.execute(
            """
            INSERT INTO mip_app.approvals (
                approval_id, campaign_id, variant_name, channel, borrower_id,
                action, actor_email, request_id
            ) VALUES (
                '44444444-4444-4444-8444-444444444499',
                NULL, NULL, NULL, 'B-0000000000099', 'hold',
                'legacy-upgrade@test.example', 'legacy-campaignless-approval'
            )
            """
        )
        cur.execute(
            """
            INSERT INTO mip_app.lead_outcomes (
                outcome_id, borrower_id, outcome_type, source_system,
                source_record_ref, created_by, payload_json
            ) VALUES (
                '99999999-9999-4999-8999-999999999991',
                'B-0000000000099', 'application_submitted', 'manual_import',
                'legacy-external-record-123', 'legacy-upgrade@test.example',
                '{}'::jsonb
            )
            """
        )
        cur.execute(
            """
            INSERT INTO mip_app.action_audit (
                audit_id, event_type, actor_email, entity_type, entity_id, request_id
            ) VALUES (
                '66666666-6666-4666-8666-666666666661', 'OUTREACH_DRAFT',
                'legacy-upgrade@test.example', 'generation',
                '77777777-7777-4777-8777-777777777771', 'legacy-generation'
            )
            """
        )
        cur.execute(
            """
            INSERT INTO mip_app.generated_outreach_drafts (
                generation_id, audit_event_id, actor_email, borrower_id,
                campaign_id, variant_name, channel, offer_code,
                generation_mode, response_hash, response_json
            ) VALUES (
                '77777777-7777-4777-8777-777777777771',
                '66666666-6666-4666-8666-666666666661',
                'legacy-upgrade@test.example', 'B-0CPWBTJMAPFY2',
                '11111111-1111-4111-8111-111111111111', NULL, 'email',
                'refi', 'governed_fallback', repeat('a', 64),
                '{"subject":"Legacy proof","body":"Preserve exactly"}'::jsonb
            )
            """
        )
        cur.execute(
            """
            INSERT INTO mip_app.activation_outbox (
                activation_id, destination_key, entity_type, entity_id,
                borrower_id, campaign_id, approval_id, offer_code, channel,
                status, request_id, created_by
            ) VALUES (
                '88888888-8888-4888-8888-888888888881', 'salesforce_crm',
                'borrower', 'B-0CPWBTJMAPFY2', 'B-0CPWBTJMAPFY2',
                '11111111-1111-4111-8111-111111111111',
                '44444444-4444-4444-8444-444444444441', 'refi', 'email',
                'dry_run', 'legacy-outbox', 'legacy-upgrade@test.example'
            )
            """
        )


def test_fresh_upgrade_and_recurring_apply_preserve_proof(
    postgres_kwargs: dict[str, str],
) -> None:
    _apply_migration(postgres_kwargs)
    _simulate_legacy_proof(postgres_kwargs, borrower_id="B-48291")

    _apply_migration(postgres_kwargs)

    with psycopg.connect(**postgres_kwargs) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT borrower_id, variant_name, channel
            FROM mip_app.approvals
            WHERE approval_id = '44444444-4444-4444-8444-444444444441'
            """
        )
        assert cur.fetchone() == ("B-0CPWBTJMAPFY2", "Benefit-led", "email")
        cur.execute(
            """
            SELECT campaign_id::text, variant_name, channel
            FROM mip_app.approvals
            WHERE approval_id = '44444444-4444-4444-8444-444444444499'
            """
        )
        assert cur.fetchone() == (None, None, None)
        cur.execute(
            """
            SELECT pg_typeof(campaign_id)::text, variant_name, channel
            FROM mip_app.generated_outreach_drafts
            WHERE generation_id = '77777777-7777-4777-8777-777777777771'
            """
        )
        assert cur.fetchone() == ("uuid", "Benefit-led", "email")
        cur.execute(
            """
            SELECT source_record_ref
            FROM mip_app.lead_outcomes
            WHERE outcome_id = '99999999-9999-4999-8999-999999999991'
            """
        )
        assert cur.fetchone() == ("legacy-external-record-123",)

    upgraded_rows = _proof_rows(postgres_kwargs)
    _apply_migration(postgres_kwargs)
    assert _proof_rows(postgres_kwargs) == upgraded_rows


def test_failed_migration_rolls_back_reviewed_trigger_quarantine(
    postgres_kwargs: dict[str, str],
) -> None:
    _apply_migration(postgres_kwargs)
    expected = set(lakebase_migrate._APP_TRIGGER_CONTRACT)
    assert _reviewed_trigger_keys(postgres_kwargs) == expected

    with pytest.raises(psycopg.Error, match="forced migration rollback"):
        lakebase_migrate._run_transaction(
            (
                """
                DO $mip_forced_rollback$
                BEGIN
                    RAISE EXCEPTION 'forced migration rollback';
                END
                $mip_forced_rollback$;
                """,
            ),
            postgres_kwargs,
            app_role="lakebase-schema-upgrade-test-role",
            allow_absent_managed_event_triggers=True,
            allow_absent_provider_schema=True,
        )

    assert _reviewed_trigger_keys(postgres_kwargs) == expected


def test_campaign_json_checks_preserve_existing_rows_and_reject_new_poison(
    postgres_kwargs: dict[str, str],
) -> None:
    _apply_migration(postgres_kwargs)
    legacy_campaign_id = uuid4()
    poisoned_fields = (
        '{"legacy_unreviewed":"preserved"}',
        '{"legacy_unreviewed":true}',
        '[{"legacy_unreviewed":true}]',
        '{"legacy_unreviewed":"preserved"}',
        '{"legacy_unreviewed":true}',
        '{"legacy_unreviewed":true}',
    )
    with psycopg.connect(**postgres_kwargs) as conn, conn.cursor() as cur:
        for constraint in _CAMPAIGN_JSON_SHAPE_CONSTRAINTS:
            cur.execute(f"ALTER TABLE mip_app.campaigns DROP CONSTRAINT {constraint}")
        cur.execute("DROP TRIGGER trg_campaigns_json_contract_enforcement ON mip_app.campaigns")
        cur.execute("ALTER TABLE mip_app.campaigns DROP COLUMN json_contract_version")
        cur.execute(
            """
            INSERT INTO mip_app.campaigns (
                campaign_id, name, owner_email, criteria, suppression_policy,
                channel_cascade, send_window, holdout, roi_assumptions
            ) VALUES (%s, 'Legacy JSON compatibility', 'legacy-upgrade@test.example',
                      %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb)
            """,
            (legacy_campaign_id, *poisoned_fields),
        )

    _apply_migration(postgres_kwargs)

    with psycopg.connect(**postgres_kwargs) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT conname, convalidated
            FROM pg_constraint
            WHERE conrelid = 'mip_app.campaigns'::regclass
              AND conname = ANY(%s::text[])
            ORDER BY conname
            """,
            (list(_CAMPAIGN_JSON_SHAPE_CONSTRAINTS),),
        )
        assert dict(cur.fetchall()) == {
            constraint: False for constraint in _CAMPAIGN_JSON_SHAPE_CONSTRAINTS
        }
        cur.execute(
            "SELECT criteria->>'legacy_unreviewed', json_contract_version "
            "FROM mip_app.campaigns "
            "WHERE campaign_id = %s",
            (legacy_campaign_id,),
        )
        assert cur.fetchone() == ("preserved", 0)
        status_request_id = f"legacy-status-{uuid4()}"
        cur.execute(
            """
            WITH updated_campaign AS (
                UPDATE mip_app.campaigns
                SET status = 'pending_review', updated_at = now()
                WHERE campaign_id = %s
                RETURNING campaign_id::text, status
            ), inserted_audit AS (
                INSERT INTO mip_app.action_audit (
                    event_type, actor_email, entity_type, entity_id,
                    request_id, metadata
                )
                SELECT 'CAMPAIGN_STATUS_UPDATE', 'legacy-upgrade@test.example',
                       'campaign', campaign_id, %s,
                       '{"status":"pending_review"}'::jsonb
                FROM updated_campaign
                RETURNING audit_id
            )
            SELECT updated_campaign.status, inserted_audit.audit_id IS NOT NULL
            FROM updated_campaign
            JOIN inserted_audit ON TRUE
            """,
            (legacy_campaign_id, status_request_id),
        )
        assert cur.fetchone() == ("pending_review", True)
        conn.commit()

        cur.execute(
            """
            SELECT c.status, c.criteria->>'legacy_unreviewed',
                   c.json_contract_version, COUNT(a.audit_id)
            FROM mip_app.campaigns AS c
            LEFT JOIN mip_app.action_audit AS a
              ON a.entity_type = 'campaign'
             AND a.entity_id = c.campaign_id::text
             AND a.request_id = %s
            WHERE c.campaign_id = %s
            GROUP BY c.campaign_id
            """,
            (status_request_id, legacy_campaign_id),
        )
        assert cur.fetchone() == ("pending_review", "preserved", 0, 1)
        with pytest.raises(psycopg.errors.CheckViolation):
            cur.execute(
                "UPDATE mip_app.campaigns "
                "SET criteria = '{\"unreviewed_change\":true}'::jsonb "
                "WHERE campaign_id = %s",
                (legacy_campaign_id,),
            )
        conn.rollback()

        with pytest.raises(psycopg.errors.CheckViolation):
            cur.execute(
                """
                INSERT INTO mip_app.campaigns (
                    name, owner_email, criteria, suppression_policy,
                    channel_cascade, send_window, holdout, roi_assumptions
                ) VALUES (
                    'New poisoned JSON', 'new-poison@test.example',
                    %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb
                )
                """,
                poisoned_fields,
            )
        conn.rollback()


def test_ai_gateway_proof_timestamp_trigger_rejects_unclaimable_clock_skew(
    postgres_kwargs: dict[str, str],
) -> None:
    _apply_migration(postgres_kwargs)

    sha = "a" * 40
    inference_table = "system.serving.endpoint_usage"
    with psycopg.connect(**postgres_kwargs, autocommit=True) as conn, conn.cursor() as cur:
        with pytest.raises(psycopg.Error) as future_error:
            cur.execute(
                """
                INSERT INTO mip_app.ai_gateway_proof_ledger (
                    git_sha, client_request_id, endpoint_name,
                    inference_table, sent_at, status
                ) VALUES (
                    %s, %s, 'mip-supervisor', %s,
                    clock_timestamp() + INTERVAL '6 minutes', 'pending'
                )
                """,
                (sha, f"mip-capability-{sha}-{'1' * 16}", inference_table),
            )
        assert future_error.value.sqlstate == "22007"

        with pytest.raises(psycopg.Error) as direct_verified_error:
            cur.execute(
                """
                INSERT INTO mip_app.ai_gateway_proof_ledger (
                    git_sha, client_request_id, endpoint_name,
                    inference_table, sent_at, verified_at,
                    verify_latency_s, status
                ) VALUES (
                    %s, %s, 'mip-supervisor', %s,
                    clock_timestamp(), clock_timestamp(), 0, 'verified'
                )
                """,
                (sha, f"mip-capability-{sha}-{'2' * 16}", inference_table),
            )
        assert direct_verified_error.value.sqlstate == "42501"

        chronology_request_id = f"mip-capability-{sha}-{'4' * 16}"
        cur.execute(
            """
            INSERT INTO mip_app.ai_gateway_proof_ledger (
                git_sha, client_request_id, endpoint_name,
                inference_table, sent_at, status
            ) VALUES (
                %s, %s, 'mip-supervisor', %s, clock_timestamp(), 'pending'
            )
            """,
            (sha, chronology_request_id, inference_table),
        )
        with pytest.raises(psycopg.Error) as chronology_error:
            cur.execute(
                """
                UPDATE mip_app.ai_gateway_proof_ledger
                SET verified_at = sent_at - INTERVAL '6 minutes',
                    verify_latency_s = 0,
                    status = 'verified',
                    attestation_alg = 'ed25519-v1',
                    attestation_key_id = '0123456789abcdef',
                    attestation_signature = repeat('A', 86)
                WHERE client_request_id = %s
                """,
                (chronology_request_id,),
            )
        assert chronology_error.value.sqlstate == "22007"

        quarantined_request_id = f"mip-capability-{sha}-{'3' * 16}"
        cur.execute(
            """
            INSERT INTO mip_app.ai_gateway_proof_ledger (
                git_sha, client_request_id, endpoint_name,
                inference_table, sent_at, status
            ) VALUES (
                %s, %s, 'mip-supervisor', %s,
                clock_timestamp() + INTERVAL '6 minutes', 'failed'
            )
            RETURNING status
            """,
            (sha, quarantined_request_id, inference_table),
        )
        assert cur.fetchone() == ("failed",)


def test_unmapped_legacy_approval_fails_without_deleting_state(
    postgres_kwargs: dict[str, str],
) -> None:
    _apply_migration(postgres_kwargs)
    _simulate_legacy_proof(postgres_kwargs, borrower_id="B-UNKNOWN")
    before = _proof_rows(postgres_kwargs)

    with pytest.raises(psycopg.errors.CheckViolation):
        _apply_migration(postgres_kwargs)

    assert _proof_rows(postgres_kwargs) == before


def test_real_postgres_rejects_proof_rewrites_and_raw_source_references(
    postgres_kwargs: dict[str, str],
) -> None:
    _apply_migration(postgres_kwargs)

    with psycopg.connect(**postgres_kwargs) as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO mip_app.action_audit (
                audit_id, event_type, actor_email, entity_type, entity_id, request_id
            ) VALUES
                ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1', 'CALL_DISPOSITION',
                 'schema-probe@test.example', 'call_disposition',
                 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb1', 'probe-call-audit'),
                ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa2', 'LEAD_OUTCOME',
                 'schema-probe@test.example', 'lead_outcome',
                 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb2', 'probe-outcome-audit'),
                ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa3', 'GROWTH_AGENT_RUN',
                 'schema-probe@test.example', 'growth_agent_run',
                 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb3', 'probe-growth-audit')
            """
        )
        cur.execute(
            """
            INSERT INTO mip_app.call_dispositions (
                disposition_id, borrower_id, lo_email, outcome, request_id
            ) VALUES (
                'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb1', 'B-0000000000001',
                'lo01@summit.example', 'connected', 'probe-call'
            )
            """
        )
        cur.execute(
            """
            INSERT INTO mip_app.lead_outcomes (
                outcome_id, borrower_id, outcome_type, source_system,
                source_record_ref, created_by, payload_json
            ) VALUES (
                'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb2', 'B-0000000000002',
                'application_submitted', 'manual_import',
                'auto-0123456789abcdef0123456789abcdef',
                'schema-probe@test.example', '{}'::jsonb
            )
            """
        )
        cur.execute(
            """
            INSERT INTO mip_app.growth_agent_runs (
                run_id, actor_email, request_id, workflow_id,
                workflow_title, route, source_assets, agent_evidence
            ) VALUES (
                'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb3',
                'schema-probe@test.example', 'probe-growth',
                'daily_refi_brief', 'Schema probe', '/lead-queue',
                ARRAY['mip.gold.lead_ranked'], '{"original":true}'::jsonb
            )
            """
        )
        for table, id_column, row_id, audit_id in (
            (
                "call_dispositions",
                "disposition_id",
                "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb1",
                "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1",
            ),
            (
                "lead_outcomes",
                "outcome_id",
                "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb2",
                "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa2",
            ),
            (
                "growth_agent_runs",
                "run_id",
                "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb3",
                "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa3",
            ),
        ):
            cur.execute(
                f"UPDATE mip_app.{table} SET audit_event_id = %s " f"WHERE {id_column} = %s",
                (audit_id, row_id),
            )

        forbidden_statements = (
            (
                "call_rewrite",
                "UPDATE mip_app.call_dispositions SET notes = 'changed' "
                "WHERE disposition_id = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb1'",
                "42501",
            ),
            (
                "outcome_rewrite",
                "UPDATE mip_app.lead_outcomes SET payload_json = '{\"changed\":true}' "
                "WHERE outcome_id = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb2'",
                "42501",
            ),
            (
                "growth_rewrite",
                "UPDATE mip_app.growth_agent_runs SET agent_evidence = '{\"changed\":true}' "
                "WHERE run_id = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb3'",
                "42501",
            ),
            (
                "raw_source_ref",
                """
                INSERT INTO mip_app.lead_outcomes (
                    borrower_id, outcome_type, source_system,
                    source_record_ref, created_by, payload_json
                ) VALUES (
                    'B-0000000000003', 'application_submitted', 'manual_import',
                    'raw-customer-record-id', 'schema-probe@test.example', '{}'::jsonb
                )
                """,
                "23514",
            ),
            (
                "call_delete",
                "DELETE FROM mip_app.call_dispositions WHERE "
                "disposition_id = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb1'",
                "42501",
            ),
            (
                "outcome_delete",
                "DELETE FROM mip_app.lead_outcomes WHERE "
                "outcome_id = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb2'",
                "42501",
            ),
            (
                "growth_delete",
                "DELETE FROM mip_app.growth_agent_runs WHERE "
                "run_id = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb3'",
                "42501",
            ),
            ("call_truncate", "TRUNCATE mip_app.call_dispositions", "42501"),
            ("outcome_truncate", "TRUNCATE mip_app.lead_outcomes", "42501"),
            (
                "growth_truncate",
                "TRUNCATE mip_app.growth_agent_runs CASCADE",
                "42501",
            ),
        )
        for savepoint, statement, sqlstate in forbidden_statements:
            lakebase_migrate._expect_database_rejection(
                cur,
                savepoint=f"probe_{savepoint}",
                statement=statement,
                expected_sqlstates=(sqlstate,),
            )


def test_real_postgres_isolates_app_and_verifier_acl(
    postgres_kwargs: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _apply_migration(postgres_kwargs)
    suffix = uuid4().hex[:12]
    app_role = f"mip_test_app_{suffix}"
    verifier_role = f"mip_test_verifier_{suffix}"

    with psycopg.connect(**postgres_kwargs, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            psql.SQL("CREATE ROLE {} LOGIN NOREPLICATION INHERIT").format(
                psql.Identifier(app_role)
            )
        )
        cur.execute(
            psql.SQL("CREATE ROLE {} LOGIN NOREPLICATION INHERIT").format(
                psql.Identifier(verifier_role)
            )
        )

    monkeypatch.setattr(lakebase_migrate, "_resolve_app_role", lambda: app_role)
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("MIP_AI_GATEWAY_VERIFIER_CLIENT_ID", verifier_role)
    try:
        lakebase_migrate._apply_app_role_grants(
            postgres_kwargs,
            role_wait_timeout_s=0,
            role_wait_interval_s=1,
            allow_absent_managed_event_triggers=True,
            allow_absent_provider_schema=True,
        )

        with psycopg.connect(**postgres_kwargs) as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    has_table_privilege(%s, 'mip_app.ai_gateway_proof_ledger', 'SELECT'),
                    has_table_privilege(%s, 'mip_app.ai_gateway_proof_ledger', 'INSERT'),
                    has_table_privilege(%s, 'mip_app.ai_gateway_proof_ledger', 'UPDATE'),
                    has_table_privilege(%s, 'mip_app.campaigns', 'SELECT')
                """,
                (app_role, app_role, app_role, app_role),
            )
            assert cur.fetchone() == (True, False, False, True)
            cur.execute(
                """
                SELECT
                    has_table_privilege(%s, 'mip_app.ai_gateway_proof_ledger', 'SELECT'),
                    has_table_privilege(%s, 'mip_app.ai_gateway_proof_ledger', 'INSERT'),
                    has_table_privilege(%s, 'mip_app.ai_gateway_proof_ledger', 'UPDATE'),
                    has_table_privilege(%s, 'mip_app.campaigns', 'SELECT'),
                    has_sequence_privilege(
                        %s,
                        'mip_app.action_audit_audit_sequence_seq',
                        'USAGE'
                    )
                """,
                (
                    verifier_role,
                    verifier_role,
                    verifier_role,
                    verifier_role,
                    verifier_role,
                ),
            )
            assert cur.fetchone() == (True, True, True, False, False)
    finally:
        with psycopg.connect(**postgres_kwargs, autocommit=True) as conn, conn.cursor() as cur:
            for role in (app_role, verifier_role):
                role_identifier = psql.Identifier(role)
                cur.execute(psql.SQL("DROP OWNED BY {}").format(role_identifier))
                cur.execute(psql.SQL("DROP ROLE {}").format(role_identifier))

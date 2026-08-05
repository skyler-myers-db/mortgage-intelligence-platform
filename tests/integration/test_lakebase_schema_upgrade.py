"""Real-Postgres contract for Lakebase fresh install, upgrade, and reapply."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from time import sleep
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql as psql

from backend.schemas.portfolio import HouseholdDedupConfig
from backend.services.campaign_treatment import (
    _CAMPAIGN_RESERVE_SQL,
    CampaignTreatmentCoordinator,
    CampaignTreatmentCreateSpec,
)
from jobs import lakebase_migrate
from jobs.lakebase_migration_integrity import _campaign_decision_intent

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


def _reviewed_treatment_response(
    name: str,
    *,
    candidate_count: int = 1,
    selected_primary_count: int = 1,
    treatment_count: int = 1,
) -> tuple[str, str]:
    household_summary = {
        "enabled": False,
        "candidate_borrower_count": candidate_count,
        "selected_primary_count": selected_primary_count,
        "suppressed_co_owner_count": candidate_count - selected_primary_count,
        "household_count": selected_primary_count,
        "owner_link_household_count": 0,
        "mailing_address_household_count": 0,
        "singleton_household_count": selected_primary_count,
        "primary_contact_strategy": "highest_opportunity_eligible",
        "source_assets": ["mip.gold.household_rollup", "mip.gold.borrower_360"],
    }
    creation_response = {
        "name": name,
        "marketable_population": treatment_count,
        "campaign_build_limit": 10_000,
        "campaign_build_eligible": True,
        "household_summary": household_summary,
    }
    return (
        json.dumps(household_summary, sort_keys=True),
        json.dumps(creation_response, sort_keys=True),
    )


def _ready_campaign(cur: Any) -> tuple[object, str, str]:
    campaign_id = uuid4()
    owner = "campaign-serialization@test.example"
    borrower_id = f"B-{uuid4().int % 10**13:013d}"
    household_summary, creation_response = _reviewed_treatment_response(
        "Serialization proof"
    )
    cur.execute(
        """
        INSERT INTO mip_app.campaigns (
            campaign_id, name, owner_email, status, criteria,
            idempotency_key, request_payload_hash,
            treatment_state, treatment_materialization_id,
            treatment_algorithm_version, treatment_contract_fingerprint,
            treatment_build_lease_until
        ) VALUES (%s, 'Serialization proof', %s, 'draft', '{}'::jsonb,
                  %s, %s, 'building', %s, 'campaign-treatment-v2', %s,
                  now() + interval '5 minutes')
        """,
        (campaign_id, owner, str(campaign_id), "7" * 64, campaign_id, "3" * 64),
    )
    cur.execute(
        """
        INSERT INTO mip_app.campaign_message_variants (
            campaign_id, variant_name, channel, subject, body,
            generation_mode, generator_label, provenance_key_id,
            provenance_issued_at, provenance_expires_at,
            provenance_copy_hash, provenance_criteria_fingerprint,
            provenance_token_digest
        ) VALUES (%s, 'Primary', 'email', 'Proof', 'Reviewed proof.',
                  'reviewed_fallback', 'Serialization proof', 'v1',
                  now(), now() + interval '1 hour', %s, %s, %s)
        """,
        (campaign_id, "0" * 64, "1" * 64, "2" * 64),
    )
    cur.execute(
        """
        UPDATE mip_app.campaigns
        SET status = 'approved', treatment_state = 'ready',
            treatment_fingerprint = %s, treatment_source_snapshot_id = %s,
            treatment_delta_version = 0, treatment_assignment_digest = %s,
            treatment_candidate_count = 1, treatment_selected_primary_count = 1,
            treatment_count = 1, treatment_holdout_count = 0,
            treatment_materialized_at = now(), treatment_build_lease_until = NULL,
            household_summary = %s::jsonb,
            creation_response = %s::jsonb
        WHERE campaign_id = %s
        """,
        (
            "4" * 64,
            "5" * 64,
            "6" * 64,
            household_summary,
            creation_response,
            campaign_id,
        ),
    )
    return campaign_id, owner, borrower_id


def _insert_campaign_decision(
    cur: Any,
    *,
    campaign_id: object,
    owner: str,
    borrower_id: str,
    approval_id: object,
    audit_id: object,
) -> None:
    request_id = str(uuid4())
    intent, intent_hash = _campaign_decision_intent(
        action="approve",
        actor=owner,
        borrower_id=borrower_id,
        campaign_id=campaign_id,
        variant_name="Primary",
        channel="email",
        owner_email=owner,
        treatment_fingerprint="4" * 64,
    )
    cur.execute(
        """
        INSERT INTO mip_app.action_audit (
            audit_id, event_type, actor_email, entity_type, entity_id,
            request_id, metadata
        ) VALUES (%s, 'OUTREACH_APPROVAL', %s, 'approval', %s, %s, '{}'::jsonb)
        """,
        (audit_id, owner, str(approval_id), request_id),
    )
    cur.execute(
        """
        INSERT INTO mip_app.approvals (
            approval_id, campaign_id, variant_name, channel, borrower_id,
            action, actor_email, request_id, decision_intent, decision_payload_hash
        ) VALUES (%s, %s, 'Primary', 'email', %s, 'approve', %s, %s, %s, %s)
        """,
        (approval_id, campaign_id, borrower_id, owner, request_id, intent, intent_hash),
    )
    cur.execute(
        """
        UPDATE mip_app.approvals
        SET decision_response = '{"approved":true}'::jsonb, audit_event_id = %s
        WHERE approval_id = %s
        """,
        (audit_id, approval_id),
    )


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


def test_default_campaign_reservation_uses_sql_null_for_absent_optional_json(
    postgres_kwargs: dict[str, str],
) -> None:
    _apply_migration(postgres_kwargs)
    materialization_id = uuid4()
    request_id = str(uuid4())
    spec = CampaignTreatmentCreateSpec(
        name="Default optional JSON proof",
        owner_email="campaign-default-reserve@test.example",
        idempotency_key=request_id,
        request_payload_hash="a" * 64,
        criteria={"marketing_eligibility": "Eligible only"},
        suppression_policy={"default": "eligible_only", "frequency_cap_days": 60},
        household_dedup=HouseholdDedupConfig(),
    )
    params = CampaignTreatmentCoordinator._reserve_params(
        spec,
        materialization_id=str(materialization_id),
        contract_fingerprint="b" * 64,
    )

    assert params["holdout"] is None
    assert params["roi_assumptions"] is None
    with psycopg.connect(**postgres_kwargs) as conn, conn.cursor() as cur:
        cur.execute(_CAMPAIGN_RESERVE_SQL, params)
        row = cur.fetchone()
        assert row is not None
        campaign_id = row[0]
        assert row[1:] == (str(materialization_id), "a" * 64, "building")
        cur.execute(
            """
            SELECT holdout IS NULL, roi_assumptions IS NULL,
                   treatment_build_lease_until > now()
            FROM mip_app.campaigns
            WHERE campaign_id = %s
            """,
            (campaign_id,),
        )
        assert cur.fetchone() == (True, True, True)


def test_upgrade_rejects_preexisting_building_with_finalized_proof(
    postgres_kwargs: dict[str, str],
) -> None:
    _apply_migration(postgres_kwargs)
    campaign_id = uuid4()
    with psycopg.connect(**postgres_kwargs) as conn, conn.cursor() as cur:
        cur.execute("DROP TRIGGER trg_campaigns_treatment_boundary ON mip_app.campaigns")
        cur.execute(
            "ALTER TABLE mip_app.campaigns "
            "DROP CONSTRAINT campaigns_nonready_treatment_proof_empty_chk"
        )
        cur.execute(
            """
            INSERT INTO mip_app.campaigns (
                campaign_id, name, owner_email, criteria,
                idempotency_key, request_payload_hash, treatment_state,
                treatment_materialization_id, treatment_algorithm_version,
                treatment_contract_fingerprint, treatment_fingerprint,
                treatment_source_snapshot_id, treatment_delta_version,
                treatment_assignment_digest, treatment_candidate_count,
                treatment_selected_primary_count, treatment_count,
                treatment_holdout_count, treatment_materialized_at,
                treatment_build_lease_until, household_summary, creation_response
            ) VALUES (
                %s, 'Preexisting poisoned building', 'poisoned-upgrade@test.example',
                '{}'::jsonb, %s, %s, 'building', %s, 'campaign-treatment-v2',
                %s, %s, %s, 0, %s, 1, 1, 1, 0, now(),
                now() + interval '5 minutes', '{"x":1}'::jsonb, '{"y":1}'::jsonb
            )
            """,
            (
                campaign_id,
                str(campaign_id),
                "1" * 64,
                campaign_id,
                "2" * 64,
                "3" * 64,
                "4" * 64,
                "5" * 64,
            ),
        )

    with pytest.raises(psycopg.errors.CheckViolation):
        _apply_migration(postgres_kwargs)

    with psycopg.connect(**postgres_kwargs) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT treatment_state, treatment_fingerprint,
                   household_summary, creation_response
            FROM mip_app.campaigns
            WHERE campaign_id = %s
            """,
            (campaign_id,),
        )
        assert cur.fetchone() == ("building", "3" * 64, {"x": 1}, {"y": 1})
        cur.execute(
            """
            SELECT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conrelid = 'mip_app.campaigns'::regclass
                  AND conname = 'campaigns_nonready_treatment_proof_empty_chk'
            )
            """
        )
        assert cur.fetchone() == (False,)


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


def test_campaign_decision_and_archive_have_two_forced_serial_orders(
    postgres_kwargs: dict[str, str],
) -> None:
    _apply_migration(postgres_kwargs)

    with psycopg.connect(**postgres_kwargs) as setup_conn, setup_conn.cursor() as cur:
        archived_campaign, owner, borrower_id = _ready_campaign(cur)
    with psycopg.connect(**postgres_kwargs) as archive_conn, archive_conn.cursor() as cur:
        cur.execute(
            "UPDATE mip_app.campaigns SET status = 'archived' WHERE campaign_id = %s",
            (archived_campaign,),
        )
    rejected_approval, rejected_audit = uuid4(), uuid4()
    rejected_conn = psycopg.connect(**postgres_kwargs)
    try:
        with (
            rejected_conn.cursor() as cur,
            pytest.raises(psycopg.errors.CheckViolation),
        ):
            _insert_campaign_decision(
                cur,
                campaign_id=archived_campaign,
                owner=owner,
                borrower_id=borrower_id,
                approval_id=rejected_approval,
                audit_id=rejected_audit,
            )
        rejected_conn.rollback()
    finally:
        rejected_conn.close()
    with psycopg.connect(**postgres_kwargs) as verify_conn, verify_conn.cursor() as cur:
        cur.execute(
            "SELECT EXISTS (SELECT 1 FROM mip_app.approvals WHERE approval_id = %s), "
            "EXISTS (SELECT 1 FROM mip_app.action_audit WHERE audit_id = %s)",
            (rejected_approval, rejected_audit),
        )
        assert cur.fetchone() == (False, False)

    with psycopg.connect(**postgres_kwargs) as setup_conn, setup_conn.cursor() as cur:
        approved_campaign, owner, borrower_id = _ready_campaign(cur)
    approval_id, audit_id = uuid4(), uuid4()
    decision_conn = psycopg.connect(**postgres_kwargs)
    archive_started = Event()
    archive_pid: list[int] = []

    def archive_after_decision_lock() -> int:
        with psycopg.connect(**postgres_kwargs) as conn, conn.cursor() as cur:
            cur.execute("SELECT pg_backend_pid()")
            pid = int(cur.fetchone()[0])
            archive_pid.append(pid)
            archive_started.set()
            cur.execute(
                "UPDATE mip_app.campaigns SET status = 'archived' WHERE campaign_id = %s",
                (approved_campaign,),
            )
            return pid

    try:
        with decision_conn.cursor() as cur:
            _insert_campaign_decision(
                cur,
                campaign_id=approved_campaign,
                owner=owner,
                borrower_id=borrower_id,
                approval_id=approval_id,
                audit_id=audit_id,
            )
        with ThreadPoolExecutor(max_workers=1) as executor:
            archive_future = executor.submit(archive_after_decision_lock)
            blocked = False
            try:
                assert archive_started.wait(timeout=5)
                with psycopg.connect(**postgres_kwargs) as observer, observer.cursor() as cur:
                    for _attempt in range(40):
                        cur.execute(
                            "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                            "WHERE pid = %s AND wait_event_type = 'Lock')",
                            (archive_pid[0],),
                        )
                        if cur.fetchone() == (True,):
                            blocked = True
                            break
                        cur.execute("SELECT pg_sleep(0.05)")
            finally:
                decision_conn.commit()
            archive_future.result(timeout=5)
            assert blocked, "campaign archive did not block on the approval share lock"
    finally:
        decision_conn.close()

    with psycopg.connect(**postgres_kwargs) as verify_conn, verify_conn.cursor() as cur:
        cur.execute(
            "SELECT c.status, a.audit_event_id = %s, e.audit_id = %s "
            "FROM mip_app.campaigns c "
            "JOIN mip_app.approvals a ON a.campaign_id = c.campaign_id "
            "JOIN mip_app.action_audit e ON e.audit_id = a.audit_event_id "
            "WHERE c.campaign_id = %s AND a.approval_id = %s",
            (audit_id, audit_id, approved_campaign, approval_id),
        )
        assert cur.fetchone() == ("archived", True, True)


def test_campaign_treatment_state_machine_freezes_ready_proof(
    postgres_kwargs: dict[str, str],
) -> None:
    _apply_migration(postgres_kwargs)

    with psycopg.connect(**postgres_kwargs) as conn, conn.cursor() as cur:
        ready_campaign, owner, _borrower_id = _ready_campaign(cur)
        transferred_owner = "campaign-transfer@test.example"
        cur.execute(
            """
            UPDATE mip_app.campaigns
            SET status = 'archived', owner_email = %s, updated_at = now()
            WHERE campaign_id = %s
            RETURNING status, owner_email, treatment_state
            """,
            (transferred_owner, ready_campaign),
        )
        assert cur.fetchone() == ("archived", transferred_owner, "ready")

        forbidden_ready_mutations = (
            (
                "ready_request_hash",
                "UPDATE mip_app.campaigns SET request_payload_hash = %s "
                "WHERE campaign_id = %s",
                ("7" * 64, ready_campaign),
                ("55000",),
            ),
            (
                "ready_creation_response",
                "UPDATE mip_app.campaigns SET creation_response = %s::jsonb "
                "WHERE campaign_id = %s",
                ('{"marketable_population":2}', ready_campaign),
                ("55000",),
            ),
            (
                "ready_household_summary",
                "UPDATE mip_app.campaigns SET household_summary = %s::jsonb "
                "WHERE campaign_id = %s",
                ('{"selected_primary_count":2}', ready_campaign),
                ("55000",),
            ),
            (
                "ready_channel_cascade",
                "UPDATE mip_app.campaigns SET channel_cascade = %s::jsonb "
                "WHERE campaign_id = %s",
                ('[{"step":1,"channel":"email"}]', ready_campaign),
                ("55000",),
            ),
            (
                "ready_treatment_fingerprint",
                "UPDATE mip_app.campaigns SET treatment_fingerprint = %s "
                "WHERE campaign_id = %s",
                ("8" * 64, ready_campaign),
                ("55000",),
            ),
        )
        for savepoint, statement, params, sqlstates in forbidden_ready_mutations:
            lakebase_migrate._expect_database_rejection(
                cur,
                savepoint=savepoint,
                statement=statement,
                params=params,
                expected_sqlstates=sqlstates,
            )

        failed_campaign = uuid4()
        cur.execute(
            """
            INSERT INTO mip_app.campaigns (
                campaign_id, name, owner_email, status, criteria,
                idempotency_key, request_payload_hash,
                treatment_state, treatment_materialization_id,
                treatment_algorithm_version, treatment_contract_fingerprint,
                treatment_build_lease_until
            ) VALUES (
                %s, 'Failed transition proof', %s, 'draft', '{}'::jsonb,
                %s, %s, 'building', %s, 'campaign-treatment-v2', %s,
                now() + interval '5 minutes'
            )
            """,
            (
                failed_campaign,
                owner,
                str(failed_campaign),
                "8" * 64,
                failed_campaign,
                "9" * 64,
            ),
        )
        lakebase_migrate._expect_database_rejection(
            cur,
            savepoint="failed_with_live_lease",
            statement=(
                "UPDATE mip_app.campaigns SET treatment_state = 'failed' "
                "WHERE campaign_id = %s"
            ),
            params=(failed_campaign,),
            expected_sqlstates=("23514",),
        )
        cur.execute(
            """
            UPDATE mip_app.campaigns
            SET treatment_state = 'failed', treatment_build_lease_until = NULL
            WHERE campaign_id = %s
            RETURNING treatment_state, treatment_build_lease_until
            """,
            (failed_campaign,),
        )
        assert cur.fetchone() == ("failed", None)
        lakebase_migrate._expect_database_rejection(
            cur,
            savepoint="failed_to_building",
            statement=(
                "UPDATE mip_app.campaigns "
                "SET treatment_state = 'building', "
                "treatment_build_lease_until = now() + interval '5 minutes' "
                "WHERE campaign_id = %s"
            ),
            params=(failed_campaign,),
            expected_sqlstates=("55000",),
        )

        building_campaign = uuid4()
        zero_holdout_campaign = uuid4()
        positive_holdout_campaign = uuid4()
        cur.execute(
            """
            INSERT INTO mip_app.campaigns (
                campaign_id, name, owner_email, status, criteria,
                idempotency_key, request_payload_hash,
                treatment_state, treatment_materialization_id,
                treatment_algorithm_version, treatment_contract_fingerprint,
                treatment_build_lease_until
            ) VALUES (
                %s, 'Building transition proof', %s, 'draft', '{}'::jsonb,
                %s, %s, 'building', %s, 'campaign-treatment-v2', %s,
                now() + interval '5 minutes'
            )
            """,
            (
                building_campaign,
                owner,
                str(building_campaign),
                "9" * 64,
                building_campaign,
                "a" * 64,
            ),
        )
        cur.execute(
            """
            INSERT INTO mip_app.campaigns (
                campaign_id, name, owner_email, status, criteria, holdout,
                idempotency_key, request_payload_hash,
                treatment_state, treatment_materialization_id,
                treatment_algorithm_version, treatment_contract_fingerprint,
                treatment_build_lease_until
            ) VALUES (
                %s, 'Positive holdout transition proof', %s, 'draft', '{}'::jsonb,
                '{"method":"hash_modulo","size_pct":50}'::jsonb,
                %s, %s, 'building', %s, 'campaign-treatment-v2', %s,
                now() + interval '5 minutes'
            )
            """,
            (
                positive_holdout_campaign,
                owner,
                str(positive_holdout_campaign),
                "1" * 64,
                positive_holdout_campaign,
                "2" * 64,
            ),
        )
        cur.execute(
            """
            INSERT INTO mip_app.campaigns (
                campaign_id, name, owner_email, status, criteria, holdout,
                idempotency_key, request_payload_hash,
                treatment_state, treatment_materialization_id,
                treatment_algorithm_version, treatment_contract_fingerprint,
                treatment_build_lease_until
            ) VALUES (
                %s, 'Zero holdout transition proof', %s, 'draft', '{}'::jsonb,
                '{"method":"hash_modulo","size_pct":0}'::jsonb,
                %s, %s, 'building', %s, 'campaign-treatment-v2', %s,
                now() + interval '5 minutes'
            )
            """,
            (
                zero_holdout_campaign,
                owner,
                str(zero_holdout_campaign),
                "e" * 64,
                zero_holdout_campaign,
                "f" * 64,
            ),
        )
        lakebase_migrate._expect_database_rejection(
            cur,
            savepoint="building_to_legacy",
            statement=(
                "UPDATE mip_app.campaigns SET treatment_state = 'legacy_unbound' "
                "WHERE campaign_id = %s"
            ),
            params=(building_campaign,),
            expected_sqlstates=("55000",),
        )
        lakebase_migrate._expect_database_rejection(
            cur,
            savepoint="building_lease_only",
            statement=(
                "UPDATE mip_app.campaigns "
                "SET treatment_build_lease_until = now() + interval '10 minutes' "
                "WHERE campaign_id = %s"
            ),
            params=(building_campaign,),
            expected_sqlstates=("23514",),
        )
        lakebase_migrate._expect_database_rejection(
            cur,
            savepoint="incomplete_ready_manifest",
            statement=(
                "UPDATE mip_app.campaigns "
                "SET treatment_state = 'ready', "
                "treatment_candidate_count = 1, "
                "treatment_selected_primary_count = 1, "
                "treatment_count = 1, treatment_holdout_count = 0, "
                "treatment_materialized_at = now(), "
                "treatment_build_lease_until = NULL, "
                "household_summary = '{\"selected_primary_count\":1}'::jsonb, "
                "creation_response = '{\"marketable_population\":1}'::jsonb "
                "WHERE campaign_id = %s"
            ),
            params=(building_campaign,),
            expected_sqlstates=("23514",),
        )
        lakebase_migrate._expect_database_rejection(
            cur,
            savepoint="invalid_ready_response_contract",
            statement=(
                "UPDATE mip_app.campaigns "
                "SET treatment_state = 'ready', treatment_fingerprint = %s, "
                "treatment_source_snapshot_id = %s, treatment_delta_version = 0, "
                "treatment_assignment_digest = %s, treatment_candidate_count = 1, "
                "treatment_selected_primary_count = 1, treatment_count = 1, "
                "treatment_holdout_count = 0, treatment_materialized_at = now(), "
                "treatment_build_lease_until = NULL, "
                "household_summary = '{\"x\":1}'::jsonb, "
                "creation_response = '{\"y\":1}'::jsonb "
                "WHERE campaign_id = %s"
            ),
            params=("b" * 64, "c" * 64, "d" * 64, building_campaign),
            expected_sqlstates=("23514",),
        )
        household_summary, creation_response = _reviewed_treatment_response(
            "Building transition proof"
        )
        contradictory_summary = json.loads(household_summary)
        contradictory_summary.update(
            {
                "owner_link_household_count": 1,
                "mailing_address_household_count": 1,
                "singleton_household_count": 1,
            }
        )
        contradictory_response = json.loads(creation_response)
        contradictory_response["household_summary"] = contradictory_summary
        lakebase_migrate._expect_database_rejection(
            cur,
            savepoint="contradictory_household_bucket_proof",
            statement=(
                "UPDATE mip_app.campaigns "
                "SET treatment_state = 'ready', treatment_fingerprint = %s, "
                "treatment_source_snapshot_id = %s, treatment_delta_version = 0, "
                "treatment_assignment_digest = %s, treatment_candidate_count = 1, "
                "treatment_selected_primary_count = 1, treatment_count = 1, "
                "treatment_holdout_count = 0, treatment_materialized_at = now(), "
                "treatment_build_lease_until = NULL, household_summary = %s::jsonb, "
                "creation_response = %s::jsonb WHERE campaign_id = %s"
            ),
            params=(
                "b" * 64,
                "c" * 64,
                "d" * 64,
                json.dumps(contradictory_summary, sort_keys=True),
                json.dumps(contradictory_response, sort_keys=True),
                building_campaign,
            ),
            expected_sqlstates=("23514",),
        )
        excessive_households = {
            **json.loads(household_summary),
            "household_count": 2,
            "singleton_household_count": 2,
        }
        dedup_mismatch = {
            **json.loads(household_summary),
            "enabled": True,
            "candidate_borrower_count": 2,
            "selected_primary_count": 2,
            "suppressed_co_owner_count": 0,
        }
        over_cap_summary = {
            **json.loads(household_summary),
            "candidate_borrower_count": 10_001,
            "selected_primary_count": 10_001,
            "suppressed_co_owner_count": 0,
            "household_count": 10_001,
            "singleton_household_count": 10_001,
        }
        over_cap_response = {
            "name": "Building transition proof",
            "marketable_population": 5_001,
            "campaign_build_limit": 10_000,
            "campaign_build_eligible": True,
            "household_summary": over_cap_summary,
        }
        impossible_disabled_summary = {
            **json.loads(household_summary),
            "candidate_borrower_count": 2,
            "selected_primary_count": 1,
            "suppressed_co_owner_count": 1,
        }
        impossible_disabled_response = {
            "name": "Building transition proof",
            "marketable_population": 1,
            "campaign_build_limit": 10_000,
            "campaign_build_eligible": True,
            "household_summary": impossible_disabled_summary,
        }
        impossible_holdout_response = {
            "name": "Building transition proof",
            "marketable_population": 0,
            "campaign_build_limit": 10_000,
            "campaign_build_eligible": True,
            "household_summary": json.loads(household_summary),
        }
        impossible_zero_holdout_response = {
            **impossible_holdout_response,
            "name": "Zero holdout transition proof",
        }
        positive_holdout_response = {
            **impossible_holdout_response,
            "name": "Positive holdout transition proof",
        }
        cur.execute(
            """
            SELECT
                mip_app.campaign_household_summary_is_reviewed(
                    %s::jsonb, %s::jsonb, 1, 1
                ),
                mip_app.campaign_household_summary_is_reviewed(
                    %s::jsonb, %s::jsonb, 2, 2
                )
            """,
            (
                json.dumps(excessive_households, sort_keys=True),
                json.dumps(
                    {
                        "enabled": False,
                        "dedupe_unit": "borrower",
                        "primary_contact_strategy": "highest_opportunity_eligible",
                    },
                    sort_keys=True,
                ),
                json.dumps(dedup_mismatch, sort_keys=True),
                json.dumps(
                    {
                        "enabled": True,
                        "dedupe_unit": "household",
                        "primary_contact_strategy": "highest_opportunity_eligible",
                    },
                    sort_keys=True,
                ),
            ),
        )
        assert cur.fetchone() == (False, False)
        lakebase_migrate._expect_database_rejection(
            cur,
            savepoint="over_cap_selected_primary_proof",
            statement=(
                "UPDATE mip_app.campaigns "
                "SET treatment_state = 'ready', treatment_fingerprint = %s, "
                "treatment_source_snapshot_id = %s, treatment_delta_version = 0, "
                "treatment_assignment_digest = %s, treatment_candidate_count = 10001, "
                "treatment_selected_primary_count = 10001, treatment_count = 5001, "
                "treatment_holdout_count = 5000, treatment_materialized_at = now(), "
                "treatment_build_lease_until = NULL, household_summary = %s::jsonb, "
                "creation_response = %s::jsonb WHERE campaign_id = %s"
            ),
            params=(
                "b" * 64,
                "c" * 64,
                "d" * 64,
                json.dumps(over_cap_summary, sort_keys=True),
                json.dumps(over_cap_response, sort_keys=True),
                building_campaign,
            ),
            expected_sqlstates=("23514",),
        )
        lakebase_migrate._expect_database_rejection(
            cur,
            savepoint="disabled_dedup_suppressed_proof",
            statement=(
                "UPDATE mip_app.campaigns "
                "SET treatment_state = 'ready', treatment_fingerprint = %s, "
                "treatment_source_snapshot_id = %s, treatment_delta_version = 0, "
                "treatment_assignment_digest = %s, treatment_candidate_count = 2, "
                "treatment_selected_primary_count = 1, treatment_count = 1, "
                "treatment_holdout_count = 0, treatment_materialized_at = now(), "
                "treatment_build_lease_until = NULL, household_summary = %s::jsonb, "
                "creation_response = %s::jsonb WHERE campaign_id = %s"
            ),
            params=(
                "b" * 64,
                "c" * 64,
                "d" * 64,
                json.dumps(impossible_disabled_summary, sort_keys=True),
                json.dumps(impossible_disabled_response, sort_keys=True),
                building_campaign,
            ),
            expected_sqlstates=("23514",),
        )
        for savepoint, campaign, response in (
            (
                "absent_holdout_member_proof",
                building_campaign,
                impossible_holdout_response,
            ),
            (
                "zero_holdout_member_proof",
                zero_holdout_campaign,
                impossible_zero_holdout_response,
            ),
        ):
            lakebase_migrate._expect_database_rejection(
                cur,
                savepoint=savepoint,
                statement=(
                    "UPDATE mip_app.campaigns "
                    "SET treatment_state = 'ready', treatment_fingerprint = %s, "
                    "treatment_source_snapshot_id = %s, treatment_delta_version = 0, "
                    "treatment_assignment_digest = %s, treatment_candidate_count = 1, "
                    "treatment_selected_primary_count = 1, treatment_count = 0, "
                    "treatment_holdout_count = 1, treatment_materialized_at = now(), "
                    "treatment_build_lease_until = NULL, household_summary = %s::jsonb, "
                    "creation_response = %s::jsonb WHERE campaign_id = %s"
                ),
                params=(
                    "b" * 64,
                    "c" * 64,
                    "d" * 64,
                    household_summary,
                    json.dumps(response, sort_keys=True),
                    campaign,
                ),
                expected_sqlstates=("23514",),
            )
        cur.execute(
            """
            UPDATE mip_app.campaigns
            SET treatment_state = 'ready', treatment_fingerprint = %s,
                treatment_source_snapshot_id = %s, treatment_delta_version = 0,
                treatment_assignment_digest = %s, treatment_candidate_count = 1,
                treatment_selected_primary_count = 1, treatment_count = 0,
                treatment_holdout_count = 1, treatment_materialized_at = now(),
                treatment_build_lease_until = NULL, household_summary = %s::jsonb,
                creation_response = %s::jsonb
            WHERE campaign_id = %s
            RETURNING treatment_state
            """,
            (
                "b" * 64,
                "c" * 64,
                "d" * 64,
                household_summary,
                json.dumps(positive_holdout_response, sort_keys=True),
                positive_holdout_campaign,
            ),
        )
        assert cur.fetchone() == ("ready",)
        cur.execute(
            """
            UPDATE mip_app.campaigns
            SET treatment_state = 'ready',
                treatment_fingerprint = %s,
                treatment_source_snapshot_id = %s,
                treatment_delta_version = 0,
                treatment_assignment_digest = %s,
                treatment_candidate_count = 1,
                treatment_selected_primary_count = 1,
                treatment_count = 1,
                treatment_holdout_count = 0,
                treatment_materialized_at = now(),
                treatment_build_lease_until = NULL,
                household_summary = %s::jsonb,
                creation_response = %s::jsonb
            WHERE campaign_id = %s
            RETURNING treatment_state
            """,
            (
                "b" * 64,
                "c" * 64,
                "d" * 64,
                household_summary,
                creation_response,
                building_campaign,
            ),
        )
        assert cur.fetchone() == ("ready",)
        lakebase_migrate._expect_database_rejection(
            cur,
            savepoint="ready_campaign_id",
            statement=(
                "UPDATE mip_app.campaigns SET campaign_id = %s "
                "WHERE campaign_id = %s"
            ),
            params=(uuid4(), building_campaign),
            expected_sqlstates=("55000",),
        )
        lakebase_migrate._expect_database_rejection(
            cur,
            savepoint="ready_created_at",
            statement=(
                "UPDATE mip_app.campaigns SET created_at = created_at - interval '1 day' "
                "WHERE campaign_id = %s"
            ),
            params=(building_campaign,),
            expected_sqlstates=("55000",),
        )
        direct_ready_id = uuid4()
        lakebase_migrate._expect_database_rejection(
            cur,
            savepoint="direct_ready_insert",
            statement=(
                "INSERT INTO mip_app.campaigns ("
                "campaign_id, name, owner_email, criteria, treatment_state"
                ") VALUES (%s, 'Direct ready', %s, '{}'::jsonb, 'ready')"
            ),
            params=(direct_ready_id, owner),
            expected_sqlstates=("23514",),
        )
        unleased_campaign = uuid4()
        lakebase_migrate._expect_database_rejection(
            cur,
            savepoint="unleased_building_insert",
            statement=(
                "INSERT INTO mip_app.campaigns ("
                "campaign_id, name, owner_email, criteria, "
                "idempotency_key, request_payload_hash, treatment_state, "
                "treatment_materialization_id, treatment_algorithm_version, "
                "treatment_contract_fingerprint, treatment_build_lease_until"
                ") VALUES (%s, 'Unleased building', %s, '{}'::jsonb, "
                "%s, %s, 'building', %s, 'campaign-treatment-v2', %s, NULL)"
            ),
            params=(
                unleased_campaign,
                owner,
                str(unleased_campaign),
                "e" * 64,
                unleased_campaign,
                "f" * 64,
            ),
            expected_sqlstates=("23514",),
        )
        poisoned_building = uuid4()
        lakebase_migrate._expect_database_rejection(
            cur,
            savepoint="building_with_finalized_proof",
            statement=(
                "INSERT INTO mip_app.campaigns ("
                "campaign_id, name, owner_email, criteria, "
                "idempotency_key, request_payload_hash, treatment_state, "
                "treatment_materialization_id, treatment_algorithm_version, "
                "treatment_contract_fingerprint, treatment_build_lease_until, "
                "treatment_fingerprint"
                ") VALUES (%s, 'Poisoned building', %s, '{}'::jsonb, "
                "%s, %s, 'building', %s, 'campaign-treatment-v2', %s, "
                "now() + interval '5 minutes', %s)"
            ),
            params=(
                poisoned_building,
                owner,
                str(poisoned_building),
                "0" * 64,
                poisoned_building,
                "1" * 64,
                "2" * 64,
            ),
            expected_sqlstates=("23514",),
        )

    reclaim_campaign = uuid4()
    old_materialization_id = uuid4()
    new_materialization_id = uuid4()
    with psycopg.connect(**postgres_kwargs) as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO mip_app.campaigns (
                campaign_id, name, owner_email, criteria,
                idempotency_key, request_payload_hash, treatment_state,
                treatment_materialization_id, treatment_algorithm_version,
                treatment_contract_fingerprint, treatment_build_lease_until
            ) VALUES (
                %s, 'Expired reclaim proof', 'campaign-reclaim@test.example',
                '{}'::jsonb, %s, %s, 'building', %s,
                'campaign-treatment-v2', %s,
                clock_timestamp() + interval '100 milliseconds'
            )
            """,
            (
                reclaim_campaign,
                str(reclaim_campaign),
                "3" * 64,
                old_materialization_id,
                "4" * 64,
            ),
        )
    sleep(0.2)
    with psycopg.connect(**postgres_kwargs) as conn, conn.cursor() as cur:
        lakebase_migrate._expect_database_rejection(
            cur,
            savepoint="expired_reclaim_null_lease",
            statement=(
                "UPDATE mip_app.campaigns "
                "SET treatment_materialization_id = %s, "
                "treatment_build_lease_until = NULL "
                "WHERE campaign_id = %s"
            ),
            params=(new_materialization_id, reclaim_campaign),
            expected_sqlstates=("55000",),
        )
        cur.execute(
            """
            UPDATE mip_app.campaigns
            SET treatment_materialization_id = %s,
                treatment_build_lease_until = now() + interval '5 minutes'
            WHERE campaign_id = %s
            RETURNING treatment_materialization_id,
                      treatment_build_lease_until > now()
            """,
            (new_materialization_id, reclaim_campaign),
        )
        assert cur.fetchone() == (new_materialization_id, True)


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

        with psycopg.connect(**postgres_kwargs) as conn, conn.cursor() as cur:
            campaign_id, owner, borrower_id = _ready_campaign(cur)
        approval_id, audit_id = uuid4(), uuid4()
        with psycopg.connect(**postgres_kwargs) as conn, conn.cursor() as cur:
            cur.execute(psql.SQL("SET ROLE {}").format(psql.Identifier(app_role)))
            _insert_campaign_decision(
                cur,
                campaign_id=campaign_id,
                owner=owner,
                borrower_id=borrower_id,
                approval_id=approval_id,
                audit_id=audit_id,
            )
        with psycopg.connect(**postgres_kwargs) as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE mip_app.campaigns SET status = 'archived' WHERE campaign_id = %s",
                (campaign_id,),
            )
        rejected_approval, rejected_audit = uuid4(), uuid4()
        rejected_conn = psycopg.connect(**postgres_kwargs)
        try:
            with rejected_conn.cursor() as cur:
                cur.execute(psql.SQL("SET ROLE {}").format(psql.Identifier(app_role)))
                with pytest.raises(psycopg.errors.CheckViolation):
                    _insert_campaign_decision(
                        cur,
                        campaign_id=campaign_id,
                        owner=owner,
                        borrower_id=borrower_id,
                        approval_id=rejected_approval,
                        audit_id=rejected_audit,
                    )
            rejected_conn.rollback()
        finally:
            rejected_conn.close()
        with psycopg.connect(**postgres_kwargs) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT EXISTS (SELECT 1 FROM mip_app.approvals WHERE approval_id = %s), "
                "EXISTS (SELECT 1 FROM mip_app.action_audit WHERE audit_id = %s)",
                (rejected_approval, rejected_audit),
            )
            assert cur.fetchone() == (False, False)
    finally:
        with psycopg.connect(**postgres_kwargs, autocommit=True) as conn, conn.cursor() as cur:
            for role in (app_role, verifier_role):
                role_identifier = psql.Identifier(role)
                cur.execute(psql.SQL("DROP OWNED BY {}").format(role_identifier))
                cur.execute(psql.SQL("DROP ROLE {}").format(role_identifier))

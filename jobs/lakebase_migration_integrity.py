"""Failure-atomic schema boundaries and outreach integrity probes."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from jobs.lakebase_migration_campaign_decision_probe import (
    _campaign_decision_intent,
    _run_campaign_decision_negative_probes,
)

_POST_SEED_MARKER = "-- MIP_LAKEBASE_POST_SEED_BEGIN"


def _split_schema_sql(schema_sql: str) -> tuple[str, str]:
    """Split schema DDL around the deterministic seed dependency boundary."""

    marker_count = schema_sql.count(_POST_SEED_MARKER)
    if marker_count != 1:
        raise RuntimeError(
            "lakebase/schema.sql must contain exactly one post-seed marker; "
            f"found {marker_count}"
        )
    pre_seed_sql, post_seed_sql = schema_sql.split(_POST_SEED_MARKER, 1)
    if not pre_seed_sql.strip() or not post_seed_sql.strip():
        raise RuntimeError("lakebase/schema.sql post-seed boundary cannot be empty")
    return pre_seed_sql, post_seed_sql


def _expect_database_rejection(
    cur: object,
    *,
    savepoint: str,
    statement: str,
    params: tuple[object, ...] = (),
    expected_sqlstates: tuple[str, ...],
) -> None:
    """Execute a negative integrity probe without aborting the outer transaction."""

    cur.execute(f"SAVEPOINT {savepoint}")  # type: ignore[attr-defined]
    try:
        cur.execute(statement, params)  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001 -- SQLSTATE is the proof contract
        sqlstate = str(getattr(exc, "sqlstate", "") or "")
        cur.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")  # type: ignore[attr-defined]
        cur.execute(f"RELEASE SAVEPOINT {savepoint}")  # type: ignore[attr-defined]
        if sqlstate not in expected_sqlstates:
            raise RuntimeError(
                f"Lakebase integrity probe {savepoint!r} failed with SQLSTATE "
                f"{sqlstate or 'unknown'}; expected {expected_sqlstates}"
            ) from exc
        return
    cur.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")  # type: ignore[attr-defined]
    cur.execute(f"RELEASE SAVEPOINT {savepoint}")  # type: ignore[attr-defined]
    raise RuntimeError(f"Lakebase integrity probe {savepoint!r} accepted a forbidden mutation")


def _run_outreach_integrity_probe(
    conn_kwargs: dict,
    *,
    connection: Any | None = None,
) -> None:
    """Prove migrated outreach constraints before the deployment commit."""

    import psycopg

    campaign_id = uuid4()
    approval_id = uuid4()
    audit_id = uuid4()
    generation_id = uuid4()
    generation_audit_id = uuid4()
    activation_id = uuid4()
    disposition_id = uuid4()
    disposition_audit_id = uuid4()
    outcome_id = uuid4()
    outcome_audit_id = uuid4()
    growth_run_id = uuid4()
    growth_audit_id = uuid4()
    actor = "lakebase-integrity-probe@databricks-apps"
    borrower_id = "B-0000000000000"
    request_id = str(uuid4())
    audit_sequence = -int(campaign_id.int % 9_000_000_000_000_000 + 1)
    generation_audit_sequence = audit_sequence - 9_000_000_000_000_001
    disposition_audit_sequence = generation_audit_sequence - 1
    outcome_audit_sequence = generation_audit_sequence - 2
    growth_audit_sequence = generation_audit_sequence - 3
    owns_connection = connection is None
    conn = connection or psycopg.connect(**conn_kwargs, autocommit=False)
    try:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL lock_timeout = '5s'")
            cur.execute("SET LOCAL statement_timeout = '30s'")
            cur.execute("SAVEPOINT outreach_integrity_probe")
            cur.execute(
                """
                INSERT INTO mip_app.sales_team (
                    email, display_label, role, capacity_per_day
                ) VALUES (%s, 'Deployment integrity probe', 'admin', 0)
                """,
                (actor,),
            )
            cur.execute(
                """
                INSERT INTO mip_app.campaigns (
                    campaign_id, name, owner_email, status, criteria,
                    treatment_state, treatment_materialization_id,
                    treatment_algorithm_version, treatment_contract_fingerprint,
                    treatment_build_lease_until
                ) VALUES (
                    %s, %s, %s, 'draft', '{}'::jsonb,
                    'building', %s, 'campaign-treatment-v2', %s, now() + interval '5 minutes'
                )
                """,
                (
                    campaign_id,
                    "Deployment integrity probe",
                    actor,
                    campaign_id,
                    "3" * 64,
                ),
            )
            cur.execute(
                """
                INSERT INTO mip_app.campaign_message_variants (
                    campaign_id, variant_name, channel, subject, body,
                    generation_mode, generator_label, provenance_key_id,
                    provenance_issued_at, provenance_expires_at,
                    provenance_copy_hash, provenance_criteria_fingerprint,
                    provenance_token_digest
                ) VALUES (
                    %s, 'Integrity proof', 'email', %s, %s,
                    'reviewed_fallback', 'Deployment integrity probe', 'v1',
                    now(), now() + interval '1 hour', %s, %s, %s
                )
                """,
                (
                    campaign_id,
                    "Integrity proof",
                    "Rollback-only deployment proof. Contact a loan officer to review options.",
                    "0" * 64,
                    "1" * 64,
                    "2" * 64,
                ),
            )
            for savepoint, column, poisoned_json in (
                (
                    "probe_campaign_criteria_shape",
                    "criteria",
                    '{"unreviewed_filter":"blocked"}',
                ),
                (
                    "probe_campaign_suppression_shape",
                    "suppression_policy",
                    '{"unreviewed_policy":"blocked"}',
                ),
                (
                    "probe_campaign_cascade_shape",
                    "channel_cascade",
                    '[{"channel":"email","step":1,"unreviewed":"blocked"}]',
                ),
                (
                    "probe_campaign_send_window_shape",
                    "send_window",
                    '{"days":["Tuesday"],"timezone":"server_local",'
                    '"start_local":"09:00","end_local":"16:00"}',
                ),
                (
                    "probe_campaign_holdout_shape",
                    "holdout",
                    '{"method":"random","size_pct":10}',
                ),
                (
                    "probe_campaign_roi_shape",
                    "roi_assumptions",
                    '{"unreviewed_assumption":1}',
                ),
            ):
                _expect_database_rejection(
                    cur,
                    savepoint=savepoint,
                    statement=(
                        f"UPDATE mip_app.campaigns SET {column} = %s::jsonb "
                        "WHERE campaign_id = %s"
                    ),
                    params=(poisoned_json, campaign_id),
                    expected_sqlstates=("23514",),
                )
            cur.execute(
                "SELECT json_contract_version FROM mip_app.campaigns WHERE campaign_id = %s",
                (campaign_id,),
            )
            if cur.fetchone() != (1,):
                raise RuntimeError(
                    "Lakebase integrity probe campaign was not written under JSON contract version 1"
                )
            cur.execute(
                """
                UPDATE mip_app.campaigns
                SET status = 'approved',
                    treatment_state = 'ready',
                    treatment_fingerprint = %s,
                    treatment_source_snapshot_id = %s,
                    treatment_delta_version = 0,
                    treatment_assignment_digest = %s,
                    treatment_candidate_count = 1,
                    treatment_selected_primary_count = 1,
                    treatment_count = 1,
                    treatment_holdout_count = 0,
                    treatment_materialized_at = now(),
                    treatment_build_lease_until = NULL
                WHERE campaign_id = %s
                """,
                ("4" * 64, "5" * 64, "6" * 64, campaign_id),
            )
            valid_intent, valid_intent_hash = _run_campaign_decision_negative_probes(
                cur,
                campaign_id=campaign_id,
                actor=actor,
                borrower_id=borrower_id,
                expect_rejection=_expect_database_rejection,
            )
            cur.execute(
                """
                INSERT INTO mip_app.action_audit (
                    audit_id, audit_sequence, event_type, actor_email,
                    entity_type, entity_id, request_id, metadata
                ) VALUES (%s, %s, 'OUTREACH_APPROVAL', %s,
                          'approval', %s, %s, '{}'::jsonb)
                """,
                (audit_id, audit_sequence, actor, str(approval_id), request_id),
            )
            cur.execute(
                """
                INSERT INTO mip_app.approvals (
                    approval_id, campaign_id, variant_name, channel,
                    borrower_id, action, actor_email, request_id,
                    decision_intent, decision_payload_hash
                ) VALUES (%s, %s, 'Integrity proof', 'email', %s,
                          'approve', %s, %s, %s, %s)
                """,
                (
                    approval_id,
                    campaign_id,
                    borrower_id,
                    actor,
                    request_id,
                    valid_intent,
                    valid_intent_hash,
                ),
            )
            cur.execute(
                """
                UPDATE mip_app.approvals
                SET decision_response = %s::jsonb, audit_event_id = %s
                WHERE approval_id = %s
                """,
                ('{"status":"approved","probe":true}', audit_id, approval_id),
            )
            cur.execute(
                """
                SELECT decision_response->>'status', audit_event_id
                FROM mip_app.approvals WHERE approval_id = %s
                """,
                (approval_id,),
            )
            finalized = cur.fetchone()
            if finalized != ("approved", audit_id):
                raise RuntimeError(
                    "Lakebase integrity probe could not verify one-time approval finalization"
                )

            cur.execute(
                """
                INSERT INTO mip_app.action_audit (
                    audit_id, audit_sequence, event_type, actor_email,
                    entity_type, entity_id, request_id, metadata
                ) VALUES (%s, %s, 'OUTREACH_DRAFT', %s,
                          'generation', %s, %s, '{}'::jsonb)
                """,
                (
                    generation_audit_id,
                    generation_audit_sequence,
                    actor,
                    str(generation_id),
                    str(uuid4()),
                ),
            )

            cur.execute(
                """
                INSERT INTO mip_app.action_audit (
                    audit_id, audit_sequence, event_type, actor_email,
                    entity_type, entity_id, request_id, metadata
                ) VALUES
                    (%s, %s, 'CALL_DISPOSITION', %s,
                     'call_disposition', %s, %s, '{}'::jsonb),
                    (%s, %s, 'LEAD_OUTCOME', %s,
                     'lead_outcome', %s, %s, '{}'::jsonb),
                    (%s, %s, 'GROWTH_AGENT_RUN', %s,
                     'growth_agent_run', %s, %s, '{}'::jsonb)
                """,
                (
                    disposition_audit_id,
                    disposition_audit_sequence,
                    actor,
                    str(disposition_id),
                    str(uuid4()),
                    outcome_audit_id,
                    outcome_audit_sequence,
                    actor,
                    str(outcome_id),
                    str(uuid4()),
                    growth_audit_id,
                    growth_audit_sequence,
                    actor,
                    str(growth_run_id),
                    str(uuid4()),
                ),
            )
            cur.execute(
                """
                INSERT INTO mip_app.call_dispositions (
                    disposition_id, borrower_id, lo_email, outcome,
                    attempt_number, notes, request_id
                ) VALUES (%s, %s, %s, 'connected', 1, 'Integrity proof', %s)
                """,
                (disposition_id, borrower_id, actor, str(uuid4())),
            )
            cur.execute(
                """
                UPDATE mip_app.call_dispositions
                SET audit_event_id = %s
                WHERE disposition_id = %s
                """,
                (disposition_audit_id, disposition_id),
            )
            cur.execute(
                """
                INSERT INTO mip_app.lead_outcomes (
                    outcome_id, borrower_id, outcome_type, source_system,
                    request_id, created_by, payload_json
                ) VALUES (%s, %s, 'application_submitted', 'manual_import',
                          %s, %s, '{}'::jsonb)
                """,
                (outcome_id, borrower_id, str(uuid4()), actor),
            )
            cur.execute(
                """
                UPDATE mip_app.lead_outcomes
                SET audit_event_id = %s
                WHERE outcome_id = %s
                """,
                (outcome_audit_id, outcome_id),
            )
            cur.execute(
                """
                INSERT INTO mip_app.growth_agent_runs (
                    run_id, actor_email, request_id, workflow_id,
                    workflow_title, route, source_assets, agent_evidence
                ) VALUES (%s, %s, %s, 'daily_refi_brief',
                          'Deployment integrity probe', '/lead-queue',
                          ARRAY['mip.gold.lead_ranked'], '{"probe":true}'::jsonb)
                """,
                (growth_run_id, actor, str(uuid4())),
            )
            cur.execute(
                """
                UPDATE mip_app.growth_agent_runs
                SET audit_event_id = %s
                WHERE run_id = %s
                """,
                (growth_audit_id, growth_run_id),
            )

            cur.execute(
                """
                SELECT
                    (SELECT audit_event_id FROM mip_app.call_dispositions
                     WHERE disposition_id = %s),
                    (SELECT audit_event_id FROM mip_app.lead_outcomes
                     WHERE outcome_id = %s),
                    (SELECT audit_event_id FROM mip_app.growth_agent_runs
                     WHERE run_id = %s)
                """,
                (disposition_id, outcome_id, growth_run_id),
            )
            if cur.fetchone() != (
                disposition_audit_id,
                outcome_audit_id,
                growth_audit_id,
            ):
                raise RuntimeError("Lakebase integrity probe could not verify audit finalization")
            cur.execute(
                """
                INSERT INTO mip_app.generated_outreach_drafts (
                    generation_id, audit_event_id, actor_email, borrower_id,
                    campaign_id, variant_name, channel, offer_code,
                    generation_mode, response_hash, response_json
                ) VALUES (%s, %s, %s, %s, %s, 'Integrity proof', 'email',
                          'refi', 'governed_fallback', %s, %s::jsonb)
                """,
                (
                    generation_id,
                    generation_audit_id,
                    actor,
                    borrower_id,
                    campaign_id,
                    "0" * 64,
                    '{"subject":"Integrity proof","body":"Rollback only"}',
                ),
            )
            cur.execute(
                """
                INSERT INTO mip_app.activation_outbox (
                    activation_id, destination_key, entity_type, entity_id,
                    borrower_id, campaign_id, approval_id, offer_code, channel,
                    status, request_id, created_by
                ) VALUES (%s, 'salesforce_crm', 'borrower', %s, %s, %s, %s,
                          'refi', 'email', 'dry_run', %s, %s)
                """,
                (
                    activation_id,
                    borrower_id,
                    borrower_id,
                    campaign_id,
                    approval_id,
                    str(uuid4()),
                    actor,
                ),
            )

            _expect_database_rejection(
                cur,
                savepoint="probe_operator_campaign_variant",
                statement="""
                    INSERT INTO mip_app.campaign_message_variants (
                        campaign_id, variant_name, channel, subject, body,
                        generation_mode, generator_label
                    ) VALUES (%s, 'Operator copy', 'email', %s, %s,
                              'operator', 'Operator edited')
                """,
                params=(
                    campaign_id,
                    "Operator copy",
                    "Contact a loan officer to review operator-authored copy.",
                ),
                expected_sqlstates=("23514",),
            )
            channel_mismatch_intent, channel_mismatch_hash = _campaign_decision_intent(
                action="approve",
                actor=actor,
                borrower_id=borrower_id,
                campaign_id=campaign_id,
                variant_name="Integrity proof",
                channel="sms",
                owner_email=actor,
                treatment_fingerprint="4" * 64,
            )
            _expect_database_rejection(
                cur,
                savepoint="probe_variant_mismatch",
                statement="""
                    INSERT INTO mip_app.approvals (
                        approval_id, campaign_id, variant_name, channel,
                        borrower_id, action, actor_email, request_id,
                        decision_intent, decision_payload_hash
                    ) VALUES (%s, %s, 'Integrity proof', 'sms', %s,
                              'approve', %s, %s, %s, %s)
                """,
                params=(
                    uuid4(),
                    campaign_id,
                    borrower_id,
                    actor,
                    str(uuid4()),
                    channel_mismatch_intent,
                    channel_mismatch_hash,
                ),
                expected_sqlstates=("23503",),
            )
            _expect_database_rejection(
                cur,
                savepoint="probe_second_finalize",
                statement="""
                    UPDATE mip_app.approvals
                    SET decision_response = %s::jsonb, audit_event_id = %s
                    WHERE approval_id = %s
                """,
                params=('{"status":"approved","probe":false}', audit_id, approval_id),
                expected_sqlstates=("42501",),
            )
            _expect_database_rejection(
                cur,
                savepoint="probe_variant_update",
                statement="""
                    UPDATE mip_app.campaign_message_variants
                    SET body = 'forbidden rewrite'
                    WHERE campaign_id = %s AND variant_name = 'Integrity proof'
                      AND channel = 'email'
                """,
                params=(campaign_id,),
                expected_sqlstates=("42501",),
            )
            _expect_database_rejection(
                cur,
                savepoint="probe_generated_update",
                statement="""
                    UPDATE mip_app.generated_outreach_drafts
                    SET response_hash = %s
                    WHERE generation_id = %s
                """,
                params=("f" * 64, generation_id),
                expected_sqlstates=("42501",),
            )
            _expect_database_rejection(
                cur,
                savepoint="probe_approval_delete",
                statement="DELETE FROM mip_app.approvals WHERE approval_id = %s",
                params=(approval_id,),
                expected_sqlstates=("42501",),
            )
            _expect_database_rejection(
                cur,
                savepoint="probe_audit_delete",
                statement="DELETE FROM mip_app.action_audit WHERE audit_id = %s",
                params=(audit_id,),
                expected_sqlstates=("42501",),
            )
            _expect_database_rejection(
                cur,
                savepoint="probe_disposition_rewrite",
                statement="""
                    UPDATE mip_app.call_dispositions
                    SET notes = 'forbidden rewrite'
                    WHERE disposition_id = %s
                """,
                params=(disposition_id,),
                expected_sqlstates=("42501",),
            )
            _expect_database_rejection(
                cur,
                savepoint="probe_disposition_second_finalize",
                statement="""
                    UPDATE mip_app.call_dispositions
                    SET audit_event_id = %s
                    WHERE disposition_id = %s
                """,
                params=(disposition_audit_id, disposition_id),
                expected_sqlstates=("42501",),
            )
            _expect_database_rejection(
                cur,
                savepoint="probe_outcome_rewrite",
                statement="""
                    UPDATE mip_app.lead_outcomes
                    SET payload_json = '{"forbidden":true}'::jsonb
                    WHERE outcome_id = %s
                """,
                params=(outcome_id,),
                expected_sqlstates=("42501",),
            )
            _expect_database_rejection(
                cur,
                savepoint="probe_unhashed_outcome_source_ref",
                statement="""
                    INSERT INTO mip_app.lead_outcomes (
                        outcome_id, borrower_id, outcome_type, source_system,
                        source_record_ref, created_by, payload_json
                    ) VALUES (%s, %s, 'application_submitted', 'manual_import',
                              'external-crm-record-123', %s, '{}'::jsonb)
                """,
                params=(uuid4(), borrower_id, actor),
                expected_sqlstates=("23514",),
            )
            _expect_database_rejection(
                cur,
                savepoint="probe_growth_run_rewrite",
                statement="""
                    UPDATE mip_app.growth_agent_runs
                    SET agent_evidence = '{"forbidden":true}'::jsonb
                    WHERE run_id = %s
                """,
                params=(growth_run_id,),
                expected_sqlstates=("42501",),
            )
            for savepoint, table, identifier_column, identifier in (
                (
                    "probe_disposition_delete",
                    "call_dispositions",
                    "disposition_id",
                    disposition_id,
                ),
                ("probe_outcome_delete", "lead_outcomes", "outcome_id", outcome_id),
                ("probe_growth_run_delete", "growth_agent_runs", "run_id", growth_run_id),
            ):
                _expect_database_rejection(
                    cur,
                    savepoint=savepoint,
                    statement=(f"DELETE FROM mip_app.{table} " f"WHERE {identifier_column} = %s"),
                    params=(identifier,),
                    expected_sqlstates=("42501",),
                )

            proof_constraints = (
                "approvals_borrower_id_format_chk",
                "approvals_channel_chk",
                "approvals_channel_required_chk",
                "approvals_campaign_variant_pair_chk",
                "approvals_campaign_variant_channel_fkey",
                "generated_outreach_campaign_variant_pair_chk",
                "generated_outreach_campaign_variant_channel_fkey",
                "call_dispositions_audit_event_id_fkey",
                "lead_outcomes_audit_event_id_fkey",
            )
            cur.execute(
                """
                SELECT conname, convalidated
                FROM pg_constraint
                WHERE connamespace = 'mip_app'::regnamespace
                  AND conname = ANY(%s::text[])
                ORDER BY conname
                """,
                (list(proof_constraints),),
            )
            constraint_state = dict(cur.fetchall())
            if constraint_state != {name: True for name in proof_constraints}:
                raise RuntimeError(
                    "Lakebase integrity probe found missing or unvalidated proof "
                    f"constraints: {constraint_state}"
                )

            reviewed_shape_constraints = (
                "campaigns_criteria_reviewed_shape_chk",
                "campaigns_suppression_policy_reviewed_shape_chk",
                "campaigns_channel_cascade_reviewed_shape_chk",
                "campaigns_send_window_reviewed_shape_chk",
                "campaigns_holdout_reviewed_shape_chk",
                "campaigns_roi_assumptions_reviewed_shape_chk",
            )
            cur.execute(
                """
                SELECT conname, convalidated
                FROM pg_constraint
                WHERE conrelid = 'mip_app.campaigns'::regclass
                  AND conname = ANY(%s::text[])
                ORDER BY conname
                """,
                (list(reviewed_shape_constraints),),
            )
            reviewed_shape_state = dict(cur.fetchall())
            if reviewed_shape_state != {name: False for name in reviewed_shape_constraints}:
                raise RuntimeError(
                    "Lakebase integrity probe found missing or prematurely validated "
                    f"campaign JSON shape constraints: {reviewed_shape_state}"
                )

            cur.execute(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM pg_trigger
                    WHERE tgrelid = 'mip_app.campaigns'::regclass
                      AND tgname = 'trg_campaigns_json_contract_enforcement'
                      AND NOT tgisinternal
                )
                """
            )
            if cur.fetchone() != (True,):
                raise RuntimeError(
                    "Lakebase integrity probe did not find campaign JSON contract enforcement"
                )

            cur.execute(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM pg_constraint
                    WHERE conname = 'ck_lead_outcomes_source_record_ref'
                      AND conrelid = 'mip_app.lead_outcomes'::regclass
                )
                """
            )
            if cur.fetchone() != (True,):
                raise RuntimeError(
                    "Lakebase integrity probe did not find the lead outcome "
                    "source reference constraint"
                )

            cur.execute(
                """
                SELECT COUNT(*)
                FROM pg_trigger t
                JOIN pg_class c ON c.oid = t.tgrelid
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'mip_app'
                  AND (
                    (c.relname = 'approvals' AND t.tgname = 'trg_approvals_no_remove')
                    OR (c.relname = 'campaign_message_variants'
                        AND t.tgname = 'trg_campaign_message_variants_immutable')
                    OR (c.relname = 'generated_outreach_drafts'
                        AND t.tgname = 'trg_generated_outreach_drafts_immutable')
                    OR (c.relname = 'action_audit'
                        AND t.tgname = 'trg_action_audit_append_only')
                    OR (c.relname = 'call_dispositions'
                        AND t.tgname = 'trg_call_dispositions_no_remove')
                    OR (c.relname = 'lead_outcomes'
                        AND t.tgname = 'trg_lead_outcomes_no_remove')
                    OR (c.relname = 'growth_agent_runs'
                        AND t.tgname = 'trg_growth_agent_runs_no_remove')
                  )
                  AND NOT t.tgisinternal
                  AND (t.tgtype & 32) = 32
                """
            )
            trigger_count_row = cur.fetchone()
            if trigger_count_row is None:
                raise RuntimeError("Lakebase integrity trigger count returned no row")
            trigger_count = int(trigger_count_row[0])
            if trigger_count != 7:
                raise RuntimeError(
                    "Lakebase integrity probe expected seven immutable TRUNCATE triggers, "
                    f"found {trigger_count}"
                )

            cur.execute(
                """
                SELECT COUNT(*)
                FROM pg_trigger t
                JOIN pg_class c ON c.oid = t.tgrelid
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'mip_app'
                  AND (
                    (c.relname = 'approvals'
                     AND t.tgname = 'trg_approvals_finalize_only')
                    OR (c.relname = 'call_dispositions'
                        AND t.tgname = 'trg_call_dispositions_finalize_only')
                    OR (c.relname = 'lead_outcomes'
                        AND t.tgname = 'trg_lead_outcomes_finalize_only')
                    OR (c.relname = 'growth_agent_runs'
                        AND t.tgname = 'trg_growth_agent_runs_finalize_only')
                  )
                  AND NOT t.tgisinternal
                  AND (t.tgtype & 1) = 1
                  AND (t.tgtype & 16) = 16
                """
            )
            finalize_trigger_count_row = cur.fetchone()
            if finalize_trigger_count_row is None:
                raise RuntimeError("Lakebase integrity finalization trigger count returned no row")
            finalize_trigger_count = int(finalize_trigger_count_row[0])
            if finalize_trigger_count != 4:
                raise RuntimeError(
                    "Lakebase integrity probe expected four row-level audit "
                    f"finalization triggers, found {finalize_trigger_count}"
                )

            cur.execute(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM pg_trigger t
                    JOIN pg_class c ON c.oid = t.tgrelid
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'mip_app'
                      AND c.relname = 'approvals'
                      AND t.tgname = 'trg_approvals_campaign_lifecycle'
                      AND NOT t.tgisinternal
                      AND (t.tgtype & 1) = 1
                      AND (t.tgtype & 4) = 4
                )
                """
            )
            if cur.fetchone() != (True,):
                raise RuntimeError(
                    "Lakebase integrity probe did not find the row-level "
                    "campaign decision lifecycle trigger"
                )

            cur.execute("CREATE TEMP TABLE mip_integrity_truncate_probe (id INTEGER)")
            cur.execute(
                """
                CREATE TRIGGER trg_mip_integrity_truncate_probe
                BEFORE TRUNCATE ON mip_integrity_truncate_probe
                FOR EACH STATEMENT
                EXECUTE FUNCTION mip_app.prevent_outreach_evidence_mutation()
                """
            )
            _expect_database_rejection(
                cur,
                savepoint="probe_truncate",
                statement="TRUNCATE mip_integrity_truncate_probe",
                expected_sqlstates=("42501",),
            )
            cur.execute("ROLLBACK TO SAVEPOINT outreach_integrity_probe")
            cur.execute("RELEASE SAVEPOINT outreach_integrity_probe")
            cur.execute(
                """
                SELECT
                    EXISTS(SELECT 1 FROM mip_app.campaigns WHERE campaign_id = %s),
                    EXISTS(SELECT 1 FROM mip_app.sales_team WHERE email = %s),
                    EXISTS(SELECT 1 FROM mip_app.approvals WHERE approval_id = %s),
                    EXISTS(
                        SELECT 1 FROM mip_app.action_audit
                        WHERE audit_id IN (%s, %s, %s, %s, %s)
                    ),
                    EXISTS(
                        SELECT 1 FROM mip_app.generated_outreach_drafts
                        WHERE generation_id = %s
                    ),
                    EXISTS(
                        SELECT 1 FROM mip_app.activation_outbox
                        WHERE activation_id = %s
                    ),
                    EXISTS(
                        SELECT 1 FROM mip_app.call_dispositions
                        WHERE disposition_id = %s
                    ),
                    EXISTS(
                        SELECT 1 FROM mip_app.lead_outcomes
                        WHERE outcome_id = %s
                    ),
                    EXISTS(
                        SELECT 1 FROM mip_app.growth_agent_runs
                        WHERE run_id = %s
                    )
                """,
                (
                    campaign_id,
                    actor,
                    approval_id,
                    audit_id,
                    generation_audit_id,
                    disposition_audit_id,
                    outcome_audit_id,
                    growth_audit_id,
                    generation_id,
                    activation_id,
                    disposition_id,
                    outcome_id,
                    growth_run_id,
                ),
            )
            if cur.fetchone() != (False,) * 9:
                raise RuntimeError("Lakebase integrity probe left transaction residue")
        if owns_connection:
            conn.rollback()
        print("[lakebase-migrate] pre-commit outreach integrity probe passed")
    except Exception:
        if owns_connection:
            conn.rollback()
        raise
    finally:
        if owns_connection:
            conn.close()

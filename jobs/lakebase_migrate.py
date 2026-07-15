"""Apply Lakebase schema + Summit Mortgage campaign seed.

Runs as a Databricks Jobs Python task (``mip_lakebase_migrate`` in
``databricks.yml``). Opens a psycopg3 connection to the
``mip-app-state`` Lakebase instance and executes, in order:

    1. The pre-seed portion of ``lakebase/schema.sql`` creates/upgrades all
       objects and installs NOT VALID compatibility constraints.
    2. ``lakebase/seed_campaigns.sql`` inserts stable, reviewed campaign
       variants before any legacy proof binding is inferred.
    3. The post-seed schema suffix deterministically backfills legacy proof,
       validates every constraint, and restores immutable triggers.

Auth model (self-contained, no env-var plumbing required):
    On Databricks the task runs under the workspace identity (service
    principal when deployed; user identity for `bundle run`). We fetch a
    fresh short-lived Postgres credential via
    ``WorkspaceClient().database.generate_database_credential(...)``
    and use the identity's user_name as the Postgres user. This avoids
    the OAuth-token-expiry problem of stuffing a long-lived password
    into .env.local / secret scope.

Env-var overrides (optional; used for local runs off Databricks):
    LAKEBASE_HOST              -- DNS name; otherwise resolved from the
                                  ``mip-app-state`` database_instance.
    LAKEBASE_USER              -- Postgres user; otherwise the current
                                  Databricks identity's user_name.
    LAKEBASE_PASSWORD          -- Postgres password; otherwise fetched
                                  via generate_database_credential.
    LAKEBASE_DATABASE          -- default ``mip_app_state``.
    LAKEBASE_PORT              -- default 5432.
    LAKEBASE_SSLMODE           -- default ``require``.
    LAKEBASE_INSTANCE_NAME     -- default ``mip-app-state``.

Exit codes:
    0 -- schema + seed applied cleanly.
    2 -- psycopg / Postgres error (full error printed for debugging).
    3 -- SDK / auth error resolving connection parameters.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from uuid import uuid4

_POST_SEED_MARKER = "-- MIP_LAKEBASE_POST_SEED_BEGIN"


def _resolve_connection() -> dict:
    """Return a dict of connection kwargs for psycopg.connect.

    Prefers env-var overrides; otherwise uses the Databricks SDK with
    the ambient workspace identity to resolve the DNS + fetch a fresh
    Postgres credential.
    """
    instance_name = os.environ.get("LAKEBASE_INSTANCE_NAME", "mip-app-state")
    host = os.environ.get("LAKEBASE_HOST")
    user = os.environ.get("LAKEBASE_USER")
    password = os.environ.get("LAKEBASE_PASSWORD")

    if host and user and password:
        return {
            "host": host,
            "port": int(os.environ.get("LAKEBASE_PORT", "5432")),
            "dbname": os.environ.get("LAKEBASE_DATABASE", "mip_app_state"),
            "user": user,
            "password": password,
            "sslmode": os.environ.get("LAKEBASE_SSLMODE", "require"),
        }

    # SDK-based resolution. Import lazily so --help doesn't require the
    # wheel and so local CI doesn't need the SDK unless resolution is
    # actually needed.
    try:
        from databricks.sdk import WorkspaceClient
    except ImportError:
        print(
            "[lakebase-migrate] databricks-sdk is not installed; either "
            "install it (`pip install databricks-sdk`) or set the "
            "LAKEBASE_* env vars explicitly.",
            file=sys.stderr,
        )
        sys.exit(3)

    try:
        w = WorkspaceClient()
        me = w.current_user.me()
        identity = me.user_name or me.display_name
        if not identity:
            print(
                "[lakebase-migrate] could not resolve current workspace " "identity user_name.",
                file=sys.stderr,
            )
            sys.exit(3)

        # Resolve via raw REST rather than the typed ``w.database`` service.
        # Older databricks-sdk builds (e.g. the baseline shipped with
        # serverless py_default) don't expose ``database`` as a typed
        # attribute; the underlying REST endpoints are stable, so
        # ``api_client.do`` is the portable surface.
        inst = w.api_client.do("GET", f"/api/2.0/database/instances/{instance_name}")
        resolved_host = host or inst.get("read_write_dns")
        if not resolved_host:
            print(
                f"[lakebase-migrate] instance {instance_name!r} has no "
                f"read_write_dns; check provisioning state.",
                file=sys.stderr,
            )
            sys.exit(3)

        cred = w.api_client.do(
            "POST",
            "/api/2.0/database/credentials",
            body={
                "request_id": (
                    f"mip-lakebase-migrate-" f"{os.environ.get('DATABRICKS_JOB_RUN_ID','local')}"
                ),
                "instance_names": [instance_name],
            },
        )
        cred_token = cred.get("token")
        if not cred_token:
            print(
                f"[lakebase-migrate] credential response missing token: {cred}",
                file=sys.stderr,
            )
            sys.exit(3)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 -- operator-facing
        print(
            f"[lakebase-migrate] SDK auth/resolution failed: {exc}",
            file=sys.stderr,
        )
        sys.exit(3)

    return {
        "host": resolved_host,
        "port": int(os.environ.get("LAKEBASE_PORT", "5432")),
        "dbname": os.environ.get("LAKEBASE_DATABASE", "mip_app_state"),
        "user": user or identity,
        "password": password or cred_token,
        "sslmode": os.environ.get("LAKEBASE_SSLMODE", "require"),
    }


def _run_transaction(
    sql_texts: tuple[str, ...],
    conn_kwargs: dict,
    *,
    verify_outreach_integrity: bool = False,
) -> None:
    import psycopg  # local import so `--help` still works without the wheel

    # Schema and seed are one deployment unit. PostgreSQL DDL is transactional,
    # so a seed/constraint failure must roll back the schema changes instead of
    # leaving a partially migrated database for the still-running app.
    conn = psycopg.connect(**conn_kwargs, autocommit=False)
    try:
        with conn.cursor() as cur:
            for sql_text in sql_texts:
                cur.execute(sql_text)
        if verify_outreach_integrity:
            _run_outreach_integrity_probe(conn_kwargs, connection=conn)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


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
    connection: object | None = None,
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
                    campaign_id, name, owner_email, status, criteria
                ) VALUES (%s, %s, %s, 'draft', '{}'::jsonb)
                """,
                (campaign_id, "Deployment integrity probe", actor),
            )
            cur.execute(
                """
                INSERT INTO mip_app.campaign_message_variants (
                    campaign_id, variant_name, channel, subject, body
                ) VALUES (%s, 'Integrity proof', 'email', %s, %s)
                """,
                (campaign_id, "Integrity proof", "Rollback-only deployment proof"),
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
                          'approve', %s, %s, 'approve', %s)
                """,
                (approval_id, campaign_id, borrower_id, actor, request_id, "0" * 64),
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
                savepoint="probe_variant_mismatch",
                statement="""
                    INSERT INTO mip_app.approvals (
                        approval_id, campaign_id, variant_name, channel,
                        borrower_id, action, actor_email, request_id
                    ) VALUES (%s, %s, 'Integrity proof', 'sms', %s,
                              'approve', %s, %s)
                """,
                params=(uuid4(), campaign_id, borrower_id, actor, str(uuid4())),
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
            trigger_count = int(cur.fetchone()[0])
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
            finalize_trigger_count = int(cur.fetchone()[0])
            if finalize_trigger_count != 4:
                raise RuntimeError(
                    "Lakebase integrity probe expected four row-level audit "
                    f"finalization triggers, found {finalize_trigger_count}"
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


def _repo_root() -> Path:
    """Return the repo root. Tolerates Databricks ipykernel exec
    contexts where ``__file__`` is not defined (the exec() path strips
    the module's ``__file__`` attribute)."""
    try:
        return Path(__file__).resolve().parents[1]
    except NameError as exc:
        # Databricks workspace runs upload the bundle under a known prefix.
        # Fall back to cwd + a couple of likely locations.
        for candidate in (Path.cwd(), Path.cwd() / "..", Path("/Workspace/Users")):
            for probe in candidate.rglob("lakebase/schema.sql"):
                return probe.parents[1]
        raise RuntimeError(
            "Cannot locate repo root — __file__ undefined and no lakebase/"
            "schema.sql found under cwd."
        ) from exc


# ---------------------------------------------------------------------------
# The app role is the Databricks App service principal client id. Lakebase
# bindings provision that exact value as the Postgres role name; app names,
# display names, and numeric ids are not interchangeable identities.
#
# Every base table is listed, including migration/operator-owned tables with
# no app privileges. Postflight compares this inventory to pg_class so a new
# table fails closed until its runtime access is reviewed explicitly.
# ---------------------------------------------------------------------------
_APP_ROLE_TABLE_PRIVILEGES: dict[str, tuple[str, ...]] = {
    "schema_migrations": (),
    "campaigns": ("SELECT", "INSERT", "UPDATE"),
    "campaign_message_variants": ("SELECT", "INSERT"),
    "tenant_disclosures": ("SELECT",),
    "sales_team": ("SELECT",),
    "lead_assignments": ("SELECT", "INSERT", "UPDATE"),
    "call_dispositions": ("SELECT", "INSERT", "UPDATE"),
    "approvals": ("SELECT", "INSERT", "UPDATE"),
    "saved_leads": ("SELECT", "INSERT", "UPDATE"),
    "outreach_drafts": ("SELECT", "INSERT", "UPDATE"),
    "activation_destinations": ("SELECT",),
    "activation_outbox": ("SELECT", "INSERT", "UPDATE"),
    "lead_outcomes": ("SELECT", "INSERT", "UPDATE"),
    "action_audit": ("SELECT", "INSERT"),
    "action_audit_archive_runs": (),
    "generated_outreach_drafts": ("SELECT", "INSERT"),
    "genie_sessions": ("SELECT", "INSERT", "UPDATE"),
    "genie_messages": ("SELECT", "INSERT"),
    "genie_cohorts": ("SELECT", "INSERT"),
    "genie_cohort_members": ("SELECT", "INSERT", "UPDATE"),
    "agent_sessions": (),
    "growth_agent_runs": ("SELECT", "INSERT", "UPDATE"),
    "growth_agent_monitors": ("SELECT", "INSERT", "UPDATE"),
    "growth_agent_notification_drafts": ("SELECT", "INSERT", "UPDATE"),
    "ai_gateway_proof_ledger": ("SELECT",),
    "feedback": ("SELECT", "INSERT", "UPDATE"),
    "loan_officers": ("SELECT",),
    "kpi_snapshots": ("SELECT",),
    "user_visits": ("SELECT", "INSERT"),
    "genie_feedback_requests": ("SELECT", "INSERT", "UPDATE"),
}

_APP_ROLE_SEQUENCE_PRIVILEGES: dict[str, tuple[str, ...]] = {
    "action_audit_audit_sequence_seq": ("USAGE",),
}

_AI_GATEWAY_VERIFIER_TABLE_PRIVILEGES = ("SELECT", "INSERT", "UPDATE")
_VERIFIER_OPTIONAL_APP_ENVS = frozenset(
    {"local", "test", "testing", "pytest", "dev", "development", "sandbox"}
)
_VERIFIER_OPTIONAL_BUNDLE_TARGETS = frozenset({"", "local", "test", "ci", "dev"})

_TABLE_PRIVILEGE_NAMES = (
    "SELECT",
    "INSERT",
    "UPDATE",
    "DELETE",
    "TRUNCATE",
    "REFERENCES",
    "TRIGGER",
)
_SEQUENCE_PRIVILEGE_NAMES = ("USAGE", "SELECT", "UPDATE")


def _resolve_app_role(workspace_client: object | None = None) -> str:
    """Resolve the one authoritative Lakebase role for the Databricks App."""
    app_name = os.environ.get("MIP_APP_NAME", "mip-app")
    if workspace_client is None:
        try:
            from databricks.sdk import WorkspaceClient
        except ImportError as exc:
            raise RuntimeError(
                "databricks-sdk is required to resolve the Databricks App role"
            ) from exc
        workspace_client = WorkspaceClient()

    try:
        app = workspace_client.apps.get(app_name)  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001 -- deployment must fail closed
        raise RuntimeError(
            f"Databricks Apps lookup failed for {app_name!r}: {type(exc).__name__}"
        ) from exc

    client_id = getattr(app, "service_principal_client_id", None)
    role = str(client_id).strip() if client_id is not None else ""
    if not role:
        raise RuntimeError(f"Databricks App {app_name!r} is missing service_principal_client_id")
    return role


def _resolve_ai_gateway_verifier_role() -> str | None:
    """Resolve the verifier writer role, permitting omission only in dev/test."""

    role = os.environ.get("MIP_AI_GATEWAY_VERIFIER_CLIENT_ID", "").strip()
    if role:
        return role

    app_env = os.environ.get("APP_ENV", "local").strip().lower() or "local"
    bundle_target = os.environ.get("DATABRICKS_BUNDLE_TARGET", "").strip().lower()
    if (
        app_env not in _VERIFIER_OPTIONAL_APP_ENVS
        or bundle_target not in _VERIFIER_OPTIONAL_BUNDLE_TARGETS
    ):
        raise RuntimeError(
            "MIP_AI_GATEWAY_VERIFIER_CLIENT_ID is required outside dev/test "
            f"(APP_ENV={app_env!r}, DATABRICKS_BUNDLE_TARGET={bundle_target!r})"
        )
    return None


def _raise_object_inventory_mismatch(
    object_type: str,
    *,
    actual: set[str],
    expected: set[str],
) -> None:
    if actual == expected:
        return
    missing = sorted(expected - actual)
    unexpected = sorted(actual - expected)
    raise RuntimeError(
        f"Lakebase {object_type} inventory mismatch: " f"missing={missing}, unexpected={unexpected}"
    )


def _postflight_app_role_grants(cur: object, role: str) -> None:
    """Verify the app role's effective privileges exactly match the matrix."""
    cur.execute(  # type: ignore[attr-defined]
        "SELECT rolname FROM pg_roles WHERE rolname = %s",
        (role,),
    )
    role_rows = cur.fetchall()  # type: ignore[attr-defined]
    if role_rows != [(role,)]:
        raise RuntimeError(f"Lakebase app-role postflight could not verify exact role {role!r}")

    cur.execute(  # type: ignore[attr-defined]
        """
        SELECT
            has_database_privilege(%s, current_database(), 'CONNECT'),
            has_database_privilege(%s, current_database(), 'CREATE'),
            has_schema_privilege(%s, 'mip_app', 'USAGE'),
            has_schema_privilege(%s, 'mip_app', 'CREATE')
        """,
        (role, role, role, role),
    )
    database_connect, database_create, schema_usage, schema_create = cur.fetchone()  # type: ignore[attr-defined]
    if not database_connect or database_create or not schema_usage or schema_create:
        raise RuntimeError(
            "Lakebase app-role database/schema postflight failed for "
            f"{role!r}: database_connect={database_connect}, "
            f"database_create={database_create}, schema_usage={schema_usage}, "
            f"schema_create={schema_create}"
        )

    cur.execute(  # type: ignore[attr-defined]
        """
        SELECT c.relname
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'mip_app' AND c.relkind IN ('r', 'p')
        ORDER BY c.relname
        """
    )
    actual_tables = {row[0] for row in cur.fetchall()}  # type: ignore[attr-defined]
    expected_tables = set(_APP_ROLE_TABLE_PRIVILEGES)
    _raise_object_inventory_mismatch("table", actual=actual_tables, expected=expected_tables)

    cur.execute(  # type: ignore[attr-defined]
        """
        SELECT c.relname, privilege.name
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        CROSS JOIN unnest(%s::text[]) AS privilege(name)
        WHERE n.nspname = 'mip_app'
          AND c.relkind IN ('r', 'p')
          AND has_table_privilege(%s, c.oid, privilege.name)
        ORDER BY c.relname, privilege.name
        """,
        (list(_TABLE_PRIVILEGE_NAMES), role),
    )
    actual_table_privileges = {table: set() for table in actual_tables}
    for table, privilege in cur.fetchall():  # type: ignore[attr-defined]
        actual_table_privileges.setdefault(table, set()).add(privilege)

    delete_tables = sorted(
        table for table, privileges in actual_table_privileges.items() if "DELETE" in privileges
    )
    if delete_tables:
        raise RuntimeError(f"Lakebase app role {role!r} has forbidden DELETE on {delete_tables}")

    expected_table_privileges = {
        table: set(privileges) for table, privileges in _APP_ROLE_TABLE_PRIVILEGES.items()
    }
    if actual_table_privileges != expected_table_privileges:
        raise RuntimeError(
            "Lakebase app-role table privilege postflight failed for "
            f"{role!r}: actual={actual_table_privileges}, "
            f"expected={expected_table_privileges}"
        )

    cur.execute(  # type: ignore[attr-defined]
        """
        SELECT c.relname
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'mip_app' AND c.relkind = 'S'
        ORDER BY c.relname
        """
    )
    actual_sequences = {row[0] for row in cur.fetchall()}  # type: ignore[attr-defined]
    expected_sequences = set(_APP_ROLE_SEQUENCE_PRIVILEGES)
    _raise_object_inventory_mismatch(
        "sequence", actual=actual_sequences, expected=expected_sequences
    )

    cur.execute(  # type: ignore[attr-defined]
        """
        SELECT c.relname, privilege.name
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        CROSS JOIN unnest(%s::text[]) AS privilege(name)
        WHERE n.nspname = 'mip_app'
          AND c.relkind = 'S'
          AND has_sequence_privilege(%s, c.oid, privilege.name)
        ORDER BY c.relname, privilege.name
        """,
        (list(_SEQUENCE_PRIVILEGE_NAMES), role),
    )
    actual_sequence_privileges = {sequence: set() for sequence in actual_sequences}
    for sequence, privilege in cur.fetchall():  # type: ignore[attr-defined]
        actual_sequence_privileges.setdefault(sequence, set()).add(privilege)
    expected_sequence_privileges = {
        sequence: set(privileges) for sequence, privileges in _APP_ROLE_SEQUENCE_PRIVILEGES.items()
    }
    if actual_sequence_privileges != expected_sequence_privileges:
        raise RuntimeError(
            "Lakebase app-role sequence privilege postflight failed for "
            f"{role!r}: actual={actual_sequence_privileges}, "
            f"expected={expected_sequence_privileges}"
        )

    cur.execute(  # type: ignore[attr-defined]
        """
        SELECT e.privilege_type
        FROM pg_default_acl d
        JOIN pg_namespace n ON n.oid = d.defaclnamespace
        CROSS JOIN LATERAL aclexplode(d.defaclacl) e
        JOIN pg_roles grantee ON grantee.oid = e.grantee
        WHERE n.nspname = 'mip_app'
          AND d.defaclobjtype = 'S'
          AND grantee.rolname = %s
        """,
        (role,),
    )
    default_sequence_privileges = [row[0] for row in cur.fetchall()]  # type: ignore[attr-defined]
    if default_sequence_privileges:
        raise RuntimeError(
            "Lakebase app role retains forbidden future-sequence default "
            f"privileges: {sorted(default_sequence_privileges)}"
        )


def _postflight_ai_gateway_verifier_grants(cur: object, role: str) -> None:
    """Verify the verifier can write only the AI Gateway proof ledger."""

    cur.execute(  # type: ignore[attr-defined]
        "SELECT rolname FROM pg_roles WHERE rolname = %s",
        (role,),
    )
    if cur.fetchall() != [(role,)]:  # type: ignore[attr-defined]
        raise RuntimeError(
            "Lakebase AI Gateway verifier postflight could not verify exact " f"role {role!r}"
        )

    cur.execute(  # type: ignore[attr-defined]
        """
        SELECT
            has_database_privilege(%s, current_database(), 'CONNECT'),
            has_database_privilege(%s, current_database(), 'CREATE'),
            has_schema_privilege(%s, 'mip_app', 'USAGE'),
            has_schema_privilege(%s, 'mip_app', 'CREATE')
        """,
        (role, role, role, role),
    )
    database_connect, database_create, schema_usage, schema_create = cur.fetchone()  # type: ignore[attr-defined]
    if not database_connect or database_create or not schema_usage or schema_create:
        raise RuntimeError(
            "Lakebase AI Gateway verifier database/schema postflight failed for "
            f"{role!r}: database_connect={database_connect}, "
            f"database_create={database_create}, schema_usage={schema_usage}, "
            f"schema_create={schema_create}"
        )

    cur.execute(  # type: ignore[attr-defined]
        """
        SELECT c.relname
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'mip_app' AND c.relkind IN ('r', 'p')
        ORDER BY c.relname
        """
    )
    actual_tables = {row[0] for row in cur.fetchall()}  # type: ignore[attr-defined]
    expected_tables = set(_APP_ROLE_TABLE_PRIVILEGES)
    _raise_object_inventory_mismatch(
        "AI Gateway verifier table",
        actual=actual_tables,
        expected=expected_tables,
    )

    cur.execute(  # type: ignore[attr-defined]
        """
        SELECT c.relname, privilege.name
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        CROSS JOIN unnest(%s::text[]) AS privilege(name)
        WHERE n.nspname = 'mip_app'
          AND c.relkind IN ('r', 'p')
          AND has_table_privilege(%s, c.oid, privilege.name)
        ORDER BY c.relname, privilege.name
        """,
        (list(_TABLE_PRIVILEGE_NAMES), role),
    )
    actual_table_privileges = {table: set() for table in actual_tables}
    for table, privilege in cur.fetchall():  # type: ignore[attr-defined]
        actual_table_privileges.setdefault(table, set()).add(privilege)
    expected_table_privileges = {table: set() for table in expected_tables}
    expected_table_privileges["ai_gateway_proof_ledger"] = set(
        _AI_GATEWAY_VERIFIER_TABLE_PRIVILEGES
    )
    if actual_table_privileges != expected_table_privileges:
        raise RuntimeError(
            "Lakebase AI Gateway verifier table privilege postflight failed for "
            f"{role!r}: actual={actual_table_privileges}, "
            f"expected={expected_table_privileges}"
        )

    cur.execute(  # type: ignore[attr-defined]
        """
        SELECT n.nspname, c.relname, privilege.name
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        CROSS JOIN unnest(%s::text[]) AS privilege(name)
        WHERE c.relkind IN ('r', 'p')
          AND n.nspname NOT IN ('pg_catalog', 'information_schema')
          AND n.nspname !~ '^pg_'
          AND NOT (
              n.nspname = 'mip_app'
              AND c.relname = 'ai_gateway_proof_ledger'
          )
          AND has_table_privilege(%s, c.oid, privilege.name)
        ORDER BY n.nspname, c.relname, privilege.name
        """,
        (list(_TABLE_PRIVILEGE_NAMES), role),
    )
    other_table_privileges = cur.fetchall()  # type: ignore[attr-defined]
    if other_table_privileges:
        raise RuntimeError(
            "Lakebase AI Gateway verifier has forbidden privileges on other tables: "
            f"{other_table_privileges}"
        )

    cur.execute(  # type: ignore[attr-defined]
        """
        SELECT c.relname
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'mip_app' AND c.relkind = 'S'
        ORDER BY c.relname
        """
    )
    actual_sequences = {row[0] for row in cur.fetchall()}  # type: ignore[attr-defined]
    expected_sequences = set(_APP_ROLE_SEQUENCE_PRIVILEGES)
    _raise_object_inventory_mismatch(
        "AI Gateway verifier sequence",
        actual=actual_sequences,
        expected=expected_sequences,
    )

    cur.execute(  # type: ignore[attr-defined]
        """
        SELECT n.nspname, c.relname, privilege.name
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        CROSS JOIN unnest(%s::text[]) AS privilege(name)
        WHERE c.relkind = 'S'
          AND n.nspname NOT IN ('pg_catalog', 'information_schema')
          AND n.nspname !~ '^pg_'
          AND has_sequence_privilege(%s, c.oid, privilege.name)
        ORDER BY n.nspname, c.relname, privilege.name
        """,
        (list(_SEQUENCE_PRIVILEGE_NAMES), role),
    )
    verifier_sequence_privileges = cur.fetchall()  # type: ignore[attr-defined]
    if verifier_sequence_privileges:
        raise RuntimeError(
            "Lakebase AI Gateway verifier has forbidden sequence privileges: "
            f"{verifier_sequence_privileges}"
        )

    cur.execute(  # type: ignore[attr-defined]
        """
        SELECT d.defaclobjtype, e.privilege_type
        FROM pg_default_acl d
        JOIN pg_namespace n ON n.oid = d.defaclnamespace
        CROSS JOIN LATERAL aclexplode(d.defaclacl) e
        JOIN pg_roles grantee ON grantee.oid = e.grantee
        WHERE n.nspname = 'mip_app'
          AND d.defaclobjtype IN ('r', 'S')
          AND grantee.rolname = %s
        """,
        (role,),
    )
    default_privileges = cur.fetchall()  # type: ignore[attr-defined]
    if default_privileges:
        raise RuntimeError(
            "Lakebase AI Gateway verifier retains forbidden default privileges: "
            f"{default_privileges}"
        )


def _apply_app_role_grants(
    conn_kwargs: dict,
    *,
    role_wait_timeout_s: float | None = None,
    role_wait_interval_s: float | None = None,
) -> None:
    import psycopg
    from psycopg import sql as psql

    role = _resolve_app_role()
    verifier_role = _resolve_ai_gateway_verifier_role()
    if verifier_role == role:
        raise RuntimeError(
            "MIP_AI_GATEWAY_VERIFIER_CLIENT_ID must identify a role distinct "
            "from the Databricks App runtime role"
        )
    timeout_s = (
        float(os.environ.get("MIP_LAKEBASE_APP_ROLE_WAIT_TIMEOUT_S", "120"))
        if role_wait_timeout_s is None
        else role_wait_timeout_s
    )
    interval_s = (
        float(os.environ.get("MIP_LAKEBASE_APP_ROLE_WAIT_INTERVAL_S", "5"))
        if role_wait_interval_s is None
        else role_wait_interval_s
    )
    if timeout_s < 0 or interval_s <= 0:
        raise ValueError("Lakebase app-role wait settings must be timeout >= 0 and interval > 0")

    conn = psycopg.connect(**conn_kwargs, autocommit=False)
    try:
        with conn.cursor() as cur:
            roles = [("app", role)]
            if verifier_role is not None:
                roles.append(("AI Gateway verifier", verifier_role))
            for role_label, database_role in roles:
                deadline = time.monotonic() + timeout_s
                while True:
                    cur.execute(
                        "SELECT rolname FROM pg_roles WHERE rolname = %s",
                        (database_role,),
                    )
                    present = cur.fetchall()
                    if present == [(database_role,)]:
                        break
                    if present:
                        raise RuntimeError(
                            "Lakebase role lookup returned a non-exact identity for "
                            f"{database_role!r}: {present}"
                        )
                    now = time.monotonic()
                    if now >= deadline:
                        raise RuntimeError(
                            f"authoritative {role_label} role not found in pg_roles "
                            f"before the Lakebase grant timeout: {database_role!r}"
                        )
                    wait_s = min(interval_s, max(0.0, deadline - now))
                    print(
                        f"[lakebase-migrate] authoritative {role_label} database role "
                        f"not visible yet; retrying in {wait_s:g}s"
                    )
                    time.sleep(wait_s)

            # End the read-only role-discovery transaction before starting the
            # failure-atomic ACL reconciliation transaction.
            conn.commit()

            cur.execute("SELECT current_database()")
            database_name = str(cur.fetchone()[0])

            role_identifier = psql.Identifier(role).as_string()
            verifier_role_identifier = (
                psql.Identifier(verifier_role).as_string() if verifier_role is not None else None
            )
            database_identifier = psql.Identifier(database_name).as_string()
            schema_identifier = psql.Identifier("mip_app").as_string()
            table_identifiers = {
                table: psql.Identifier("mip_app", table).as_string()
                for table in _APP_ROLE_TABLE_PRIVILEGES
            }
            sequence_identifiers = {
                sequence: psql.Identifier("mip_app", sequence).as_string()
                for sequence in _APP_ROLE_SEQUENCE_PRIVILEGES
            }

            # Remove prior broad/direct/default access before adding the exact
            # current matrix. All revokes run before the first grant.
            cur.execute(f"REVOKE CREATE ON DATABASE {database_identifier} FROM {role_identifier}")
            cur.execute(
                f"REVOKE ALL PRIVILEGES ON SCHEMA {schema_identifier} " f"FROM {role_identifier}"
            )
            for table_identifier in table_identifiers.values():
                cur.execute(
                    f"REVOKE ALL PRIVILEGES ON TABLE {table_identifier} " f"FROM {role_identifier}"
                )
            for sequence_identifier in sequence_identifiers.values():
                cur.execute(
                    f"REVOKE ALL PRIVILEGES ON SEQUENCE {sequence_identifier} "
                    f"FROM {role_identifier}"
                )
            cur.execute(
                f"ALTER DEFAULT PRIVILEGES IN SCHEMA {schema_identifier} "
                "REVOKE ALL PRIVILEGES ON SEQUENCES "
                f"FROM {role_identifier}"
            )
            if verifier_role_identifier is not None:
                cur.execute(
                    f"REVOKE CREATE ON DATABASE {database_identifier} "
                    f"FROM {verifier_role_identifier}"
                )
                cur.execute(
                    f"REVOKE ALL PRIVILEGES ON SCHEMA {schema_identifier} "
                    f"FROM {verifier_role_identifier}"
                )
                for table_identifier in table_identifiers.values():
                    cur.execute(
                        f"REVOKE ALL PRIVILEGES ON TABLE {table_identifier} "
                        f"FROM {verifier_role_identifier}"
                    )
                for sequence_identifier in sequence_identifiers.values():
                    cur.execute(
                        f"REVOKE ALL PRIVILEGES ON SEQUENCE {sequence_identifier} "
                        f"FROM {verifier_role_identifier}"
                    )
                cur.execute(
                    f"ALTER DEFAULT PRIVILEGES IN SCHEMA {schema_identifier} "
                    "REVOKE ALL PRIVILEGES ON TABLES "
                    f"FROM {verifier_role_identifier}"
                )
                cur.execute(
                    f"ALTER DEFAULT PRIVILEGES IN SCHEMA {schema_identifier} "
                    "REVOKE ALL PRIVILEGES ON SEQUENCES "
                    f"FROM {verifier_role_identifier}"
                )

            cur.execute(f"GRANT USAGE ON SCHEMA {schema_identifier} TO {role_identifier}")
            for table, privileges in _APP_ROLE_TABLE_PRIVILEGES.items():
                if not privileges:
                    continue
                cur.execute(
                    f"GRANT {', '.join(privileges)} ON TABLE "
                    f"{table_identifiers[table]} TO {role_identifier}"
                )
            for sequence, privileges in _APP_ROLE_SEQUENCE_PRIVILEGES.items():
                cur.execute(
                    f"GRANT {', '.join(privileges)} ON SEQUENCE "
                    f"{sequence_identifiers[sequence]} TO {role_identifier}"
                )
            if verifier_role_identifier is not None:
                cur.execute(
                    f"GRANT USAGE ON SCHEMA {schema_identifier} " f"TO {verifier_role_identifier}"
                )
                cur.execute(
                    "GRANT "
                    f"{', '.join(_AI_GATEWAY_VERIFIER_TABLE_PRIVILEGES)} "
                    f"ON TABLE {table_identifiers['ai_gateway_proof_ledger']} "
                    f"TO {verifier_role_identifier}"
                )

            _postflight_app_role_grants(cur, role)
            if verifier_role is not None:
                _postflight_ai_gateway_verifier_grants(cur, verifier_role)
            conn.commit()
            verifier_summary = (
                f"; AI Gateway verifier grants applied to {verifier_role!r}"
                if verifier_role is not None
                else "; verifier role omitted for dev/test"
            )
            print(f"[lakebase-migrate] app-role grants applied to {role!r}" f"{verifier_summary}")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main() -> None:
    conn_kwargs = _resolve_connection()
    repo_root = _repo_root()
    schema_sql = (repo_root / "lakebase" / "schema.sql").read_text(encoding="utf-8")
    seed_sql = (repo_root / "lakebase" / "seed_campaigns.sql").read_text(encoding="utf-8")
    pre_seed_schema_sql, post_seed_schema_sql = _split_schema_sql(schema_sql)

    try:
        _run_transaction(
            (pre_seed_schema_sql, seed_sql, post_seed_schema_sql),
            conn_kwargs,
            verify_outreach_integrity=True,
        )
        print(
            "[lakebase-migrate] schema + Summit Mortgage seed + integrity proof "
            "applied atomically"
        )
    except Exception as exc:  # noqa: BLE001 -- operator-facing failure
        print(f"[lakebase-migrate] failed: {exc}", file=sys.stderr)
        sys.exit(2)

    # Grants are part of migration correctness. A successful schema with an
    # unusable runtime role is a false-green deploy: every audited mutation
    # fails even though SELECT 1 health remains green.
    try:
        _apply_app_role_grants(conn_kwargs)
    except Exception as exc:  # noqa: BLE001 -- operator-facing deployment gate
        print(
            "[lakebase-migrate] app-role grants failed "
            f"({type(exc).__name__}: {exc}); refusing a false-green deploy. "
            "See docs/security/GRANTS.md §Lakebase.",
            file=sys.stderr,
        )
        sys.exit(2)


if __name__ == "__main__":
    main()

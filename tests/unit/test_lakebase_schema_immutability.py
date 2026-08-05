"""Lakebase contracts for campaign idempotency and immutable outreach proof."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_SCHEMA = Path("lakebase/schema.sql").read_text(encoding="utf-8")
_SEED = Path("lakebase/seed_campaigns.sql").read_text(encoding="utf-8")


def _migration_source() -> str:
    jobs = Path("jobs")
    paths = [jobs / "lakebase_migrate.py", *sorted(jobs.glob("lakebase_migration_*.py"))]
    return "\n".join(path.read_text(encoding="utf-8") for path in paths)


def _table_ddl(table: str) -> str:
    match = re.search(
        rf"CREATE TABLE IF NOT EXISTS mip_app\.{table} \((.*?)\n\);",
        _SCHEMA,
        flags=re.DOTALL,
    )
    assert match is not None
    return match.group(1)


def test_campaign_idempotency_is_owner_scoped_and_hash_backed() -> None:
    campaigns = _table_ddl("campaigns")

    assert "owner_email  TEXT NOT NULL" in campaigns
    assert "idempotency_key TEXT" in campaigns
    assert "request_payload_hash TEXT" in campaigns
    assert re.search(
        r"CREATE UNIQUE INDEX IF NOT EXISTS idx_campaigns_owner_idempotency\s+"
        r"ON mip_app\.campaigns \(owner_email, idempotency_key\)\s+"
        r"WHERE idempotency_key IS NOT NULL;",
        _SCHEMA,
    )
    assert "2026_07_14_campaign_idempotency" in _SCHEMA


def test_generated_outreach_campaign_id_is_uuid_fk_with_safe_text_migration() -> None:
    generated = _table_ddl("generated_outreach_drafts")

    assert "campaign_id    UUID" in generated
    assert "LOCK TABLE mip_app.generated_outreach_drafts IN ACCESS EXCLUSIVE MODE" in (_SCHEMA)
    assert "campaign_id IS NOT NULL" in _SCHEMA
    assert "malformed UUID value" in _SCHEMA
    assert "orphaned campaign reference" in _SCHEMA
    assert "ALTER COLUMN campaign_id TYPE UUID USING campaign_id::uuid" in _SCHEMA
    assert "generated_outreach_campaign_variant_channel_fkey" in _SCHEMA


def test_fresh_install_never_drops_a_trigger_before_its_table_exists() -> None:
    for match in re.finditer(
        r"DROP TRIGGER IF EXISTS [a-z_]+\s+ON mip_app\.([a-z_]+);",
        _SCHEMA,
        flags=re.MULTILINE,
    ):
        table = match.group(1)
        create_position = _SCHEMA.find(f"CREATE TABLE IF NOT EXISTS mip_app.{table} (")
        assert create_position >= 0, table
        assert create_position < match.start(), table


def test_seed_precedes_legacy_proof_backfill_and_hard_validation() -> None:
    from jobs import lakebase_migrate

    pre_seed, post_seed = lakebase_migrate._split_schema_sql(_SCHEMA)

    assert "CREATE TABLE IF NOT EXISTS mip_app.generated_outreach_drafts" in pre_seed
    assert "INSERT INTO mip_app.campaign_message_variants" in _SEED
    assert "UPDATE mip_app.approvals AS approval" in post_seed
    assert "UPDATE mip_app.generated_outreach_drafts AS draft" in post_seed
    assert "VALIDATE CONSTRAINT approvals_campaign_variant_channel_fkey" in post_seed
    assert "VALIDATE CONSTRAINT generated_outreach_campaign_variant_channel_fkey" in post_seed
    assert "CREATE TRIGGER trg_approvals_finalize_only" in post_seed
    assert "CREATE TRIGGER trg_approvals_campaign_lifecycle" in post_seed
    assert "CREATE TRIGGER trg_generated_outreach_drafts_immutable" in post_seed


def test_campaign_json_shape_checks_are_post_seed_not_valid_and_deploy_probed() -> None:
    from jobs import lakebase_migrate

    pre_seed, post_seed = lakebase_migrate._split_schema_sql(_SCHEMA)
    migrate_source = _migration_source()
    constraints = {
        "campaigns_criteria_reviewed_shape_chk": "campaign_criteria_is_reviewed",
        "campaigns_suppression_policy_reviewed_shape_chk": (
            "campaign_suppression_policy_is_reviewed"
        ),
        "campaigns_channel_cascade_reviewed_shape_chk": ("campaign_channel_cascade_is_reviewed"),
        "campaigns_send_window_reviewed_shape_chk": "campaign_send_window_is_reviewed",
        "campaigns_holdout_reviewed_shape_chk": "campaign_holdout_is_reviewed",
        "campaigns_roi_assumptions_reviewed_shape_chk": ("campaign_roi_assumptions_is_reviewed"),
    }

    for constraint, function_name in constraints.items():
        assert f"DROP CONSTRAINT IF EXISTS {constraint};" in pre_seed
        assert re.search(
            rf"ADD CONSTRAINT {constraint}\s+CHECK \(.*?"
            rf"mip_app\.{function_name}\(.*?\) IS TRUE.*?\)\s+NOT VALID;",
            post_seed,
            flags=re.DOTALL,
        )
        assert f'"{constraint}"' in migrate_source
    assert "VALIDATE CONSTRAINT campaigns_" not in post_seed
    assert "2026_07_15_campaign_json_reviewed_shapes" in post_seed
    assert "json_contract_version SMALLINT NOT NULL DEFAULT 1" in pre_seed
    assert "SET json_contract_version = 0" in pre_seed
    assert "CREATE OR REPLACE FUNCTION mip_app.enforce_campaign_json_contract()" in pre_seed
    assert re.search(
        r"CREATE TRIGGER trg_campaigns_json_contract_enforcement\s+"
        r"BEFORE INSERT OR UPDATE ON mip_app\.campaigns\s+"
        r"FOR EACH ROW\s+"
        r"EXECUTE FUNCTION mip_app\.enforce_campaign_json_contract\(\);",
        pre_seed,
    )
    assert post_seed.count("json_contract_version = 0") == len(constraints)
    assert "2026_07_15_campaign_json_contract_version" in post_seed
    assert "trg_campaigns_json_contract_enforcement" in migrate_source
    assert "document->>'route' = '/lead-queue'" in pre_seed
    assert "document->>'route' LIKE '/lead-queue?%'" in pre_seed
    assert "document->>'route' LIKE '/lead-queue%'" not in pre_seed
    for savepoint in (
        "probe_campaign_criteria_shape",
        "probe_campaign_suppression_shape",
        "probe_campaign_cascade_shape",
        "probe_campaign_send_window_shape",
        "probe_campaign_holdout_shape",
        "probe_campaign_roi_shape",
    ):
        assert savepoint in migrate_source


def test_recurring_apply_has_no_destructive_proof_or_outbox_dml() -> None:
    destructive_dml = re.compile(
        r"^\s*(?:DELETE\s+FROM|TRUNCATE(?:\s+TABLE)?)\s+"
        r"mip_app\.(?:approvals|generated_outreach_drafts|activation_outbox)\b",
        flags=re.IGNORECASE | re.MULTILINE,
    )

    assert destructive_dml.search(_SCHEMA) is None
    assert destructive_dml.search(_SEED) is None


def test_legacy_approval_ids_are_exactly_mapped_or_validation_fails() -> None:
    expected_mappings = {
        "B-48291": "B-0CPWBTJMAPFY2",
        "B-48294": "B-1IB0UGBTFYM20",
        "B-48295": "B-102FL7THC6Q3L",
        "B-48292": "B-1BCZXFQYCX715",
        "B-48293": "B-1VU4FO4XBQPC4",
    }

    for legacy_id, borrower_id in expected_mappings.items():
        assert f"'{legacy_id}', '{borrower_id}'" in _SCHEMA
    assert "VALIDATE CONSTRAINT approvals_borrower_id_format_chk" in _SCHEMA
    assert "approvals_borrower_id_format_chk left NOT VALID" not in _SCHEMA


def test_outreach_proof_is_bound_to_exact_campaign_variant_and_channel() -> None:
    approvals = _table_ddl("approvals")
    generated = _table_ddl("generated_outreach_drafts")

    assert "variant_name  TEXT" in approvals
    assert "channel       TEXT CHECK (channel IN ('email','sms','direct_mail'))" in approvals
    assert "variant_name   TEXT" in generated
    assert (
        "channel        TEXT NOT NULL CHECK (channel IN ('email','sms','direct_mail'))" in generated
    )
    assert "CHECK ((campaign_id IS NULL) = (variant_name IS NULL)) NOT VALID" in _SCHEMA
    assert re.search(
        r"ADD CONSTRAINT approvals_channel_chk\s+"
        r"CHECK \(channel IN \('email','sms','direct_mail'\)\) NOT VALID;",
        _SCHEMA,
    )
    assert re.search(
        r"ADD CONSTRAINT approvals_channel_required_chk\s+"
        r"CHECK \(campaign_id IS NULL OR channel IS NOT NULL\) NOT VALID;",
        _SCHEMA,
    )
    for constraint, table in (
        ("approvals_campaign_variant_channel_fkey", "approvals"),
        ("generated_outreach_campaign_variant_channel_fkey", "generated_outreach_drafts"),
    ):
        assert constraint in _SCHEMA
        assert re.search(
            rf"ALTER TABLE mip_app\.{table}.*?ADD CONSTRAINT {constraint}.*?"
            r"FOREIGN KEY \(campaign_id, variant_name, channel\).*?"
            r"REFERENCES mip_app\.campaign_message_variants"
            r"\(campaign_id, variant_name, channel\).*?NOT VALID;",
            _SCHEMA,
            flags=re.DOTALL,
        )
    assert "2026_07_14_outreach_variant_binding" in _SCHEMA


def test_campaignless_legacy_approvals_remain_campaignless() -> None:
    from jobs import lakebase_migrate

    _pre_seed, post_seed = lakebase_migrate._split_schema_sql(_SCHEMA)

    assert "CHECK (campaign_id IS NULL OR channel IS NOT NULL) NOT VALID" in post_seed
    assert "approval.campaign_id IS NOT NULL AND approval.channel IS NULL" in post_seed
    assert not re.search(
        r"UPDATE mip_app\.approvals.*?SET\s+campaign_id\s*=",
        post_seed,
        flags=re.DOTALL,
    )


@pytest.mark.parametrize(
    ("table", "trigger"),
    [
        (
            "generated_outreach_drafts",
            "trg_generated_outreach_drafts_immutable",
        ),
        (
            "campaign_message_variants",
            "trg_campaign_message_variants_immutable",
        ),
    ],
)
def test_outreach_evidence_tables_have_statement_level_immutable_triggers(
    table: str,
    trigger: str,
) -> None:
    pattern = (
        rf"CREATE TRIGGER {trigger}\s+"
        rf"BEFORE UPDATE OR DELETE OR TRUNCATE ON mip_app\.{table}\s+"
        r"FOR EACH STATEMENT\s+"
        r"EXECUTE FUNCTION mip_app\.prevent_outreach_evidence_mutation\(\);"
    )

    assert re.search(pattern, _SCHEMA)


def test_action_audit_append_only_trigger_is_preserved() -> None:
    assert "CREATE OR REPLACE FUNCTION mip_app.prevent_action_audit_mutation()" in (_SCHEMA)
    assert re.search(
        r"CREATE TRIGGER trg_action_audit_append_only\s+"
        r"BEFORE UPDATE OR DELETE OR TRUNCATE ON mip_app\.action_audit\s+"
        r"FOR EACH STATEMENT\s+"
        r"EXECUTE FUNCTION mip_app\.prevent_action_audit_mutation\(\);",
        _SCHEMA,
    )


def test_approvals_only_allow_one_time_audit_finalization() -> None:
    assert "CREATE OR REPLACE FUNCTION mip_app.enforce_approval_finalize_only()" in _SCHEMA
    assert "to_jsonb(NEW) - ARRAY['decision_response', 'audit_event_id']" in _SCHEMA
    assert "OLD.decision_response IS NOT NULL" in _SCHEMA
    assert "NEW.audit_event_id IS NULL" in _SCHEMA
    assert re.search(
        r"CREATE TRIGGER trg_approvals_finalize_only\s+"
        r"BEFORE UPDATE ON mip_app\.approvals\s+"
        r"FOR EACH ROW\s+"
        r"EXECUTE FUNCTION mip_app\.enforce_approval_finalize_only\(\);",
        _SCHEMA,
    )


def test_campaign_decision_insert_locks_and_revalidates_lifecycle_proof() -> None:
    assert "CREATE OR REPLACE FUNCTION mip_app.enforce_campaign_decision_lifecycle()" in _SCHEMA
    assert "FROM mip_app.campaigns" in _SCHEMA
    assert "FOR SHARE;" in _SCHEMA
    assert "campaign_status NOT IN ('approved', 'live', 'active')" in _SCHEMA
    assert "campaign_treatment_state IS DISTINCT FROM 'ready'" in _SCHEMA
    assert (
        "campaign_treatment_algorithm_version IS DISTINCT FROM "
        "'campaign-treatment-v2'"
    ) in _SCHEMA
    assert (
        "(NEW.decision_intent::jsonb)->>'campaign_treatment_fingerprint'"
    ) not in _SCHEMA
    assert "decision_document->>'campaign_treatment_fingerprint'" in _SCHEMA
    assert "decision_document->>'campaign_owner_email'" in _SCHEMA
    assert "decision_document->>'action' IS DISTINCT FROM NEW.action" in _SCHEMA
    assert "decision_document->>'campaign_id' IS DISTINCT FROM NEW.campaign_id::TEXT" in _SCHEMA
    assert "decision_document->>'variant_name' IS DISTINCT FROM NEW.variant_name" in _SCHEMA
    assert "decision_document->>'channel' IS DISTINCT FROM NEW.channel" in _SCHEMA
    assert "decision_document->>'offer_code' IS DISTINCT FROM NEW.offer_code" in _SCHEMA
    assert "lower(btrim(decision_owner_email))" in _SCHEMA
    assert "lower(btrim(campaign_owner_email))" in _SCHEMA
    assert (
        "encode(sha256(convert_to(NEW.decision_intent, 'UTF8')), 'hex')"
        in _SCHEMA
    )
    assert re.search(
        r"CREATE TRIGGER trg_approvals_campaign_lifecycle\s+"
        r"BEFORE INSERT ON mip_app\.approvals\s+"
        r"FOR EACH ROW\s+"
        r"EXECUTE FUNCTION mip_app\.enforce_campaign_decision_lifecycle\(\);",
        _SCHEMA,
    )


@pytest.mark.parametrize(
    ("table", "finalize_trigger", "remove_trigger"),
    [
        (
            "call_dispositions",
            "trg_call_dispositions_finalize_only",
            "trg_call_dispositions_no_remove",
        ),
        (
            "lead_outcomes",
            "trg_lead_outcomes_finalize_only",
            "trg_lead_outcomes_no_remove",
        ),
        (
            "growth_agent_runs",
            "trg_growth_agent_runs_finalize_only",
            "trg_growth_agent_runs_no_remove",
        ),
    ],
)
def test_audit_linked_proof_is_immutable_after_insert(
    table: str,
    finalize_trigger: str,
    remove_trigger: str,
) -> None:
    assert "CREATE OR REPLACE FUNCTION mip_app.enforce_audit_event_finalize_only()" in _SCHEMA
    assert "to_jsonb(NEW) - 'audit_event_id'" in _SCHEMA
    assert "OLD.audit_event_id IS NOT NULL" in _SCHEMA
    assert "NEW.audit_event_id IS NULL" in _SCHEMA
    assert re.search(
        rf"CREATE TRIGGER {finalize_trigger}\s+"
        rf"BEFORE UPDATE ON mip_app\.{table}\s+"
        r"FOR EACH ROW\s+"
        r"EXECUTE FUNCTION mip_app\.enforce_audit_event_finalize_only\(\);",
        _SCHEMA,
    )
    assert re.search(
        rf"CREATE TRIGGER {remove_trigger}\s+"
        rf"BEFORE DELETE OR TRUNCATE ON mip_app\.{table}\s+"
        r"FOR EACH STATEMENT\s+"
        r"EXECUTE FUNCTION mip_app\.prevent_outreach_evidence_mutation\(\);",
        _SCHEMA,
    )


def test_outcome_audit_links_are_real_foreign_keys_and_validated() -> None:
    for constraint, table in (
        ("call_dispositions_audit_event_id_fkey", "call_dispositions"),
        ("lead_outcomes_audit_event_id_fkey", "lead_outcomes"),
    ):
        assert re.search(
            rf"ALTER TABLE mip_app\.{table}.*?ADD CONSTRAINT {constraint}.*?"
            r"FOREIGN KEY \(audit_event_id\) REFERENCES mip_app\.action_audit\(audit_id\).*?"
            r"NOT VALID;",
            _SCHEMA,
            flags=re.DOTALL,
        )
        assert f"VALIDATE CONSTRAINT {constraint};" in _SCHEMA

    assert "2026_07_14_outcome_and_agent_run_immutability" in _SCHEMA


def test_lead_outcome_source_reference_requires_hmac_alias_for_new_writes() -> None:
    assert re.search(
        r"ADD CONSTRAINT ck_lead_outcomes_source_record_ref\s+"
        r"CHECK \(\s*source_record_ref IS NULL\s+"
        r"OR source_record_ref ~ '\^auto-\[a-f0-9\]\{32\}\$'\s*\) NOT VALID;",
        _SCHEMA,
    )
    assert "VALIDATE CONSTRAINT ck_lead_outcomes_source_record_ref" not in _SCHEMA
    assert "2026_07_14_hmac_outcome_source_reference" in _SCHEMA
    assert re.search(
        r"CREATE TRIGGER trg_approvals_no_remove\s+"
        r"BEFORE DELETE OR TRUNCATE ON mip_app\.approvals\s+"
        r"FOR EACH STATEMENT\s+"
        r"EXECUTE FUNCTION mip_app\.prevent_outreach_evidence_mutation\(\);",
        _SCHEMA,
    )


def test_seed_approvals_are_bound_to_seeded_immutable_variants() -> None:
    seed = Path("lakebase/seed_campaigns.sql").read_text(encoding="utf-8")

    assert "INSERT INTO mip_app.campaign_message_variants" in seed
    assert "ON CONFLICT (campaign_id, variant_name, channel) DO NOTHING" in seed
    assert "approval_id, campaign_id, variant_name, channel, borrower_id" in seed
    assert seed.count("'Benefit-led',") >= 8

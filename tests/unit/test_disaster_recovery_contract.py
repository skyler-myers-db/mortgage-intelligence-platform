from __future__ import annotations

from pathlib import Path


def test_disaster_recovery_runbook_covers_operational_scenarios() -> None:
    text = Path("docs/disaster-recovery.md").read_text(encoding="utf-8")

    for phrase in (
        "Lakebase Corrupt Or Rolled Back",
        "Gold Tables Corrupt Or Bad Refresh",
        "Bad App Snapshot Or Frontend Regression",
        "Bundle Resource Regression",
        "Genie Space Deleted Or Misconfigured",
        "Governed Action Secret Rotation",
        "Audit Ledger Archival",
        "RPO",
        "RTO target",
    ):
        assert phrase in text

    for command in (
        "databricks bundle run mip_lakebase_migrate -t client",
        "databricks bundle run mip_refresh_scores -t client",
        "CI=1 ./scripts/deploy.sh -t client --no-confirm",
        "git checkout <prior-good-sha>",
        "tools/databricks/provision_genie_space.py",
        "tools/databricks/export_action_audit.py",
    ):
        assert command in text


def test_lakebase_schema_records_migrations_and_archive_runs() -> None:
    schema_sql = Path("lakebase/schema.sql").read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS mip_app.schema_migrations" in schema_sql
    assert "version     TEXT PRIMARY KEY" in schema_sql
    assert "2026_05_18_dr_backup_contract" in schema_sql
    assert "CREATE TABLE IF NOT EXISTS mip_app.action_audit_archive_runs" in schema_sql
    assert "cutoff_event_at" in schema_sql
    assert "destination_uri" in schema_sql
    assert "row_count" in schema_sql
    assert "INSERT INTO mip_app.schema_migrations" in schema_sql


def test_production_lakebase_targets_enable_readable_secondaries() -> None:
    """A customer install attaches readable secondaries unless its profile says otherwise.

    M1-PR2 retired the prod target, which set them on; the client target reads
    var.lakebase_readable_secondaries (default off, a demo install's posture),
    and the profile sets it on for a customer install with no explicit value.
    """
    from tests.fixtures.client_profiles import customer_profile, demo_profile
    from tools.clients import profile as views
    from tools.clients.environment import RawProfile, resolve

    bundle = Path("databricks.yml").read_text(encoding="utf-8")

    assert "prod_otlp:" not in bundle
    assert "enable_readable_secondaries: ${var.lakebase_readable_secondaries}" in bundle
    assert "lakebase_readable_secondaries:" in bundle
    customer = resolve(RawProfile(values=customer_profile(MIP_LAKEBASE_READABLE_SECONDARIES=None)))
    assert views.client_target_vars(customer)["lakebase_readable_secondaries"] == "true"
    explicit = resolve(RawProfile(values=customer_profile(MIP_LAKEBASE_READABLE_SECONDARIES="false")))
    assert views.client_target_vars(explicit)["lakebase_readable_secondaries"] == "false"
    demo = resolve(RawProfile(values=demo_profile(MIP_LAKEBASE_READABLE_SECONDARIES=None)))
    assert "lakebase_readable_secondaries" not in views.client_target_vars(demo)


def test_action_audit_export_helper_is_copy_only() -> None:
    script = Path("tools/databricks/export_action_audit.py").read_text(encoding="utf-8")

    assert "FROM mip_app.action_audit" in script
    assert "INSERT INTO mip_app.action_audit_archive_runs" in script
    assert "DELETE FROM mip_app.action_audit" not in script
    assert "UPDATE mip_app.action_audit" not in script
    assert "jsonl.gz" in script


def test_hmac_rotation_contract_is_exposed_to_operators() -> None:
    env_example = Path(".env.example").read_text(encoding="utf-8")
    settings_py = Path("backend/config/settings.py").read_text(encoding="utf-8")
    genie_action_tokens = Path("backend/services/genie_action_tokens.py").read_text(
        encoding="utf-8"
    )

    for name in (
        "MIP_GENIE_ACTION_SECRET_CURRENT",
        "MIP_GENIE_ACTION_SECRET_PREVIOUS",
        "MIP_GENIE_ACTION_SECRET_KID",
        "MIP_GENIE_ACTION_SECRET_PREVIOUS_KID",
    ):
        assert name in env_example

    for attr in (
        "mip_genie_action_secret_current",
        "mip_genie_action_secret_previous",
        "mip_genie_action_secret_kid",
        "mip_genie_action_secret_previous_kid",
    ):
        assert attr in settings_py

    assert '"kid"' in genie_action_tokens
    assert "_previous_action_token_key" in genie_action_tokens

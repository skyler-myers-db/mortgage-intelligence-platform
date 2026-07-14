from __future__ import annotations

import pytest

from tools.databricks import app_deploy_payload
from tools.databricks.app_deploy_payload import build_payload


@pytest.fixture(autouse=True)
def _hermetic_operator_config(monkeypatch, tmp_path):
    """Isolate every test from the operator's real machine configuration.

    ``build_payload`` deliberately overlays the repo-root ``.env.local`` and
    the process environment (that's the production deploy contract). But the
    TESTS must not change verdict based on what a developer machine has
    configured — the 2026-06-11 audit-response run found the no-bootstrap
    governance tests failing solely because the operator had added
    MIP_ADMIN_EMAILS to .env.local, exactly as deploy docs instruct.
    """
    monkeypatch.setattr(app_deploy_payload, "ENV_LOCAL", tmp_path / ".env.local.absent")
    for name in app_deploy_payload.NON_SECRET_OPERATOR_VARS:
        monkeypatch.delenv(name, raising=False)
    for name in app_deploy_payload.SECRET_RESOURCE_BINDINGS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv(app_deploy_payload.PREVIOUS_SECRET_KID_ENV, raising=False)
    monkeypatch.setenv("MIP_COTALITY_ID_MASK_SECRET", "test-mask-secret")
    monkeypatch.setenv("MIP_GENIE_ACTION_SECRET_CURRENT", "test-action-secret")


def _env_map(payload: dict[str, object]) -> dict[str, dict[str, str]]:
    return {item["name"]: item for item in payload["env_vars"]}  # type: ignore[index]


def test_app_deploy_payload_preserves_resource_bindings_and_safe_runtime_config(
    monkeypatch,
) -> None:
    monkeypatch.setenv("MIP_LENDER_NAME", "Acme Mortgage")
    monkeypatch.setenv("MIP_TRUST_FORWARDED_HEADERS", "false")
    monkeypatch.setenv("MIP_RUM_ENABLED", "1")
    monkeypatch.setenv("MIP_GENIE_CONCURRENCY_LIMIT", "8")
    monkeypatch.setenv("MIP_LIVE_CAPABILITY_PROBE_TTL_S", "45")
    monkeypatch.setenv("MIP_GIT_SHA", "abc123def456")

    payload = build_payload(
        source_code_path="/Workspace/app/files",
        target="prod",
        current_user_email="se@example.com",
        catalog="acme_mip",
        schema="gold",
    )
    env = _env_map(payload)

    assert payload["source_code_path"] == "/Workspace/app/files"
    assert env["DATABRICKS_WAREHOUSE_ID"] == {
        "name": "DATABRICKS_WAREHOUSE_ID",
        "value_from": "sql_warehouse",
    }
    assert env["GENIE_SPACE_ID"]["value_from"] == "genie_space"
    assert env["LAKEBASE_HOST"]["value_from"] == "database"
    assert env["MIP_LIFECYCLE_SYNC_JOB_ID"]["value_from"] == "lifecycle_sync_job"
    assert env["MIP_FRED_RATES_JOB_ID"]["value_from"] == "fred_rates_job"
    assert env["MIP_SILVER_REFRESH_JOB_ID"]["value_from"] == "silver_refresh_job"
    assert env["MIP_GOLD_REFRESH_JOB_ID"]["value_from"] == "gold_refresh_job"
    assert env["MIP_GENIE_ACTION_SECRET_CURRENT"]["value_from"] == "genie_action_current_secret"
    assert "MIP_GENIE_ACTION_SECRET_PREVIOUS" not in env
    assert "MIP_GENIE_ACTION_SECRET_PREVIOUS_KID" not in env
    assert env["MIP_DEFAULT_CATALOG"]["value"] == "acme_mip"
    assert env["MIP_LEADS_WARM_INTERVAL_S"]["value"] == "0"
    assert env["MIP_LENDER_NAME"]["value"] == "Acme Mortgage"
    assert env["MIP_TRUST_FORWARDED_HEADERS"]["value"] == "false"
    assert env["MIP_RUM_ENABLED"]["value"] == "1"
    assert env["MIP_GENIE_CONCURRENCY_LIMIT"]["value"] == "8"
    assert env["MIP_LIVE_CAPABILITY_PROBE_TTL_S"]["value"] == "45"
    assert env["MIP_GIT_SHA"]["value"] == "abc123def456"
    assert "MIP_ADMIN_EMAILS" not in env


def test_app_deploy_payload_does_not_overlay_lakebase_dsn_fragments(monkeypatch) -> None:
    """Databricks Apps database bindings own PG* runtime values.

    Local `.env.local` DSN hints are useful for laptops and migration jobs, but
    they must not override the bound app database. A stale LAKEBASE_DATABASE
    value would make the deployed app connect to the wrong Postgres database.
    """
    monkeypatch.setenv("LAKEBASE_PORT", "6543")
    monkeypatch.setenv("LAKEBASE_DATABASE", "wrong_db")
    monkeypatch.setenv("LAKEBASE_SSLMODE", "disable")

    payload = build_payload(source_code_path="/Workspace/app/files", target="prod")
    env = _env_map(payload)

    assert env["PGHOST"]["value_from"] == "database"
    assert env["LAKEBASE_HOST"]["value_from"] == "database"
    assert "LAKEBASE_PORT" not in env
    assert "LAKEBASE_DATABASE" not in env
    assert "LAKEBASE_SSLMODE" not in env


def test_payload_does_not_bootstrap_current_user_admin(monkeypatch) -> None:
    monkeypatch.delenv("MIP_ADMIN_EMAILS", raising=False)
    monkeypatch.delenv("MIP_ADMIN_GROUP_NAME", raising=False)

    dev = build_payload(
        source_code_path="/Workspace/app/files",
        target="dev",
        current_user_email="skyler@entrada.ai",
    )
    prod = build_payload(
        source_code_path="/Workspace/app/files",
        target="prod",
        current_user_email="skyler@entrada.ai",
    )

    assert "MIP_ADMIN_EMAILS" not in _env_map(dev)
    assert "MIP_ADMIN_EMAILS" not in _env_map(prod)


def test_payload_includes_explicit_admin_operator_vars(monkeypatch) -> None:
    monkeypatch.setenv("MIP_ADMIN_EMAILS", "admin@example.com")
    monkeypatch.setenv("MIP_ADMIN_GROUP_NAME", "risk-admin")

    payload = build_payload(source_code_path="/Workspace/app/files", target="dev")
    env = _env_map(payload)

    assert env["MIP_ADMIN_EMAILS"]["value"] == "admin@example.com"
    assert env["MIP_ADMIN_GROUP_NAME"]["value"] == "risk-admin"


def test_payload_allows_operator_to_enable_lead_rewarm(monkeypatch) -> None:
    monkeypatch.setenv("MIP_LEADS_WARM_INTERVAL_S", "120")

    payload = build_payload(source_code_path="/Workspace/app/files", target="dev")
    env = _env_map(payload)

    assert env["MIP_LEADS_WARM_INTERVAL_S"]["value"] == "120"


def test_payload_binds_cotality_mask_secret_without_serializing_value(monkeypatch) -> None:
    monkeypatch.setenv("MIP_COTALITY_ID_MASK_SECRET", "customer-mask-secret")

    payload = build_payload(
        source_code_path="/Workspace/app/files",
        target="prod",
        app_env="prod",
    )
    env = _env_map(payload)

    assert env["APP_ENV"]["value"] == "prod"
    assert env["MIP_COTALITY_ID_MASK_SECRET"] == {
        "name": "MIP_COTALITY_ID_MASK_SECRET",
        "value_from": "cotality_id_mask_secret",
    }
    assert "customer-mask-secret" not in str(payload)


def test_payload_includes_previous_action_secret_only_for_explicit_rotation_grace(
    monkeypatch,
) -> None:
    monkeypatch.setenv("MIP_GENIE_ACTION_SECRET_CURRENT", "current-action-secret")
    monkeypatch.setenv("MIP_GENIE_ACTION_SECRET_PREVIOUS", "previous-action-secret")
    monkeypatch.setenv("MIP_GENIE_ACTION_SECRET_KID", "v3")
    monkeypatch.setenv("MIP_GENIE_ACTION_SECRET_PREVIOUS_KID", "v2")

    payload = build_payload(
        source_code_path="/Workspace/app/files",
        target="prod",
        app_env="prod",
    )
    env = _env_map(payload)

    assert env["MIP_GENIE_ACTION_SECRET_CURRENT"]["value_from"] == "genie_action_current_secret"
    assert env["MIP_GENIE_ACTION_SECRET_PREVIOUS"]["value_from"] == "genie_action_previous_secret"
    assert env["MIP_GENIE_ACTION_SECRET_KID"]["value"] == "v3"
    assert env["MIP_GENIE_ACTION_SECRET_PREVIOUS_KID"]["value"] == "v2"
    assert "current-action-secret" not in str(payload)
    assert "previous-action-secret" not in str(payload)


def test_payload_rejects_previous_action_secret_without_rotation_kid(monkeypatch) -> None:
    monkeypatch.setenv("MIP_GENIE_ACTION_SECRET_PREVIOUS", "previous-action-secret")

    with pytest.raises(ValueError, match="MIP_GENIE_ACTION_SECRET_PREVIOUS_KID"):
        build_payload(
            source_code_path="/Workspace/app/files",
            target="prod",
            app_env="prod",
        )


def test_payload_omits_stale_previous_kid_without_previous_secret(monkeypatch) -> None:
    monkeypatch.setenv("MIP_GENIE_ACTION_SECRET_PREVIOUS_KID", "v2")

    payload = build_payload(
        source_code_path="/Workspace/app/files",
        target="prod",
        app_env="prod",
    )
    env = _env_map(payload)

    assert "MIP_GENIE_ACTION_SECRET_PREVIOUS" not in env
    assert "MIP_GENIE_ACTION_SECRET_PREVIOUS_KID" not in env


def test_payload_omits_placeholder_previous_secret(monkeypatch) -> None:
    monkeypatch.setenv("MIP_GENIE_ACTION_SECRET_PREVIOUS", "REDACTED")
    monkeypatch.setenv("MIP_GENIE_ACTION_SECRET_PREVIOUS_KID", "v2")

    payload = build_payload(
        source_code_path="/Workspace/app/files",
        target="prod",
        app_env="prod",
    )
    env = _env_map(payload)

    assert "MIP_GENIE_ACTION_SECRET_PREVIOUS" not in env
    assert "MIP_GENIE_ACTION_SECRET_PREVIOUS_KID" not in env


def test_payload_uses_secret_resource_when_operator_value_is_placeholder(monkeypatch) -> None:
    monkeypatch.setenv("MIP_COTALITY_ID_MASK_SECRET", "REDACTED")

    payload = build_payload(
        source_code_path="/Workspace/app/files",
        target="prod",
        app_env="prod",
    )
    assert (
        _env_map(payload)["MIP_COTALITY_ID_MASK_SECRET"]["value_from"] == "cotality_id_mask_secret"
    )
    assert "REDACTED" not in str(payload)


def test_payload_never_serializes_absent_current_action_secret(monkeypatch) -> None:
    monkeypatch.delenv("MIP_GENIE_ACTION_SECRET_CURRENT")

    payload = build_payload(
        source_code_path="/Workspace/app/files",
        target="dev",
        app_env="sandbox",
    )
    assert (
        _env_map(payload)["MIP_GENIE_ACTION_SECRET_CURRENT"]["value_from"]
        == "genie_action_current_secret"
    )


def test_payload_local_runtime_can_omit_durable_secrets(monkeypatch) -> None:
    monkeypatch.delenv("MIP_COTALITY_ID_MASK_SECRET")
    monkeypatch.delenv("MIP_GENIE_ACTION_SECRET_CURRENT")

    payload = build_payload(
        source_code_path="/Workspace/app/files",
        target="dev",
        app_env="local",
    )

    env = _env_map(payload)
    assert env["MIP_COTALITY_ID_MASK_SECRET"]["value_from"] == "cotality_id_mask_secret"
    assert env["MIP_GENIE_ACTION_SECRET_CURRENT"]["value_from"] == "genie_action_current_secret"

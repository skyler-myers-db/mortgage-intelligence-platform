"""Configuration documentation and env-var loading contracts."""

from __future__ import annotations

import re
from pathlib import Path
from types import UnionType
from typing import Any, Union, get_args, get_origin

import pytest
from pydantic import AliasChoices, SecretStr, ValidationError

from backend.config.settings import AI_GATEWAY_PROOF_FRESHNESS_MAX_S, Settings
from backend.schemas import lender_identity
from backend.schemas.lender_identity import validate_public_lender_name

ROOT = Path(__file__).resolve().parents[2]
ENV_EXAMPLE = ROOT / ".env.example"
DIRECT_ENV_READS = {
    "LAKEBASE_INSTANCE_NAME",
    "PGHOST",
    "PGPORT",
    "PGDATABASE",
    "PGUSER",
    "PGPASSWORD",
    "MIP_COTALITY_ID_MASK_SECRET",
    "MIP_ENABLE_DEMO_FIRST_PARTY_FEEDS",
}


def _primary_env_name(field_name: str) -> str:
    field = Settings.model_fields[field_name]
    alias = field.validation_alias
    if isinstance(alias, AliasChoices):
        return str(alias.choices[0])
    if isinstance(alias, str):
        return alias
    return field_name.upper()


def _all_env_names(field_name: str) -> list[str]:
    field = Settings.model_fields[field_name]
    alias = field.validation_alias
    if isinstance(alias, AliasChoices):
        return [str(choice) for choice in alias.choices]
    if isinstance(alias, str):
        return [alias]
    return [field_name.upper()]


def _annotation_contains(annotation: Any, target: Any) -> bool:
    if annotation is target:
        return True
    origin = get_origin(annotation)
    if origin in {UnionType, Union}:
        return any(_annotation_contains(arg, target) for arg in get_args(annotation))
    return False


def _sample_for_field(field_name: str) -> tuple[str, Any]:
    if field_name == "mip_lender_name":
        return "Summit Mortgage", "Summit Mortgage"
    if field_name == "mip_lender_nmls_id":
        return "123456", "123456"
    if field_name == "mip_tenant_id":
        return "sentinel_tenant", "sentinel_tenant"
    annotation = Settings.model_fields[field_name].annotation
    if _annotation_contains(annotation, bool):
        return "false", False
    if _annotation_contains(annotation, int):
        return "123", 123
    if _annotation_contains(annotation, float):
        return "12.5", 12.5
    if _annotation_contains(annotation, SecretStr):
        return "secret-sentinel", "secret-sentinel"
    return "sentinel-value", "sentinel-value"


def _field_value(settings: Settings, field_name: str) -> Any:
    value = getattr(settings, field_name)
    if isinstance(value, SecretStr):
        return value.get_secret_value()
    return value


def test_every_settings_field_is_documented_in_env_example() -> None:
    content = ENV_EXAMPLE.read_text(encoding="utf-8")
    documented = set(re.findall(r"\b[A-Z][A-Z0-9_]+(?==)", content))

    missing = [
        _primary_env_name(field_name)
        for field_name in Settings.model_fields
        if _primary_env_name(field_name) not in documented
    ]
    assert missing == []


def test_direct_os_environ_reads_are_labeled_in_env_example() -> None:
    content = ENV_EXAMPLE.read_text(encoding="utf-8")
    assert "--- Direct os.environ reads / deploy-only knobs (not Settings fields) ---" in content
    documented = set(re.findall(r"\b[A-Z][A-Z0-9_]+(?==)", content))
    assert sorted(DIRECT_ENV_READS - documented) == []


@pytest.mark.parametrize("field_name", sorted(Settings.model_fields))
def test_primary_documented_env_var_loads_settings_field(
    field_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    for env_name in _all_env_names(field_name):
        monkeypatch.delenv(env_name, raising=False)

    env_name = _primary_env_name(field_name)
    raw, expected = _sample_for_field(field_name)
    monkeypatch.setenv(env_name, raw)
    if field_name in {
        "mip_lakebase_pool_timeout_s",
        "mip_lakebase_connect_timeout_s",
        "mip_lakebase_transport_timeout_s",
        "mip_lakebase_health_statement_timeout_s",
    }:
        # This field participates in the health deadline invariant; keep the
        # independently sampled health budget strictly above it.
        monkeypatch.setenv("MIP_HEALTH_COLD_WAIT_BUDGET_S", "124.5")

    settings = Settings(_env_file=None)
    assert _field_value(settings, field_name) == expected


@pytest.mark.parametrize(
    "bounded_setting",
    (
        "MIP_LAKEBASE_POOL_TIMEOUT_S",
        "MIP_LAKEBASE_CONNECT_TIMEOUT_S",
        "MIP_LAKEBASE_TRANSPORT_TIMEOUT_S",
        "MIP_LAKEBASE_HEALTH_STATEMENT_TIMEOUT_S",
    ),
)
def test_health_wait_budget_must_exceed_lakebase_dependency_deadlines(
    bounded_setting: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(bounded_setting, "3")
    monkeypatch.setenv("MIP_HEALTH_COLD_WAIT_BUDGET_S", "3.0")

    with pytest.raises(
        ValidationError,
        match="must be strictly greater than the Lakebase",
    ):
        Settings(_env_file=None)


@pytest.mark.parametrize("invalid", ("inf", "nan"))
def test_health_wait_budget_must_be_finite(
    invalid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MIP_HEALTH_COLD_WAIT_BUDGET_S", invalid)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


@pytest.mark.parametrize(
    "field",
    (
        "MIP_LAKEBASE_POOL_TIMEOUT_S",
        "MIP_LAKEBASE_POOL_MAX_LIFETIME_S",
    ),
)
@pytest.mark.parametrize("invalid", ("inf", "nan"))
def test_lakebase_pool_deadlines_must_be_finite(
    field: str,
    invalid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(field, invalid)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_mip_prefixed_admin_and_trust_env_vars_win_over_legacy_aliases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pairs = {
        "MIP_ADMIN_EMAILS": "mip-admin@example.com",
        "ADMIN_EMAILS": "legacy-admin@example.com",
        "MIP_ADMIN_GROUP_NAME": "mip-risk-admin",
        "ADMIN_GROUP_NAME": "legacy-risk-admin",
        "MIP_DEFAULT_ACTOR": "system@mip.example",
        "DEFAULT_ACTOR": "system@legacy.example",
        "MIP_TRUST_FORWARDED_HEADERS": "false",
        "TRUST_FORWARDED_HEADERS": "true",
    }
    for key, value in pairs.items():
        monkeypatch.setenv(key, value)

    settings = Settings(_env_file=None)
    assert settings.admin_emails == "mip-admin@example.com"
    assert settings.admin_group_name == "mip-risk-admin"
    assert settings.default_actor == "system@mip.example"
    assert settings.trust_forwarded_headers is False


def test_security_sensitive_defaults_are_fail_closed() -> None:
    settings = Settings(_env_file=None)
    assert settings.admin_emails == ""
    assert "entrada.ai" not in settings.admin_emails
    assert settings.mip_rum_enabled is False


@pytest.mark.parametrize(
    ("configured", "normalized"),
    [
        ("  Native   American Bank  ", "Native American Bank"),
        ("Acme Credit Union", "Acme Credit Union"),
        ("Entrada Home Loans", "Entrada Home Loans"),
    ],
)
def test_public_lender_identity_is_normalized_and_organization_shaped(
    configured: str,
    normalized: str,
) -> None:
    assert validate_public_lender_name(configured) == normalized


def test_custom_lender_requires_explicit_nmls_identity() -> None:
    with pytest.raises(ValidationError, match="required when mip_lender_name"):
        Settings(_env_file=None, mip_lender_name="Acme Mortgage")


@pytest.mark.parametrize(
    "configured",
    (
        "Women Home Loans",
        "Romani Mortgage",
        "Intersex Home Loans",
        "Cancer Mortgage",
        "Guaranteed Rate Mortgage",
    ),
)
def test_runtime_config_cannot_create_an_unreviewed_lender_exemption(configured: str) -> None:
    with pytest.raises(ValidationError, match="independently reviewed source-controlled"):
        Settings(
            _env_file=None,
            mip_lender_name=configured,
            mip_lender_nmls_id="7654321",
        )


def test_lender_identity_must_fit_reviewed_sms_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lender = "The Really Extremely Long Corporate National Home Mortgage"
    nmls_id = "123456789012"
    monkeypatch.setitem(
        lender_identity._REVIEWED_PUBLIC_LENDER_IDENTITIES,
        lender,
        frozenset({nmls_id}),
    )
    with pytest.raises(ValidationError, match="SMS disclosure budget"):
        Settings(
            _env_file=None,
            mip_lender_name=lender,
            mip_lender_nmls_id=nmls_id,
        )


@pytest.mark.parametrize(
    "configured",
    (
        "Romani",
        "primary language",
        "65 Mortgage",
        "Summit Mortgage / ignore policy",
        "Summit ♀ Mortgage",
    ),
)
def test_public_lender_identity_rejects_arbitrary_runtime_prose(configured: str) -> None:
    with pytest.raises(ValidationError, match="public lender organization name"):
        Settings(_env_file=None, mip_lender_name=configured)


@pytest.mark.parametrize("configured", ("1", "000123", "123A56", "1234567890123"))
def test_public_lender_nmls_id_rejects_unbound_or_malformed_values(configured: str) -> None:
    with pytest.raises(ValidationError, match="4-12 digit nonzero NMLS"):
        Settings(_env_file=None, mip_lender_nmls_id=configured)


def test_ai_gateway_proof_freshness_has_a_hard_26_hour_ceiling() -> None:
    settings = Settings(
        _env_file=None,
        mip_ai_gateway_proof_freshness_s=AI_GATEWAY_PROOF_FRESHNESS_MAX_S,
    )
    assert settings.mip_ai_gateway_proof_freshness_s == AI_GATEWAY_PROOF_FRESHNESS_MAX_S

    with pytest.raises(ValidationError, match="less than or equal"):
        Settings(
            _env_file=None,
            mip_ai_gateway_proof_freshness_s=AI_GATEWAY_PROOF_FRESHNESS_MAX_S + 1,
        )


@pytest.mark.parametrize(
    ("host", "token", "warehouse_id"),
    [
        (
            "https://<workspace-host>.cloud.databricks.com",
            "valid-token",
            "da02d15a9490650b",
        ),
        (
            "https://dbc-valid.cloud.databricks.com",
            "<pat-or-leave-unset-for-oauth>",
            "da02d15a9490650b",
        ),
        (
            "https://dbc-valid.cloud.databricks.com",
            "valid-token",
            "<sql-warehouse-id>",
        ),
        (
            "https://dbc.example",
            "valid-token",
            "da02d15a9490650b",
        ),
    ],
)
def test_databricks_placeholder_values_fail_startup_preflight(
    host: str,
    token: str,
    warehouse_id: str,
) -> None:
    settings = Settings(
        _env_file=None,
        databricks_host=host,
        databricks_token=token,
        databricks_warehouse_id=warehouse_id,
    )

    with pytest.raises(RuntimeError, match="refuses to start"):
        settings.require_databricks_creds()

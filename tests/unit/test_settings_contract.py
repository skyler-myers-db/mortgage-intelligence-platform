"""Configuration documentation and env-var loading contracts."""

from __future__ import annotations

import re
from pathlib import Path
from types import UnionType
from typing import Any, Union, get_args, get_origin

import pytest
from pydantic import AliasChoices, SecretStr

from backend.config.settings import Settings

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
    "MIP_EXPOSE_RAW_COTALITY_IDS",
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

    settings = Settings(_env_file=None)
    assert _field_value(settings, field_name) == expected


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

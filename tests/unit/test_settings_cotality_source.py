"""The App's Cotality source settings: canonical defaults, source_names refusals.

``Settings.mip_cotality_source_{catalog,schema,table_prefix}``
(``backend/config/cotality_source_settings.py``) read the same
``MIP_COTALITY_SOURCE_*`` names the deploy controls do, default to the
canonical share, and validate through ``backend/config/source_names.py``: the
same template refusal, the same shapes and the same ``-`` for "no table
prefix". There is no second parser.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.config import source_names
from backend.config.settings import Settings
from backend.config.source_names import CANONICAL, CotalitySource

_FIELDS = {
    source_names.CATALOG_ENV: "mip_cotality_source_catalog",
    source_names.SCHEMA_ENV: "mip_cotality_source_schema",
    source_names.TABLE_PREFIX_ENV: "mip_cotality_source_table_prefix",
}


@pytest.fixture(autouse=True)
def _no_operator_source(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in source_names.ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def _settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> Settings:
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return Settings(_env_file=None)  # type: ignore[call-arg]


def test_each_field_reads_the_deploy_control_name() -> None:
    assert {
        name: Settings.model_fields[field].validation_alias for name, field in _FIELDS.items()
    } == {name: None for name in _FIELDS}
    assert tuple(field.upper() for field in _FIELDS.values()) == source_names.ENV_NAMES


def test_unset_is_the_canonical_share(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch)

    assert settings.cotality_source() == CANONICAL
    assert (
        settings.mip_cotality_source_catalog,
        settings.mip_cotality_source_schema,
        settings.mip_cotality_source_table_prefix,
    ) == ("cotality_mortgage_data", "corelogic", "entrada_eval_")


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_is_the_canonical_name(monkeypatch: pytest.MonkeyPatch, blank: str) -> None:
    settings = _settings(monkeypatch, **dict.fromkeys(source_names.ENV_NAMES, blank))

    assert settings.cotality_source() == CANONICAL
    assert settings.mip_cotality_source_table_prefix == CANONICAL.table_prefix


def test_configured_names_resolve_as_source_names_resolves_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = {
        source_names.CATALOG_ENV: " acme_share ",
        source_names.SCHEMA_ENV: "mortgage",
        source_names.TABLE_PREFIX_ENV: "acme_",
    }
    settings = _settings(monkeypatch, **env)

    assert settings.cotality_source() == source_names.from_env(env)
    assert settings.cotality_source() == CotalitySource("acme_share", "mortgage", "acme_")
    # Stored as configured, stripped.
    assert settings.mip_cotality_source_catalog == "acme_share"


def test_no_table_prefix_is_spelled_dash_and_resolves_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(monkeypatch, **{source_names.TABLE_PREFIX_ENV: "-"})

    assert settings.mip_cotality_source_table_prefix == source_names.NO_TABLE_PREFIX
    assert settings.cotality_source().table_prefix == ""
    assert settings.cotality_source().table("mls_listing_v1") == (
        "cotality_mortgage_data.corelogic.mls_listing_v1"
    )


@pytest.mark.parametrize("template", ["<cotality-source-catalog>", "acme_<x>", "00000000PLACEHOLDER"])
@pytest.mark.parametrize("env_name", source_names.ENV_NAMES)
def test_a_template_value_refuses_the_settings(
    monkeypatch: pytest.MonkeyPatch, env_name: str, template: str
) -> None:
    with pytest.raises(ValidationError) as refusal:
        _settings(monkeypatch, **{env_name: template})

    assert f"{env_name} is a template value; set it or leave it unset" in str(refusal.value)
    # A field error: it quotes its own input and none of the other settings.
    assert [error["loc"] for error in refusal.value.errors()] == [(_FIELDS[env_name],)]


@pytest.mark.parametrize(
    ("env_name", "value"),
    [
        (source_names.CATALOG_ENV, "Acme_Share"),
        (source_names.CATALOG_ENV, "acme-share"),
        (source_names.SCHEMA_ENV, "mortgage.feed"),
        (source_names.SCHEMA_ENV, "9feed"),
        (source_names.TABLE_PREFIX_ENV, "acme-"),
        (source_names.TABLE_PREFIX_ENV, "Acme_"),
        (source_names.TABLE_PREFIX_ENV, "a" * 65),
    ],
)
def test_a_malformed_name_refuses_the_settings(
    monkeypatch: pytest.MonkeyPatch, env_name: str, value: str
) -> None:
    with pytest.raises(ValidationError, match="must be a lowercase unquoted identifier"):
        _settings(monkeypatch, **{env_name: value})

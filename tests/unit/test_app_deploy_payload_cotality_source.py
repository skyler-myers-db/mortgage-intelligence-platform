"""The App payload carries a Cotality source name only when it is not canonical.

``tools/databricks/app_deploy_payload.py`` reads ``MIP_COTALITY_SOURCE_*``
from the process env, then ``.env.local``, like every operator value, and
validates each through ``backend/config/source_names.py`` before the App would
refuse it at boot. A name rides only when it is set and differs from the
canonical one, as configuration spells it; the env-name set of a canonical
install is pinned in tests/unit/test_settings_d0_env_parity.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.config import source_names
from tools.databricks import app_deploy_payload
from tools.databricks.app_deploy_payload import build_payload


@pytest.fixture(autouse=True)
def _hermetic_operator_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    env_local = tmp_path / ".env.local"
    monkeypatch.setattr(app_deploy_payload, "ENV_LOCAL", env_local)
    for name in (
        *app_deploy_payload.NON_SECRET_OPERATOR_VARS,
        *app_deploy_payload.SECRET_RESOURCE_BINDINGS,
        app_deploy_payload.PREVIOUS_SECRET_KID_ENV,
        "MIP_PRESENTER_MODE",
        *source_names.ENV_NAMES,
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MIP_COTALITY_ID_MASK_SECRET", "test-mask-secret")
    monkeypatch.setenv("MIP_GENIE_ACTION_SECRET_CURRENT", "test-action-secret")
    return env_local


def _source_env() -> dict[str, str]:
    payload = build_payload(source_code_path="/Workspace/app-src/release", target="dev")
    return {
        item["name"]: item["value"]
        for item in payload["env_vars"]  # type: ignore[attr-defined]
        if item["name"] in source_names.ENV_NAMES
    }


@pytest.mark.parametrize(
    ("env_name", "value", "sent"),
    [
        (source_names.CATALOG_ENV, "acme_share", "acme_share"),
        (source_names.CATALOG_ENV, "  acme_share ", "acme_share"),
        (source_names.SCHEMA_ENV, "mortgage", "mortgage"),
        (source_names.TABLE_PREFIX_ENV, "acme_", "acme_"),
        (source_names.TABLE_PREFIX_ENV, "-", "-"),
    ],
)
def test_each_non_canonical_name_rides_alone(
    monkeypatch: pytest.MonkeyPatch, env_name: str, value: str, sent: str
) -> None:
    monkeypatch.setenv(env_name, value)

    assert _source_env() == {env_name: sent}


def test_the_env_local_overlay_supplies_the_names(
    _hermetic_operator_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _hermetic_operator_config.write_text(
        "MIP_COTALITY_SOURCE_CATALOG=acme_share\nMIP_COTALITY_SOURCE_SCHEMA=corelogic\n",
        encoding="utf-8",
    )
    assert _source_env() == {source_names.CATALOG_ENV: "acme_share"}

    # A non-empty process value wins over .env.local, as for every operator value.
    monkeypatch.setenv(source_names.CATALOG_ENV, "zyrplax_feed")
    assert _source_env() == {source_names.CATALOG_ENV: "zyrplax_feed"}


@pytest.mark.parametrize("env_name", source_names.ENV_NAMES)
@pytest.mark.parametrize("template", ["<cotality-source-catalog>", "00000000PLACEHOLDER"])
def test_a_template_value_refuses_the_payload(
    monkeypatch: pytest.MonkeyPatch, env_name: str, template: str
) -> None:
    monkeypatch.setenv(env_name, template)

    with pytest.raises(ValueError, match=f"{env_name} is a template value"):
        _source_env()


@pytest.mark.parametrize(
    ("env_name", "value"),
    [
        (source_names.CATALOG_ENV, "Acme_Share"),
        (source_names.SCHEMA_ENV, "-"),
        (source_names.TABLE_PREFIX_ENV, "acme-"),
    ],
)
def test_a_malformed_name_refuses_the_payload(
    monkeypatch: pytest.MonkeyPatch, env_name: str, value: str
) -> None:
    monkeypatch.setenv(env_name, value)

    with pytest.raises(ValueError, match="must be a lowercase unquoted identifier"):
        _source_env()

"""D0 parity: an App deployed with today's env names behaves byte-identically.

D0 is the live paychex deployment. Its env is whatever
``tools/databricks/app_deploy_payload.py`` emits for paychex's operator
configuration: 32 names, the set the piecewise emergency recipe gates on
against the live deployment. M3-PR2 adds the ``mip_cotality_source_*``
settings with canonical defaults, so a piecewise deploy of main with D0's env
must change nothing (plan section 4.13, attack O10):

* the payload builder, given D0's operator inputs, still emits exactly those
  32 names, and a ``MIP_COTALITY_SOURCE_*`` value adds its name only when it
  is set and differs from the canonical name;
* Settings loaded in a fresh process from exactly D0's env names give the
  same compiled guard patterns, asset registry and lineage manifest response
  as an empty env and as the canonical names spelled out, with no configured
  guard pattern at all, and the share tables at their canonical names.

The values here are symbolic: the test needs D0's names, never paychex's
identifiers. The tree-against-origin/main digest proof for the same snapshot
is in the PR that introduced this file.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.config import source_names
from backend.config.source_names import CANONICAL, TABLE_STEMS
from tests.fixtures.cotality_source_probe import run_fresh
from tools.databricks import app_deploy_payload
from tools.databricks.app_deploy_payload import build_payload

# What paychex's operator configuration sets beyond the builder's own
# defaults, by name. The values are placeholders.
D0_OPERATOR_INPUTS = {
    "MIP_LENDER_NAME": "Summit Mortgage",
    "MIP_LENDER_NMLS_ID": "123456",
    "MIP_GIT_SHA": "0123456789abcdef0123456789abcdef01234567",
    "MIP_ADMIN_EMAILS": "admin@example.invalid",
    "MIP_ADMIN_IDENTITIES": "00000000-0000-4000-8000-000000000001",
    "MIP_APPROVER_EMAILS": "approver@example.invalid",
    "MIP_APPROVER_IDENTITIES": "00000000-0000-4000-8000-000000000002",
    "MIP_LAKEBASE_SYNC_CATALOG": "mip_app_state",
    "MIP_LAKEBASE_SYNC_SCHEMA": "mip_sync",
    "MIP_LAKEBASE_SYNC_TABLES": "source_readiness,segment_population,funnel_snapshot_daily",
    "MIP_COTALITY_ID_MASK_SECRET": "presence-only",
    "MIP_GENIE_ACTION_SECRET_CURRENT": "presence-only",
}
D0_NAME_COUNT = 32

_CANONICAL_SPELLED = {
    source_names.CATALOG_ENV: CANONICAL.catalog,
    source_names.SCHEMA_ENV: CANONICAL.schema,
    source_names.TABLE_PREFIX_ENV: CANONICAL.table_prefix,
}


def _d0_operator_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """D0's operator inputs and nothing from this machine's configuration."""

    monkeypatch.setattr(app_deploy_payload, "ENV_LOCAL", tmp_path / ".env.local.absent")
    for name in (
        *app_deploy_payload.NON_SECRET_OPERATOR_VARS,
        *app_deploy_payload.SECRET_RESOURCE_BINDINGS,
        app_deploy_payload.PREVIOUS_SECRET_KID_ENV,
        "MIP_PRESENTER_MODE",
        *source_names.ENV_NAMES,
    ):
        monkeypatch.delenv(name, raising=False)
    for name, value in D0_OPERATOR_INPUTS.items():
        monkeypatch.setenv(name, value)


@pytest.fixture(autouse=True)
def _hermetic_operator_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _d0_operator_config(monkeypatch, tmp_path)


def _d0_payload_env() -> list[dict[str, str]]:
    payload = build_payload(source_code_path="/Workspace/app-src/release", target="dev")
    return payload["env_vars"]  # type: ignore[return-value]


def _names(env_vars: list[dict[str, str]]) -> list[str]:
    return [item["name"] for item in env_vars]


def _d0_settings_env() -> dict[str, str]:
    """D0's names with the values the App would see; a resource stays symbolic."""

    return {
        item["name"]: item.get("value", f"resource:{item.get('value_from')}")
        for item in _d0_payload_env()
    }


def test_the_payload_builder_emits_exactly_d0s_name_set() -> None:
    names = _names(_d0_payload_env())

    assert len(names) == len(set(names)) == D0_NAME_COUNT
    assert not set(names) & set(source_names.ENV_NAMES)


@pytest.mark.parametrize(
    "canonical",
    [
        _CANONICAL_SPELLED,
        {**_CANONICAL_SPELLED, source_names.TABLE_PREFIX_ENV: " entrada_eval_ "},
        dict.fromkeys(source_names.ENV_NAMES, ""),
        dict.fromkeys(source_names.ENV_NAMES, "   "),
    ],
    ids=["spelled", "padded", "blank", "spaces"],
)
def test_a_canonical_or_blank_source_adds_no_name(
    monkeypatch: pytest.MonkeyPatch, canonical: dict[str, str]
) -> None:
    baseline = _d0_payload_env()
    for name, value in canonical.items():
        monkeypatch.setenv(name, value)

    assert _d0_payload_env() == baseline


def test_only_a_set_non_canonical_name_is_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    baseline = _d0_payload_env()
    monkeypatch.setenv(source_names.CATALOG_ENV, "acme_share")
    monkeypatch.setenv(source_names.SCHEMA_ENV, CANONICAL.schema)
    monkeypatch.setenv(source_names.TABLE_PREFIX_ENV, "-")

    env_vars = _d0_payload_env()

    assert [item for item in env_vars if item not in baseline] == [
        {"name": source_names.CATALOG_ENV, "value": "acme_share"},
        {"name": source_names.TABLE_PREFIX_ENV, "value": source_names.NO_TABLE_PREFIX},
    ]
    assert [item for item in env_vars if item in baseline] == baseline


@pytest.fixture(scope="module")
def d0_snapshots(tmp_path_factory: pytest.TempPathFactory) -> dict[str, object]:
    """Fresh-process snapshots: D0's exact env, an empty env, canonical spelled out."""

    cwd = tmp_path_factory.mktemp("d0-parity")
    with pytest.MonkeyPatch.context() as monkeypatch:
        _d0_operator_config(monkeypatch, cwd)
        d0_env = _d0_settings_env()
    assert len(d0_env) == D0_NAME_COUNT
    return {
        "d0": run_fresh(d0_env, "snapshot", cwd),
        "empty": run_fresh({}, "snapshot", cwd),
        "canonical_spelled": run_fresh({**d0_env, **_CANONICAL_SPELLED}, "snapshot", cwd),
    }


def test_d0s_env_gives_byte_identical_guards_registry_and_lineage(
    d0_snapshots: dict[str, object],
) -> None:
    assert d0_snapshots["d0"] == d0_snapshots["empty"] == d0_snapshots["canonical_spelled"]


def test_d0_compiles_no_configured_guard_pattern(d0_snapshots: dict[str, object]) -> None:
    snapshot = d0_snapshots["d0"]
    assert isinstance(snapshot, dict)
    assert snapshot["settings"] == ["cotality_mortgage_data", "corelogic", "entrada_eval_"]
    assert snapshot["guards"]["configured_source_patterns"] == []
    # The canonical patterns still carry the canonical catalog themselves.
    assert r"\bcotality_mortgage_data\b" in snapshot["guards"]["growth_agent._PROMPT_JAILBREAK_RE"][0]
    assert any(
        r"cotality_mortgage_data\." in pattern
        for pattern, _ in snapshot["guards"]["genie_prompt_guardrails._SCOPE_BYPASS_PROMPT_PATTERNS"]
    )


def test_d0_names_every_share_table_by_its_canonical_name(
    d0_snapshots: dict[str, object],
) -> None:
    snapshot = d0_snapshots["d0"]
    assert isinstance(snapshot, dict)
    share = [row for row in snapshot["registry"] if row["source_key"] is not None]
    assert sorted(row["key"] for row in share) == sorted(f"entrada_eval_{stem}" for stem in TABLE_STEMS)
    for row in share:
        # The values the registry spelled out before the settings existed.
        assert (row["catalog_name"], row["schema_name"]) == ("cotality_mortgage_data", "corelogic")
        assert row["object_name"] == row["key"]
        assert row["fqn"] == row["logical_fqn"] == row["source_key"]
        assert row["fqn"] == f"cotality_mortgage_data.corelogic.{row['key']}"
    for family in snapshot["lineage"]["families"]:
        for node in family["nodes"]:
            if node["layer"] == "raw_share":
                assert node["source_key"] == node["fqn"]
                assert node["fqn"].startswith("cotality_mortgage_data.corelogic.entrada_eval_")
            else:
                assert node["source_key"] is None

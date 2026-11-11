"""An adopted core blue's restore payload, rebuilt with blue's exact env-name set (M2-PR7b).

Databricks redacts env values from ``get_deployment``, so the core release
rebuilds blue's tier-b restore payload with the real payload builder, as the
emergency recipe built the adopted deployment, and hands it to the
pre-activation proof only when its env-name set is exactly blue's: D0's 32
names, with neither agentic switch and no lease even though the governed run
exports one, nor the profile's optional role names D0 never carried even
though client #1's profile sets them.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from tests.fixtures import rollback_records as rr
from tests.unit.test_settings_d0_env_parity import D0_NAMES, _d0_operator_config
from tools.databricks import core_blue
from tools.databricks.app_rollback_record_v7 import build_v7_record

INPUTS: dict[str, Any] = {
    "install_class": "demo",
    "app_env": "sandbox",
    "catalog": "mip",
    "schema": "gold",
    "lakebase_instance": rr.LAKEBASE,
}


def _adopted(names: list[str], **overrides: Any) -> dict[str, Any]:
    return build_v7_record(
        expected_lakebase_instance=rr.LAKEBASE, **rr.v7_core_fields(env_names=names, **overrides)
    )


@pytest.fixture(autouse=True)
def _d0(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _d0_operator_config(monkeypatch, tmp_path)
    # The governed run exports its own commit and lease, and agentic defaults
    # (the Supervisor name, the proof verify key); blue's payload carries none.
    monkeypatch.setenv("MIP_GIT_SHA", "e" * 40)
    monkeypatch.setenv("MIP_APP_DEPLOYMENT_LEASE_ID", rr.LEASE_ID)
    monkeypatch.setenv("MIP_AGENT_SUPERVISOR_NAME", "Mortgage Growth Agent")
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_VERIFY_KEY", "verify-key-EXAMPLE")


def test_d0s_restore_payload_is_rebuilt_with_its_32_names_and_its_own_commit() -> None:
    record = _adopted(sorted(D0_NAMES))

    payload, why = core_blue.blue_restore_payload(record, **INPUTS)

    assert payload is not None, why
    env = {item["name"]: item for item in payload["env_vars"]}  # type: ignore[union-attr]
    assert sorted(env) == sorted(D0_NAMES) and len(env) == 32
    assert env["MIP_GIT_SHA"]["value"] == rr.GIT_SHA, "blue's commit, never the run's"
    assert "MIP_APP_DEPLOYMENT_LEASE_ID" not in env and "MIP_AI_GATEWAY" not in env
    assert env["MIP_CAMPAIGN_TREATMENT_RUNTIME_ENABLED"]["value"] == "0", "as the emergency recipe built it"
    assert payload["source_code_path"] == record["restore_source_path"], "the immutable copy"
    assert os.environ["MIP_GIT_SHA"] == "e" * 40 and os.environ["MIP_APP_DEPLOYMENT_LEASE_ID"] == rr.LEASE_ID


def _governed_inputs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, **overrides: str | None) -> dict[str, str]:
    """The governed run's inputs: .env.local is the client profile, its controls exported."""

    from tests.fixtures.client_profiles import core_tier_profile, env_file_text
    from tools.clients import profile as views
    from tools.clients.environment import RawProfile, resolve
    from tools.databricks import app_deploy_payload

    values = core_tier_profile(**overrides)
    env_local = tmp_path / "profile.env.local"
    env_local.write_text(env_file_text({**values, "DATABRICKS_WAREHOUSE_ID": "warehouse-1"}), encoding="utf-8")
    monkeypatch.setattr(app_deploy_payload, "ENV_LOCAL", env_local)
    controls = views.deploy_controls(resolve(RawProfile(values=values)))
    for name, value in controls.items():  # resolve_client_profile exports these
        monkeypatch.setenv(name, value)
    return controls


def test_the_governed_runs_client_profile_rebuilds_d0s_32_names(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Review of #319: client #1's profile sets the optional role fields, which D0 never carried."""

    from tools.clients.profile import OPTIONAL_PAYLOAD_ENV_NAMES

    controls = _governed_inputs(monkeypatch, tmp_path)
    assert set(OPTIONAL_PAYLOAD_ENV_NAMES) <= set(controls), "the profile sets every optional field"

    payload, why = core_blue.blue_restore_payload(_adopted(sorted(D0_NAMES)), **INPUTS)

    assert payload is not None, why
    names = sorted(str(item["name"]) for item in payload["env_vars"])  # type: ignore[union-attr]
    assert names == sorted(D0_NAMES), "blue's exact set: the optional names D0 lacks are left out"
    with_one = sorted({*D0_NAMES, "MIP_AUDITOR_IDENTITIES"})
    payload, why = core_blue.blue_restore_payload(_adopted(with_one), **INPUTS)
    assert payload is not None, why
    env = {item["name"]: item.get("value") for item in payload["env_vars"]}  # type: ignore[union-attr]
    assert env["MIP_AUDITOR_IDENTITIES"] == controls["MIP_AUDITOR_IDENTITIES"], "one blue carried, kept"


def test_only_the_profiles_optional_names_are_left_out_never_a_required_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from tests.fixtures.client_profiles import core_tier_profile
    from tools.clients import profile as views
    from tools.clients.environment import RawProfile, resolve
    from tools.clients.profile import OPTIONAL_PAYLOAD_ENV_NAMES

    full = views.app_payload_env(resolve(RawProfile(values=core_tier_profile())))
    bare = views.app_payload_env(
        resolve(RawProfile(values=core_tier_profile(**dict.fromkeys(OPTIONAL_PAYLOAD_ENV_NAMES))))
    )
    assert sorted(set(full) - set(bare)) == sorted(OPTIONAL_PAYLOAD_ENV_NAMES), "the list is the profile's own"
    _governed_inputs(monkeypatch, tmp_path)
    payload, why = core_blue.blue_restore_payload(_adopted(sorted({*D0_NAMES, "MIP_OTEL_ENDPOINT"})), **INPUTS)
    assert payload is None and "lacks MIP_OTEL_ENDPOINT" in why
    names = sorted(D0_NAMES)
    names.remove("MIP_RUM_ENABLED")  # a name the builder always ships, blue lacking it, still refuses
    payload, why = core_blue.blue_restore_payload(_adopted(names), **INPUTS)
    assert payload is None and "adds MIP_RUM_ENABLED" in why


def test_a_blue_whose_set_holds_both_switches_gets_them_as_zero() -> None:
    names = sorted({*D0_NAMES, "MIP_AI_GATEWAY", "MIP_AGENT_ORCHESTRATOR"})

    payload, why = core_blue.blue_restore_payload(_adopted(names), **INPUTS)

    assert payload is not None, why
    env = {item["name"]: item.get("value") for item in payload["env_vars"]}  # type: ignore[union-attr]
    assert (env["MIP_AI_GATEWAY"], env["MIP_AGENT_ORCHESTRATOR"]) == ("0", "0")


def test_a_set_the_rebuild_cannot_reproduce_hands_in_no_payload() -> None:
    payload, why = core_blue.blue_restore_payload(_adopted([*sorted(D0_NAMES), "MIP_OTEL_ENDPOINT"]), **INPUTS)

    assert payload is None and "lacks MIP_OTEL_ENDPOINT" in why


def test_an_operator_value_that_turns_an_agentic_switch_on_hands_in_no_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MIP_AI_GATEWAY", "1")
    with_switches = sorted({*D0_NAMES, "MIP_AI_GATEWAY", "MIP_AGENT_ORCHESTRATOR"})

    for names in (sorted(D0_NAMES), with_switches):
        payload, why = core_blue.blue_restore_payload(_adopted(names), **INPUTS)
        assert payload is None and "refused" in why, why


def test_a_recorded_payload_or_no_core_blue_needs_no_rebuild() -> None:
    recorded = _adopted(rr.core_env_names(), restore_payload=rr.payload(rr.adopted_folder()))
    assert core_blue.blue_restore_payload(recorded, **INPUTS) == (None, "blue's restore payload is already recorded")
    assert core_blue.blue_restore_payload(None, **INPUTS)[0] is None
    assert core_blue.blue_restore_payload(rr.v6_record(), **INPUTS)[0] is None


def test_a_blue_bound_to_another_database_is_rebuilt_with_its_database_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Review of #330: a client bound to a non-default database carries LAKEBASE_DATABASE in blue's set.

    The deploy hands the rebuild the database it binds (``--lakebase-database``,
    as it hands ``app_deploy_payload``), so the rebuilt set is blue's; without
    it the builder's default adds no name and the rebuild refuses.
    """

    from tools.databricks import app_core_rollback

    names = sorted({*D0_NAMES, "LAKEBASE_DATABASE"})
    payload, why = core_blue.blue_restore_payload(_adopted(names), **INPUTS, lakebase_database="acme_app_state")
    assert payload is not None, why
    env = {item["name"]: item.get("value") for item in payload["env_vars"]}  # type: ignore[union-attr]
    assert env["LAKEBASE_DATABASE"] == "acme_app_state"
    assert core_blue.blue_restore_payload(_adopted(names), **INPUTS)[1].endswith("lacks LAKEBASE_DATABASE)")
    default, why = core_blue.blue_restore_payload(_adopted(sorted(D0_NAMES)), **INPUTS, lakebase_database="mip_app_state")
    assert default is not None and "LAKEBASE_DATABASE" not in {item["name"] for item in default["env_vars"]}, why  # type: ignore[index]

    # The command the deploy runs threads the flag through to the rebuild.
    monkeypatch.setattr(core_blue, "load_core_blue", lambda *_a, **_k: _adopted(names))
    out = tmp_path / "blue-restore-payload.json"
    argv = ["restore-payload", "--app-name", rr.APP_NAME, "--scope", "scope", "--lakebase-instance", rr.LAKEBASE,
            "--agentic-tier", "off", "--install-class", "demo", "--app-env", "sandbox", "--out", str(out)]
    line = app_core_rollback.run(app_core_rollback._parser().parse_args([*argv, "--lakebase-database", "acme_app_state"]), None)
    assert line == "restore-payload: blue's restore payload was rebuilt with blue's exact env-name set"
    assert '"LAKEBASE_DATABASE"' in out.read_text(encoding="utf-8")

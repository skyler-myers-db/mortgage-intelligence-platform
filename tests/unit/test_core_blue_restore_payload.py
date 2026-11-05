"""An adopted core blue's restore payload, rebuilt with blue's exact env-name set (M2-PR7b).

Databricks redacts env values from ``get_deployment``, so the core release
rebuilds blue's tier-b restore payload with the real payload builder, as the
emergency recipe built the adopted deployment, and hands it to the
pre-activation proof only when its env-name set is exactly blue's: D0's 32
names, with neither agentic switch and no lease even though the governed run
exports one.
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

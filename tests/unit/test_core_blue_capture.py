"""The core release's signed capture: a v7 core record, provenance captured (M2-PR7b)."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from tests.fixtures import rollback_records as rr
from tests.fixtures.adopt_live_world import DIST, REPO_FILES
from tests.fixtures.core_blue_world import CoreWorld, adopted_core_world
from tools.databricks import app_rollback_record_contract as contract
from tools.databricks import core_blue_capture as capture
from tools.databricks import core_blue_restore
from tools.databricks.adopted_source_copy import dist_digest, folder_digest
from tools.databricks.core_blue import CoreBlueRefused

RELEASE_ID = "0123456789ab-fedcba987654"
STAGED = {**REPO_FILES, **DIST}
SWITCHES = {"MIP_AGENT_ORCHESTRATOR": "0", "MIP_AI_GATEWAY": "0"}


@pytest.fixture(autouse=True)
def _keys(monkeypatch: pytest.MonkeyPatch) -> None:
    rr.signing_env(monkeypatch)
    monkeypatch.setattr(contract, "assert_owned_app_rollback_scope", lambda *_a, **_kw: None)


def _green_payload(**env: str) -> dict[str, object]:
    return rr.payload(rr.RELEASE_FOLDER, {**SWITCHES, **env})


def _activate(world: CoreWorld, payload: dict[str, object], deployment_id: str = "deployment-green") -> None:
    """Green activated from this release's folder, its snapshot byte-equal to the staged release."""

    names = sorted(str(item["name"]) for item in payload["env_vars"])  # type: ignore[union-attr]
    world.add_deployment(deployment_id, source=str(payload["source_code_path"]), env_names=names)
    for root in (str(payload["source_code_path"]), world.snapshot(deployment_id)):
        for relpath, data in STAGED.items():
            world.tree.add_file(f"{root.removeprefix('/Workspace')}/{relpath}", data)


@pytest.fixture
def world(tmp_path: Path) -> CoreWorld:
    built = adopted_core_world(tmp_path)
    _activate(built, _green_payload())
    return built


def _capture(world: CoreWorld, **overrides: Any) -> dict[str, Any]:
    payload = overrides.pop("payload", _green_payload())
    health = overrides.pop("health", lambda: (world.main, None, rr.LEASE_ID, world.active))
    values: dict[str, Any] = {
        "app_name": rr.APP_NAME,
        "scope": rr.SCOPE,
        "lakebase_instance": rr.LAKEBASE,
        "agentic_tier": "off",
        "payload": payload,
        "release_id": RELEASE_ID,
        "staged": STAGED,
        "expected_git_sha": world.main,
        "lease_id": rr.LEASE_ID,
        "genie_space_id": rr.GENIE_SPACE_ID,
        "reviewed_resources": copy.deepcopy(rr.APP_RESOURCES),
        "health": health,
        "lease": world.lease,
        "treatment": world.converge_treatment,
        **overrides,
    }
    return capture.capture_core_green(world, **values)


def test_the_core_capture_signs_a_v7_core_record_over_the_core_resource_proof(world: CoreWorld) -> None:
    record = _capture(world)

    assert (record["version"], record["tier"], record["provenance"]) == (7, "core", "captured")
    assert record["deployment_id"] == "deployment-green" and record["git_sha"] == world.main
    assert record["restore_source_path"] == record["source_code_path"] == rr.RELEASE_FOLDER
    assert record["restore_payload"] == _green_payload(), "the deployed payload, so restore rebuilds nothing"
    assert record["source_tree_sha256"] == folder_digest(STAGED)
    assert (record["frontend_provenance"], record["frontend_reference_sha256"]) == (
        "verified",
        dist_digest(STAGED),
    ), "this release built its own dist"
    assert (record["ack_purpose"], record["ack_sha256"], record["predecessor_deployment_id"]) == (None, None, None)
    assert not {"gateway_resources", "gateway_binding_sha256", "pending_proxy_credential_retirement_ids"} & set(record)
    assert world.treatments == ["quiesce", "runtime"]
    assert world.record() == record and world.apps.mutations == []


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"agentic_tier": "on"}, "tier_agentic"),
        ({"payload": _green_payload(MIP_AI_GATEWAY="1")}, "payload_invalid"),
        ({"payload": _green_payload(MIP_AGENT_MODEL="databricks-model")}, "env_names_differ"),
        ({"payload": rr.payload(rr.adopted_folder(), SWITCHES)}, "payload_invalid"),
        ({"release_id": "fedcba987654-0123456789ab"}, "source_not_release"),
        ({"lease_id": "22222222-2222-4222-8222-222222222222"}, "payload_invalid"),
    ],
)
def test_the_capture_refuses_a_payload_or_tier_that_is_not_the_core_releases(
    world: CoreWorld, change: dict[str, Any], code: str
) -> None:
    before = world.record()
    with pytest.raises(CoreBlueRefused) as refused:
        _capture(world, **change)
    assert refused.value.code == code
    assert world.record() == before and world.treatments == []


@pytest.mark.parametrize(
    "health",
    [("other", None, rr.LEASE_ID), ("sha", "b" * 64, rr.LEASE_ID), ("sha", None, "33333333-3333-4333-8333-333333333333")],
    ids=["commit", "gateway-binding", "lease"],
)
def test_the_capture_refuses_a_health_that_is_not_this_core_green(world: CoreWorld, health: tuple[Any, ...]) -> None:
    sha = world.main if health[0] == "sha" else "e" * 40
    with pytest.raises(CoreBlueRefused) as refused:
        _capture(world, health=lambda: (sha, health[1], health[2], world.active))
    assert refused.value.code == "health_mismatch" and world.treatments == []


def test_a_serving_binding_refuses_the_capture_by_name(world: CoreWorld) -> None:
    world.resources = [
        *copy.deepcopy(rr.APP_RESOURCES),
        {"name": "agent", "serving_endpoint": {"name": "mip-gateway", "permission": "CAN_QUERY"}},
    ]
    with pytest.raises(CoreBlueRefused) as refused:
        _capture(world)
    assert refused.value.code == "bindings_invalid" and "serving_endpoint" in str(refused.value)


def test_a_release_folder_that_is_not_the_staged_release_refuses(world: CoreWorld) -> None:
    world.tree.add_file(f"{rr.RELEASE_FOLDER.removeprefix('/Workspace')}/backend/extra.py", b"x\n")
    with pytest.raises(CoreBlueRefused) as refused:
        _capture(world)
    assert refused.value.code == "tree_drift" and world.treatments == []


def test_a_health_drift_after_the_runtime_grant_quiesces_treatment_again(world: CoreWorld) -> None:
    answers = iter([(world.main, None, rr.LEASE_ID, world.active), ("e" * 40, None, rr.LEASE_ID, world.active)])
    with pytest.raises(CoreBlueRefused) as refused:
        _capture(world, health=lambda: next(answers))
    assert refused.value.code == "health_mismatch"
    assert world.treatments == ["quiesce", "runtime", "quiesce"]


def test_a_captured_core_green_is_restorable_by_the_core_restore(world: CoreWorld) -> None:
    captured = _capture(world)
    _activate(world, _green_payload(MIP_EXTRA="next"), "deployment-next")  # a later green, then a rollback
    world.treatments.clear()

    record = core_blue_restore.restore_core_blue(
        world, **world.kwargs(), expected_rollback_deployment_id="deployment-green"
    )

    assert world.apps.deployed == [captured["restore_payload"]]
    assert record["predecessor_deployment_id"] == "deployment-green"
    assert record["provenance"] == "captured" and world.treatments == ["quiesce", "runtime"]

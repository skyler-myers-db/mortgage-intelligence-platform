"""The v7 rollback record (M2-PR7): golden v5/v6 unchanged, the core and agentic kinds, the slot."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from backend.agents.gateway_contract import gateway_exact_resource_digest
from tests.fixtures import rollback_records as rr
from tools.databricks import app_rollback_record_contract as contract
from tools.databricks import app_rollback_record_v7 as v7

# sha256 of the signed record's canonical bytes, and of its validated form,
# computed from tests/fixtures/rollback_records.py on the pre-v7 tree
# (origin/feat/agentic-tier-sites 20d3f5b9) and re-checked on this one.
GOLDEN = {
    5: (
        "b1f1d55ec73243b0791c15572200a6d00efdf53955f1382d3e76575456936a82",
        "51b5e1d51e7446b8bd2449914b4aa63c2fd902ca6cab8a2a91543f3c34fa068d",
    ),
    6: (
        "985442087a8a9320dcfd89bbdff3c380908e3e26326f00b386ea6a4290f9be18",
        "a229dca6f7bdfd40b97e7dcde83b3ac6caa06f00f90af3ef3d6b1e845022d87c",
    ),
}


@pytest.fixture(autouse=True)
def _keys(monkeypatch: pytest.MonkeyPatch) -> None:
    rr.signing_env(monkeypatch)
    monkeypatch.setattr(contract, "assert_owned_app_rollback_scope", lambda *_a, **_kw: None)


def _workspace() -> Any:
    return SimpleNamespace(secrets=rr.Secrets())


def _sha(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=list).encode()).hexdigest()


def _load(workspace: Any, **kwargs: Any) -> dict[str, Any]:
    return contract._load_record(
        workspace,
        app_name=rr.APP_NAME,
        scope=rr.SCOPE,
        expected_lakebase_instance=rr.LAKEBASE,
        **kwargs,
    )


# --- golden v5 and v6 -----------------------------------------------------------------


@pytest.mark.parametrize("version", (5, 6))
def test_golden_v5_and_v6_records_sign_and_verify_byte_identically(version: int) -> None:
    built = rr.v5_record() if version == 5 else rr.v6_record()
    serialized = json.dumps(contract._sign_record(built), sort_keys=True, separators=(",", ":"))

    assert hashlib.sha256(serialized.encode()).hexdigest() == GOLDEN[version][0]
    contract._verify_record_attestation(json.loads(serialized))
    validated = contract._validated_record(
        json.loads(serialized), app_name=rr.APP_NAME, expected_lakebase_instance=rr.LAKEBASE
    )
    assert _sha(validated) == GOLDEN[version][1]


@pytest.mark.parametrize("version", (5, 6))
def test_golden_records_load_through_the_v5_v6_reader_unchanged(version: int) -> None:
    workspace = _workspace()
    built = rr.v5_record() if version == 5 else rr.v6_record()
    contract.save_versioned_record(workspace, scope=rr.SCOPE, record=built)

    key = (
        contract._legacy_record_key(rr.APP_NAME)
        if version == 5
        else contract._record_key(rr.APP_NAME)
    )
    assert workspace.secrets.puts == [key]
    assert _sha(_load(workspace)) == GOLDEN[version][1]
    assert _load(workspace, accept_v7=True) == _load(workspace)


# --- v7: the slot, the signature and the readers ----------------------------------------


def test_a_v7_record_shares_the_current_slot_with_its_own_algorithm() -> None:
    workspace = _workspace()
    contract.save_versioned_record(workspace, scope=rr.SCOPE, record=rr.v7_core_record())

    assert workspace.secrets.puts == [contract._record_key(rr.APP_NAME)], "never a shadowing key"
    stored = json.loads(workspace.secrets.values[(rr.SCOPE, contract._record_key(rr.APP_NAME))])
    assert stored["attestation_alg"] == "ed25519-app-rollback-v7" and stored["version"] == 7
    loaded = contract.load_signed_record(
        workspace, app_name=rr.APP_NAME, scope=rr.SCOPE, expected_lakebase_instance=rr.LAKEBASE
    )
    assert loaded is not None and v7.unsigned(loaded) == rr.v7_core_record()


def test_the_v5_v6_reader_refuses_a_v7_record_unless_tier_aware() -> None:
    workspace = _workspace()
    contract.save_versioned_record(workspace, scope=rr.SCOPE, record=rr.v7_core_record())

    with pytest.raises(RuntimeError) as refused:
        _load(workspace)
    assert contract.V7_REFUSAL in str(refused.value) and "core-tier" in str(refused.value)
    assert _load(workspace, accept_v7=True)["tier"] == "core"


def test_a_retired_tombstone_reads_as_no_record_for_every_reader() -> None:
    workspace = _workspace()
    tombstone = rr.v7_core_record(provenance="retired", ack_purpose="unadopt")
    contract.save_versioned_record(workspace, scope=rr.SCOPE, record=tombstone)

    for kwargs in ({}, {"accept_v7": True}):
        with pytest.raises(
            RuntimeError, match="^no server-owned last-good App rollback contract exists"
        ):
            _load(workspace, **kwargs)
    kept = contract.load_signed_record(
        workspace, app_name=rr.APP_NAME, scope=rr.SCOPE, expected_lakebase_instance=rr.LAKEBASE
    )
    assert (
        kept is not None and kept["restore_source_path"] == rr.adopted_folder()
    ), "nothing is deleted"


def test_a_tombstones_remedy_is_the_re_adoption_the_bootstrap_gate_allows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bootstrap gate refuses over any record, so a tombstone never names the rebase."""

    from tools.databricks import app_rollback_bootstrap_gate as gate
    from tools.databricks import prestop_rollback_record as prestop

    monkeypatch.setattr(gate, "assert_owned_app_rollback_scope", lambda *_a, **_kw: None)
    workspace = _workspace()
    with pytest.raises(RuntimeError, match="run the explicit bootstrap command") as empty:
        _load(workspace)
    gate.assert_rollback_record_absent(workspace, app_name=rr.APP_NAME, scope=rr.SCOPE)

    tombstone = rr.v7_core_record(provenance="retired", ack_purpose="unadopt")
    contract.save_versioned_record(workspace, scope=rr.SCOPE, record=tombstone)
    with pytest.raises(RuntimeError) as retired:
        _load(workspace)
    message = str(retired.value)
    assert "bootstrap" not in message and "--phase adopt" in message
    assert "retired adopt-live tombstone" in message
    with pytest.raises(RuntimeError, match="refusing the one-time unsigned rebase"):
        gate.assert_rollback_record_absent(workspace, app_name=rr.APP_NAME, scope=rr.SCOPE)
    assert (
        prestop._classify(retired.value)
        == prestop._classify(empty.value)
        == (prestop.ROLLBACK_RECORD_ABSENT)
    ), "every consumer still reads a tombstone as no record"
    assert "--phase adopt" in prestop._REMEDY_ABSENT


def test_an_agentic_tier_unadopts_remedy_names_the_core_tier_its_re_adoption_needs(
    tmp_path: Path,
) -> None:
    """Unadopt runs in either tier, adopt in the core tier only: the remedy says which."""

    from tests.fixtures.adopt_live_world import World, make_repo
    from tools.databricks import adopt_live
    from tools.databricks import prestop_rollback_record as prestop

    repo, main, side = make_repo(tmp_path)
    world = World(repo=repo, main=main, side=side)
    world.apply()
    world.apply("unadopt", tier="on")
    with pytest.raises(RuntimeError) as retired:
        _load(world)
    message = str(retired.value)
    assert message.startswith("no server-owned last-good App rollback contract exists")
    assert "--phase adopt from a profile whose MIP_AGENTIC_TIER is off" in message
    assert "tier_agentic" in message and "in the core tier" in message
    assert "MIP_AGENTIC_TIER off" in prestop._REMEDY_ABSENT
    with pytest.raises(adopt_live.AdoptLiveRefused) as agentic:
        adopt_live.compute_plan(world, "adopt", world.target(tier="on"), world.readers())
    assert agentic.value.code == "tier_agentic", "the agentic tier cannot take the remedy"
    assert world.apply()["provenance"] == "adopted", "the core tier's re-adoption the remedy names"


@pytest.mark.parametrize("tamper", ("field", "algorithm"))
def test_a_tampered_v7_record_fails_its_signature(tamper: str) -> None:
    workspace = _workspace()
    contract.save_versioned_record(workspace, scope=rr.SCOPE, record=rr.v7_core_record())
    key = (rr.SCOPE, contract._record_key(rr.APP_NAME))
    stored = json.loads(workspace.secrets.values[key])
    if tamper == "field":
        stored["frontend_provenance"] = (
            "verified" if stored["frontend_provenance"] != "verified" else "unverified"
        )
    else:
        stored["attestation_alg"] = contract.RECORD_ATTESTATION_ALG
    workspace.secrets.values[key] = json.dumps(stored)

    with pytest.raises(RuntimeError, match="signature is invalid|attestation identity is invalid"):
        _load(workspace, accept_v7=True)


# --- the core kind ---------------------------------------------------------------------


def test_a_v7_core_record_refuses_a_serving_endpoint_binding() -> None:
    serving = {"name": "agent", "serving_endpoint": {"name": "gateway", "permission": "CAN_QUERY"}}

    with pytest.raises(v7.V7RecordError, match="refuses a serving_endpoint resource binding"):
        rr.v7_core_record(app_resources=[*rr.APP_RESOURCES, serving])
    record = rr.v7_core_record()
    with pytest.raises(v7.V7RecordError, match="serving_endpoint"):
        v7.assert_core_resource_proof(record, live=[*rr.APP_RESOURCES, serving])


@pytest.mark.parametrize(
    "name",
    (
        "MIP_AGENT_PROXY_CLIENT_ID",
        "MIP_AI_GATEWAY_ENDPOINT",
        "MIP_AGENT_SERVING_ENDPOINT",
        "MIP_AGENT_SUPERVISOR_ID",
        "MIP_AGENT_MODEL",
        "MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY",
        "MIP_GATEWAY_MODEL_ATTESTATION_PREVIOUS_VERIFY_KEY",
        "MIP_AGENT_EVAL_EXPERIMENT",
        "MIP_AGENT_EVAL_RUN_ID",
        "MIP_AI_GATEWAY_PROOF_VERIFY_KEY",
        "MIP_REVIEWED_FUNCTION_OWNER",
    ),
)
def test_a_v7_core_record_refuses_a_serving_gateway_or_proxy_env_name(name: str) -> None:
    with pytest.raises(v7.V7RecordError, match="Supervisor, agent-model, eval or proxy env name"):
        rr.v7_core_record(env_names=sorted([*rr.core_env_names(), name]))


def test_the_core_flags_off_are_not_agentic_names() -> None:
    assert not v7.is_agentic_env_name("MIP_AI_GATEWAY") and not v7.is_agentic_env_name(
        "MIP_AGENT_ORCHESTRATOR"
    )


def test_the_agentic_names_are_every_agentic_name_the_payload_builder_emits() -> None:
    from tests.unit.test_settings_d0_env_parity import D0_NAMES
    from tools.databricks import app_deploy_payload as builder

    assert v7.CORE_OFF_SWITCHES == builder.CORE_TIER_OFF_SWITCHES
    names = builder.NON_SECRET_OPERATOR_VARS
    block = names[names.index("MIP_AGENT_ORCHESTRATOR") : names.index("MIP_AGENT_EVAL_RUN_ID") + 1]
    unclassified = [
        name
        for name in block
        if name not in {*v7.CORE_OFF_SWITCHES, "MIP_LAKEBASE_SYNC"}
        and not v7.is_agentic_env_name(name)
    ]
    assert unclassified == [], "every name of the builder's agentic block is an agentic name"
    assert [name for name in names if v7.is_agentic_env_name(name)] == [
        name for name in block if name not in {*v7.CORE_OFF_SWITCHES, "MIP_LAKEBASE_SYNC"}
    ], "and no name outside it is"
    assert not any(v7.is_agentic_env_name(name) for name in D0_NAMES), "D0 still adopts"


@pytest.mark.parametrize(
    ("switches", "ok"),
    (
        ({"MIP_AI_GATEWAY": "0", "MIP_AGENT_ORCHESTRATOR": "0"}, True),
        ({"MIP_AI_GATEWAY": "1", "MIP_AGENT_ORCHESTRATOR": "0"}, False),
        ({"MIP_AI_GATEWAY": "0", "MIP_AGENT_ORCHESTRATOR": "1"}, False),
        ({"MIP_AI_GATEWAY": "true", "MIP_AGENT_ORCHESTRATOR": "0"}, False),
        ({"MIP_AI_GATEWAY": "off", "MIP_AGENT_ORCHESTRATOR": "0"}, False),
        ({"MIP_AI_GATEWAY": "value_from", "MIP_AGENT_ORCHESTRATOR": "0"}, False),
    ),
)
def test_a_core_restore_payload_never_switches_an_agentic_surface_on(
    switches: dict[str, str], ok: bool
) -> None:
    payload = rr.payload(
        rr.adopted_folder(), {k: v for k, v in switches.items() if v != "value_from"}
    )
    for name, value in switches.items():
        if value == "value_from":
            payload["env_vars"].append({"name": name, "value_from": "sql_warehouse"})  # type: ignore[attr-defined]
    names = sorted([*rr.core_env_names(), *switches])
    if ok:
        record = rr.v7_core_record(env_names=names, restore_payload=payload)
        assert record["restore_payload"] == payload
        return
    with pytest.raises(v7.V7RecordError, match="must carry MIP_(AI_GATEWAY|AGENT_ORCHESTRATOR) as"):
        rr.v7_core_record(env_names=names, restore_payload=payload)


def test_a_verified_frontend_names_the_reference_digest_it_matched() -> None:
    rr.v7_core_record(frontend_provenance="unverified", frontend_reference_sha256=None)
    for overrides in (
        {"frontend_reference_sha256": None},
        {"frontend_reference_sha256": "not-a-digest"},
        {"frontend_provenance": "unverified"},
    ):
        with pytest.raises(v7.V7RecordError, match="frontend provenance is invalid"):
            rr.v7_core_record(**overrides)


def test_a_v7_core_record_carries_no_gateway_fields() -> None:
    record = {**rr.v7_core_record(), "gateway_resources": {}}

    with pytest.raises(v7.V7RecordError, match="not exactly the core tier's"):
        v7.validated_v7_record(record, app_name=rr.APP_NAME, expected_lakebase_instance=rr.LAKEBASE)


def test_the_core_resource_proof_is_the_records_and_the_profiles_bindings_only() -> None:
    record = rr.v7_core_record()
    v7.assert_core_resource_proof(record, live=rr.APP_RESOURCES, reviewed=rr.APP_RESOURCES)

    drifted = copy.deepcopy(rr.APP_RESOURCES)
    drifted[1]["sql_warehouse"]["id"] = "other-warehouse"  # type: ignore[index]
    with pytest.raises(v7.V7RecordError, match="drifted from the signed core record"):
        v7.assert_core_resource_proof(record, live=drifted)
    with pytest.raises(v7.V7RecordError, match="not the profile's reviewed bindings"):
        v7.assert_core_resource_proof(record, live=rr.APP_RESOURCES, reviewed=drifted)


@pytest.mark.parametrize(
    ("profile", "ok"), (("off", True), ("on", False), ("", False), ("core", False))
)
def test_a_records_tier_must_equal_the_profiles(profile: str, ok: bool) -> None:
    record = rr.v7_core_record()
    if ok:
        v7.assert_record_tier(record, agentic_tier=profile)
    else:
        with pytest.raises(v7.V7RecordError):
            v7.assert_record_tier(record, agentic_tier=profile)


# --- the agentic kind ------------------------------------------------------------------


def _gateway() -> dict[str, Any]:
    resources = {
        **rr.GATEWAY_RESOURCES,
        "resource_digest": gateway_exact_resource_digest(rr.GATEWAY_RESOURCES),
    }
    return {
        "gateway_resources": resources,
        "gateway_binding_sha256": "b" * 64,
        "pending_proxy_credential_retirement_ids": [],
    }


def _agentic_fields(**overrides: Any) -> dict[str, Any]:
    source = rr.adopted_folder()
    release = (
        "/Workspace/Users/"
        + rr.DEPLOYER
        + "/.bundle/mortgage-intelligence-platform/client-one/app-src/"
    )
    release += "0123456789ab-fedcba987654"
    fields = rr.v7_core_fields(
        tier="agentic",
        provenance="captured",
        ack_purpose=None,
        ack_sha256=None,
        restore_source_path=release,
        restore_payload=rr.payload(release),
    )
    del source
    fields.update(overrides)
    return fields


def test_a_v7_agentic_record_keeps_the_v6_gateway_contract() -> None:
    record = v7.build_v7_record(
        expected_lakebase_instance=rr.LAKEBASE, gateway=_gateway(), **_agentic_fields()
    )

    assert (
        record["gateway_resources"]["resource_digest"]
        == _gateway()["gateway_resources"]["resource_digest"]
    )


def test_a_v7_agentic_record_still_requires_the_gateway_proof() -> None:
    with pytest.raises(v7.V7RecordError, match="not exactly the agentic tier's"):
        v7.build_v7_record(expected_lakebase_instance=rr.LAKEBASE, **_agentic_fields())
    tampered = _gateway()
    tampered["gateway_resources"] = {**tampered["gateway_resources"], "resource_digest": "0" * 64}
    with pytest.raises(RuntimeError, match="Gateway resource contract digest is invalid"):
        v7.build_v7_record(
            expected_lakebase_instance=rr.LAKEBASE, gateway=tampered, **_agentic_fields()
        )


def test_the_core_proof_never_applies_to_an_agentic_record() -> None:
    record = v7.build_v7_record(
        expected_lakebase_instance=rr.LAKEBASE, gateway=_gateway(), **_agentic_fields()
    )

    with pytest.raises(v7.V7RecordError, match="core-tier record only"):
        v7.assert_core_resource_proof(record, live=rr.APP_RESOURCES)


# --- provenance, acks and the restore payload ------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {"ack_purpose": "unadopt"},
        {"ack_purpose": "no_rollback_for"},
        {"ack_sha256": None},
        {"provenance": "retired", "ack_purpose": "adopt_live"},
        {"frontend_provenance": "maybe"},
        {"treatment_mode": "open"},
        {"predecessor_deployment_id": "deployment-blue"},
        {"git_sha": "abc123"},
    ],
    ids=lambda overrides: "-".join(f"{key}={value}" for key, value in overrides.items()),
)
def test_an_adopted_record_names_its_reviewed_ack_and_fields(overrides: dict[str, Any]) -> None:
    with pytest.raises(v7.V7RecordError):
        rr.v7_core_record(**overrides)


def test_a_captured_record_carries_its_payload_a_verified_frontend_and_no_ack() -> None:
    fields = _agentic_fields(tier="core")
    v7.build_v7_record(expected_lakebase_instance=rr.LAKEBASE, **fields)
    for overrides in (
        {"restore_payload": None},
        {"frontend_provenance": "unverified"},
        {"ack_purpose": "adopt_live"},
    ):
        with pytest.raises(v7.V7RecordError):
            v7.build_v7_record(expected_lakebase_instance=rr.LAKEBASE, **{**fields, **overrides})


def test_the_restore_payload_is_the_copy_with_blues_env_names() -> None:
    ok = rr.v7_core_record(restore_payload=rr.payload(rr.adopted_folder()))
    assert ok["restore_payload_sha256"] == contract._payload_digest(ok["restore_payload"])

    with pytest.raises(v7.V7RecordError, match="not the immutable restore source"):
        rr.v7_core_record(restore_payload=rr.payload(rr.SNAPSHOT))
    with pytest.raises(v7.V7RecordError, match="env-name set is not blue's"):
        rr.v7_core_record(restore_payload=rr.payload(rr.adopted_folder(), {"MIP_EXTRA": "x"}))
    record = {
        **rr.v7_core_record(restore_payload=rr.payload(rr.adopted_folder())),
        "restore_payload_sha256": "0" * 64,
    }
    with pytest.raises(v7.V7RecordError, match="restore payload digest does not match"):
        v7.validated_v7_record(record, app_name=rr.APP_NAME, expected_lakebase_instance=rr.LAKEBASE)


def test_an_adopted_restore_source_is_an_adopted_copy_never_a_snapshot() -> None:
    for path in (rr.SNAPSHOT, rr.LEGACY_FOLDER, rr.adopted_folder().replace("adopted-", "adopt-")):
        with pytest.raises(v7.V7RecordError, match="restore source is invalid"):
            rr.v7_core_record(restore_source_path=path)

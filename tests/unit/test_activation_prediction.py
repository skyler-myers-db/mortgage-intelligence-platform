"""The core release's pre-activation rollback proof, predicted read-only before the lease (B3).

Client #1's blue is adopted in CI, so its frontend is ``unverified`` and the
release's pre-activation proof (step 10b, after the lease, the apply and the
window) needs the exact ``restore_unverified_frontend`` ack. These pin that
``activation_prediction.predict`` reaches the late gate's own verdict before
the lease, with the late gate's loader and payload checks; that the digest the
plan prints is the one the whole run accepts, across the quiesce window's
re-pin (the ack names blue's immutable copy, which a re-pin keeps); that a
supersede-shaped record, another commit or a malformed ack never matches; that
the no_rollback_for ack defers every verdict it can stand in for; and that
nothing it prints carries an id, a path or a value.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from tests.fixtures import rollback_records as rr
from tests.fixtures.core_blue_world import GREEN_RELEASE, GREEN_SHA, CoreWorld, adopted_core_world
from tools.databricks import activation_prediction as ap
from tools.databricks import app_rollback_record_contract as contract
from tools.databricks import blue_restore_proof as proof
from tools.databricks import core_blue, core_blue_repin

LEASE = core_blue.LEASE_ENV
# The adopted D0-shaped blue: the emergency recipe sets no lease name.
NAMES = sorted(name for name in rr.core_env_names() if name != LEASE)
INPUTS: dict[str, Any] = {
    "install_class": "demo",
    "app_env": "sandbox",
    "catalog": "mip",
    "schema": "gold",
    "lakebase_instance": rr.LAKEBASE,
    "lakebase_database": "mip_app_state",
}
SENTINEL_VALUE = "sentinel-value-never-printed"


@pytest.fixture(autouse=True)
def _keys(monkeypatch: pytest.MonkeyPatch) -> None:
    rr.signing_env(monkeypatch)
    monkeypatch.setattr(contract, "assert_owned_app_rollback_scope", lambda *_a, **_kw: None)
    monkeypatch.setattr(ap, "_scope_names", lambda _workspace: {rr.SCOPE})  # the owned scope exists
    monkeypatch.delenv(LEASE, raising=False)


def builder(names: list[str] | None = None, **values: str) -> Any:
    """A payload builder that rebuilds ``names`` (blue's set by default) from the shell, as the real one does."""

    def build(*, source_code_path: str, **_kwargs: Any) -> dict[str, object]:
        env: list[dict[str, str]] = [{"name": "DATABRICKS_WAREHOUSE_ID", "value_from": "sql_warehouse"}]
        for name in names or NAMES:
            if name != "DATABRICKS_WAREHOUSE_ID":
                value = values.get(name) or os.environ.get(name) or rr.CORE_ENV.get(name, SENTINEL_VALUE)
                env.append({"name": name, "value": value})
        return {"source_code_path": source_code_path, "mode": "SNAPSHOT", "env_vars": env}

    return build


def _world(tmp_path: Path, *, verified: bool = False, names: list[str] | None = None) -> CoreWorld:
    tmp_path.mkdir(parents=True, exist_ok=True)
    world = adopted_core_world(tmp_path, verified=verified, env_names=list(names or NAMES))
    world.secrets.puts.clear()  # the adoption's own write; a prediction writes none
    world.lease_calls = 0
    return world


def _predict(world: CoreWorld, phase: str, **kwargs: Any) -> ap.Prediction:
    values: dict[str, Any] = {"run_git_sha": GREEN_SHA, "payload_inputs": INPUTS, "build": builder(), **kwargs}
    return ap.predict(
        world, app_name=rr.APP_NAME, scope=rr.SCOPE, lakebase_instance=rr.LAKEBASE, phase=phase, **values
    )


def _late_gate(world: CoreWorld, ack: str | None) -> str:
    """Step 10b as the core release runs it: rebuild blue's payload, then prepare-activation."""

    record = world.record()
    payload, why = core_blue.blue_restore_payload(record, build=builder(), **INPUTS)
    assert payload is not None, why
    return proof.prepare_activation(
        world,
        app_name=rr.APP_NAME,
        scope=rr.SCOPE,
        lakebase_instance=rr.LAKEBASE,
        lease=world.lease,
        agentic_tier="off",
        green_git_sha=GREEN_SHA,
        green_release_id=GREEN_RELEASE,
        restore_payload=payload,
        unverified_frontend_ack=ack,
    )


def _window_repin(world: CoreWorld) -> str:
    """The quiesce window's start and re-pin: a new deployment of blue's own folder, signed as blue."""

    record = world.record()
    assert record is not None
    start = "deployment-window-start"
    world.add_deployment(start, source=record["source_code_path"], env_names=list(record["env_names"]))
    world.snapshot_source(start, record["source_code_path"])
    core_blue_repin.repin_core_start(
        world,
        app_name=rr.APP_NAME,
        scope=rr.SCOPE,
        lakebase_instance=rr.LAKEBASE,
        deployment_id=start,
        expected_rollback_deployment_id=record["deployment_id"],
        health=world.serves,
        lease=world.lease,
    )
    return start


# --- the verdicts ---------------------------------------------------------------------------


def test_a_verified_blue_with_a_rebuildable_payload_passes_with_no_digest(tmp_path: Path) -> None:
    world = _world(tmp_path, verified=True)
    for phase in ap.PHASES:
        prediction = _predict(world, phase)
        assert (prediction.code, prediction.frontend_ack) == ("pass", None), prediction
    assert world.apps.mutations == [] and world.secrets.puts == [] and world.lease_calls == 0


def test_the_plan_prints_exactly_the_digest_the_pre_activation_proof_demands(tmp_path: Path) -> None:
    world = _world(tmp_path)
    prediction = _predict(world, "plan")
    assert prediction.code == "pass" and prediction.frontend_ack is not None

    with pytest.raises(proof.RestoreProofRefused) as refused:
        _late_gate(world, None)
    assert refused.value.code == "unverified_frontend_unacked"
    assert f"MIP_ACK_RESTORE_UNVERIFIED_FRONTEND={prediction.frontend_ack}" in str(refused.value)
    assert "recorded before activation" in _late_gate(world, prediction.frontend_ack)


def test_a_release_admits_exactly_the_digest_and_refuses_every_other_ack(tmp_path: Path) -> None:
    world = _world(tmp_path)
    digest = _predict(world, "plan").frontend_ack
    assert digest is not None
    for phase in ("release", "complete"):
        for ack, code in ((None, "ack_missing"), ("", "ack_missing"), ("abc", "ack_malformed"), ("0" * 64, "ack_mismatch")):
            prediction = _predict(world, phase, frontend_ack=ack)
            assert prediction.refused and prediction.code == "unverified_frontend_unacked", prediction
            assert f"({code})" in prediction.message and prediction.frontend_ack == digest
            assert f'operation_acks="... restore_unverified_frontend={digest}"' in prediction.message
            assert f"MIP_ACK_RESTORE_UNVERIFIED_FRONTEND={digest}" in prediction.message
        assert _predict(world, phase, frontend_ack=f" {digest}\n").code == "pass"
    assert world.apps.mutations == [] and world.secrets.puts == []


def test_the_plans_digest_survives_the_windows_re_pin_through_activation_and_tier_b(tmp_path: Path) -> None:
    """The whole run accepts the one digest: the window re-pins blue, then step 10b, then the trap's tier b."""

    world = _world(tmp_path)
    before = world.record()
    digest = _predict(world, "plan").frontend_ack
    assert before is not None and digest is not None

    start = _window_repin(world)
    after = world.record()
    assert after is not None and after["deployment_id"] == start != before["deployment_id"]
    assert after["predecessor_deployment_id"] == before["deployment_id"]
    assert after["restore_source_path"] == before["restore_source_path"], "a re-pin keeps the copy"
    assert _predict(world, "release", frontend_ack=digest).code == "pass", "after the re-pin too"

    _late_gate(world, digest)
    recorded = world.record()
    assert recorded is not None and recorded["restore_payload_sha256"] is not None
    restored = proof.tier_b_payload(
        world, app_name=rr.APP_NAME, record=recorded, agentic_tier="off",
        unverified_frontend_ack=digest, run_git_sha=GREEN_SHA,
    )  # fmt: skip
    assert restored["source_code_path"] == recorded["restore_source_path"]


def test_the_digest_never_crosses_a_copy_a_commit_or_a_payload(tmp_path: Path) -> None:
    world = _world(tmp_path)
    record = world.record()
    digest = _predict(world, "plan").frontend_ack
    assert record is not None and digest is not None
    payload, _why = core_blue.blue_restore_payload(record, build=builder(), **INPUTS)
    assert payload is not None
    plan = proof.unverified_frontend_plan(
        app_name=rr.APP_NAME, record={**record, "restore_payload_sha256": contract._payload_digest(payload)}, run_git_sha=GREEN_SHA
    )
    assert proof.ack_digest(ap.PURPOSE, plan) == digest
    payloaded = {**record, "restore_payload_sha256": plan["restore_payload_sha256"]}
    repinned = {**payloaded, "deployment_id": "deployment-of-a-start", "predecessor_deployment_id": record["deployment_id"]}
    assert proof.unverified_frontend_plan(app_name=rr.APP_NAME, record=repinned, run_git_sha=GREEN_SHA) == plan
    other_copy = {**payloaded, "restore_source_path": record["restore_source_path"].replace("adopted-", "adopted-f")}
    superseded = proof.unverified_frontend_plan(app_name=rr.APP_NAME, record=other_copy, run_git_sha=GREEN_SHA)
    assert superseded["restore_to"] != plan["restore_to"], "a supersede adopts into another copy"
    other_payload = {**plan, "restore_payload_sha256": "f" * 64}
    for other in (superseded, other_payload):
        assert proof.ack_digest(ap.PURPOSE, other) != digest
    assert _predict(world, "release", frontend_ack=digest, run_git_sha="e" * 40).code == "unverified_frontend_unacked"
    assert _predict(world, "release", frontend_ack=digest, build=builder(MIP_LAKEBASE_INSTANCE=rr.LAKEBASE, APP_ENV="prod")).refused


def test_no_rollback_given_defers_every_verdict_it_stands_in_for_but_never_in_a_plan(tmp_path: Path) -> None:
    unbuildable = builder([*NAMES, "MIP_OTEL_ENDPOINT"])
    cases: tuple[tuple[CoreWorld, dict[str, Any], str], ...] = (
        (_world(tmp_path / "a"), {"build": unbuildable}, "no_rollback_proof"),
        (_world(tmp_path / "b"), {}, "unverified_frontend_unacked"),
        (_world(tmp_path / "c", names=sorted({*NAMES, LEASE})), {"build": builder(sorted({*NAMES, LEASE}))}, "frontend_ack_unpredictable"),
    )
    for world, kwargs, code in cases:
        for phase in ("release", "complete"):
            assert _predict(world, phase, **kwargs).code == code
            deferred = _predict(world, phase, no_rollback_ack_given=True, **kwargs)
            assert deferred.code == "deferred" and code in deferred.message, deferred
        if code != "unverified_frontend_unacked":  # a plan prints that one's digest instead
            assert _predict(world, "plan", no_rollback_ack_given=True, **kwargs).code == code


def test_a_payload_the_rebuild_cannot_reproduce_is_no_rollback_proof_naming_names_only(tmp_path: Path) -> None:
    world = _world(tmp_path)
    for build, named in (
        (builder([*NAMES, "MIP_OTEL_ENDPOINT"]), "adds MIP_OTEL_ENDPOINT"),
        (builder([name for name in NAMES if name != "MIP_GIT_SHA"]), "lacks MIP_GIT_SHA"),
    ):
        prediction = _predict(world, "plan", build=build)
        assert prediction.code == "no_rollback_proof" and named in prediction.message
        assert SENTINEL_VALUE not in prediction.message


def test_a_rebuilt_payload_the_late_gate_would_refuse_is_payload_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = _world(tmp_path)
    record = world.record()
    assert record is not None
    good, _why = core_blue.blue_restore_payload(record, build=builder(), **INPUTS)
    assert good is not None
    elsewhere = {**good, "source_code_path": rr.LEGACY_FOLDER}
    monkeypatch.setattr(core_blue, "blue_restore_payload", lambda *_a, **_kw: (elsewhere, "rebuilt"))
    prediction = _predict(world, "release")
    assert prediction.code == "payload_invalid" and rr.LEGACY_FOLDER not in prediction.message


def test_a_blue_whose_names_hold_the_lease_cannot_be_acked_before_the_lease(tmp_path: Path) -> None:
    names = sorted({*NAMES, LEASE})
    unverified = _predict(_world(tmp_path / "u", names=names), "plan", build=builder(names))
    assert unverified.code == "frontend_ack_unpredictable" and unverified.frontend_ack is None
    verified = _predict(_world(tmp_path / "v", verified=True, names=names), "release", build=builder(names))
    assert verified.code == "pass", "a verified blue needs no ack, whatever its payload binds"


def test_the_loader_is_the_late_gates_own(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    world = _world(tmp_path)
    contract.save_versioned_record(world, scope=rr.SCOPE, record=rr.v6_record())
    agentic = _predict(world, "release")
    assert agentic.code == "blue_tier_mismatch", "a v5/v6 blue refuses as the core rebuild and proof refuse it"

    fresh = _world(tmp_path / "fresh")
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_VERIFY_KEY", rr.SIGNING_KEY)  # not the record's key
    tampered = _predict(fresh, "plan")
    assert tampered.code == "record_unreadable" and "RuntimeError" in tampered.message


def test_no_record_has_no_blue_and_reports_env_name_parity(tmp_path: Path) -> None:
    world = _world(tmp_path)
    world.secrets.values.clear()
    holds = _predict(world, "release")
    assert holds.code == "pass" and "parity holds" in holds.message and "32" not in holds.message

    differs = builder([name for name in NAMES if name != "APP_ENV"])
    for phase in ap.PHASES:
        noted = _predict(world, phase, build=differs)
        assert noted.code == "pass" and "DIFFERS" in noted.message and "lacks APP_ENV" in noted.message, (
            "nothing to protect: a note; the plans before an adoption gate step 7 through env-names"
        )
        assert "step" not in f"{holds.message} {noted.message}".lower(), "the runbook's steps are env-names' alone"


def test_no_rollback_scope_is_no_record_as_in_a_first_install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    world = _world(tmp_path)
    monkeypatch.setattr(ap, "_scope_names", lambda _workspace: set())
    monkeypatch.setattr(contract, "assert_owned_app_rollback_scope", _refuse_absent_scope)
    for phase in ap.PHASES:
        prediction = _predict(world, phase)
        assert prediction.code == "pass" and "no signed blue to protect" in prediction.message


def _refuse_absent_scope(*_a: Any, **_kw: Any) -> None:
    raise RuntimeError("App rollback secret scope is absent")


def test_env_name_parity_reads_the_newest_deployment_and_prints_names_only(tmp_path: Path) -> None:
    world = _world(tmp_path)
    world.add_deployment("deployment-newer", source=rr.LEGACY_FOLDER, env_names=sorted({*NAMES, "MIP_OTEL_ENDPOINT"}))
    verdict, message = ap.env_name_parity(world, app_name=rr.APP_NAME, payload_inputs=INPUTS, build=builder())
    assert verdict == "differs" and "lacks MIP_OTEL_ENDPOINT" in message and "step" not in message.lower()
    for secret in ("deployment-newer", rr.LEGACY_FOLDER, SENTINEL_VALUE, rr.GIT_SHA):
        assert secret not in message
    assert world.apps.mutations == []


def test_the_derived_names_stand_in_only_when_the_shell_has_not_derived_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tools.databricks import app_deploy_payload

    env_local = tmp_path / "empty.env.local"
    env_local.write_text("", encoding="utf-8")
    monkeypatch.setattr(app_deploy_payload, "ENV_LOCAL", env_local)
    names = sorted({*NAMES, *ap.GOVERNED_DERIVED_NAMES})
    world = _world(tmp_path, names=names)
    for name in ap.GOVERNED_DERIVED_NAMES:
        monkeypatch.delenv(name, raising=False)

    def only_set(*, source_code_path: str, **_kwargs: Any) -> dict[str, object]:
        present = [name for name in names if name not in ap.GOVERNED_DERIVED_NAMES or os.environ.get(name)]
        return builder(present)(source_code_path=source_code_path)

    bare, _ = ap.env_name_parity(world, app_name=rr.APP_NAME, payload_inputs=INPUTS, build=only_set)
    governed, message = ap.env_name_parity(
        world, app_name=rr.APP_NAME, payload_inputs=INPUTS, build=only_set, derived_sentinels=True
    )
    assert (bare, governed) == ("differs", "same"), message
    assert all(name not in os.environ for name in ap.GOVERNED_DERIVED_NAMES), "the stand-ins never leak"


def test_a_builder_failure_prints_its_class_never_its_message(tmp_path: Path) -> None:
    def leaky(**_kwargs: Any) -> dict[str, object]:
        raise ValueError(f"MIP_WAREHOUSE_KEEP_WARM must be one of off (got {SENTINEL_VALUE!r})")

    prediction = _predict(_world(tmp_path), "plan", build=leaky)
    assert prediction.code == "no_rollback_proof" and "(ValueError)" in prediction.message
    assert SENTINEL_VALUE not in prediction.message


# --- the command line -----------------------------------------------------------------------


def _main(world: CoreWorld, *argv: str) -> int:
    return ap.main(list(argv), workspace_factory=lambda: world)


def _argv(phase: str, *extra: str) -> list[str]:
    return [
        "predict", "--phase", phase, "--app-name", rr.APP_NAME, "--scope", rr.SCOPE,
        "--lakebase-instance", rr.LAKEBASE, "--lakebase-database", "mip_app_state", "--agentic-tier", "off",
        "--install-class", "demo", "--app-env", "sandbox", "--catalog", "mip", "--schema", "gold",
        "--source-git-sha", GREEN_SHA, *extra,
    ]  # fmt: skip


def test_the_cli_prints_the_digest_in_a_plan_and_its_step_summary_only_there(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(core_blue, "blue_restore_payload", _with_builder(core_blue.blue_restore_payload))
    world = _world(tmp_path)
    summary = tmp_path / "summary.md"
    assert _main(world, *_argv("plan", "--step-summary", str(summary))) == 0
    out = capsys.readouterr().out
    digest = _predict(world, "plan").frontend_ack
    assert f"\nrestore_unverified_frontend={digest}\n" in f"\n{out}"
    assert f"`restore_unverified_frontend={digest}`" in summary.read_text(encoding="utf-8")

    release_summary = tmp_path / "release.md"
    assert _main(world, *_argv("release", "--step-summary", str(release_summary))) == 2
    err = capsys.readouterr().err
    assert digest is not None and "REFUSED unverified_frontend_unacked" in err and digest in err
    assert not release_summary.exists(), "only a plan writes the step summary"
    assert _main(world, *_argv("release", "--restore-unverified-frontend-ack", str(digest))) == 0
    assert _main(world, *_argv("release", "--no-rollback-ack-given")) == 0
    record = world.record()
    assert record is not None
    printed = capsys.readouterr()
    for secret in (record["deployment_id"], record["restore_source_path"], record["source_code_path"]):
        assert secret not in printed.out + printed.err
    assert world.apps.mutations == [] and world.secrets.puts == []


def test_an_unexpected_failure_prints_its_class_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom(*_a: Any, **_kw: Any) -> None:
        raise KeyError(SENTINEL_VALUE)

    monkeypatch.setattr(ap, "predict", boom)
    assert _main(_world(tmp_path), *_argv("release")) == 2
    err = capsys.readouterr().err
    assert "could not complete (KeyError)" in err and SENTINEL_VALUE not in err


def _with_builder(original: Any) -> Any:
    def call(record: Any, **kwargs: Any) -> Any:
        return original(record, **{**kwargs, "build": builder()})

    return call


# --- env-names: the plans before an adoption ----------------------------------------------


class _Untouchable:
    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"read before the identity was proven: {name}")


def _profile_world(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, values: dict[str, str] | None = None, **profile: str | None
) -> tuple[Any, CoreWorld]:
    """The CI shell at the live checks: .env.local is the client profile; the App is the adopted world's."""

    from tests.fixtures.client_profiles import IDENTITY_IDS, core_tier_profile, env_file_text
    from tests.fixtures.live_workspace import LiveWorkspace
    from tools.databricks import app_deploy_payload

    values = values or core_tier_profile(**profile)
    tmp_path.mkdir(parents=True, exist_ok=True)
    env_local = tmp_path / "profile.env.local"
    env_local.write_text(env_file_text(values), encoding="utf-8")
    monkeypatch.setattr(app_deploy_payload, "ENV_LOCAL", env_local)
    monkeypatch.setattr(app_deploy_payload, "build_payload", builder())
    for name in ("MIP_LAKEBASE_INSTANCE", "LAKEBASE_INSTANCE_NAME"):
        monkeypatch.delenv(name, raising=False)
    world = _world(tmp_path / "world")
    world.secrets.values.clear()  # before an adoption: no record
    live = LiveWorkspace(values, identity_ids=IDENTITY_IDS)
    return live, world


def _workspace(live: Any, apps: Any) -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(
        config=live.config, get_workspace_id=live.get_workspace_id, metastores=live.metastores,
        current_user=live.current_user, apps=apps,
    )  # fmt: skip


def test_env_names_measures_parity_once_the_identity_is_the_profiles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    live, world = _profile_world(tmp_path, monkeypatch)
    summary = tmp_path / "summary.md"
    assert ap.main(["env-names", "--step-summary", str(summary)], workspace_factory=lambda: _workspace(live, world.apps)) == 0
    out = capsys.readouterr().out
    assert "env-name parity holds" in out and "no env-name difference" in summary.read_text(encoding="utf-8")
    assert live.calls[:3] == ["GET scim/Me (org id)", "GET current-metastore-assignment", "GET scim/Me"]

    world.add_deployment("deployment-newer", source=rr.LEGACY_FOLDER, env_names=sorted({*NAMES, "SALESFORCE_PASSWORD"}))
    assert ap.main(["env-names"], workspace_factory=lambda: _workspace(live, world.apps)) == 2
    err = capsys.readouterr().err
    assert "DIFFERS" in err and "lacks SALESFORCE_PASSWORD" in err and "do not start step 7" in err
    for secret in ("deployment-newer", rr.LEGACY_FOLDER, SENTINEL_VALUE):
        assert secret not in err
    assert world.apps.mutations == []


def test_env_names_reads_nothing_more_on_a_workspace_or_principal_not_the_profiles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from tests.fixtures.client_profiles import IDENTITY_IDS, core_tier_profile
    from tests.fixtures.live_workspace import LiveWorkspace

    _live, _world_unused = _profile_world(tmp_path, monkeypatch)
    overrides: tuple[dict[str, Any], ...] = ({"principal": "someone-else"}, {"workspace_id": "999"}, {"host": "https://other.example"})
    for override in overrides:
        stranger = _workspace(LiveWorkspace(core_tier_profile(), identity_ids=IDENTITY_IDS, **override), _Untouchable())
        assert ap.main(["env-names"], workspace_factory=lambda: stranger) == 2  # noqa: B023 - called at once
        assert "not measured: the workspace or the deployer is not the profile's" in capsys.readouterr().err


def test_env_names_needs_a_core_profile_that_resolves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def never() -> Any:
        raise AssertionError("no workspace client without a resolved core profile")

    from tests.fixtures.client_profiles import DEMO_PROFILE

    _profile_world(tmp_path / "agentic", monkeypatch, values=dict(DEMO_PROFILE))
    assert ap.main(["env-names"], workspace_factory=never) == 0
    assert "agentic tier's blue is not rebuilt" in capsys.readouterr().out
    _profile_world(tmp_path / "broken", monkeypatch, MIP_APP_NAME=None)
    assert ap.main(["env-names"], workspace_factory=never) == 2
    assert "the client profile refuses" in capsys.readouterr().err


def test_a_missing_frontend_ack_names_both_routes_it_is_really_handed_in_by() -> None:
    from tools.databricks.operation_ack import REMEDY, AckRefused, require_ack

    with pytest.raises(AckRefused) as refused:
        require_ack(ap.PURPOSE, {"app_name": rr.APP_NAME}, None)
    assert "export MIP_ACK_RESTORE_UNVERIFIED_FRONTEND=<its sha256> for this run only" in str(refused.value)
    assert "the dispatch input operation_acks restore_unverified_frontend=<its sha256>" in REMEDY[ap.PURPOSE]
    assert "operation_acks" not in REMEDY["no_rollback_for"], "no_rollback_for has no carrier"

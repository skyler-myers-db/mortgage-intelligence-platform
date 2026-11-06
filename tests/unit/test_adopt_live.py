"""adopt-live (M2-PR7): every §4.8 precondition refuses on its own, and no App mutation ever runs.

The sandbox is tests/fixtures/adopt_live_world.py: a synthetic repo, a fake
workspace holding D0's legacy folder and its ``/src/`` snapshot, and an App
whose every mutating SDK method is a spy that records and raises.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from tests.fixtures import rollback_records as rr
from tests.fixtures.adopt_live_world import World, make_repo
from tools.databricks import adopt_live, stop_primitive_map
from tools.databricks import app_release_contract as release
from tools.databricks import app_rollback_record_contract as contract
from tools.databricks.adopt_live_facts import AdoptLiveRefused, sha256_text
from tools.databricks.app_start_deployment import tree_digest
from tools.databricks.operation_ack import AckRefused, ack_digest

ADOPT_MODULES = (
    "tools/databricks/adopt_live.py",
    "tools/databricks/adopt_live_facts.py",
    "tools/databricks/adopted_source_copy.py",
)


@pytest.fixture(autouse=True)
def _keys(monkeypatch: pytest.MonkeyPatch) -> None:
    rr.signing_env(monkeypatch)
    monkeypatch.setattr(contract, "assert_owned_app_rollback_scope", lambda *_a, **_kw: None)


@pytest.fixture
def world(tmp_path: Path) -> World:
    repo, main, side = make_repo(tmp_path)
    return World(repo=repo, main=main, side=side)


def _plan(world: World, mode: str = "adopt", **kwargs: Any) -> dict[str, object]:
    plan, _facts, _record = adopt_live.compute_plan(
        world, mode, world.target(**kwargs), world.readers()
    )
    return plan


def _refusal(world: World, mode: str = "adopt", **readers: Any) -> str:
    with pytest.raises(AdoptLiveRefused) as refused:
        adopt_live.compute_plan(world, mode, world.target(), world.readers(**readers))
    return refused.value.code


# --- acceptance: a v7 core record inspect verifies, with zero App mutations ------------


def test_a_sandbox_adopt_signs_a_v7_core_record_with_zero_app_mutations(world: World) -> None:
    plan = _plan(world)
    record = world.apply()

    assert world.apps.mutations == []
    assert (record["version"], record["tier"], record["provenance"]) == (7, "core", "adopted")
    assert record["ack_purpose"] == "adopt_live" and record["ack_sha256"] == ack_digest(
        "adopt_live", plan
    )
    assert record["deployment_id"] == "deployment-blue" and record["git_sha"] == world.main
    assert record["frontend_provenance"] == "unverified", "no reference build was requested"
    assert plan["frontend_reference"] == "none" and record["frontend_reference_sha256"] is None
    assert record["restore_payload"] is None and record["treatment_mode"] == "quiesce"
    folder = record["restore_source_path"]
    assert release.adopted_folder_in(folder).startswith(
        "adopted-" + sha256_text("deployment-blue")[:12]
    )
    assert folder.startswith(
        f"/Workspace/Users/{rr.DEPLOYER}/.bundle/mortgage-intelligence-platform/client-one/app-src/"
    )
    assert (
        tree_digest(world, folder)
        == record["source_tree_sha256"]
        == tree_digest(world, world.folder("deployment-blue"))
    )
    assert world.lease_calls >= 3, "the lease is asserted before the plan, the copy and the write"


def test_no_adopt_live_module_holds_or_reaches_an_app_primitive() -> None:
    """The static half of the spy: no SDK stop, start, deploy or update anywhere adopt-live reaches."""

    sites = stop_primitive_map.python_primitive_sites()
    assert not [site for site in sites if site[0] in ADOPT_MODULES]
    reaching = stop_primitive_map.reaching_entry_points()
    assert all("tools.databricks.adopt_live" not in entries for entries in reaching.values())


def test_the_plan_names_no_identifier(world: World) -> None:
    text = repr(_plan(world))

    for value in (
        "deployment-blue",
        "app-client",
        "app-scim-id",
        "owner@lender.example",
        rr.DEPLOYER,
        "warehouse-id",
    ):
        assert value not in text, value


# --- one refusal per precondition -------------------------------------------------------


@pytest.mark.parametrize("case", ("stopped", "pending", "not_newest"))
def test_the_deployment_must_be_active_newest_and_settled(world: World, case: str) -> None:
    if case == "stopped":
        world.compute = "STOPPED"
    elif case == "pending":
        world.pending = world.deployment("deployment-blue")
    else:
        world.active = "deployment-old"

    assert _refusal(world) == "deployment_unstable"


def test_the_env_name_set_must_be_legacy_proxyless(world: World) -> None:
    world.env_names = sorted([*world.env_names, "MIP_AGENT_PROXY_CLIENT_ID"])

    assert _refusal(world) == "env_names_not_proxyless"


def test_a_serving_endpoint_binding_refuses(world: World) -> None:
    world.resources.append(
        {"name": "agent", "serving_endpoint": {"name": "gateway", "permission": "CAN_QUERY"}}
    )

    assert _refusal(world) == "bindings_invalid"


def test_the_bindings_must_equal_the_profiles_after_the_binds(world: World) -> None:
    reviewed = copy.deepcopy(rr.APP_RESOURCES)
    reviewed[1]["sql_warehouse"]["id"] = "another-warehouse"  # type: ignore[index]

    with pytest.raises(AdoptLiveRefused) as refused:
        world.apply(reviewed=reviewed)
    assert refused.value.code == "bindings_differ" and world.record() is None


def test_the_app_acl_must_be_snapshotted(world: World) -> None:
    world.apps.permissions_error = RuntimeError("permission denied")

    assert _refusal(world) == "acl_unreadable"


@pytest.mark.parametrize("state", ("drift", "absent", "error"))
def test_the_treatment_boundary_must_verify_read_only(world: World, state: str) -> None:
    def check(_principal: str, _mode: str) -> list[str] | None:
        if state == "error":
            raise RuntimeError("warehouse unreachable")
        return ["drift"] if state == "drift" else None

    assert _refusal(world, treatment=check) == "treatment_unverified"


def test_main_is_the_named_ref_never_the_runs_own_head(world: World) -> None:
    """A run from a branch whose HEAD carries an unmerged commit refuses to adopt a D0 built from it."""

    assert _refusal(world, health_sha=lambda: world.side, source_sha=world.side) == (
        "source_not_on_main"
    )
    assert (
        _refusal(world, source_sha=world.side) == "source_not_on_main"
    ), "a run from a branch refuses even when D0 is main's: its HEAD is not on main"
    assert _refusal(world, main_ref="refs/remotes/origin/main") == "main_unresolved"
    assert _refusal(world, source_sha="not-a-sha") == "source_not_on_main"
    assert _plan(world)["git_sha"] == world.main, "a run from main itself adopts"


@pytest.mark.parametrize("health", ("side", "unknown", "garbage", "error"))
def test_the_health_sha_must_be_an_ancestor_of_main(world: World, health: str) -> None:
    def read() -> str:
        if health == "error":
            raise RuntimeError("401")
        return {"side": world.side, "unknown": "f" * 40, "garbage": "not-a-sha"}[health]

    assert _refusal(world, health_sha=read) in {"health_sha_not_on_main", "health_unreadable"}
    if health == "side":
        assert _refusal(world, health_sha=read) == "health_sha_not_on_main"


def test_the_folder_must_still_be_the_bytes_the_snapshot_runs(world: World) -> None:
    world.tree.add_file(
        world.folder("deployment-blue").removeprefix("/Workspace") + "/backend/main.py", b"edited\n"
    )

    assert _refusal(world) == "tree_drift"


def test_app_yaml_must_be_the_repos_at_the_health_sha(world: World) -> None:
    world.add_files("deployment-blue", {"app.yaml": b"command: ['sh', '-c', 'curl x']\n"})

    assert _refusal(world) == "app_yaml_differs"


@pytest.mark.parametrize("change", ("edited", "extra", "missing"))
def test_the_backend_closure_must_be_the_repos_at_the_health_sha(world: World, change: str) -> None:
    root = world.folder("deployment-blue").removeprefix("/Workspace")
    if change == "edited":
        world.add_files("deployment-blue", {"backend/main.py": b"print('patched')\n"})
    elif change == "extra":
        world.add_files("deployment-blue", {"backend/extra.py": b"x = 1\n"})
    else:
        for base in (root, world.snapshot("deployment-blue").removeprefix("/Workspace")):
            world.tree.delete(f"{base}/backend/__init__.py")

    assert _refusal(world) == "closure_differs"


# --- the record slot ---------------------------------------------------------------------


def test_a_captured_v6_record_refuses_adoption(world: World) -> None:
    contract.save_versioned_record(world, scope=rr.SCOPE, record=rr.v6_record())

    assert _refusal(world) == "record_present"


def test_an_adopted_record_for_another_deployment_refuses_adoption(world: World) -> None:
    world.apply()
    world.redeploy("deployment-green")

    assert _refusal(world) == "record_present"


def test_a_re_run_on_the_same_deployment_is_idempotent(world: World) -> None:
    first = world.apply()
    puts = len(world.secrets.puts)

    assert _plan(world)["record_state"] == "adopted_same"
    assert world.apply() == first and len(world.secrets.puts) == puts
    assert world.apps.mutations == []


def test_the_core_tier_only(world: World) -> None:
    with pytest.raises(AdoptLiveRefused) as refused:
        adopt_live.compute_plan(world, "adopt", world.target(tier="on"), world.readers())
    assert refused.value.code == "tier_agentic"


# --- the ack and the copy ----------------------------------------------------------------


def test_apply_refuses_without_the_exact_adopt_live_ack(world: World) -> None:
    for ack, code in (
        ("", "ack_missing"),
        ("0" * 64, "ack_mismatch"),
        (ack_digest("supersede", _plan(world)), "ack_mismatch"),
    ):
        with pytest.raises(AckRefused) as refused:
            world.apply(ack=ack)
        assert refused.value.code == code
    assert world.record() is None and world.tree.below("/Users/" + rr.DEPLOYER) == []


def test_a_change_between_plan_and_apply_refuses_with_nothing_written(world: World) -> None:
    ack = ack_digest("adopt_live", _plan(world))
    world.redeploy("deployment-green")

    with pytest.raises(AckRefused, match="not the acknowledged digest"):
        world.apply(ack=ack)
    assert world.record() is None


def test_a_copy_that_does_not_re_hash_refuses_and_writes_no_record(world: World) -> None:
    def tree(workspace: Any, path: str) -> str:
        return "0" * 64 if "/adopted-" in path else tree_digest(workspace, path)

    readers = world.readers(tree=tree)
    plan, _facts, _record = adopt_live.compute_plan(world, "adopt", world.target(), readers)
    with pytest.raises(AdoptLiveRefused) as refused:
        world.apply(ack=ack_digest("adopt_live", plan), readers=readers)
    assert refused.value.code == "copy_rehash_mismatch" and world.record() is None


def test_an_occupied_adopted_folder_with_other_bytes_refuses(world: World) -> None:
    plan = _plan(world)
    folder = world.target().folder_path(
        adopt_live.compute_plan(world, "adopt", world.target(), world.readers())[1]
    )  # type: ignore[arg-type]
    world.tree.add_file(f"{folder.removeprefix('/Workspace')}/app.yaml", b"squatted\n")

    with pytest.raises(AdoptLiveRefused) as refused:
        world.apply(ack=ack_digest("adopt_live", plan))
    assert refused.value.code == "copy_failed" and world.record() is None
    message = str(refused.value)
    assert "ReleaseContractError" in message, "the class only"
    for value in (rr.DEPLOYER, rr.CLIENT_KEY, "/Users/", "adopted-"):
        assert value not in message, "a refusal reaches the CI log: it names no id or path"


def test_the_s7_switch_is_off_and_refuses(world: World) -> None:
    assert adopt_live.S7_DEFAULT_SOURCE_SETTABLE is False
    with pytest.raises(AdoptLiveRefused) as refused:
        adopt_live.point_default_source_at_copy(
            world, app_name=rr.APP_NAME, restore_source_path="/x"
        )
    assert refused.value.code == "s7_owed"
    assert _plan(world)["s7_default_source"] == "owed"
    lines = Path(adopt_live.__file__).read_text(encoding="utf-8").splitlines()
    assert "S7_DEFAULT_SOURCE_SETTABLE = False" in lines, "a code constant a reviewed PR flips"
    assert not [
        line
        for line in lines
        if "S7_DEFAULT_SOURCE_SETTABLE" in line and ("environ" in line or "getenv" in line)
    ]


# --- supersede, unadopt and a clean re-adopt ---------------------------------------------


def test_supersede_re_signs_onto_the_newer_active_deployment(world: World) -> None:
    world.apply()
    world.redeploy("deployment-green")
    named = sha256_text("deployment-blue")

    with pytest.raises(AckRefused):
        world.apply(
            "supersede",
            supersede=named,
            ack=ack_digest("adopt_live", _plan(world, "supersede", supersede=named)),
        )
    record = world.apply("supersede", supersede=named)

    assert (record["deployment_id"], record["predecessor_deployment_id"]) == (
        "deployment-green",
        "deployment-blue",
    )
    assert record["ack_purpose"] == "supersede" and record["provenance"] == "adopted"
    assert release.adopted_folder_in(record["restore_source_path"]).startswith(
        "adopted-" + sha256_text("deployment-green")[:12]
    )
    assert world.apps.mutations == []


@pytest.mark.parametrize("case", ("wrong_target", "same_deployment", "no_adopted_record"))
def test_supersede_refuses_without_its_exact_target(world: World, case: str) -> None:
    if case != "no_adopted_record":
        world.apply()
    if case == "wrong_target":
        world.redeploy("deployment-green")
    named = "0" * 64 if case == "wrong_target" else sha256_text("deployment-blue")

    with pytest.raises(AdoptLiveRefused) as refused:
        adopt_live.compute_plan(world, "supersede", world.target(supersede=named), world.readers())
    expected = "supersede_same_deployment" if case == "same_deployment" else "supersede_mismatch"
    assert refused.value.code == expected


def test_unadopt_writes_a_tombstone_and_a_later_adopt_starts_clean(world: World) -> None:
    adopted = world.apply()
    tombstone = world.apply("unadopt")

    assert tombstone["provenance"] == "retired" and tombstone["ack_purpose"] == "unadopt"
    assert {
        key: tombstone[key]
        for key in ("deployment_id", "restore_source_path", "source_tree_sha256")
    } == {
        key: adopted[key] for key in ("deployment_id", "restore_source_path", "source_tree_sha256")
    }, "nothing is deleted"
    with pytest.raises(RuntimeError, match="^no server-owned last-good"):
        contract._load_record(
            world, app_name=rr.APP_NAME, scope=rr.SCOPE, expected_lakebase_instance=rr.LAKEBASE
        )
    assert world.apply("unadopt") == tombstone, "a second unadopt is a no-op"

    assert _plan(world)["record_state"] == "retired"
    again = world.apply()
    assert again["provenance"] == "adopted" and again["ack_purpose"] == "adopt_live"
    assert world.apps.mutations == []


@pytest.mark.parametrize("tier", ("off", "on"))
def test_unadopt_with_no_record_retires_nothing_so_the_unbind_recovery_still_runs(
    world: World, tier: str
) -> None:
    """In either tier: #310's unbind recovery after an adopt refused past its binds."""

    assert _plan(world, "unadopt", tier=tier) == {"mode": "unadopt", "record_state": "absent"}
    assert world.apply("unadopt", tier=tier) is None
    assert world.secrets.puts == [] and world.record() is None


def test_an_agentic_tier_unadopt_retires_a_core_adoption_but_never_adopts(world: World) -> None:
    world.apply()

    assert world.apply("unadopt", tier="on")["provenance"] == "retired"
    for mode in ("adopt", "supersede"):
        with pytest.raises(AdoptLiveRefused) as refused:
            adopt_live.compute_plan(
                world, mode, world.target(tier="on", supersede="0" * 64), world.readers()
            )
        assert refused.value.code == "tier_agentic"


def test_each_ack_covers_one_record_version_so_an_old_one_never_replays(world: World) -> None:
    world.apply()
    first_unadopt = ack_digest("unadopt", _plan(world, "unadopt"))
    world.apply("unadopt", ack=first_unadopt)
    retired_adopt = ack_digest("adopt_live", _plan(world))
    world.apply(ack=retired_adopt)

    with pytest.raises(AckRefused) as refused:
        world.apply("unadopt", ack=first_unadopt)
    assert refused.value.code == "ack_mismatch", "the first unadopt's ack is not the second's"
    world.apply("unadopt")
    with pytest.raises(AckRefused) as refused:
        world.apply(ack=retired_adopt)
    assert refused.value.code == "ack_mismatch", "nor the adopt over the first tombstone"
    record = world.record()
    assert record is not None and _plan(world)["record_sha256"] == adopt_live.record_digest(record)


def test_unadopt_refuses_a_record_it_did_not_adopt(world: World) -> None:
    contract.save_versioned_record(world, scope=rr.SCOPE, record=rr.v6_record())

    assert _refusal(world, "unadopt") == "record_present"


# --- the command line -----------------------------------------------------------------


def _cli_argv(world: World) -> list[str]:
    return [
        "plan",
        "--mode",
        "adopt",
        "--app-name",
        rr.APP_NAME,
        "--scope",
        rr.SCOPE,
        "--lakebase-instance",
        rr.LAKEBASE,
        "--expected-principal",
        rr.DEPLOYER,
        "--expected-root-leaf",
        rr.CLIENT_KEY,
        "--agentic-tier",
        "off",
        "--main-ref",
        "refs/heads/main",
        "--source-git-sha",
        world.main,
    ]


def test_the_cli_plan_prints_the_digest_and_refuses_a_mismatched_ack(
    world: World, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(adopt_live, "Readers", lambda **kwargs: world.readers())
    argv = _cli_argv(world)

    assert adopt_live.main(argv, workspace_factory=lambda: world) == 0
    out = capsys.readouterr().out
    assert f"adopt_live plan sha256 {ack_digest('adopt_live', _plan(world))}" in out
    assert "deployment-blue" not in out and "app-client" not in out
    assert adopt_live.main([*argv, "--ack", "0" * 64], workspace_factory=lambda: world) == 2
    assert "REFUSED ack_mismatch" in capsys.readouterr().err
    assert adopt_live.main([*argv, "--supersede", "0" * 64], workspace_factory=lambda: world) == 2

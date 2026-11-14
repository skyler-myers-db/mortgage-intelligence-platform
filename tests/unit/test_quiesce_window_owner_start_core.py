"""The owner-start re-pin over a v7 core blue: client #1's shape (wave 7c).

The cutover's run 1 opens its window over an adopted v7 core record. If that
run dies after the stop and the owner starts the App by hand, the next
release re-pins the owner's start with ``app_core_rollback repin
--from-owner-start``: the same classification and exact ``owner_start_repin``
ack as the v5/v6 path (tests/unit/test_quiesce_window_owner_start.py), then
the core record's own proofs (``core_blue_repin.repin_core_start``: the
record's signed tree, its env-name set, the core resource proof and health)
and the next v7 version, ``predecessor_deployment_id`` blue.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.fixtures import rollback_records as rr
from tests.fixtures.core_blue_world import CoreWorld
from tests.unit.test_quiesce_window_v7_start import (  # noqa: F401 - the autouse keys and the world
    BLUE,
    _keys,
    _open,
    _start_and_await,
    world,
)
from tools.databricks import app_core_rollback as cli
from tools.databricks import app_start_deployment as starts
from tools.databricks import core_blue
from tools.databricks import quiesce_window_journal as journal
from tools.databricks import quiesce_window_recovery as recovery

ARGV = [
    "repin", "--from-owner-start", "--app-name", rr.APP_NAME, "--scope", rr.SCOPE,
    "--lakebase-instance", rr.LAKEBASE, "--agentic-tier", "off", "--base-url", "https://mip.example",
    "--deployment-lease-id", rr.LEASE_ID, "--deployment-source-git-sha", rr.GIT_SHA,
]  # fmt: skip


@pytest.fixture(autouse=True)
def _cli(_keys: None, world: CoreWorld, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811 - the keys first
    monkeypatch.setenv("MIP_BEARER_TOKEN", "token")
    monkeypatch.delenv(recovery.ACK_ENV, raising=False)
    monkeypatch.setattr(core_blue, "core_health", lambda *_a, **_kw: world.serves)
    monkeypatch.setattr(cli, "_lease", lambda *_a, **_kw: world.lease)


def _owner_start(world: CoreWorld) -> str:  # noqa: F811
    """The owner's raw ``apps start``, then time passes until its deployment is terminal."""

    before = frozenset(world.deployments)
    world.apps.start(rr.APP_NAME)
    created = starts.find_start_deployment(world, app_name=rr.APP_NAME, baseline=before)
    starts.wait_for_terminal(world, app_name=rr.APP_NAME, deployment_id=created)
    return created


def _classify(world: CoreWorld) -> recovery.Recovery:  # noqa: F811
    return recovery.classify(world, app_name=rr.APP_NAME, current=journal.read(world, app_name=rr.APP_NAME))


def _run(world: CoreWorld, *extra: str) -> int:  # noqa: F811
    return cli.main([*ARGV, *extra], workspace_factory=lambda: world)


def _frozen(world: CoreWorld) -> tuple[Any, ...]:  # noqa: F811
    stored = world.tree.nodes[journal.journal_path(rr.APP_NAME)].data
    return (stored, world.record(), list(world.apps.mutations), world.compute, world.active)


def test_the_core_tool_re_pins_an_owner_start_only_with_its_exact_ack(
    world: CoreWorld, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]  # noqa: F811
) -> None:
    before = world.record()
    assert before is not None
    opened = _open(world)
    assert BLUE in opened["start_baseline_ids"]
    started = _owner_start(world)  # the runner died after the stop; the owner broke glass
    found = _classify(world)
    assert (found.kind, found.owner_start, found.creator_class) == ("owner_start", "candidate", "unknown")
    assert found.body is not None and found.body["start_deployment_id"] == started
    frozen = _frozen(world)

    assert _run(world) == 2
    refused = capsys.readouterr().err
    assert f"REFUSED owner_start_unacked: ack_missing: this window's owner_start_repin plan is {found.sha256}" in refused
    assert started not in refused and BLUE not in refused, "codes and the digest only"
    monkeypatch.setenv(recovery.ACK_ENV, "0" * 64)
    assert _run(world) == 2 and "owner_start_unacked: ack_mismatch" in capsys.readouterr().err
    assert _frozen(world) == frozen, "a refusal writes nothing and starts, stops and deploys nothing"

    monkeypatch.setenv(recovery.ACK_ENV, found.sha256)
    for flags in (("--to", started), ("--from-deadman",), ("--expected-tree-sha256", "a" * 64)):
        assert _run(world, *flags) == 2 and "REFUSED usage" in capsys.readouterr().err
    assert _frozen(world) == frozen
    assert _run(world) == 0

    record = world.record()
    assert (record["version"], record["tier"], record["provenance"]) == (7, "core", "adopted")
    assert record["deployment_id"] == started and record["predecessor_deployment_id"] == BLUE
    assert (record["ack_purpose"], record["ack_sha256"]) == (before["ack_purpose"], before["ack_sha256"])
    closed = journal.read(world, app_name=rr.APP_NAME)
    assert (closed["state"], closed["closed_by"], closed["repinned_deployment_id"]) == ("closed", "owner_start_repin", started)
    assert world.apps.mutations == ["stop", "start"], "the window's stop and the owner's start; nothing else"
    assert world.compute == "ACTIVE" and world.active == started and world.health_calls[-1] == started
    assert _run(world) == 2 and "REFUSED owner_start_no_window" in capsys.readouterr().err


def test_a_core_window_whose_own_start_was_refused_is_closed_by_the_owner_start(
    world: CoreWorld, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    _open(world)
    world.start_final = "FAILED"
    with pytest.raises(RuntimeError, match="start_not_succeeded"):
        _start_and_await(world)
    refused = journal.read(world, app_name=rr.APP_NAME)
    assert refused["state"] == "window_refused" and refused["start_deployment_id"] and world.compute == "STOPPED"
    world.start_final = "SUCCEEDED"
    started = _owner_start(world)
    found = _classify(world)
    assert found.owner_start == "candidate" and found.body is not None
    assert found.body["known_start_ids"] == [refused["start_deployment_id"]], "the run's own refused start is known"

    monkeypatch.setenv(recovery.ACK_ENV, found.sha256)
    assert _run(world) == 0
    closed = journal.read(world, app_name=rr.APP_NAME)
    assert closed["closed_by"] == "owner_start_repin" and closed["repinned_deployment_id"] == started
    assert closed["start_deployment_id"] == refused["start_deployment_id"], "the refused start keeps its place"
    assert world.record()["deployment_id"] == started


def _tampered_snapshot(world: CoreWorld, started: str) -> None:  # noqa: F811
    world.tree.add_file(f"{world.snapshot(started).removeprefix('/Workspace')}/backend/late.py", b"x\n")


def _other_commit(world: CoreWorld, _started: str) -> None:  # noqa: F811
    world.main = "f" * 40


def _other_bindings(world: CoreWorld, _started: str) -> None:  # noqa: F811
    import copy

    other = copy.deepcopy(rr.APP_RESOURCES)
    other[1]["sql_warehouse"]["id"] = "warehouse-other"  # type: ignore[index]
    world.resources = other


def _changed_folder(world: CoreWorld, _started: str) -> None:  # noqa: F811
    world.tree.add_file(f"{world.folder(BLUE).removeprefix('/Workspace')}/backend/late.py", b"x\n")


@pytest.mark.parametrize(
    ("break_it", "code"),
    (
        (_tampered_snapshot, "start_tree_digest"),
        (_other_commit, "health_mismatch"),
        (_other_bindings, "resource_drift"),
        (_changed_folder, "owner_start_unproven"),
    ),
    ids=("snapshot-not-the-signed-tree", "health", "bindings", "restore-folder-changed"),
)
def test_a_core_owner_start_the_proofs_do_not_bear_out_is_never_re_pinned(
    world: CoreWorld, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], break_it: Any, code: str  # noqa: F811
) -> None:
    _open(world)
    started = _owner_start(world)
    break_it(world, started)
    found = _classify(world)
    assert found.owner_start == "candidate", "the classification is no proof: the exact ack was even given"
    monkeypatch.setenv(recovery.ACK_ENV, found.sha256)
    frozen = _frozen(world)

    assert _run(world) == 2
    assert f"REFUSED {code}" in capsys.readouterr().err
    assert _frozen(world) == frozen and world.record()["deployment_id"] == BLUE


def test_a_core_start_with_another_env_name_set_does_not_have_the_shape_of_a_start(
    world: CoreWorld, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]  # noqa: F811
) -> None:
    _open(world)
    started = _owner_start(world)
    world.env_sets[started] = [*world.env_sets[started], "MIP_EXTRA"]
    assert _classify(world).owner_start == "owner_start_not_a_start"
    monkeypatch.setenv(recovery.ACK_ENV, "0" * 64)
    assert _run(world) == 2 and "REFUSED owner_start_not_a_start" in capsys.readouterr().err
    assert world.record()["deployment_id"] == BLUE

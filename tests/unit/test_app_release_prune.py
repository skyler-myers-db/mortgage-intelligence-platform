"""``app_release_prune``: delete only unreferenced release folders, or nothing.

Every rule in the tool's docstring has a test here: what is kept (current,
rollback record and App default source in any spelling, KEEP_PREVIOUS newest
previous, a folder holding a snapshot), what is never touched (files/, names
that are not release ids, plain files), the per-delete re-checks, the
MAX_DELETIONS cap, the time budget, reconciliation of an ambiguous delete,
and each uncertainty that skips the run and deletes nothing.
tests/fixtures/fake_workspace.py is the workspace.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from databricks.sdk.errors import InternalError, PermissionDenied
from databricks.sdk.service.workspace import ObjectType

from tests.fixtures.fake_workspace import FakeWorkspaceClient, deployment
from tools.databricks import app_release_contract as contract
from tools.databricks import app_release_prune as prune

PRINCIPAL = "deployer@example.com"
SHA = "0123456789abcdef0123456789abcdef01234567"
CURRENT = f"{SHA[:12]}-{'c' * 12}"
APP_ID = "app-id"
FILES_ROOT = contract.files_root(principal=PRINCIPAL, target="dev")
API_ROOT = contract.api_path(contract.release_root(principal=PRINCIPAL, target="dev"))


def rid(n: int) -> str:
    return f"{n:012x}-{n:012x}"


def path(release_id: str) -> str:
    return contract.release_path(principal=PRINCIPAL, target="dev", release_id=release_id)


def api(release_id: str) -> str:
    return contract.api_path(path(release_id))


def _time(n: int) -> str:
    return f"2026-10-{n:02d}T10:00:00Z"


def _world(old: int = 5) -> FakeWorkspaceClient:
    """Releases rid(1)..rid(old) deployed in order, then CURRENT (active, newest)."""
    client = FakeWorkspaceClient()
    tree = client.workspace
    tree.add_file(f"{contract.api_path(FILES_ROOT)}/app.yaml", b"bundle sync\n")
    deployments = []
    for n in range(1, old + 1):
        tree.add_file(f"{api(rid(n))}/app.yaml", b"x\n")
        deployments.append(
            deployment(f"dep-{n}", path(rid(n)), create_time=_time(n), snapshot=f"/Workspace/Users/sp/src/{n}")
        )
    tree.add_file(f"{api(CURRENT)}/app.yaml", b"x\n")
    active = deployment("dep-current", path(CURRENT), create_time=_time(old + 1),
                        snapshot="/Workspace/Users/sp/src/current")
    client.apps.app = SimpleNamespace(
        id=APP_ID, name="mip-app", active_deployment=active, pending_deployment=None
    )
    client.apps.deployments = [*deployments, active]
    return client


def _target(rollback: str = "") -> prune.Target:
    return prune.check_args(
        app_name="mip-app", expected_app_id=APP_ID, current_release=CURRENT,
        rollback_deployment_id=rollback, source_git_sha=SHA, files_root=FILES_ROOT,
        principal=PRINCIPAL, target="dev", root_leaf=None,
    )


def _held() -> None:
    return None


def _run(client: FakeWorkspaceClient, rollback: str = "", lease: Any = _held) -> prune.PruneReport:
    return prune.prune(client, _target(rollback), lease_check=lease)


def _releases(client: FakeWorkspaceClient) -> set[str]:
    return {
        name.removeprefix(f"{API_ROOT}/") for name in client.workspace.nodes
        if name.startswith(f"{API_ROOT}/") and name.count("/") == API_ROOT.count("/") + 1
    }


def test_keeps_the_current_and_two_newest_previous_and_deletes_the_rest() -> None:
    client = _world(old=5)

    report = _run(client)

    assert report.skipped == ""
    assert report.kept == {CURRENT: "current", rid(5): "previous", rid(4): "previous"}
    assert report.deleted == [rid(1), rid(2), rid(3)]
    assert _releases(client) == {CURRENT, rid(4), rid(5)}
    assert client.workspace.ops("delete") == [api(rid(n)) for n in (1, 2, 3)]
    assert client.workspace.exists(f"{contract.api_path(FILES_ROOT)}/app.yaml")


def test_previous_counts_distinct_folders_newest_first() -> None:
    client = _world(old=3)
    # A redeploy of release 1 after release 3: release 1 is now the newest previous.
    client.apps.deployments.insert(
        0, deployment("dep-redeploy", path(rid(1)), create_time="2026-10-03T11:00:00Z")
    )

    report = _run(client)

    assert report.kept == {CURRENT: "current", rid(1): "previous", rid(3): "previous"}
    assert report.deleted == [rid(2)]


def test_the_rollback_record_folder_is_kept_beyond_the_previous_two() -> None:
    client = _world(old=5)

    report = _run(client, rollback="dep-1")

    assert report.kept[rid(1)] == "rollback record"
    assert report.deleted == [rid(2), rid(3)]


def test_a_rollback_record_on_a_snapshot_keeps_no_extra_folder() -> None:
    client = _world(old=3)
    client.apps.deployments.append(
        deployment("dep-restore", "/Workspace/Users/sp/src/1", create_time=_time(1))
    )

    report = _run(client, rollback="dep-restore")

    assert report.deleted == [rid(1)]


LETTERED = rid(0xABC)  # hex letters, so a case change of the whole path changes the id segment
SPELLINGS = {
    "canonical": path(LETTERED),
    "trailing_slash": f"{path(LETTERED)}/",
    "no_workspace_prefix": path(LETTERED).removeprefix("/Workspace"),
    "principal_case": path(LETTERED).replace(PRINCIPAL, PRINCIPAL.upper()),
    "upper": path(LETTERED).upper(),
}


@pytest.mark.parametrize("spelling", sorted(SPELLINGS))
@pytest.mark.parametrize("where", ["rollback", "default"])
def test_a_rollback_or_default_source_keeps_its_folder_in_any_spelling(where: str, spelling: str) -> None:
    client = _world(old=5)
    client.workspace.add_dir(api(LETTERED))  # otherwise unreferenced: it would be deleted
    rollback = ""
    if where == "rollback":
        client.apps.deployments.insert(
            0, deployment("dep-abc", SPELLINGS[spelling], create_time="2026-09-01T10:00:00Z")
        )
        rollback = "dep-abc"
    else:
        client.apps.app.default_source_code_path = SPELLINGS[spelling]

    report = _run(client, rollback=rollback)

    assert report.skipped == ""
    assert report.kept[LETTERED] == ("rollback record" if where == "rollback" else "App default source")
    assert report.deleted == [rid(1), rid(2), rid(3)]
    assert client.workspace.exists(api(LETTERED))


def test_a_folder_holding_a_deployment_snapshot_is_never_deleted() -> None:
    client = _world(old=4)
    client.apps.deployments[0].deployment_artifacts.source_code_path = f"{path(rid(1))}/src/x"

    report = _run(client)

    assert report.kept[rid(1)] == "holds a deployment snapshot"
    assert report.deleted == [rid(2)]


def test_only_release_id_directories_of_the_release_root_are_candidates() -> None:
    client = _world(old=2)
    tree = client.workspace
    tree.add_dir(f"{API_ROOT}/0123abcd")  # a legacy short-sha folder (owner cleanup)
    tree.add_dir(f"{API_ROOT}/{rid(0xAB9).upper()}")
    tree.add_file(f"{API_ROOT}/{rid(8)}", b"a plain file with a release-id name\n")
    tree.add_file(f"{API_ROOT}/notes.txt", b"x\n")
    tree.add_dir(f"{API_ROOT}/{rid(7)}")  # unreferenced: published, never deployed

    report = _run(client)

    assert report.deleted == [rid(7)]
    assert len(report.ignored) == 4
    for name in ("0123abcd", rid(0xAB9).upper(), rid(8), "notes.txt"):
        assert tree.exists(f"{API_ROOT}/{name}")


def test_at_most_max_deletions_per_run() -> None:
    client = _world(old=2)
    for n in range(100, 125):
        client.workspace.add_dir(api(rid(n)))

    report = _run(client)

    assert prune.MAX_DELETIONS == 20
    assert len(report.deleted) == 20 and len(report.left) == 5
    assert len(client.workspace.ops("delete")) == 20
    assert _run(client).deleted == [rid(n) for n in range(120, 125)]


def test_no_delete_starts_once_the_time_budget_is_spent() -> None:
    client = _world(old=5)
    ticks = iter([0.0, prune.TIME_BUDGET_SECONDS - 1.0, float(prune.TIME_BUDGET_SECONDS)])

    report = prune.prune(client, _target(), lease_check=_held, clock=lambda: next(ticks))

    assert report.deleted == [rid(1)]
    assert report.skipped == f"stopped before {rid(2)}: the 600 s time budget is spent"
    assert report.left == [rid(2), rid(3)]
    assert client.workspace.ops("delete") == [api(rid(1))]


def test_a_missing_release_root_means_nothing_to_prune() -> None:
    client = _world(old=0)
    del client.workspace.nodes[api(CURRENT)]
    del client.workspace.nodes[API_ROOT]

    report = _run(client)

    assert (report.skipped, report.deleted) == ("", [])


def _skip_case(client: FakeWorkspaceClient, case: str) -> tuple[str, Any]:
    rollback, lease = "", _held
    apps = client.apps
    if case == "lease":
        def lease() -> None:
            raise RuntimeError("App deployment lease was released")
    elif case == "app_read":
        apps.fail["get"] = InternalError("boom")
    elif case == "deployments_read":
        apps.fail["list_deployments"] = PermissionDenied("no")
    elif case == "app_id":
        apps.app.id = "another-app"
    elif case == "pending":
        apps.app.pending_deployment = deployment("dep-next", path(rid(1)), create_time=_time(9))
    elif case == "active_not_current":
        apps.app.active_deployment = apps.deployments[-2]
    elif case == "active_without_id":
        apps.app.active_deployment = deployment("", path(CURRENT), create_time=_time(9))
    elif case == "active_not_listed":
        apps.deployments.pop()
    elif case == "no_source":
        apps.deployments[0].source_code_path = None
    elif case == "no_create_time":
        apps.deployments[0].create_time = None
    elif case == "naive_create_time":
        apps.deployments[0].create_time = "2026-10-01T10:00:00"
    elif case == "repeated_id":
        apps.deployments.append(deployment("dep-1", path(rid(1)), create_time=_time(1)))
    elif case == "rollback_not_listed":
        rollback = "dep-gone"
    elif case == "root_unreadable":
        client.workspace.fail_before[("list", API_ROOT)] = PermissionDenied("no")
    return rollback, lease


SKIPS = ["lease", "app_read", "deployments_read", "app_id", "pending", "active_not_current",
         "active_without_id", "active_not_listed", "no_source", "no_create_time",
         "naive_create_time", "repeated_id", "rollback_not_listed", "root_unreadable"]


@pytest.mark.parametrize("case", SKIPS)
def test_any_uncertainty_skips_the_run_and_deletes_nothing(case: str) -> None:
    client = _world(old=5)
    rollback, lease = _skip_case(client, case)
    before = set(client.workspace.nodes)

    report = _run(client, rollback=rollback, lease=lease)

    assert report.skipped and report.deleted == []
    assert client.workspace.ops("delete") == []
    assert set(client.workspace.nodes) == before


@pytest.mark.parametrize(
    "change", ["pending", "lease", "active_moved", "app_id", "folder_is_file", "default_names_next"]
)
def test_each_delete_rechecks_the_lease_the_app_and_the_folder(change: str) -> None:
    client = _world(old=5)
    leases = {"calls": 0}

    def lease() -> None:
        leases["calls"] += 1
        if change == "lease" and leases["calls"] > 2:
            raise RuntimeError("lease expired")

    def after_first_delete(_path: str) -> None:
        if change == "pending":
            client.apps.app.pending_deployment = deployment("dep-x", path(rid(9)), create_time=_time(20))
        elif change == "active_moved":
            client.apps.app.active_deployment = client.apps.deployments[1]
        elif change == "app_id":
            client.apps.app.id = "replaced-app"
        elif change == "folder_is_file":
            client.workspace.nodes[api(rid(2))] = client.workspace.nodes[f"{api(rid(1))}/app.yaml"]
        elif change == "default_names_next":
            client.apps.app.default_source_code_path = f"{path(rid(2))}/"

    client.workspace.hooks["delete"] = after_first_delete

    report = _run(client, lease=lease)

    assert report.deleted == [rid(1)]
    assert report.skipped.startswith(f"stopped before {rid(2)}")
    assert report.left == [rid(2), rid(3)]
    assert client.workspace.ops("delete") == [api(rid(1))]


def test_an_ambiguous_delete_that_landed_is_reconciled() -> None:
    client = _world(old=3)
    client.workspace.fail_after[("delete", api(rid(1)))] = InternalError("timeout")

    assert _run(client).deleted == [rid(1)]


@pytest.mark.parametrize("fault", ["refused", "silently_kept"])
def test_a_delete_that_cannot_be_reconciled_stops_the_run(fault: str) -> None:
    client = _world(old=4)
    if fault == "refused":
        client.workspace.fail_before[("delete", api(rid(1)))] = InternalError("refused")
    else:
        client.workspace.delete = lambda *_args, **_kwargs: None  # type: ignore[method-assign]

    with pytest.raises(prune.Unreconciled):
        _run(client)
    assert client.workspace.exists(api(rid(1))) and client.workspace.exists(api(rid(2)))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_git_sha", SHA[:12]),
        ("current_release", f"{'f' * 12}-{'c' * 12}"),
        ("current_release", "0123abcd"),
        ("files_root", contract.files_root(principal=PRINCIPAL, target="dev", root_leaf="dev-ci")),
        ("app_name", ""),
        ("expected_app_id", " "),
        ("principal", "src"),
    ],
)
def test_bad_arguments_are_refused_before_anything_remote(field: str, value: str) -> None:
    kwargs: dict[str, Any] = {
        "app_name": "mip-app", "expected_app_id": APP_ID, "current_release": CURRENT,
        "rollback_deployment_id": "", "source_git_sha": SHA, "files_root": FILES_ROOT,
        "principal": PRINCIPAL, "target": "dev", "root_leaf": None, field: value,
    }
    if field == "principal":
        kwargs["files_root"] = contract.files_root(principal=value, target="dev")

    with pytest.raises(contract.ReleaseContractError):
        prune.check_args(**kwargs)


def _cli(client: Any, *extra: str, lease: str = "lease-1", sha: str = SHA) -> list[str]:
    return [
        "--app-name", "mip-app", "--expected-app-id", APP_ID, "--current-release", CURRENT,
        "--deployment-lease-id", lease, "--source-git-sha", sha, "--files-root", FILES_ROOT,
        "--expected-principal", PRINCIPAL, "--expected-target", "dev", *extra,
    ]


def test_the_cli_prunes_under_the_lease_and_exits_0(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    client = _world(old=3)
    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(prune.app_deployment_lease, "assert_held",
                        lambda workspace, **kwargs: seen.append(kwargs))

    assert prune.main(_cli(client), client=lambda: client) == 0

    out = capsys.readouterr().out
    assert f"deleted: {rid(1)}" in out and "kept: " in out
    assert seen and all(
        item == {"app_name": "mip-app", "lease_id": "lease-1", "source_git_sha": SHA} for item in seen
    )
    assert len(seen) == 2  # once up front, once before the single delete


def test_the_cli_skips_without_a_lease_or_a_client_and_exits_2_only_on_bad_arguments(
    capsys: pytest.CaptureFixture[str],
) -> None:
    def no_client() -> Any:
        raise ValueError("no auth")

    assert prune.main(_cli(None, lease=""), client=no_client) == 0
    assert "skipped: no deployment lease id" in capsys.readouterr().out
    assert prune.main(_cli(None), client=no_client) == 0
    assert "skipped: no workspace client" in capsys.readouterr().out
    assert prune.main(_cli(None, sha="bad"), client=no_client) == 2
    assert capsys.readouterr().err.startswith("refused: ")


def test_the_cli_exits_1_when_a_delete_cannot_be_reconciled(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    client = _world(old=3)
    client.workspace.fail_before[("delete", api(rid(1)))] = InternalError("refused")
    monkeypatch.setattr(prune.app_deployment_lease, "assert_held", lambda *_a, **_k: None)

    assert prune.main(_cli(client), client=lambda: client) == 1
    assert "stopped: " in capsys.readouterr().err


def test_the_retention_constants_and_the_typed_listing() -> None:
    assert (prune.KEEP_PREVIOUS, prune.MAX_DELETIONS, prune.TIME_BUDGET_SECONDS) == (2, 20, 600)
    assert FakeWorkspaceClient().workspace.get_status("/").object_type is ObjectType.DIRECTORY

"""The dead-man restarts blue only when the runner died inside a restorable window (M0-PR9).

``jobs/window_deadman.py`` runs as the one-time job the window submits. The
fake estate is M0-PR8's (``apps start`` creates a NEW deployment, spike S7's
worst case) and the journal is the real signed one ``open_window`` writes.
At the deadline, with the journal still exactly this window's ``window_open``,
it starts the App's stopped blue and records ``deadman_restarted`` with the
deployment the start created; a contract window stays stopped; a window that
moved on, another window's journal or an App that is not the stopped blue is
never started. The AST contract pins its calls: start and get, plus the
journal read and its one create-only marker write.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest
from databricks.sdk.errors import ResourceAlreadyExists

from tests.fixtures.quiesce_window_estate import (
    APP_NAME,
    BLUE_ID,
    RELEASE,
    Estate,
    StartBehaviour,
    load_deadman,
    run_deadman,
)
from tests.unit.test_quiesce_window_start_repin import (  # noqa: F401 - the autouse env
    LEASE_ID,
    _env,
    _estate,
    _open,
)
from tools.databricks import quiesce_window_journal as journal

REPO = Path(__file__).resolve().parents[2]
DEADMAN = REPO / "jobs" / "window_deadman.py"
NOW = 1_800_000_000.0
DEADLINE = int(NOW) + 3600


DEADMAN_NS = load_deadman(DEADMAN.read_bytes(), "window_deadman_under_test")


class Clock:
    """A fake epoch clock that ``sleep`` advances."""

    def __init__(self, now: float = NOW) -> None:
        self.now, self.sleeps = now, 0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps += 1
        self.now += seconds


def _marker_path(token: str) -> str:
    return journal.journal_path(APP_NAME).replace(".quiesce-window.json", f".quiesce-window.deadman-{token}.json")


def _argv(opened: dict[str, Any], **overrides: str) -> list[str]:
    token = DEADMAN_NS["window_token"](opened["window_sha256"], opened["lease_id"])
    values = {
        "--app": APP_NAME,
        "--app-id": "app-object-id",
        "--journal": journal.journal_path(APP_NAME),
        "--marker": _marker_path(token),
        "--token": token,
        "--window-sha256": opened["window_sha256"],
        "--blue-deployment-id": opened["blue_deployment_id"],
        "--restore-source": opened["restore_source"],
        "--deadline": str(DEADLINE),
        "--blue-restorable": "true" if opened["blue_restorable"] else "false",
        "--start-timeout-seconds": "600",
        "--poll-seconds": "60",
        **overrides,
    }
    # The armed marker is the token's, as the arm derives it (armed_path).
    armed = _marker_path(values["--token"]).replace(".deadman-", ".armed-")
    values.setdefault("--armed-marker", armed)
    return [item for pair in values.items() for item in pair]


def _deadman(estate: Estate, argv: list[str], clock: Clock | None = None) -> int:
    clock = clock or Clock()
    return int(DEADMAN_NS["main"](argv, workspace_factory=lambda: estate.workspace, clock=clock, sleep=clock.sleep))


def _marker(estate: Estate, opened: dict[str, Any]) -> dict[str, Any] | None:
    token = DEADMAN_NS["window_token"](opened["window_sha256"], opened["lease_id"])
    raw = estate.files.data.get(_marker_path(token))
    return None if raw is None else json.loads(raw)


# --- the restart ----------------------------------------------------------------------


def test_the_submitted_run_restarts_blue_from_the_published_bytes() -> None:
    # The arm's own parameters, run by the fake Jobs service from the file the
    # window published: the parameter contract between the two ends.
    estate = _estate()
    _open(estate)
    run = estate.jobs.only_run()
    deadline = int(run.tasks[0].spark_python_task.parameters[run.tasks[0].spark_python_task.parameters.index("--deadline") + 1])
    clock = Clock(now=float(deadline - 30))
    assert run_deadman(estate, run.run_id, clock=clock, sleep=clock.sleep) == 0
    assert estate.apps.compute == "ACTIVE" and estate.apps.active_id == "deployment-start-1"
    markers = [path for path in estate.files.data if ".deadman-" in path]
    assert len(markers) == 1 and json.loads(estate.files.data[markers[0]])["state"] == "deadman_restarted"


def test_a_dead_runners_open_window_is_restarted_at_the_deadline_on_the_start_deployment() -> None:
    estate = _estate()
    opened = _open(estate)
    assert estate.apps.compute == "STOPPED" and journal.state_of(journal.read(estate.workspace, app_name=APP_NAME)) == "window_open"
    clock = Clock()

    assert _deadman(estate, _argv(opened), clock) == 0
    marker = _marker(estate, opened)
    assert marker is not None and marker["state"] == "deadman_restarted"
    assert marker["start_deployment_id"] == "deployment-start-1" != BLUE_ID, "the start's own deployment"
    assert marker["terminal_state"] == "SUCCEEDED" and marker["blue_deployment_id"] == BLUE_ID
    assert (estate.apps.starts, estate.apps.stops, estate.apps.deploys, estate.apps.updates) == (1, 1, 0, 0)
    assert estate.apps.compute == "ACTIVE" and estate.apps.active_id == "deployment-start-1"
    assert clock.now >= DEADLINE, "never before the deadline"
    assert journal.read(estate.workspace, app_name=APP_NAME)["state"] == "window_open", "never signs the journal"


def test_a_contract_window_is_left_stopped_and_recorded() -> None:
    estate = _estate()
    opened = _open(estate)
    assert _deadman(estate, _argv(opened, **{"--blue-restorable": "false"})) == 0
    assert _marker(estate, opened)["state"] == "deadman_left_stopped"
    assert (estate.apps.starts, estate.apps.compute) == (0, "STOPPED")


def test_a_journal_that_says_the_window_is_not_restorable_wins_over_the_flag() -> None:
    estate = _estate()
    opened = _open(estate)
    current = journal.read(estate.workspace, app_name=APP_NAME)
    raw = json.loads(estate.files.data[journal.journal_path(APP_NAME).removeprefix("/Workspace")])
    raw["blue_restorable"] = False  # the dead-man reads the bytes; it never signs them
    estate.files.data[journal.journal_path(APP_NAME)] = json.dumps(raw).encode()
    assert current is not None
    assert _deadman(estate, _argv(opened)) == 0
    assert _marker(estate, opened)["state"] == "deadman_left_stopped" and estate.apps.starts == 0


@pytest.mark.parametrize("state", ("window_starting", "closed"))
def test_a_window_that_moved_on_ends_the_dead_man_before_the_deadline(state: str) -> None:
    estate = _estate()
    opened = _open(estate)
    journal.write(
        estate.workspace, app_name=APP_NAME, expected=opened, changes={"state": state}, assert_lease_held=lambda: None
    )
    clock = Clock()
    assert _deadman(estate, _argv(opened), clock) == 0
    assert clock.now < DEADLINE and estate.apps.starts == 0 and _marker(estate, opened) is None


def test_another_windows_journal_never_ends_the_wait_and_is_never_started() -> None:
    # Before the open's journal write lands the journal is absent or an older
    # window's: the dead-man keeps waiting, and at the deadline does nothing.
    estate = _estate()
    opened = _open(estate)
    token = DEADMAN_NS["window_token"]("e" * 64, LEASE_ID)
    for argv in (
        _argv(opened, **{"--window-sha256": "e" * 64, "--token": token}),
        _argv(opened, **{"--token": DEADMAN_NS["window_token"](opened["window_sha256"], "another-lease")}),
    ):
        clock = Clock()
        assert _deadman(estate, argv, clock) == 0
        assert clock.now >= DEADLINE and estate.apps.starts == 0
    assert _deadman(estate, _argv(opened, **{"--token": "not-hex"})) == 2


@pytest.mark.parametrize(
    ("break_it", "reason"),
    (
        (lambda estate: setattr(estate.apps, "compute", "ACTIVE"), "app_not_stopped"),
        (lambda estate: setattr(estate.apps, "active_id", "deployment-other"), "active_not_blue"),
        (lambda estate: setattr(estate.apps, "default_source", "/Workspace/Users/x/files"), "default_source_drift"),
        (lambda estate: setattr(estate.apps, "pending_id", BLUE_ID), "pending_deployment"),
    ),
    ids=("restarted-by-hand", "another-active", "default-source", "pending"),
)
def test_an_app_that_is_not_the_windows_stopped_blue_is_never_started(break_it: Any, reason: str) -> None:
    estate = _estate()
    opened = _open(estate)
    break_it(estate)
    assert _deadman(estate, _argv(opened)) == 1
    marker = _marker(estate, opened)
    assert marker["state"] == "deadman_skipped" and marker["reason"] == reason
    assert estate.apps.starts == 0


def test_an_app_with_another_id_is_never_started() -> None:
    estate = _estate()
    opened = _open(estate)
    assert _deadman(estate, _argv(opened, **{"--app-id": "another-app-object-id"})) == 1
    assert _marker(estate, opened)["reason"] == "app_identity" and estate.apps.starts == 0


def test_a_failed_start_is_recorded_with_its_deployment_and_never_retried() -> None:
    estate = _estate(StartBehaviour(state="FAILED"))
    opened = _open(estate)
    assert _deadman(estate, _argv(opened)) == 1
    marker = _marker(estate, opened)
    assert marker["state"] == "deadman_start_failed" and marker["start_deployment_id"] == "deployment-start-1"
    assert estate.apps.starts == 1


def test_a_start_without_a_readable_deployment_is_recorded_and_never_started_twice() -> None:
    estate = _estate(StartBehaviour(readable_pending_id=False))
    opened = _open(estate)
    estate.apps.hidden_start = ""  # the id never surfaces
    real_start = estate.apps.start

    def start_hiding(name: str) -> object:
        waiter = real_start(name)
        estate.apps.hidden_start = ""
        return waiter

    estate.apps.start = start_hiding  # type: ignore[method-assign]
    assert _deadman(estate, _argv(opened)) == 1
    assert _marker(estate, opened)["state"] == "deadman_start_unreadable" and estate.apps.starts == 1


def _armed_path(opened: dict[str, Any]) -> str:
    argv = _argv(opened)
    return argv[argv.index("--armed-marker") + 1]


def test_the_run_arms_first_and_never_over_another_windows_armed_marker() -> None:
    estate = _estate()
    opened = _open(estate)
    del estate.files.data[_armed_path(opened)]  # the fake Jobs service armed it at the submit
    assert _deadman(estate, _argv(opened)) == 0
    armed = json.loads(estate.files.data[_armed_path(opened)])
    assert armed["state"] == "deadman_armed" and armed["window_sha256"] == opened["window_sha256"]
    assert armed["recorded_at"].startswith("2027-01-15T08:00"), "written at the run's start, before its wait"

    other = _estate()
    opened = _open(other)
    other.files.data[_armed_path(opened)] = json.dumps(
        {"schema": "mip.quiesce-window-deadman-armed.v1", "token": "f" * 64}
    ).encode()
    with pytest.raises(RuntimeError, match="another armed marker"):
        _deadman(other, _argv(opened))
    assert other.apps.starts == 0 and _marker(other, opened) is None


def test_the_marker_is_create_only() -> None:
    estate = _estate()
    opened = _open(estate)
    assert _deadman(estate, _argv(opened)) == 0
    with pytest.raises(ResourceAlreadyExists):
        _deadman(estate, _argv(opened, **{"--blue-restorable": "false"}))


def test_the_token_is_stable_per_window_and_differs_across_windows() -> None:
    token = DEADMAN_NS["window_token"]
    assert token("a" * 64, "lease-1") == token("a" * 64, "lease-1")
    assert len({token("a" * 64, "lease-1"), token("b" * 64, "lease-1"), token("a" * 64, "lease-2")}) == 3
    assert len(token("a" * 64, "lease-1")) == 64, "a jobs idempotency token holds at most 64 characters"


# --- what it may call ---------------------------------------------------------------

ALLOWED_CALLS = {
    ("apps", "get"),
    ("apps", "get_deployment"),
    ("apps", "start"),
    ("workspace", "download"),
    ("workspace", "upload"),
}


def _client_calls(tree: ast.AST) -> tuple[set[tuple[str, str]], list[int]]:
    """Every ``window.workspace.<service>.<method>(...)`` call, through an alias too.

    The second item lists the lines of any other use of the client: a use the
    contract cannot read is a failure, never a pass.
    """

    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    calls: set[tuple[str, str]] = set()
    aliases: dict[str, str] = {}
    strays: list[int] = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Attribute)
            and node.attr == "workspace"
            and isinstance(node.value, ast.Name)
            and node.value.id == "window"
        ):
            continue
        service = parents.get(node)
        use = parents.get(service) if isinstance(service, ast.Attribute) else None
        if isinstance(use, ast.Attribute) and isinstance(parents.get(use), ast.Call) and parents[use].func is use:
            calls.add((service.attr, use.attr))  # type: ignore[union-attr]
            continue
        assign = parents.get(use) if isinstance(use, ast.Tuple) else use
        if isinstance(assign, ast.Assign) and isinstance(service, ast.Attribute):
            target = assign.targets[0]
            if isinstance(use, ast.Tuple) and isinstance(target, ast.Tuple):
                name = target.elts[use.elts.index(service)]
            else:
                name = target
            if isinstance(name, ast.Name):
                aliases[name.id] = service.attr
                continue
        strays.append(node.lineno)
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in aliases
        ):
            calls.add((aliases[node.func.value.id], node.func.attr))
    return calls, strays


def test_the_dead_man_calls_only_start_and_get_plus_its_journal_and_marker() -> None:
    calls, strays = _client_calls(ast.parse(DEADMAN.read_text(encoding="utf-8")))
    assert calls == ALLOWED_CALLS and strays == []
    assert ("apps", "start") in calls and not {method for service, method in calls if service == "apps"} & {
        "stop",
        "deploy",
        "update",
        "deploy_and_wait",
        "stop_and_wait",
        "start_and_wait",
    }


@pytest.mark.parametrize(
    "stray",
    (
        "    window.workspace.apps.stop(window.args.app)\n",
        "    window.workspace.apps.deploy(window.args.app, None)\n",
        "    window.workspace.jobs.submit()\n",
        "    window.workspace.permissions.set('apps', 'x')\n",
        "    apps, args = window.workspace.apps, window.args\n    apps.update(args.app, None)\n",
        "    client = window.workspace\n    client.apps.stop(window.args.app)\n",
        "    getattr(window.workspace, 'apps').stop(window.args.app)\n",
    ),
)
def test_the_call_contract_notices_any_other_call(stray: str) -> None:
    text = DEADMAN.read_text(encoding="utf-8").replace(
        "def start_blue(window: Window) -> str:\n", f"def start_blue(window: Window) -> str:\n{stray}", 1
    )
    calls, strays = _client_calls(ast.parse(text))
    assert calls != ALLOWED_CALLS or strays


def test_the_dead_man_is_self_contained_stdlib_plus_the_sdk() -> None:
    tree = ast.parse(DEADMAN.read_text(encoding="utf-8"))
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    assert "databricks" in roots and not roots & {"tools", "backend", "jobs", "tests", "cryptography"}
    stdlib = {"__future__", "argparse", "hashlib", "io", "json", "re", "sys", "time", "collections", "dataclasses", "datetime", "typing"}
    assert roots <= stdlib | {"databricks"}


def test_the_dead_man_ships_in_the_release_folder() -> None:
    from tools.databricks.app_runtime_closure import UPLOAD, owning_entry

    entry = owning_entry("jobs/window_deadman.py", UPLOAD)
    assert entry is not None and entry.kind == "upload", "staged and published with every release"
    assert RELEASE.split("/app-src/")[1], "the fixture's release folder is an app-src release"
    first = DEADMAN.read_text(encoding="utf-8").splitlines()[0]
    assert "Databricks notebook source" not in first, "an AUTO import keeps it a FILE"

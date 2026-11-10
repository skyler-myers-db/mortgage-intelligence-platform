"""Every fake Apps API that starts or stops an App answers the way spike S7 measured.

A fake that answers the way the code hoped (a pending id in the start
response, an active deployment on a STOPPED App, a FAILED deployment that
never became active, an update that moves the default source) is the defect
class behind S7's critical findings: the start path passed every test and
would have refused every real window. Each lifecycle fake is driven here
through the measured script (tests/fixtures/measured_apps.py), and a static
sweep refuses any fake ``start`` in tests/ that answers with a deployment.
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tests.fixtures import measured_apps as measured
from tests.fixtures import prestop_estate
from tests.fixtures import rollback_records as rr
from tests.fixtures.core_blue_world import CoreWorld, adopted_core_world
from tests.fixtures.quiesce_window_estate import (
    APP_NAME,
    BLUE_ID,
    OTHER_SOURCE,
    RELEASE,
    SOURCE_FILES,
    StartBehaviour,
    make_estate,
)

REPO = Path(__file__).resolve().parents[2]
ENV = ["DATABRICKS_WAREHOUSE_ID", "MIP_GIT_SHA"]


def _id(deployment: object) -> str:
    return str(getattr(deployment, "deployment_id", "") or "")


def _listed(apps: Any) -> set[str]:
    return {_id(item) for item in apps.list_deployments(APP_NAME)}


def test_the_measurements_are_the_spikes() -> None:
    assert measured.COMPUTE_START_S == (96, 108)
    assert measured.PENDING_VISIBLE_S == 1 and measured.TERMINAL_AFTER_S == (3, 6)
    assert measured.HIDDEN_READS >= 2, "the start response AND the GET after it see nothing"
    assert "default_source_code_path" not in measured.CREATE_UPDATE_MASKS
    with pytest.raises(measured.InvalidUpdateMask):
        measured.check_create_update_mask("description,default_source_code_path")
    measured.check_create_update_mask("description")


# --- the quiesce-window estate (tests/fixtures/quiesce_window_estate.py) -------------------


def _stopped_estate(behaviour: StartBehaviour | None = None) -> Any:
    estate = make_estate(ENV, behaviour)
    estate.apps.stop(APP_NAME)
    return estate


def test_the_estates_stopped_get_has_no_active_or_pending_deployment() -> None:
    apps = _stopped_estate().apps
    app = apps.get(APP_NAME)
    assert app.compute_status.state == "STOPPED"
    assert app.active_deployment is None and app.pending_deployment is None
    assert app.default_source_code_path == RELEASE, "the last active deployment's folder"


def test_the_estates_start_shows_its_deployment_only_reads_later_then_pending_once_then_active() -> None:
    apps = _stopped_estate().apps
    before = _listed(apps)
    response = apps.start(APP_NAME).response
    assert response.active_deployment is None and response.pending_deployment is None
    assert response.compute_status.state == "STARTING"
    immediate = apps.get(APP_NAME)
    assert (immediate.active_deployment, immediate.pending_deployment) == (None, None)
    assert immediate.compute_status.state == "STARTING"

    reads = 1
    while _listed(apps) == before:
        reads += 1
        assert reads <= measured.HIDDEN_READS + 1, "the new deployment shows after the delay"
    assert reads == measured.HIDDEN_READS, "hidden for exactly the measured reads"
    new = (_listed(apps) - before).pop()
    shown = apps.get(APP_NAME)
    assert _id(shown.pending_deployment) == new and shown.active_deployment is None
    assert shown.compute_status.state == "ACTIVE", "the id shows only once the compute is ACTIVE"
    after = apps.get(APP_NAME)
    assert after.pending_deployment is None and _id(after.active_deployment) == new
    assert after.active_deployment.status.state == "IN_PROGRESS"
    assert apps.get_deployment(APP_NAME, new).status.state == "IN_PROGRESS"
    assert apps.get_deployment(APP_NAME, new).status.state == "SUCCEEDED"
    started = apps.get_deployment(APP_NAME, new)
    assert started.mode == "SNAPSHOT" and new != BLUE_ID, "a start always makes a new deployment"
    assert started.source_code_path == RELEASE


def test_the_estates_start_snapshots_the_current_bytes_with_the_newest_env_set() -> None:
    estate = _stopped_estate()
    estate.files.put_tree(RELEASE, {"backend/runtime.py": b"print('edited after the last deploy')\n"})
    estate.apps.start(APP_NAME)
    while len(estate.apps.deployments) == 1:
        estate.apps.get(APP_NAME)
    new = next(item for item in estate.apps.deployments if item != BLUE_ID)
    artifact = estate.apps.deployments[new].deployment_artifacts.source_code_path
    assert estate.files.data[f"{artifact.removeprefix('/Workspace')}/backend/runtime.py"].startswith(b"print('edited")
    assert sorted(item.name for item in estate.apps.deployments[new].env_vars) == sorted(ENV)


def test_the_estates_failed_deployment_becomes_active_and_moves_the_default_source() -> None:
    estate = _stopped_estate(StartBehaviour(source=OTHER_SOURCE, state="FAILED"))
    estate.files.put_tree(OTHER_SOURCE, SOURCE_FILES)
    estate.apps.start(APP_NAME)
    while estate.apps.get(APP_NAME).active_deployment is None:
        pass
    new = _id(estate.apps.get(APP_NAME).active_deployment)
    assert estate.apps.get_deployment(APP_NAME, new).status.state == "IN_PROGRESS"
    assert estate.apps.get_deployment(APP_NAME, new).status.state == "FAILED"
    app = estate.apps.get(APP_NAME)
    assert _id(app.active_deployment) == new, "a FAILED deployment stays active: nothing falls back"
    assert app.default_source_code_path == OTHER_SOURCE, "and it moved the default source"
    estate.apps.behaviour = StartBehaviour()
    estate.apps.stop(APP_NAME)
    estate.apps.start(APP_NAME)
    while estate.apps.get(APP_NAME).active_deployment is None:
        pass
    rerun = estate.apps.get(APP_NAME).active_deployment
    assert rerun.source_code_path == OTHER_SOURCE, "a start on a FAILED-active App reruns its folder"


def test_the_estates_update_ignores_the_default_source_and_create_update_rejects_its_mask() -> None:
    apps = make_estate(ENV).apps
    payload = {"name": APP_NAME, "default_source_code_path": OTHER_SOURCE, "resources": []}
    apps.update(APP_NAME, SimpleNamespace(as_dict=lambda: payload))
    assert apps.get(APP_NAME).default_source_code_path == RELEASE
    with pytest.raises(measured.InvalidUpdateMask):
        apps.create_update(APP_NAME, "default_source_code_path", app=SimpleNamespace())
    assert apps.get(APP_NAME).default_source_code_path == RELEASE


# --- the core blue world (tests/fixtures/core_blue_world.py) ----------------------------


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> CoreWorld:
    rr.signing_env(monkeypatch)
    from tools.databricks import app_rollback_record_contract as contract

    monkeypatch.setattr(contract, "assert_owned_app_rollback_scope", lambda *_a, **_kw: None)
    return adopted_core_world(tmp_path)


def test_the_core_worlds_stopped_get_has_no_active_deployment(world: CoreWorld) -> None:
    world.compute = "STOPPED"
    app = world.apps.get(rr.APP_NAME)
    assert app.active_deployment is None and app.pending_deployment is None
    assert app.default_source_code_path == world.folder("deployment-blue")


def test_the_core_worlds_start_answers_with_nothing_and_shows_its_deployment_later(world: CoreWorld) -> None:
    world.compute = "STOPPED"
    before = {_id(item) for item in world.apps.list_deployments(rr.APP_NAME)}
    response = world.apps.start(rr.APP_NAME).response
    assert response.active_deployment is None and response.pending_deployment is None
    assert world.apps.get(rr.APP_NAME).active_deployment is None
    reads = 1
    while {_id(item) for item in world.apps.list_deployments(rr.APP_NAME)} == before:
        reads += 1
    assert reads == measured.HIDDEN_READS
    new = ({_id(item) for item in world.apps.list_deployments(rr.APP_NAME)} - before).pop()
    shown = world.apps.get(rr.APP_NAME)
    assert _id(shown.pending_deployment) == new and shown.active_deployment is None
    assert _id(world.apps.get(rr.APP_NAME).active_deployment) == new
    assert world.apps.get_deployment(rr.APP_NAME, new).status.state == "SUCCEEDED"
    assert world.apps.get_deployment(rr.APP_NAME, new).source_code_path == world.folder("deployment-blue")


def test_the_core_worlds_update_moves_bindings_only(world: CoreWorld) -> None:
    resource = SimpleNamespace(as_dict=lambda: {"name": "x"})
    world.apps.update(rr.APP_NAME, SimpleNamespace(resources=[resource], default_source_code_path="/elsewhere"))
    assert world.apps.get(rr.APP_NAME).default_source_code_path == world.folder("deployment-blue")


def test_adopt_lives_spy_and_the_prestop_estate_show_no_active_deployment_when_stopped(
    world: CoreWorld,
) -> None:
    world.compute = "STOPPED"
    assert world.apps.reads.get(rr.APP_NAME).active_deployment is None, "adopt_live_world.SpyApps"
    estate = prestop_estate.clean_estate()
    estate["app"]["state"] = "STOPPED"
    app = prestop_estate.FakeWorkspace(estate).apps.get(estate["app"]["name"])
    assert app.active_deployment is None and app.pending_deployment is None


# --- the sweep ------------------------------------------------------------------------------


def _hopeful_starts(path: Path) -> list[str]:
    """A fake ``start`` that answers with a deployment id: the shape the real API never has."""

    found = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.FunctionDef) or node.name not in {"start", "start_and_wait"}:
            continue
        for keyword in (item for item in ast.walk(node) if isinstance(item, ast.keyword)):
            if keyword.arg in {"pending_deployment", "active_deployment"} and not (
                isinstance(keyword.value, ast.Constant) and keyword.value.value is None
            ):
                found.append(f"{path.name}:{node.lineno}")
    return found


def test_no_fake_start_in_tests_answers_with_a_deployment() -> None:
    hopeful = [hit for path in sorted((REPO / "tests").rglob("*.py")) for hit in _hopeful_starts(path)]
    assert hopeful == [], "spike S7: a start's response and the GET after it show no deployment"


def test_the_sweep_catches_the_old_hopeful_shape(tmp_path: Path) -> None:
    probe = tmp_path / "hopeful.py"
    probe.write_text(
        "from types import SimpleNamespace\n"
        "def start(name):\n"
        "    return SimpleNamespace(response=SimpleNamespace(pending_deployment=SimpleNamespace(deployment_id='d')))\n",
        encoding="utf-8",
    )
    assert _hopeful_starts(probe), "the positive control: the pre-S7 fakes' shape is caught"

"""The window's deployment baseline, and the run's own start over a moved one (wave 7c).

``window_open`` journals the App's deployment ids right before the stop. The
run's own ``start`` then refuses ``window_start_baseline_moved``, before any
write, start or stop, unless the App is still the stopped App the window
left. Before this change it took a fresh baseline at the start, which already
held an owner's break-glass start, ran ``apps start`` on a running App and
then stopped the owner's start again when its own start could not be read.

Spike S7 measured why the listing alone is not enough (the fake models it,
tests/fixtures/measured_apps.py): a start's deployment id is invisible for
about 100 s while the compute is STARTING. So the guard also requires the
compute STOPPED with nothing pending. *Moved* means NEW ids only: a
deployment that left the listing never keeps a stopped App from its start.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tests.fixtures import rollback_records as rr
from tests.fixtures.core_blue_world import CoreWorld
from tests.fixtures.quiesce_window_estate import (
    APP_NAME,
    BLUE_ARTIFACT,
    BLUE_ID,
    OWNER_CALLER,
    RELEASE,
    Estate,
    StartBehaviour,
    deployment,
    window_hooks,
)
from tests.unit.test_quiesce_window_start_repin import (  # noqa: F401 - the autouse env
    GIT_SHA,
    LEASE_ID,
    _await,
    _env,
    _estate,
    _open,
    _plan,
    _start,
    _stop,
)
from tools.databricks import app_start_deployment as starts
from tools.databricks import quiesce_window as window
from tools.databricks import quiesce_window_journal as journal

OLD_ID = "deployment-old"
JOURNAL = journal.journal_path(APP_NAME)


def _with_an_older_deployment(estate: Estate) -> Estate:
    estate.apps.deployments[OLD_ID] = deployment(
        OLD_ID,
        source=RELEASE,
        artifact=BLUE_ARTIFACT,
        env_names=[item.name for item in estate.apps.deployments[BLUE_ID].env_vars],
        created="2026-09-01T00:00:00Z",
    )
    return estate


def _calls(estate: Estate) -> tuple[int, int, int, int]:
    return (estate.apps.starts, estate.apps.stops, estate.apps.deploys, estate.apps.updates)


def _journal_state(estate: Estate) -> tuple[str, list[str]]:
    current = journal.read(estate.workspace, app_name=APP_NAME)
    return current["state"], current["start_baseline_ids"]


# --- window_open journals the baseline ---------------------------------------------------


def test_window_open_journals_every_listed_deployment_id_before_the_stop() -> None:
    estate = _with_an_older_deployment(_estate())
    opened = _open(estate)
    assert opened["state"] == "window_open" and opened["start_baseline_ids"] == sorted([BLUE_ID, OLD_ID])
    assert estate.apps.stops == 1


def test_open_refuses_a_listing_that_does_not_hold_blue_and_stops_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    # Blue was the App's last deployment at both re-checks; the listing the baseline is
    # taken from must hold it too, or no owner start could ever be told apart from it.
    estate = _with_an_older_deployment(_estate())
    plan = _plan(estate)
    monkeypatch.setattr(window, "deployment_ids", lambda *_a, **_k: frozenset({OLD_ID}))
    with pytest.raises(RuntimeError, match="blue is not among the App's listed deployments"):
        window.open_window(
            estate.workspace,
            app_name=APP_NAME,
            plan=plan.full(),
            acknowledgement=plan.sha256,
            lease_id=LEASE_ID,
            source_git_sha=GIT_SHA,
            assert_lease_held=lambda: None,
            stop=_stop(estate),
            **window_hooks(estate),
        )
    assert estate.apps.stops == 0 and journal.read(estate.workspace, app_name=APP_NAME) is None
    assert estate.jobs.cancels == [estate.jobs.only_run().run_id], "the armed dead-man was cancelled"


# --- the run's own start over a moved baseline -------------------------------------------


def test_an_owner_start_still_invisible_in_the_listing_refuses_the_runs_start() -> None:
    """The judge's case: the id shows about 100 s after the start (S7); the compute state catches it."""

    estate = _estate()
    _open(estate)
    estate.apps.behaviour = StartBehaviour(hidden_reads=50)  # invisible for the whole test
    estate.apps.caller = OWNER_CALLER
    estate.apps.start(APP_NAME)  # the owner's break-glass, while the run is still alive
    app = estate.apps.get(APP_NAME)
    assert app.compute_status.state == "STARTING" and app.active_deployment is None and app.pending_deployment is None
    assert starts.deployment_ids(estate.workspace, app_name=APP_NAME) == {BLUE_ID}, "nothing new is listed yet"
    before, stored = _calls(estate), estate.files.data[JOURNAL]
    assert before[:2] == (1, 1), "the owner's start and the window's own stop"

    with pytest.raises(RuntimeError, match="window_start_baseline_moved"):
        _start(estate)

    assert _calls(estate) == before, "zero apps.start and zero stop by the run"
    assert estate.files.data[JOURNAL] == stored and _journal_state(estate) == ("window_open", [BLUE_ID])
    assert estate.apps.compute == "STARTING", "the owner's start is left in flight"


def test_an_owner_start_that_landed_refuses_the_runs_start_and_is_never_stopped() -> None:
    estate = _estate()
    _open(estate)
    estate.apps.caller = OWNER_CALLER
    created = starts.start_app(estate.workspace, app_name=APP_NAME)
    starts.wait_for_terminal(estate.workspace, app_name=APP_NAME, deployment_id=created)
    before, stored = _calls(estate), estate.files.data[JOURNAL]

    with pytest.raises(RuntimeError, match="window_start_baseline_moved") as refused:
        _start(estate)

    assert created not in str(refused.value) and "deployment-" not in str(refused.value), "no id in the message"
    assert _calls(estate) == before and estate.files.data[JOURNAL] == stored
    assert estate.apps.compute == "ACTIVE" and estate.apps.active_id == created


def test_a_new_deployment_listed_on_a_stopped_app_refuses_too() -> None:
    estate = _estate()
    _open(estate)
    estate.apps.deployments["deployment-landed"] = deployment(
        "deployment-landed", source=RELEASE, artifact=BLUE_ARTIFACT, env_names=[], created="2026-10-05T00:00:00Z"
    )
    before = _calls(estate)
    with pytest.raises(RuntimeError, match="window_start_baseline_moved"):
        _start(estate)
    assert _calls(estate) == before and _journal_state(estate) == ("window_open", [BLUE_ID])


def test_a_pending_deployment_on_a_stopped_app_refuses_too() -> None:
    estate = _estate()
    _open(estate)
    estate.apps.unmeasured_stopped_pending = "deployment-pending"
    before = _calls(estate)
    with pytest.raises(RuntimeError, match="window_start_baseline_moved"):
        _start(estate)
    assert _calls(estate) == before and _journal_state(estate)[0] == "window_open"


def test_a_deployment_that_left_the_listing_never_keeps_a_stopped_app_from_its_start() -> None:
    """New ids only, never a symmetric difference; and the journaled baseline is kept whole."""

    estate = _with_an_older_deployment(_estate())
    _open(estate)
    del estate.apps.deployments[OLD_ID]  # it dropped out of the listing inside the window

    assert _start(estate) == "deployment-start-1" and estate.apps.starts == 1
    assert _journal_state(estate) == ("window_started", sorted([BLUE_ID, OLD_ID]))
    assert _await(estate) == "deployment-start-1"


def test_a_window_journaled_before_the_baseline_existed_still_starts_from_a_fresh_listing() -> None:
    estate = _with_an_older_deployment(_estate())
    _open(estate)
    stored = json.loads(estate.files.data[JOURNAL])
    unsigned = {key: value for key, value in stored.items() if not key.startswith("attestation_")}
    estate.files.data[JOURNAL] = json.dumps(journal._sign({**unsigned, "start_baseline_ids": []})).encode()

    assert _start(estate) == "deployment-start-1"
    assert _journal_state(estate) == ("window_started", sorted([BLUE_ID, OLD_ID]))


def test_an_unmoved_window_starts_exactly_once_as_before() -> None:
    estate = _estate()
    _open(estate)
    assert _start(estate) == "deployment-start-1"
    assert _calls(estate) == (1, 1, 0, 0) and _journal_state(estate) == ("window_started", [BLUE_ID])


# --- the same guard over a v7 core blue --------------------------------------------------


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> CoreWorld:
    from tests.fixtures.core_blue_world import adopted_core_world
    from tools.databricks import app_rollback_record_contract as contract
    from tools.databricks import quiesce_window_facts as facts
    from tools.databricks.app_deployment_lease import LEASE_ROOT

    rr.signing_env(monkeypatch)
    monkeypatch.setattr(contract, "assert_owned_app_rollback_scope", lambda *_a, **_kw: None)
    monkeypatch.setattr(facts, "app_or_none", lambda workspace, *, app_name: workspace.apps.get(app_name))
    adopted = adopted_core_world(tmp_path)
    adopted.tree.add_dir(LEASE_ROOT)
    return adopted


def _core_open(world: CoreWorld) -> dict[str, Any]:
    from tests.unit.test_quiesce_window_v7_start import _open as open_core

    return open_core(world)


def _core_stop(world: CoreWorld) -> Any:
    def stop() -> str:
        world.apps.mutations.append("stop")
        world.compute = "STOPPED"
        return "stopped"

    return stop


def test_a_core_window_journals_its_baseline_and_refuses_its_start_over_an_owner_start(world: CoreWorld) -> None:
    opened = _core_open(world)
    assert opened["start_baseline_ids"] == sorted(world.deployments) and "deployment-blue" in opened["start_baseline_ids"]
    world.start_hidden_reads = 50
    world.apps.start(rr.APP_NAME)  # the owner's break-glass: STARTING, its id not shown
    assert world.apps.mutations == ["stop", "start"]
    listed = [item.deployment_id for item in world.apps.list_deployments(rr.APP_NAME)]
    assert sorted(listed) == opened["start_baseline_ids"], "nothing new is listed yet"

    with pytest.raises(RuntimeError, match="window_start_baseline_moved"):
        window.start_window(world, app_name=rr.APP_NAME, assert_lease_held=lambda: None, stop=_core_stop(world))

    assert world.apps.mutations == ["stop", "start"], "zero apps.start and zero stop by the run"
    assert journal.read(world, app_name=rr.APP_NAME)["state"] == "window_open" and world.compute == "STARTING"


def test_the_guard_reads_and_never_writes() -> None:
    estate = _estate()
    _open(estate)
    apps = SimpleNamespace(
        get=estate.apps.get,
        list_deployments=estate.apps.list_deployments,
    )  # no start, stop, deploy or update: a call of one would raise AttributeError
    workspace = SimpleNamespace(apps=apps)
    assert starts.require_unmoved_baseline(workspace, app_name=APP_NAME, journaled=frozenset({BLUE_ID})) == {BLUE_ID}
    assert starts.require_unmoved_baseline(workspace, app_name=APP_NAME, journaled=frozenset()) == {BLUE_ID}
    kept = starts.require_unmoved_baseline(workspace, app_name=APP_NAME, journaled=frozenset({BLUE_ID, OLD_ID}))
    assert kept == {BLUE_ID, OLD_ID}, "an id that left the listing stays in the baseline"
    assert starts.BASELINE_MOVED == "window_start_baseline_moved" and starts.BASELINE_MOVED not in starts.PROOF_CODES

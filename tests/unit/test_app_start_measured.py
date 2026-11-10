"""The start path and blue's identity against the Apps API spike S7 measured (M0-PR8, M0-PR9).

Every test here runs on the measured fake (tests/fixtures/measured_apps.py)
and reproduces one of S7's critical findings against origin/main 711af8be:

* S7-1a: ``start_app`` took the start's id from the start response's
  ``pending_deployment`` or one GET right after, which never show it, so
  every window refused ``start_unreadable``. It now finds the one id outside
  the deployment ids listed before the start, polled at most every 5 s.
* S7-3 and a STOPPED App's GET: ``last_deployment_is`` read the active
  deployment a stopped App's GET omits, so tier a, ``blue_current`` and the
  window's open refused every stopped blue, and it trusted the newest
  SUCCEEDED deployment where a FAILED newer one decides what a start reruns.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.fixtures.measured_apps import VirtualClock, virtual_start_clock
from tests.fixtures.quiesce_window_estate import (
    APP_NAME,
    BLUE_ID,
    OTHER_SOURCE,
    RELEASE,
    Estate,
    StartBehaviour,
    deployment,
)
from tests.unit.test_quiesce_window_start_repin import (  # noqa: F401 - the autouse env
    ENV_NAMES,
    _await,
    _env,
    _estate,
    _open,
    _plan,
    _repin,
    _start,
)
from tools.databricks import app_start_deployment as starts
from tools.databricks import quiesce_window as window
from tools.databricks import quiesce_window_journal as journal
from tools.databricks.blue_restore_proof import RestoreProofRefused, assert_tier_a


class RunnerKilled(BaseException):
    """The runner dies (a job kill): no ``except Exception`` handler sees it."""


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> VirtualClock:
    return virtual_start_clock(monkeypatch)


def _stopped(behaviour: StartBehaviour | None = None) -> Estate:
    estate = _estate(behaviour)
    estate.apps.stop(APP_NAME)
    return estate


def _failed_green(estate: Estate, *, active: bool = True) -> None:
    """A green deploy that FAILED: newer than blue; active and the default source when it got there (S7-3)."""

    estate.apps.files.put_tree(OTHER_SOURCE, {"app.yaml": b"command: [exit, '1']\n"})
    estate.apps.deployments["deployment-green"] = deployment(
        "deployment-green",
        source=OTHER_SOURCE,
        artifact="/Workspace/Users/app-sp/src/deployment-green",
        env_names=ENV_NAMES,
        state="FAILED",
        created="2026-10-01T12:00:00Z",
    )
    if active:
        estate.apps.active_id = "deployment-green"
        estate.apps.default_source = OTHER_SOURCE


# --- S7-1a: finding the deployment a start created ----------------------------------------


def test_a_start_is_found_only_after_the_measured_delay_polled_at_most_every_5_s(clock: VirtualClock) -> None:
    estate = _stopped()

    created = starts.start_app(estate.workspace, app_name=APP_NAME)

    assert created == "deployment-start-1" != BLUE_ID and estate.apps.starts == 1
    assert clock.sleeps and max(clock.sleeps) <= 5.0, "the id shows ~100 s later, pending ~1 s"


def test_an_active_deployment_from_the_baseline_is_never_taken_as_the_starts(clock: VirtualClock) -> None:
    estate = _stopped()
    real_get = estate.apps.get

    def get_showing_blue(name: str) -> Any:
        app = real_get(name)
        if app.active_deployment is None and app.pending_deployment is None:
            app.active_deployment = estate.apps.deployments[BLUE_ID]  # an older one, shown as active
        return app

    estate.apps.get = get_showing_blue  # type: ignore[method-assign]
    assert starts.start_app(estate.workspace, app_name=APP_NAME) == "deployment-start-1"


@pytest.mark.parametrize(
    ("behaviour", "code"),
    ((StartBehaviour(twin=True), "start_ambiguous"), (StartBehaviour(never_visible=True), "start_timeout")),
    ids=("two-new-ids", "none-by-the-deadline"),
)
def test_a_start_without_exactly_one_new_id_refuses(
    behaviour: StartBehaviour, code: str, clock: VirtualClock
) -> None:
    estate = _stopped(behaviour)
    with pytest.raises(starts.StartProofError) as refused:
        starts.start_app(estate.workspace, app_name=APP_NAME)
    assert refused.value.code == code and code in starts.PROOF_CODES
    if code == "start_timeout":
        assert clock.now >= starts.START_WAIT_TIMEOUT_S and max(clock.sleeps) <= 5.0


def test_a_window_runner_killed_after_its_start_resumes_from_the_journaled_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    estate = _estate()
    _open(estate)

    def start_then_die(workspace: Any, *, app_name: str, baseline: frozenset[str]) -> str:
        workspace.apps.start(app_name)
        raise RunnerKilled  # the runner dies before it journals the start's id

    monkeypatch.setattr(window, "start_app", start_then_die)
    with pytest.raises(RunnerKilled):
        _start(estate)
    intent = journal.read(estate.workspace, app_name=APP_NAME)
    assert intent["state"] == "window_starting" and intent["start_baseline_ids"] == [BLUE_ID]
    monkeypatch.setattr(window, "start_app", starts.start_app)

    assert _start(estate) == "deployment-start-1" and estate.apps.starts == 1, "found, never started again"
    assert journal.read(estate.workspace, app_name=APP_NAME)["start_deployment_id"] == "deployment-start-1"
    assert _await(estate) == "deployment-start-1"


def test_an_intent_without_a_baseline_resumes_to_a_refusal_never_a_guess() -> None:
    estate = _estate()
    opened = _open(estate)
    journal.write(
        estate.workspace,
        app_name=APP_NAME,
        expected=opened,
        changes={"state": "window_starting"},
        assert_lease_held=lambda: None,
    )
    estate.apps.start(APP_NAME)
    with pytest.raises(RuntimeError, match="start_unreadable"):
        _start(estate)
    assert estate.apps.starts == 1 and estate.apps.compute == "STOPPED"


# --- blue's identity on a STOPPED App (tier a) ----------------------------------------------


def test_a_stopped_app_whose_newest_deployment_is_blue_is_blue_current(clock: VirtualClock) -> None:
    estate = _stopped()
    assert estate.apps.get(APP_NAME).active_deployment is None, "the measured GET"

    assert starts.last_deployment_is(estate.workspace, app_name=APP_NAME, deployment_id=BLUE_ID)
    assert starts.current_deployment_id(estate.workspace, app_name=APP_NAME) == BLUE_ID
    assert_tier_a(estate.workspace, app_name=APP_NAME, record={"deployment_id": BLUE_ID})


@pytest.mark.parametrize("stopped", (True, False), ids=("stopped", "running"))
def test_a_failed_active_app_refuses_tier_a(stopped: bool) -> None:
    estate = _estate()
    _failed_green(estate)
    if stopped:
        estate.apps.stop(APP_NAME)

    assert not starts.last_deployment_is(estate.workspace, app_name=APP_NAME, deployment_id=BLUE_ID)
    with pytest.raises(RestoreProofRefused) as refused:
        assert_tier_a(estate.workspace, app_name=APP_NAME, record={"deployment_id": BLUE_ID})
    assert refused.value.code == "tier_a_refused"
    if stopped:
        assert starts.current_deployment_id(estate.workspace, app_name=APP_NAME) == "deployment-green"


def test_a_newer_failed_deployment_refuses_tier_a_even_while_blue_is_active() -> None:
    # Newest SUCCEEDED == blue is not enough: the newest deployment of ANY state decides.
    estate = _estate()
    _failed_green(estate, active=False)
    assert estate.apps.get(APP_NAME).active_deployment.deployment_id == BLUE_ID
    assert not starts.last_deployment_is(estate.workspace, app_name=APP_NAME, deployment_id=BLUE_ID)


def test_a_stopped_apps_default_source_must_be_blues() -> None:
    estate = _stopped()
    estate.apps.default_source = OTHER_SOURCE
    assert not starts.last_deployment_is(estate.workspace, app_name=APP_NAME, deployment_id=BLUE_ID)
    estate.apps.default_source = ""
    assert not starts.last_deployment_is(estate.workspace, app_name=APP_NAME, deployment_id=BLUE_ID)
    estate.apps.default_source = RELEASE
    assert starts.last_deployment_is(estate.workspace, app_name=APP_NAME, deployment_id=BLUE_ID)


def test_a_window_over_an_already_stopped_blue_plans_opens_starts_proves_and_re_pins(
    clock: VirtualClock,
) -> None:
    # The cutover may start from a stopped App (the live App was stopped on 2026-11-09).
    estate = _stopped()
    plan = _plan(estate)
    assert plan.code == "window" and plan.facts.blue_current, "blue_current on a stopped App"

    _open(estate)
    assert estate.apps.stops == 1, "already stopped: the open's stop finds it STOPPED"
    assert _start(estate) == "deployment-start-1"
    proven = _await(estate)
    record = _repin(estate, proven)
    assert record["deployment_id"] == proven and record["predecessor_deployment_id"] == BLUE_ID
    assert estate.apps.compute == "ACTIVE" and estate.apps.starts == 1


def test_a_window_over_a_failed_active_stopped_app_refuses_before_any_stop() -> None:
    estate = _estate()
    _failed_green(estate)
    estate.apps.stop(APP_NAME)
    stops = estate.apps.stops
    plan = _plan(estate)
    assert "window_blue_not_current" in plan.refusals
    assert estate.apps.stops == stops and estate.apps.starts == 0

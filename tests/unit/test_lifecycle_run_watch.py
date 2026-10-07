"""Job-mode lifecycle syncs move the workflow generation once a request sees them end.

Warehouse deferred #3 (delivery-06 bump contract). ``run_now`` returns a run
id and the sync finishes minutes later in the Jobs service; nothing moved the
workflow generation, so the cached workflow counts trailed by up to one soft
TTL plus a stale serve. ``lifecycle_run_watch`` checks the runs the App
submitted, but only when a request builds a workflow key: no timer, at most
one ``get_run`` per run per 15 s, a one-hour window. Every test injects the
clock and the executor.
"""

from __future__ import annotations

import logging
import sys
import threading
import types
from collections.abc import Callable, Iterator
from concurrent.futures import Executor, Future
from types import SimpleNamespace
from typing import Any

import pytest
from databricks.sdk.service.jobs import BaseRun, Run, RunLifeCycleState, RunResultState, RunState

from backend.services import job_trigger, lifecycle_run_watch
from backend.services.gold_cache import workflow_generation, workflow_key


class _InlineExecutor(Executor):
    def __init__(self) -> None:
        self.submitted = 0

    def submit(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Future[Any]:
        self.submitted += 1
        future: Future[Any] = Future()
        future.set_result(fn(*args, **kwargs))
        return future


class _DeferredExecutor(Executor):
    def __init__(self) -> None:
        self.jobs: list[Callable[[], Any]] = []

    def submit(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Future[Any]:
        self.jobs.append(lambda: fn(*args, **kwargs))
        return Future()

    def run_all(self) -> None:
        jobs, self.jobs = self.jobs, []
        for job in jobs:
            job()


class _RefusingExecutor(Executor):
    def submit(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Future[Any]:
        raise RuntimeError("cannot schedule new futures after interpreter shutdown")


class _Jobs:
    """``WorkspaceClient.jobs`` for one run: answers ``get_run`` with SDK objects."""

    def __init__(self) -> None:
        self.life_cycle = RunLifeCycleState.RUNNING
        self.result: RunResultState | None = None
        self.error: BaseException | None = None
        self.calls: list[int] = []

    def get_run(self, run_id: int) -> Run:
        self.calls.append(run_id)
        if self.error is not None:
            raise self.error
        return Run(run_id=run_id, state=RunState(life_cycle_state=self.life_cycle, result_state=self.result))


def _workspace() -> tuple[Any, _Jobs]:
    jobs = _Jobs()
    return SimpleNamespace(jobs=jobs), jobs


def _events(caplog: pytest.LogCaptureFixture, name: str) -> list[logging.LogRecord]:
    return [record for record in caplog.records if getattr(record, "mip_event", None) == name]


def _watch_threads() -> set[threading.Thread]:
    return {t for t in threading.enumerate() if t.name.startswith(lifecycle_run_watch.WATCH_THREAD_PREFIX)}


@pytest.fixture(autouse=True)
def _fresh_watch() -> Iterator[None]:
    lifecycle_run_watch._reset_for_tests()
    try:
        yield
    finally:
        lifecycle_run_watch._reset_for_tests()


def test_no_pending_run_submits_nothing_and_starts_no_thread() -> None:
    before = _watch_threads()
    recorder = _DeferredExecutor()

    lifecycle_run_watch.observe(now=0.0, executor=recorder)
    workflow_key("watch.test.idle")

    assert recorder.jobs == []
    assert lifecycle_run_watch._EXECUTOR is None
    # A thread an earlier test's (reset) executor left may still be exiting.
    assert _watch_threads() <= before, "no new watch thread"


def test_a_run_without_an_id_is_unobservable(caplog: pytest.LogCaptureFixture) -> None:
    workspace, _jobs = _workspace()
    with caplog.at_level(logging.WARNING):
        lifecycle_run_watch.note_submitted(workspace, job_id=42, run_id=None, now=0.0)

    [event] = _events(caplog, "lifecycle_job_unobservable")
    assert event.mip_extras == {"job_id": 42}  # type: ignore[attr-defined]
    assert lifecycle_run_watch.pending_run_ids() == []


def test_two_observes_within_the_interval_make_one_get_run() -> None:
    workspace, jobs = _workspace()
    inline = _InlineExecutor()
    lifecycle_run_watch.note_submitted(workspace, job_id=42, run_id=7, now=0.0)

    lifecycle_run_watch.observe(now=1.0, executor=inline)
    lifecycle_run_watch.observe(now=15.9, executor=inline)
    assert jobs.calls == [7]

    lifecycle_run_watch.observe(now=16.0, executor=inline)
    assert jobs.calls == [7, 7]


def test_running_keeps_the_run_and_terminated_bumps_exactly_once(caplog: pytest.LogCaptureFixture) -> None:
    workspace, jobs = _workspace()
    inline = _InlineExecutor()
    generation = workflow_generation()
    lifecycle_run_watch.note_submitted(workspace, job_id=42, run_id=7, now=0.0)
    assert workflow_generation() == generation, "submitting is not finishing"

    lifecycle_run_watch.observe(now=1.0, executor=inline)
    assert workflow_generation() == generation
    assert lifecycle_run_watch.pending_run_ids() == [7]

    jobs.life_cycle, jobs.result = RunLifeCycleState.TERMINATED, RunResultState.SUCCESS
    with caplog.at_level(logging.INFO):
        lifecycle_run_watch.observe(now=16.0, executor=inline)

    assert workflow_generation() == generation + 1
    assert lifecycle_run_watch.pending_run_ids() == []
    [event] = _events(caplog, "lifecycle_sync_completed")
    assert event.mip_extras == {  # type: ignore[attr-defined]
        "mode": "job",
        "job_id": 42,
        "run_id": 7,
        "result_state": "SUCCESS",
        "generation": generation + 1,
    }

    for later in (31.0, 46.0, 120.0):
        lifecycle_run_watch.observe(now=later, executor=inline)
    assert jobs.calls == [7, 7], "a finished run is never checked again"
    assert workflow_generation() == generation + 1


@pytest.mark.parametrize(
    ("life_cycle", "result"),
    [
        (RunLifeCycleState.TERMINATED, RunResultState.FAILED),
        (RunLifeCycleState.INTERNAL_ERROR, RunResultState.FAILED),
        (RunLifeCycleState.SKIPPED, None),
    ],
)
def test_every_terminal_state_bumps_and_names_its_result(
    life_cycle: RunLifeCycleState, result: RunResultState | None, caplog: pytest.LogCaptureFixture
) -> None:
    workspace, jobs = _workspace()
    jobs.life_cycle, jobs.result = life_cycle, result
    generation = workflow_generation()
    lifecycle_run_watch.note_submitted(workspace, job_id=42, run_id=9, now=0.0)

    with caplog.at_level(logging.INFO):
        lifecycle_run_watch.observe(now=1.0, executor=_InlineExecutor())

    assert workflow_generation() == generation + 1
    [event] = _events(caplog, "lifecycle_sync_completed")
    assert event.mip_extras["result_state"] == (result.value if result else None)  # type: ignore[attr-defined]


def test_a_get_run_error_keeps_the_run_pending_without_a_bump(caplog: pytest.LogCaptureFixture) -> None:
    workspace, jobs = _workspace()
    jobs.error = RuntimeError("401 from https://adb-1.example/api/2.1/jobs/runs/get?token=abc")
    generation = workflow_generation()
    lifecycle_run_watch.note_submitted(workspace, job_id=42, run_id=7, now=0.0)

    with caplog.at_level(logging.WARNING):
        lifecycle_run_watch.observe(now=1.0, executor=_InlineExecutor())

    assert workflow_generation() == generation
    assert lifecycle_run_watch.pending_run_ids() == [7]
    [event] = _events(caplog, "lifecycle_job_watch_error")
    assert event.mip_extras == {"exc_type": "RuntimeError"}  # type: ignore[attr-defined]
    assert "adb-1" not in caplog.text and "token" not in caplog.text

    jobs.error = None
    jobs.life_cycle = RunLifeCycleState.TERMINATED
    lifecycle_run_watch.observe(now=16.0, executor=_InlineExecutor())
    assert workflow_generation() == generation + 1, "the next check still sees it finish"


def test_a_run_pending_for_an_hour_expires_without_a_bump(caplog: pytest.LogCaptureFixture) -> None:
    workspace, jobs = _workspace()
    generation = workflow_generation()
    lifecycle_run_watch.note_submitted(workspace, job_id=42, run_id=7, now=0.0)

    with caplog.at_level(logging.WARNING):
        lifecycle_run_watch.observe(now=3600.0, executor=_InlineExecutor())

    assert jobs.calls == []
    assert workflow_generation() == generation
    assert lifecycle_run_watch.pending_run_ids() == []
    [event] = _events(caplog, "lifecycle_job_watch_expired")
    assert event.mip_extras == {"job_id": 42, "run_id": 7}  # type: ignore[attr-defined]


def test_an_executor_refusing_work_is_swallowed_and_frees_the_next_check() -> None:
    workspace, jobs = _workspace()
    lifecycle_run_watch.note_submitted(workspace, job_id=42, run_id=7, now=0.0)

    lifecycle_run_watch.observe(now=1.0, executor=_RefusingExecutor())
    lifecycle_run_watch.observe(now=16.0, executor=_InlineExecutor())

    assert jobs.calls == [7]


def test_one_check_is_in_flight_at_a_time() -> None:
    workspace, jobs = _workspace()
    deferred = _DeferredExecutor()
    lifecycle_run_watch.note_submitted(workspace, job_id=42, run_id=7, now=0.0)

    lifecycle_run_watch.observe(now=1.0, executor=deferred)
    lifecycle_run_watch.observe(now=30.0, executor=deferred)
    assert len(deferred.jobs) == 1

    deferred.run_all()
    lifecycle_run_watch.observe(now=31.0, executor=deferred)
    assert len(deferred.jobs) == 1 and jobs.calls == [7]


def test_the_newest_eight_runs_are_kept() -> None:
    workspace, _jobs = _workspace()
    for run_id in range(1, 10):
        lifecycle_run_watch.note_submitted(workspace, job_id=42, run_id=run_id, now=float(run_id))
    assert lifecycle_run_watch.pending_run_ids() == list(range(2, 10))


def test_building_a_workflow_key_is_what_observes() -> None:
    workspace, jobs = _workspace()
    jobs.life_cycle = RunLifeCycleState.TERMINATED
    clock = [0.0]
    inline = _InlineExecutor()
    lifecycle_run_watch._reset_for_tests(now=lambda: clock[0], executor=inline)
    generation = workflow_generation()
    lifecycle_run_watch.note_submitted(workspace, job_id=42, run_id=7)

    workflow_key("watch.test.counts")

    assert jobs.calls == [7]
    assert workflow_generation() == generation + 1


def test_a_job_mode_trigger_registers_its_run(monkeypatch: pytest.MonkeyPatch) -> None:
    workspace, jobs = _workspace()
    jobs.run_now = lambda job_id: SimpleNamespace(run_id=4242)  # type: ignore[attr-defined]
    sdk = types.ModuleType("databricks.sdk")
    sdk.WorkspaceClient = lambda: workspace  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "databricks.sdk", sdk)
    monkeypatch.setenv("MIP_LIFECYCLE_SYNC_MODE", "job")
    monkeypatch.setenv("MIP_LIFECYCLE_SYNC_JOB_ID", "42")
    job_trigger._reset_for_tests()
    try:
        job_trigger.trigger_lifecycle_sync(reason="test")
        assert lifecycle_run_watch.pending_run_ids() == [4242]
        lifecycle_run_watch.observe(now=None, executor=_InlineExecutor())
        assert jobs.calls == [4242]
    finally:
        job_trigger._reset_for_tests()
    assert lifecycle_run_watch.pending_run_ids() == [], "the trigger reset forgets the watch too"


# --- the foreign arm (W5c w5-field-vitals, delivery-06 remainder) ----------------

_BASELINE_MS = 1_760_000_000_000


class _ForeignJobs:
    """``WorkspaceClient.jobs`` answering ``list_runs`` (and ``get_run`` for an own run)."""

    def __init__(self) -> None:
        self.runs: list[BaseRun] = []
        self.error: BaseException | None = None
        self.list_calls: list[dict[str, Any]] = []
        self.get_calls: list[int] = []

    def list_runs(self, **kwargs: Any) -> Iterator[BaseRun]:
        self.list_calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return iter(self.runs)

    def get_run(self, run_id: int) -> Run:
        self.get_calls.append(run_id)
        return Run(run_id=run_id, state=RunState(life_cycle_state=RunLifeCycleState.TERMINATED))


def _ended(run_id: int, end_ms: int, result: RunResultState = RunResultState.SUCCESS) -> BaseRun:
    return BaseRun(
        run_id=run_id,
        end_time=end_ms,
        state=RunState(life_cycle_state=RunLifeCycleState.TERMINATED, result_state=result),
    )


@pytest.fixture
def foreign(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[_ForeignJobs, list[int]]]:
    jobs = _ForeignJobs()
    builds: list[int] = []

    def _factory() -> Any:
        builds.append(1)
        return SimpleNamespace(jobs=jobs)

    monkeypatch.setenv("MIP_LIFECYCLE_SYNC_JOB_ID", "42")
    lifecycle_run_watch._reset_for_tests(workspace_factory=_factory)
    lifecycle_run_watch.enable_foreign_run_watch(now_ms=_BASELINE_MS)
    yield jobs, builds


def test_a_foreign_run_after_the_baseline_bumps_once(
    foreign: tuple[_ForeignJobs, list[int]], caplog: pytest.LogCaptureFixture
) -> None:
    jobs, builds = foreign
    jobs.runs = [_ended(9001, _BASELINE_MS + 5_000, RunResultState.FAILED), _ended(9000, _BASELINE_MS + 1_000)]
    generation = workflow_generation()

    with caplog.at_level(logging.INFO):
        lifecycle_run_watch.observe(now=100.0, executor=_InlineExecutor())

    assert workflow_generation() == generation + 1, "one bump for the newer runs"
    assert jobs.list_calls == [{"job_id": 42, "completed_only": True, "limit": 5}]
    [event] = _events(caplog, "lifecycle_sync_completed")
    assert event.mip_extras == {  # type: ignore[attr-defined]
        "mode": "job_foreign", "job_id": 42, "run_id": 9001, "result_state": "FAILED",
    }

    # The baseline moved to the newest end time: the same page bumps nothing.
    lifecycle_run_watch.observe(now=200.0, executor=_InlineExecutor())
    assert len(jobs.list_calls) == 2 and workflow_generation() == generation + 1
    assert builds == [1], "the client is built once and cached"


def test_a_run_that_ended_before_the_baseline_does_not_bump(foreign: tuple[_ForeignJobs, list[int]]) -> None:
    jobs, _ = foreign
    jobs.runs = [_ended(9000, _BASELINE_MS - 1), _ended(8999, _BASELINE_MS)]
    generation = workflow_generation()

    lifecycle_run_watch.observe(now=100.0, executor=_InlineExecutor())

    assert len(jobs.list_calls) == 1
    assert workflow_generation() == generation


def test_an_own_run_is_bumped_once_never_twice(foreign: tuple[_ForeignJobs, list[int]]) -> None:
    jobs, _ = foreign
    own = SimpleNamespace(jobs=jobs)
    lifecycle_run_watch.note_submitted(own, job_id=42, run_id=7, now=0.0)
    jobs.runs = [_ended(7, _BASELINE_MS + 2_000)]
    generation = workflow_generation()

    lifecycle_run_watch.observe(now=100.0, executor=_InlineExecutor())

    assert jobs.get_calls == [7], "the pending arm saw it finish"
    assert len(jobs.list_calls) == 1, "and the foreign arm listed it"
    assert workflow_generation() == generation + 1, "one bump, not two"


def test_two_observes_within_a_minute_make_one_list_runs(foreign: tuple[_ForeignJobs, list[int]]) -> None:
    jobs, _ = foreign
    inline = _InlineExecutor()

    lifecycle_run_watch.observe(now=100.0, executor=inline)
    lifecycle_run_watch.observe(now=159.9, executor=inline)
    assert len(jobs.list_calls) == 1

    lifecycle_run_watch.observe(now=160.0, executor=inline)
    assert len(jobs.list_calls) == 2


def test_a_foreign_check_in_flight_is_never_doubled(foreign: tuple[_ForeignJobs, list[int]]) -> None:
    jobs, _ = foreign
    deferred = _DeferredExecutor()

    lifecycle_run_watch.observe(now=100.0, executor=deferred)
    lifecycle_run_watch.observe(now=500.0, executor=deferred)
    assert len(deferred.jobs) == 1

    deferred.run_all()
    lifecycle_run_watch.observe(now=600.0, executor=deferred)
    assert len(deferred.jobs) == 1 and len(jobs.list_calls) == 1


def test_list_runs_raising_warns_without_a_bump_and_keeps_the_baseline(
    foreign: tuple[_ForeignJobs, list[int]], caplog: pytest.LogCaptureFixture
) -> None:
    jobs, _ = foreign
    jobs.error = RuntimeError("403 from https://adb-1.example/api/2.2/jobs/runs/list?token=abc")
    jobs.runs = [_ended(9000, _BASELINE_MS + 1_000)]
    generation = workflow_generation()

    with caplog.at_level(logging.WARNING):
        lifecycle_run_watch.observe(now=100.0, executor=_InlineExecutor())

    assert workflow_generation() == generation
    [event] = _events(caplog, "lifecycle_job_watch_error")
    assert event.mip_extras == {"exc_type": "RuntimeError"}  # type: ignore[attr-defined]
    assert "adb-1" not in caplog.text and "token" not in caplog.text

    jobs.error = None
    lifecycle_run_watch.observe(now=160.0, executor=_InlineExecutor())
    assert workflow_generation() == generation + 1, "the unchanged baseline still sees the run"


def test_unbound_means_no_observer_and_no_jobs_call(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    calls: list[str] = []

    def _factory() -> Any:
        calls.append("built")
        return SimpleNamespace(jobs=_ForeignJobs())

    monkeypatch.delenv("MIP_LIFECYCLE_SYNC_JOB_ID", raising=False)
    lifecycle_run_watch._reset_for_tests(workspace_factory=_factory)
    with caplog.at_level(logging.INFO):
        lifecycle_run_watch.enable_foreign_run_watch(now_ms=_BASELINE_MS)

    assert [r.mip_event for r in caplog.records if hasattr(r, "mip_event")].count("lifecycle_foreign_watch_unbound") == 1
    lifecycle_run_watch.observe(now=100.0, executor=_InlineExecutor())
    workflow_key("watch.test.unbound")
    assert calls == []
    assert lifecycle_run_watch.observe not in _registered_observers()


def _registered_observers() -> list[Callable[[], None]]:
    from backend.services import gold_cache

    with gold_cache._GENERATION_OBSERVERS_LOCK:
        return list(gold_cache._GENERATION_OBSERVERS)

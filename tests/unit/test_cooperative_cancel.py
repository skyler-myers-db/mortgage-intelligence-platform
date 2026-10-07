"""The owner's stop crosses the resilience layers as a stop (W5c genie-03).

``CooperativeCancel`` (``GenieTurnCancelled``) must never look like a
dependency failure: inside ``Resilient`` it is not retried, not wrapped as
``DependencyDownError`` and leaves the breaker's counters alone (a half-open
probe slot goes back); ``timed_dependency`` ends it as ``cancelled`` at INFO
and never counts it as a health error; the Genie client's poll loop stops on
it only under a true cancel scope, and the scope never leaks into the next
task a pooled thread runs.
"""

from __future__ import annotations

import io
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest

from backend.services import genie_client
from backend.services import observability as obs
from backend.services.cooperative_cancel import CooperativeCancel
from backend.services.genie_client import GenieClient
from backend.services.genie_completion_stages import (
    GenieJobStage,
    GenieTurnCancelled,
    cancel_probe,
    cancel_scope,
    cooperative_cancel_point,
    report_stage,
    stage_sink,
)
from backend.services.resilience import DependencyDownError, Resilient, with_retry
from backend.services.resilience_breaker import CircuitBreaker


def _capture(logger: logging.Logger) -> io.StringIO:
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(obs.StructuredFormatter())
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    return buf


def _events(buf: io.StringIO) -> list[dict[str, Any]]:
    return [json.loads(line) for line in buf.getvalue().splitlines() if line]


def test_the_stop_is_a_base_exception_and_never_an_exception() -> None:
    assert issubclass(GenieTurnCancelled, CooperativeCancel)
    assert issubclass(CooperativeCancel, BaseException)
    assert not issubclass(CooperativeCancel, Exception)


# ------------------------------------------------------------- resilience


def test_a_cancel_inside_resilient_is_not_retried_wrapped_or_counted_by_the_breaker() -> None:
    breaker = CircuitBreaker("genie-test", failure_threshold=3, cooldown_s=20.0)
    breaker.record_failure()
    breaker.record_failure()
    resilient = Resilient[Any](
        breaker=breaker,
        dependency_name="genie",
        attempts=3,
        backoff_base=0.0,
        retry_on=(BaseException,),  # even a caller that retries everything
    )
    calls: list[int] = []

    def stopped() -> Any:
        calls.append(1)
        raise GenieTurnCancelled()

    with pytest.raises(GenieTurnCancelled):
        resilient.call(stopped)

    assert calls == [1], "never retried"
    assert breaker.state == CircuitBreaker.CLOSED
    assert breaker._failure_count == 2, "no failure recorded, and no success reset either"


def test_a_cancel_never_surfaces_as_dependency_down() -> None:
    resilient = Resilient[Any](breaker=CircuitBreaker("genie-test-2"), dependency_name="genie", attempts=1)

    with pytest.raises(BaseException) as raised:
        resilient.call(lambda: (_ for _ in ()).throw(GenieTurnCancelled()))

    assert type(raised.value) is GenieTurnCancelled
    assert not isinstance(raised.value, DependencyDownError)


def test_a_cancelled_half_open_probe_returns_its_slot_without_a_transition() -> None:
    clock = [0.0]
    breaker = CircuitBreaker("genie-test-3", failure_threshold=1, cooldown_s=5.0, now=lambda: clock[0])
    breaker.record_failure()
    clock[0] = 10.0
    assert breaker.state == CircuitBreaker.HALF_OPEN
    resilient = Resilient[Any](breaker=breaker, dependency_name="genie", attempts=1)

    with pytest.raises(GenieTurnCancelled):
        resilient.call(lambda: (_ for _ in ()).throw(GenieTurnCancelled()))

    assert breaker.state == CircuitBreaker.HALF_OPEN
    assert breaker.allow() is True, "the probe slot went back; a stranded slot would refuse forever"


def _opens_and_half_opens(name: str) -> tuple[CircuitBreaker, list[float]]:
    clock = [0.0]
    return CircuitBreaker(name, failure_threshold=1, cooldown_s=5.0, now=lambda: clock[0]), clock


def test_a_cancelled_call_admitted_closed_gives_back_no_probe_slot() -> None:
    breaker, clock = _opens_and_half_opens("genie-test-closed-cancel")
    resilient = Resilient[Any](breaker=breaker, dependency_name="genie", attempts=1)

    def admitted_closed_then_stopped() -> Any:
        # While this call runs (admitted CLOSED: no probe slot), the
        # dependency fails elsewhere, the cool-down elapses and another
        # caller takes the one half-open probe slot.
        breaker.record_failure()
        clock[0] = 10.0
        assert breaker.allow() is True
        raise GenieTurnCancelled()

    with pytest.raises(GenieTurnCancelled):
        resilient.call(admitted_closed_then_stopped)

    assert breaker.state == CircuitBreaker.HALF_OPEN
    assert breaker.allow() is False, "the other caller still holds the only probe slot"


def test_a_probe_from_an_earlier_half_open_period_never_returns_a_newer_slot() -> None:
    breaker, clock = _opens_and_half_opens("genie-test-stale-probe")
    breaker.record_failure()
    clock[0] = 10.0
    resilient = Resilient[Any](breaker=breaker, dependency_name="genie", attempts=1)

    def stale_probe_then_stopped() -> Any:
        # This call holds the first half-open period's probe. A late failure
        # re-opens the breaker, the cool-down elapses again and another
        # caller takes the second period's only slot.
        breaker.record_failure()
        clock[0] = 20.0
        assert breaker.allow() is True
        raise GenieTurnCancelled()

    with pytest.raises(GenieTurnCancelled):
        resilient.call(stale_probe_then_stopped)

    assert breaker.state == CircuitBreaker.HALF_OPEN
    assert breaker.allow() is False, "the second period's probe is still in flight"


def test_with_retry_passes_a_cancel_through_on_the_first_attempt() -> None:
    calls: list[int] = []

    def stopped() -> None:
        calls.append(1)
        raise GenieTurnCancelled()

    with pytest.raises(GenieTurnCancelled):
        with_retry(stopped, attempts=3, retry_on=(BaseException,), sleep=lambda _s: None)

    assert calls == [1]


# ------------------------------------------------------------ observability


def test_timed_dependency_ends_a_cancel_as_cancelled_at_info_and_counts_no_error() -> None:
    log = logging.getLogger("test.coop.cancel")
    buf = _capture(log)
    obs._reset_counters_for_tests()
    try:
        with pytest.raises(GenieTurnCancelled), obs.timed_dependency("genie", "call", logger=log):
            raise GenieTurnCancelled()
    finally:
        log.handlers.clear()

    start, end = _events(buf)
    assert (start["event"], end["event"]) == ("dependency_call_start", "dependency_call_end")
    assert (end["outcome"], end["level"]) == ("cancelled", "INFO")
    assert "exc_type" not in end and "exc_msg" not in end
    assert obs.recent_error_count() == 0


# ------------------------------------------------------- the Genie poll loop


def _client(monkeypatch: pytest.MonkeyPatch, states: list[str]) -> tuple[GenieClient, list[float]]:
    client = GenieClient(host="https://example.cloud.databricks.com", token="t", space_id="space-1")
    script = iter(states)
    sleeps: list[float] = []
    monkeypatch.setattr(client, "_get", lambda url, *, timeout_s=None: {"status": next(script)})
    monkeypatch.setattr(genie_client.time, "sleep", sleeps.append)
    return client, sleeps


@pytest.mark.parametrize("scope", [None, lambda: False], ids=["no_scope", "false_scope"])
def test_the_poll_loop_runs_on_without_a_true_scope(monkeypatch: pytest.MonkeyPatch, scope: Any) -> None:
    client, sleeps = _client(monkeypatch, ["IN_PROGRESS", "EXECUTING_QUERY", "COMPLETED"])

    with cancel_scope(scope):
        body = client._poll_message("conv-1", "msg-1", timeout_s=600)

    assert body["status"] == "COMPLETED"
    assert len(sleeps) == 2


def test_the_poll_loop_stops_before_its_next_sleep_under_a_true_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    client, sleeps = _client(monkeypatch, ["IN_PROGRESS", "COMPLETED"])

    with cancel_scope(lambda: True), pytest.raises(GenieTurnCancelled):
        client._poll_message("conv-1", "msg-1", timeout_s=600)

    assert sleeps == [], "stopped before sleeping, never after another poll"


def test_the_owner_threads_sink_predicate_stops_its_own_polls(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _sleeps = _client(monkeypatch, ["IN_PROGRESS", "COMPLETED"])

    with stage_sink(lambda *_: None, cancelled=lambda: True), pytest.raises(GenieTurnCancelled):
        client._poll_message("conv-1", "msg-1", timeout_s=600)


def test_ask_logs_a_stopped_call_as_cancelled_never_as_a_query_error(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _sleeps = _client(monkeypatch, ["IN_PROGRESS"])
    monkeypatch.setattr(client, "_start_conversation", lambda question: ("conv-1", "msg-1"))
    buf = _capture(genie_client.log)
    try:
        with cancel_scope(lambda: True), pytest.raises(GenieTurnCancelled):
            client.ask("How many borrowers are in the money?")
    finally:
        genie_client.log.handlers.clear()
        genie_client.log.propagate = True

    events = {event["event"]: event for event in _events(buf)}
    assert "genie_query_error" not in events
    end = events["genie_query_end"]
    assert (end["outcome"], end["level"], end["operation"]) == ("cancelled", "INFO", "ask")
    assert "How many" not in buf.getvalue()


# ----------------------------------------------------------- the scope itself


def test_a_non_owner_report_stage_is_a_cancel_point_and_reports_nothing() -> None:
    reported: list[GenieJobStage] = []
    with stage_sink(lambda stage, *_: reported.append(stage), cancelled=lambda: True):
        probe = cancel_probe()
    assert probe is not None and probe() is True
    errors: list[BaseException] = []

    def sub_turn() -> None:
        try:
            with cancel_scope(probe):
                report_stage(GenieJobStage.VERIFYING)
        except BaseException as exc:  # noqa: BLE001 - recorded for the assertion
            errors.append(exc)

    worker = threading.Thread(target=sub_turn)
    worker.start()
    worker.join(5)

    assert [type(error) for error in errors] == [GenieTurnCancelled]
    assert reported == []
    report_stage(GenieJobStage.VERIFYING)  # no runner, no scope: a no-op
    cooperative_cancel_point()


def test_the_scope_never_leaks_into_the_next_task_on_the_same_pool_thread() -> None:
    def scoped() -> int:
        with cancel_scope(lambda: True):
            try:
                cooperative_cancel_point()
            except GenieTurnCancelled:
                return threading.get_ident()
        raise AssertionError("the scope did not stop the task")

    def next_task() -> int:
        cooperative_cancel_point()  # must not raise: nothing scopes this task
        return threading.get_ident()

    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(scoped).result(5)
        second = pool.submit(next_task).result(5)

    assert first == second, "the same pool thread ran both"

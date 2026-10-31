"""The process-local last-Genie-turn record: classification, recording, sanitization.

2026-10-28: every live Genie turn on dev ended FAILED with
``BUDGET_EXCEEDED_EXCEPTION`` while health said ``genie: up``. The client now
records each real turn's outcome (closed class + UTC time only) where it
already handles the result; health reads the record and never runs a turn.
"""

from __future__ import annotations

import io
import json
import urllib.error
from datetime import UTC, datetime, timedelta
from typing import Any, get_args

import pytest

from backend.schemas.health import GenieTurnErrorClass
from backend.services import genie_turn_outcome as outcome
from backend.services.cooperative_cancel import CooperativeCancel
from backend.services.genie_client import GenieClient, GenieClientError

# The envelope the dev workspace returned on every turn from 2026-10-26T22:29Z.
_LIVE_BUDGET_ENVELOPE = {
    "error": "RESOURCE_EXHAUSTED: You've reached your budget.",
    "type": "BUDGET_EXCEEDED_EXCEPTION",
}
_SECRET_QUESTION = "Which borrowers named Jordan Avery at 12 Elm St owe the most?"
_SECRET_SQL = "SELECT owner_name, ssn FROM mip.gold.leads WHERE owner_name = 'Jordan Avery'"


@pytest.fixture(autouse=True)
def _fresh_record() -> None:
    outcome._reset_genie_turn_outcome_for_tests()


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        # The live failure, and the same failure with only the free text.
        ({"state": "FAILED", "error_type": "BUDGET_EXCEEDED_EXCEPTION",
          "detail": _LIVE_BUDGET_ENVELOPE["error"]}, "BUDGET_EXCEEDED"),
        ({"state": "FAILED", "detail": "RESOURCE_EXHAUSTED: You've reached your budget."},
         "BUDGET_EXCEEDED"),
        ({"status_code": 429, "detail": json.dumps(
            {"error_code": "RESOURCE_EXHAUSTED", "message": "You've reached your budget."})},
         "BUDGET_EXCEEDED"),
        ({"status_code": 429, "detail": json.dumps({"error_code": "BUDGET_EXCEEDED"})},
         "BUDGET_EXCEEDED"),
        ({"state": "FAILED", "detail": "RESOURCE_EXHAUSTED: monthly quota exhausted"},
         "QUOTA_EXCEEDED"),
        ({"status_code": 403, "detail": json.dumps({"error_code": "QUOTA_EXCEEDED"})},
         "QUOTA_EXCEEDED"),
        ({"status_code": 429, "detail": "Too many requests"}, "RATE_LIMITED"),
        ({"state": "FAILED", "error_type": "RATE_LIMIT_EXCEEDED_GENERIC_EXCEPTION"},
         "RATE_LIMITED"),
        ({"state": "FAILED", "detail": "RESOURCE_EXHAUSTED: slow down"}, "RATE_LIMITED"),
        ({"state": "TIMEOUT"}, "TIMEOUT"),
        ({"state": "FAILED", "error_type": "REPLY_PROCESS_TIMEOUT_EXCEPTION"}, "TIMEOUT"),
        ({"status_code": 504}, "TIMEOUT"),
        ({"status_code": 401}, "AUTH"),
        ({"status_code": 403, "detail": "forbidden"}, "AUTH"),
        ({"status_code": 404}, "NOT_FOUND"),
        ({"status_code": 503}, "UPSTREAM_ERROR"),
        ({"state": "CANCELLED"}, "CANCELED"),
        ({"state": "QUERY_RESULT_EXPIRED"}, "EXPIRED"),
        ({"status_code": 400}, "FAILED"),
        ({"state": "FAILED", "error_type": "SQL_EXECUTION_EXCEPTION"}, "FAILED"),
        ({}, "UNKNOWN"),
    ],
)
def test_classifier_maps_upstream_failures_onto_the_closed_set(
    kwargs: dict[str, Any], expected: str
) -> None:
    assert outcome.classify_genie_failure(**kwargs) == expected


@pytest.mark.parametrize(
    "kwargs",
    [
        # A SQL error that quotes a budget/quota column is not a spend wall.
        {"state": "FAILED", "error_type": "SQL_EXECUTION_EXCEPTION",
         "detail": "Column marketing_budget is not a quota_bucket"},
        # A leading free-text token is not a structured budget code.
        {"state": "FAILED", "detail": "BUDGET_COLUMN_MISSING: budget not found"},
        # A punctuated free-text "type" is not a code.
        {"state": "FAILED", "error_type": "budget exceeded!", "detail": "you hit the budget"},
    ],
)
def test_budget_wording_without_a_budget_code_is_not_a_budget_wall(
    kwargs: dict[str, Any],
) -> None:
    assert outcome.classify_genie_failure(**kwargs) == "FAILED"


def test_transport_failures_classify_by_the_cause_chain() -> None:
    assert outcome.classify_transport_failure(TimeoutError("read")) == "TIMEOUT"
    assert outcome.classify_transport_failure(urllib.error.URLError(TimeoutError())) == "TIMEOUT"
    assert outcome.classify_transport_failure(urllib.error.URLError("refused")) == "NETWORK"
    wrapped = GenieClientError("Network error")
    wrapped.__cause__ = urllib.error.URLError(TimeoutError())
    assert outcome.classify_transport_failure(wrapped) == "TIMEOUT"


def test_the_service_vocabulary_is_the_schema_vocabulary() -> None:
    assert frozenset(get_args(GenieTurnErrorClass)) == outcome.GENIE_TURN_ERROR_CLASSES
    assert outcome.QUOTA_DEGRADED_CLASSES <= outcome.GENIE_TURN_ERROR_CLASSES
    assert "RATE_LIMITED" not in outcome.QUOTA_DEGRADED_CLASSES


def test_nothing_is_recorded_before_the_first_turn() -> None:
    assert outcome.last_genie_turn() is None
    assert outcome.last_genie_turn_payload() is None
    assert outcome.genie_turn_degraded_reason() is None


def test_success_and_failure_records_carry_a_utc_timestamp_and_the_latest_wins() -> None:
    before = datetime.now(UTC)
    outcome.record_genie_turn_failure(GenieClientError("x", error_class="BUDGET_EXCEEDED"))
    failed = outcome.last_genie_turn_payload()
    assert failed is not None
    assert failed["outcome"] == "failed"
    assert failed["error_class"] == "BUDGET_EXCEEDED"
    at = failed["at"]
    assert isinstance(at, datetime) and at.utcoffset() == timedelta(0)
    assert before <= at <= datetime.now(UTC)

    outcome.record_genie_turn_success()
    assert outcome.last_genie_turn_payload() == {
        "outcome": "success",
        "error_class": None,
        "at": outcome.last_genie_turn().at,  # type: ignore[union-attr]
    }


def test_free_text_never_reaches_the_record() -> None:
    """Question text, SQL and an off-vocabulary class all collapse to a class token."""

    leaky = GenieClientError(
        f"HTTP 400 from Genie API: {_SECRET_QUESTION} {_SECRET_SQL}",
        status_code=400,
        error_class=f"FAILED: {_SECRET_SQL}",
    )
    outcome.record_genie_turn_failure(leaky)
    payload = outcome.last_genie_turn_payload()
    assert payload is not None
    assert payload["error_class"] == "UNKNOWN"
    assert set(payload) == {"outcome", "error_class", "at"}
    dumped = repr(outcome.last_genie_turn()) + repr(payload)
    for fragment in ("Jordan", "Elm", "SELECT", "ssn", "owe the most"):
        assert fragment not in dumped


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (ValueError("bad row 'Jordan Avery'"), "UNKNOWN"),
        (TimeoutError("socket read"), "TIMEOUT"),
        (ConnectionResetError("reset"), "NETWORK"),
        (GenieClientError("no class set"), "UNKNOWN"),
    ],
)
def test_exceptions_without_a_raise_site_class_fall_back_to_a_closed_class(
    exc: BaseException, expected: str
) -> None:
    outcome.record_genie_turn_failure(exc)
    record = outcome.last_genie_turn()
    assert record is not None and record.error_class == expected


def test_recording_never_raises_into_the_callers_handler() -> None:
    class Hostile(Exception):
        @property
        def error_class(self) -> str:
            raise RuntimeError("boom")

    outcome.record_genie_turn_failure(Hostile())  # must not raise


@pytest.mark.parametrize(
    ("error_class", "degrades"),
    [
        ("BUDGET_EXCEEDED", True),
        ("QUOTA_EXCEEDED", True),
        ("RATE_LIMITED", False),
        ("TIMEOUT", False),
        ("FAILED", False),
    ],
)
def test_only_budget_and_quota_failures_degrade(error_class: str, degrades: bool) -> None:
    outcome.record_genie_turn_failure(GenieClientError("x", error_class=error_class))
    assert outcome.genie_turn_degraded_reason() == (error_class if degrades else None)


def test_the_degraded_window_expires_and_a_later_success_clears_it() -> None:
    outcome.record_genie_turn_failure(GenieClientError("x", error_class="BUDGET_EXCEEDED"))
    recorded = outcome.last_genie_turn()
    assert recorded is not None
    edge = recorded.monotonic_at + outcome.QUOTA_DEGRADED_WINDOW_S
    assert outcome.genie_turn_degraded_reason(now_monotonic=edge) == "BUDGET_EXCEEDED"
    assert outcome.genie_turn_degraded_reason(now_monotonic=edge + 1.0) is None

    outcome.record_genie_turn_success()
    assert outcome.genie_turn_degraded_reason() is None


# ---------------------------------------------------------------------------
# The real GenieClient records at the sites that already handle turn results.
# ---------------------------------------------------------------------------


class _Resp(io.BytesIO):
    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def _client(monkeypatch: pytest.MonkeyPatch, responses: list[Any]) -> GenieClient:
    queue = iter(responses)

    def _urlopen(req: Any, timeout: float = 30) -> _Resp:  # noqa: ARG001
        nxt = next(queue)
        if isinstance(nxt, Exception):
            raise nxt
        return _Resp(json.dumps(nxt).encode("utf-8"))

    monkeypatch.setattr("backend.services.genie_client.urllib.request.urlopen", _urlopen)
    monkeypatch.setattr("backend.services.genie_client.time.sleep", lambda _s: None)
    return GenieClient(host="https://ws.example", token="t", space_id="sp-1", timeout_s=2)


def _http_error(status: int, body: str) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "http://ws.example/x", status, "err", None, io.BytesIO(body.encode())  # type: ignore[arg-type]
    )


_START = {"conversation_id": "c-1", "message": {"id": "m-1"}}


@pytest.mark.parametrize(
    "envelope",
    [
        _LIVE_BUDGET_ENVELOPE,
        # Each half alone must still classify: the structured type...
        {"type": "BUDGET_EXCEEDED_EXCEPTION", "error": "You've reached your budget."},
        # ...and the canonical code prefix of the text when the type is absent.
        {"error": "RESOURCE_EXHAUSTED: You've reached your budget."},
    ],
)
def test_ask_records_the_live_budget_failure(
    monkeypatch: pytest.MonkeyPatch, envelope: dict[str, str]
) -> None:
    client = _client(monkeypatch, [_START, {"status": "FAILED", "id": "m-1", "error": envelope}])
    with pytest.raises(GenieClientError) as raised:
        client.ask(_SECRET_QUESTION)
    assert raised.value.error_class == "BUDGET_EXCEEDED"
    assert raised.value.state == "FAILED"
    record = outcome.last_genie_turn()
    assert record is not None and (record.outcome, record.error_class) == ("failed", "BUDGET_EXCEEDED")
    assert outcome.genie_turn_degraded_reason() == "BUDGET_EXCEEDED"


def test_ask_records_a_success(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(
        monkeypatch,
        [_START, {"status": "COMPLETED", "id": "m-1", "attachments": [{"text": {"content": "42"}}]}],
    )
    client.ask("How many borrowers are in the money?")
    record = outcome.last_genie_turn()
    assert record is not None and (record.outcome, record.error_class) == ("success", None)


def test_submit_records_a_turn_that_could_not_start(monkeypatch: pytest.MonkeyPatch) -> None:
    body = json.dumps({"error_code": "RESOURCE_EXHAUSTED", "message": "You've reached your budget."})
    client = _client(monkeypatch, [_http_error(429, body)])
    with pytest.raises(GenieClientError):
        client.submit_message(_SECRET_QUESTION)
    record = outcome.last_genie_turn()
    assert record is not None and record.error_class == "BUDGET_EXCEEDED"


def test_peek_records_terminal_outcomes_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """The async path: the browser never calls complete after a FAILED peek."""

    client = _client(
        monkeypatch,
        [
            {"status": "EXECUTING_QUERY", "id": "m-1"},
            {"status": "FAILED", "id": "m-1", "error": _LIVE_BUDGET_ENVELOPE},
            {"status": "COMPLETED", "id": "m-2"},
        ],
    )
    client.peek_message("c-1", "m-1")
    assert outcome.last_genie_turn() is None
    client.peek_message("c-1", "m-1")
    failed = outcome.last_genie_turn()
    assert failed is not None and failed.error_class == "BUDGET_EXCEEDED"
    client.peek_message("c-1", "m-2")
    succeeded = outcome.last_genie_turn()
    assert succeeded is not None and succeeded.outcome == "success"


def test_resume_records_the_terminal_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, [{"status": "FAILED", "id": "m-1", "error": _LIVE_BUDGET_ENVELOPE}])
    with pytest.raises(GenieClientError):
        client.resume_message("c-1", "m-1")
    record = outcome.last_genie_turn()
    assert record is not None and record.error_class == "BUDGET_EXCEEDED"


def test_ping_and_a_failed_peek_get_record_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, [_http_error(429, "{}"), _http_error(503, "{}")])
    assert client.ping() is False
    with pytest.raises(GenieClientError):
        client.peek_message("c-1", "m-1")
    assert outcome.last_genie_turn() is None


def test_a_cancelled_turn_records_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, [])

    def _stop(_question: str) -> tuple[str, str]:
        raise CooperativeCancel()

    monkeypatch.setattr(client, "_start_conversation", _stop)
    with pytest.raises(CooperativeCancel):
        client.ask("stop me")
    assert outcome.last_genie_turn() is None

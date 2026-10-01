"""A missing table is a definitive answer: one attempt, no breaker failure.

w3-rate-lever review (warehouse follow-on). A roll-forward that adds a gold
table promotes the App before ``mip_refresh_scores`` builds it (#255 did
exactly this with ``gold.rate_sensitivity_book``). Before this fix every read
of the missing table ran three statements with backoff sleeps and then called
``record_failure`` on the process-wide ``warehouse`` breaker: five in a row
opened it and every warehouse-backed route answered 503 ``breaker_open``
against a healthy warehouse; one landing as the half-open probe re-opened it.

Pins, from the classifier up to the route:

* ``TABLE_OR_VIEW_NOT_FOUND`` / SQLSTATE 42P01, ``SCHEMA_NOT_FOUND`` and
  ``UNRESOLVED_ROUTINE`` (error classes only, 2026-09-30) in the message
  classify as ``DatabricksSqlObjectMissingError`` (a ``DatabricksSqlError``
  subclass); a permission refusal still wins; error codes alone, "not found"
  prose, the near-miss SQLSTATEs and the shared SQLSTATEs 42704 / 42883
  without their class stay plain;
* both raise sites (a FAILED statement, an HTTP error body) raise it with the
  statement id and state kept;
* ``with_retry`` gives up on it at once; ``Resilient`` records a breaker
  SUCCESS and raises ``DependencyDownError`` of kind ``retries_exhausted``
  (``retryable`` True, the existing stop-and-surface wire kind) with the typed
  error on ``last_error``; transient and permission behaviour is unchanged;
* the production client is wired with the classification;
* ``GET /api/v1/geo/rate-sensitivity`` through that production client answers
  200 ``built=false`` after one statement for a missing lane table, and a
  constant-detail 503 after one statement per request for any other missing
  table, with the breaker still closed.
"""

from __future__ import annotations

import io
import logging
import urllib.error
import urllib.request
from collections.abc import Iterator
from email.message import Message
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.config.settings import settings
from backend.main import app
from backend.services.databricks_sql import (
    DatabricksSqlClient,
    DatabricksSqlError,
    DatabricksSqlObjectMissingError,
    DatabricksSqlPermissionError,
    _reset_sql_client_for_tests,
    _sql_error_class,
    get_sql_client,
)
from backend.services.repositories.factory import _reset_singletons_for_tests
from backend.services.resilience import (
    CircuitBreaker,
    DependencyDownError,
    Resilient,
    _reset_breakers_for_tests,
    get_breaker,
    with_retry,
)

_MISSING_BOOK = (
    "[TABLE_OR_VIEW_NOT_FOUND] The table or view `mip`.`gold`.`rate_sensitivity_book` cannot be "
    "found. Verify the spelling and correctness of the schema and catalog. SQLSTATE: 42P01"
)
_MISSING_B360 = (
    "[TABLE_OR_VIEW_NOT_FOUND] The table or view `mip`.`gold`.`borrower_360` cannot be found. "
    "SQLSTATE: 42P01"
)
_REFUSAL = "[INSUFFICIENT_PERMISSIONS] User does not have SELECT on Table 'mip.gold.x'. SQLSTATE: 42501"
# The documented formats of the two classes added 2026-09-30 (delivery-06).
_MISSING_SCHEMA = (
    "[SCHEMA_NOT_FOUND] The schema `mip`.`gold` cannot be found. Verify the spelling and "
    "correctness of the schema and catalog. SQLSTATE: 42704"
)
_MISSING_ROUTINE = (
    "[UNRESOLVED_ROUTINE] Cannot resolve routine `mip`.`gold`.`fn_lead_score` on search path "
    "[`system`.`builtin`, `system`.`session`, `mip`.`gold`]. SQLSTATE: 42883"
)


class _Calls:
    def __init__(self, error: BaseException | None = None) -> None:
        self.error = error
        self.count = 0

    def __call__(self) -> str:
        self.count += 1
        if self.error is not None:
            raise self.error
        return "ok"


def _resilient(breaker: CircuitBreaker, *, object_missing: bool = True) -> Resilient[Any]:
    """The production warehouse wrapper's shape, with zero backoff."""
    return Resilient[Any](
        breaker=breaker,
        dependency_name="warehouse",
        attempts=3,
        backoff_base=0.0,
        backoff_max=0.0,
        retry_on=(DatabricksSqlError, OSError),
        permission_denied_on=(DatabricksSqlPermissionError,),
        object_missing_on=(DatabricksSqlObjectMissingError,) if object_missing else (),
    )


# ---------------------------------------------------------------------------
# The classifier.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("message", "error_code", "expected"),
    [
        (_MISSING_BOOK, None, DatabricksSqlObjectMissingError),
        (_MISSING_BOOK, "BAD_REQUEST", DatabricksSqlObjectMissingError),
        ("[TABLE_OR_VIEW_NOT_FOUND] `mip`.`gold`.`x` cannot be found.", None, DatabricksSqlObjectMissingError),
        ("Relation does not exist. SQLSTATE: 42P01", None, DatabricksSqlObjectMissingError),
        ("Relation does not exist. SQLSTATE 42P01", None, DatabricksSqlObjectMissingError),
        # A refusal wins when a message carries both, and so does its code.
        (f"{_REFUSAL} {_MISSING_BOOK}", None, DatabricksSqlPermissionError),
        (_MISSING_BOOK, "PERMISSION_DENIED", DatabricksSqlPermissionError),
        # A missing schema or routine: their error classes (2026-09-30).
        (_MISSING_SCHEMA, None, DatabricksSqlObjectMissingError),
        (_MISSING_ROUTINE, None, DatabricksSqlObjectMissingError),
        # Controls: near-miss SQLSTATE, empty, an error code alone, prose, and
        # the SQLSTATEs other error classes share, without the class.
        ("SQLSTATE: 42P010", None, DatabricksSqlError),
        ("[TABLE_OR_VIEW_NOT_FOUND_IN_CACHE] x", None, DatabricksSqlError),
        ("[SCHEMA_NOT_FOUND_ANYWHERE] x", None, DatabricksSqlError),
        (None, None, DatabricksSqlError),
        ("the table was not found", "NOT_FOUND", DatabricksSqlError),
        ("[ROUTINE_ALREADY_EXISTS] x. SQLSTATE: 42723", None, DatabricksSqlError),
        ("[DATATYPE_MISMATCH] Some other failure. SQLSTATE: 42704", None, DatabricksSqlError),
        ("[WRONG_NUM_ARGS] Some other failure. SQLSTATE: 42883", None, DatabricksSqlError),
    ],
)
def test_the_classifier_owns_the_missing_table_marker(
    message: str | None, error_code: str | None, expected: type[DatabricksSqlError]
) -> None:
    assert _sql_error_class(message, error_code) is expected


def test_the_missing_table_class_is_a_plain_sql_error_subclass() -> None:
    assert issubclass(DatabricksSqlObjectMissingError, DatabricksSqlError)
    assert not issubclass(DatabricksSqlObjectMissingError, DatabricksSqlPermissionError)


def test_a_failed_statement_raises_the_typed_error_with_its_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    client = DatabricksSqlClient("https://workspace.example", "test-token", "warehouse-id", timeout_s=50)

    def fake_post(url: str, body: dict[str, Any]) -> dict[str, Any]:
        return {
            "statement_id": "stmt-7",
            "status": {"state": "FAILED", "error": {"error_code": "BAD_REQUEST", "message": _MISSING_BOOK}},
        }

    monkeypatch.setattr(client, "_post", fake_post)
    with pytest.raises(DatabricksSqlObjectMissingError) as raised:
        client.execute("SELECT 1")
    assert raised.value.state == "FAILED" and raised.value.statement_id == "stmt-7"


def test_an_http_error_body_raises_the_typed_error(monkeypatch: pytest.MonkeyPatch) -> None:
    client = DatabricksSqlClient("https://workspace.example", "test-token", "warehouse-id", timeout_s=50)

    def fake_urlopen(request: urllib.request.Request, timeout: float) -> Any:
        raise urllib.error.HTTPError(
            request.full_url, 404, "Not Found", Message(), io.BytesIO(_MISSING_BOOK.encode("utf-8"))
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(DatabricksSqlObjectMissingError, match="HTTP 404"):
        client.execute("SELECT 1")


# ---------------------------------------------------------------------------
# Retry and breaker.
# ---------------------------------------------------------------------------


def test_with_retry_makes_one_call_and_no_sleeps() -> None:
    calls = _Calls(DatabricksSqlObjectMissingError(_MISSING_BOOK))
    sleeps: list[float] = []
    with pytest.raises(DatabricksSqlObjectMissingError):
        with_retry(
            calls,
            attempts=3,
            retry_on=(DatabricksSqlError,),
            give_up_on=(DatabricksSqlObjectMissingError,),
            sleep=sleeps.append,
        )
    assert calls.count == 1 and sleeps == []


def test_repeated_not_founds_never_open_the_breaker() -> None:
    breaker = CircuitBreaker("warehouse-test", failure_threshold=2)
    resilient = _resilient(breaker)
    missing = _Calls(DatabricksSqlObjectMissingError(_MISSING_BOOK))

    for attempt in range(1, 11):
        with pytest.raises(DependencyDownError) as raised:
            resilient.call(missing)
        assert missing.count == attempt, "one attempt per call"
        error = raised.value
        assert error.kind == DependencyDownError.KIND_RETRIES_EXHAUSTED
        assert error.retryable is True
        assert isinstance(error.last_error, DatabricksSqlObjectMissingError)
        assert breaker.state == CircuitBreaker.CLOSED
    assert resilient.call(_Calls()) == "ok"


def test_a_not_found_half_open_probe_closes_the_breaker() -> None:
    clock = [0.0]
    breaker = CircuitBreaker("warehouse-test", failure_threshold=1, cooldown_s=5.0, now=lambda: clock[0])
    resilient = _resilient(breaker)
    with pytest.raises(DependencyDownError):
        resilient.call(_Calls(DatabricksSqlError("HTTP 503 from Databricks SQL API: busy")))
    assert breaker.state == CircuitBreaker.OPEN

    clock[0] = 6.0
    with pytest.raises(DependencyDownError) as raised:
        resilient.call(_Calls(DatabricksSqlObjectMissingError(_MISSING_BOOK)))

    assert raised.value.kind == DependencyDownError.KIND_RETRIES_EXHAUSTED
    assert breaker.state == CircuitBreaker.CLOSED
    assert resilient.call(_Calls()) == "ok"


def test_transient_failures_keep_three_attempts_and_a_breaker_failure() -> None:
    breaker = CircuitBreaker("warehouse-test", failure_threshold=2)
    resilient = _resilient(breaker)
    flaky = _Calls(DatabricksSqlError("HTTP 503 from Databricks SQL API: busy"))
    for expected_state in (CircuitBreaker.CLOSED, CircuitBreaker.OPEN):
        with pytest.raises(DependencyDownError) as raised:
            resilient.call(flaky)
        assert raised.value.kind == DependencyDownError.KIND_RETRIES_EXHAUSTED
        assert breaker.state == expected_state
    assert flaky.count == 6


def test_permission_refusals_are_unchanged() -> None:
    breaker = CircuitBreaker("warehouse-test", failure_threshold=2)
    refused = _Calls(DatabricksSqlPermissionError(_REFUSAL))
    with pytest.raises(DependencyDownError) as raised:
        _resilient(breaker).call(refused)
    assert refused.count == 1
    assert raised.value.kind == DependencyDownError.KIND_PERMISSION_DENIED
    assert raised.value.retryable is False
    assert breaker.state == CircuitBreaker.CLOSED


def test_the_classification_is_opt_in_per_dependency() -> None:
    """A Resilient without ``object_missing_on`` (Genie, Lakebase) is unchanged."""
    breaker = CircuitBreaker("other-test", failure_threshold=1)
    missing = _Calls(DatabricksSqlObjectMissingError(_MISSING_BOOK))
    with pytest.raises(DependencyDownError):
        _resilient(breaker, object_missing=False).call(missing)
    assert missing.count == 3
    assert breaker.state == CircuitBreaker.OPEN


# ---------------------------------------------------------------------------
# Production wiring, and the route through it.
# ---------------------------------------------------------------------------


class _Warehouse:
    """Fake ``_post`` for the production client: answers every statement FAILED."""

    def __init__(self) -> None:
        self.message = _MISSING_BOOK
        self.statements: list[str] = []

    def post(self, url: str, body: dict[str, Any]) -> dict[str, Any]:
        self.statements.append(body["statement"])
        return {
            "statement_id": f"stmt-{len(self.statements)}",
            "status": {"state": "FAILED", "error": {"error_code": "BAD_REQUEST", "message": self.message}},
        }


def _reset_production_wiring() -> None:
    _reset_sql_client_for_tests()
    _reset_singletons_for_tests()
    _reset_breakers_for_tests()


@pytest.fixture
def warehouse(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Warehouse]:
    monkeypatch.setattr(
        type(settings),
        "require_databricks_creds",
        lambda _self: ("https://workspace.example", lambda: "test-token", "warehouse-id"),
    )
    _reset_production_wiring()
    fake = _Warehouse()
    try:
        client = get_sql_client()
        monkeypatch.setattr(client._client, "_post", fake.post)  # type: ignore[attr-defined]
        yield fake
    finally:
        _reset_production_wiring()


def test_production_client_is_wired_to_fail_fast_on_a_missing_table(warehouse: _Warehouse) -> None:
    client = get_sql_client()
    breaker = client.resilient.breaker  # type: ignore[attr-defined]

    with pytest.raises(DependencyDownError) as raised:
        client.execute("SELECT 1")

    assert warehouse.statements == ["SELECT 1"], "one attempt, no retries"
    assert raised.value.kind == DependencyDownError.KIND_RETRIES_EXHAUSTED
    assert isinstance(raised.value.last_error, DatabricksSqlObjectMissingError)
    assert breaker is get_breaker("warehouse")
    assert breaker.state == CircuitBreaker.CLOSED


@pytest.mark.parametrize(
    "message",
    [
        # Each marker on its own: the error class, then the SQLSTATE.
        "[TABLE_OR_VIEW_NOT_FOUND] The table or view `mip`.`gold`.`rate_sensitivity_book` cannot be found.",
        "Table or view `mip`.`gold`.`rate_sensitivity_rollup` cannot be found. SQLSTATE: 42P01",
    ],
    ids=["error-class", "sqlstate"],
)
def test_route_answers_not_built_after_one_statement_for_a_missing_lane_table(
    warehouse: _Warehouse, message: str
) -> None:
    warehouse.message = message
    response = TestClient(app).get("/api/v1/geo/rate-sensitivity")

    assert response.status_code == 200, response.text
    assert response.json()["built"] is False
    assert len(warehouse.statements) == 1
    assert get_breaker("warehouse").state == CircuitBreaker.CLOSED


def test_route_answers_a_constant_503_for_any_other_missing_table(
    warehouse: _Warehouse, caplog: pytest.LogCaptureFixture
) -> None:
    warehouse.message = _MISSING_B360
    api = TestClient(app)

    with caplog.at_level(logging.WARNING):
        for request_number in range(1, 7):
            response = api.get("/api/v1/geo/rate-sensitivity")
            assert response.status_code == 503
            assert len(warehouse.statements) == request_number, "one statement per request"

    body = response.json()
    assert body["retryable"] is True
    assert body["reason"] == "retries_exhausted"
    assert body["dependency"] == "warehouse"
    assert body["detail"] == "warehouse is temporarily unavailable"
    for leaked in ("borrower_360", "mip.gold", "TABLE_OR_VIEW_NOT_FOUND", "42P01"):
        assert leaked not in response.text
    assert get_breaker("warehouse").state == CircuitBreaker.CLOSED, "six not-founds, threshold five"
    handled = [
        record for record in caplog.records
        if getattr(record, "mip_event", None) == "dependency_down_handled"
    ]
    assert handled
    assert handled[-1].mip_extras["last_error_type"] == "DatabricksSqlObjectMissingError"  # type: ignore[attr-defined]


@pytest.mark.parametrize("message", [_MISSING_SCHEMA, _MISSING_ROUTINE], ids=["schema", "routine"])
def test_a_missing_schema_or_routine_fails_fast_as_retries_exhausted(message: str) -> None:
    """One attempt, no sleeps, a breaker SUCCESS, the unchanged wire kind."""
    sleeps: list[float] = []
    once = _Calls(_sql_error_class(message)(message))
    with pytest.raises(DatabricksSqlObjectMissingError):
        with_retry(
            once,
            attempts=3,
            retry_on=(DatabricksSqlError,),
            give_up_on=(DatabricksSqlObjectMissingError,),
            sleep=sleeps.append,
        )
    assert once.count == 1 and sleeps == []

    breaker = CircuitBreaker("warehouse-test", failure_threshold=1)
    missing = _Calls(_sql_error_class(message)(message))
    for attempt in (1, 2):
        with pytest.raises(DependencyDownError) as raised:
            _resilient(breaker).call(missing)
        assert missing.count == attempt, "one attempt per call"
        assert raised.value.kind == DependencyDownError.KIND_RETRIES_EXHAUSTED
        assert raised.value.retryable is True
        assert isinstance(raised.value.last_error, DatabricksSqlObjectMissingError)
        assert breaker.state == CircuitBreaker.CLOSED, "a definitive answer is a breaker success"


def test_a_missing_gold_schema_stays_a_503_after_one_statement(warehouse: _Warehouse) -> None:
    """Only the Rate Lever's own tables answer built=false; a missing schema is a 503."""
    warehouse.message = _MISSING_SCHEMA

    response = TestClient(app).get("/api/v1/geo/rate-sensitivity")

    assert response.status_code == 503
    assert response.json()["reason"] == "retries_exhausted"
    assert len(warehouse.statements) == 1
    assert get_breaker("warehouse").state == CircuitBreaker.CLOSED

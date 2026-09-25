"""A warehouse permission refusal fails fast and is never reported as warming.

2026-09-25: the Rate Lever's live statement hit ``[INSUFFICIENT_PERMISSIONS]
User does not have USE SCHEMA on Schema 'mip.silver'``. The resilient SQL
client retried it three times, counted it as a breaker failure and answered
503 ``retryable: true`` / "warehouse temporarily unavailable". A missing grant
is a definitive answer from a reachable warehouse. Pins:

* the client raises ``DatabricksSqlPermissionError`` (a ``DatabricksSqlError``
  subclass, so every existing ``except`` still catches it) for the UC error
  class, SQLSTATE 42501, or a FAILED statement's PERMISSION_DENIED code, and a
  plain ``DatabricksSqlError`` for anything else (an expired-token 403, a
  missing table);
* ``Resilient`` never retries it, records a breaker SUCCESS (repeated
  refusals never open the breaker; a half-open probe closes rather than
  stranding its slot) and raises ``DependencyDownError`` of kind
  ``permission_denied`` whose ``retryable`` is False;
* transient failures keep their exact retry / breaker-failure behaviour;
* the 503 body says ``retryable: false`` / ``reason: permission_denied`` with a
  constant detail that names no object;
* the production client is wired with the classification.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.config.settings import settings
from backend.main import app
from backend.services.databricks_sql import (
    DatabricksSqlClient,
    DatabricksSqlError,
    DatabricksSqlPermissionError,
    _reset_sql_client_for_tests,
    _sql_error_class,
    get_sql_client,
)
from backend.services.repositories import get_rate_sensitivity_repository
from backend.services.resilience import CircuitBreaker, DependencyDownError, Resilient, with_retry

_LIVE_REFUSAL = (
    "[INSUFFICIENT_PERMISSIONS] Insufficient privileges: User does not have USE SCHEMA on "
    "Schema 'mip.silver'. SQLSTATE: 42501"
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


def _resilient(breaker: CircuitBreaker) -> Resilient[Any]:
    return Resilient[Any](
        breaker=breaker,
        dependency_name="warehouse",
        attempts=3,
        backoff_base=0.0,
        backoff_max=0.0,
        retry_on=(DatabricksSqlError, OSError),
        permission_denied_on=(DatabricksSqlPermissionError,),
    )


@pytest.mark.parametrize(
    ("message", "error_code", "expected"),
    [
        (_LIVE_REFUSAL, None, DatabricksSqlPermissionError),
        ("User does not have SELECT on Table 'mip.gold.x'. SQLSTATE: 42501", None, DatabricksSqlPermissionError),
        ("[INSUFFICIENT_PERMISSIONS] Insufficient privileges", "BAD_REQUEST", DatabricksSqlPermissionError),
        ("User is not authorized", "PERMISSION_DENIED", DatabricksSqlPermissionError),
        ("[TABLE_OR_VIEW_NOT_FOUND] `mip`.`gold`.`x` cannot be found. SQLSTATE: 42P01", None, DatabricksSqlError),
        ('{"error_code":"403","message":"Invalid Token"}', None, DatabricksSqlError),
        ("Statement timed out while the warehouse started", "DEADLINE_EXCEEDED", DatabricksSqlError),
        ("SQLSTATE: 425010", None, DatabricksSqlError),
        (None, None, DatabricksSqlError),
    ],
)
def test_only_authorization_refusals_classify_as_permission_errors(
    message: str | None, error_code: str | None, expected: type[DatabricksSqlError]
) -> None:
    assert _sql_error_class(message, error_code) is expected
    assert issubclass(DatabricksSqlPermissionError, DatabricksSqlError)


def test_client_raises_the_permission_error_for_a_refused_statement(monkeypatch: pytest.MonkeyPatch) -> None:
    client = DatabricksSqlClient("https://workspace.example", "test-token", "warehouse-id", timeout_s=50)

    def fake_post(url: str, body: dict[str, Any]) -> dict[str, Any]:
        return {
            "statement_id": "stmt-1",
            "status": {"state": "FAILED", "error": {"error_code": "BAD_REQUEST", "message": _LIVE_REFUSAL}},
        }

    monkeypatch.setattr(client, "_post", fake_post)
    with pytest.raises(DatabricksSqlPermissionError) as raised:
        client.execute("SELECT 1")
    assert raised.value.state == "FAILED" and raised.value.statement_id == "stmt-1"


def test_client_keeps_other_failures_plain(monkeypatch: pytest.MonkeyPatch) -> None:
    client = DatabricksSqlClient("https://workspace.example", "test-token", "warehouse-id", timeout_s=50)

    def fake_post(url: str, body: dict[str, Any]) -> dict[str, Any]:
        return {"status": {"state": "FAILED", "error": {"message": "[TABLE_OR_VIEW_NOT_FOUND] x"}}}

    monkeypatch.setattr(client, "_post", fake_post)
    with pytest.raises(DatabricksSqlError) as raised:
        client.execute("SELECT 1")
    assert type(raised.value) is DatabricksSqlError


def test_with_retry_gives_up_on_a_definitive_subclass_of_a_retried_type() -> None:
    calls = _Calls(DatabricksSqlPermissionError(_LIVE_REFUSAL))
    sleeps: list[float] = []
    with pytest.raises(DatabricksSqlPermissionError):
        with_retry(
            calls,
            attempts=3,
            retry_on=(DatabricksSqlError,),
            give_up_on=(DatabricksSqlPermissionError,),
            sleep=sleeps.append,
        )
    assert calls.count == 1 and sleeps == []


def test_permission_refusal_fails_fast_as_a_non_retryable_dependency_error() -> None:
    breaker = CircuitBreaker("warehouse-test", failure_threshold=2)
    calls = _Calls(DatabricksSqlPermissionError(_LIVE_REFUSAL))

    with pytest.raises(DependencyDownError) as raised:
        _resilient(breaker).call(calls)

    assert calls.count == 1, "a permission refusal is never retried"
    error = raised.value
    assert error.kind == DependencyDownError.KIND_PERMISSION_DENIED
    assert error.retryable is False
    assert isinstance(error.last_error, DatabricksSqlPermissionError)
    assert breaker.state == CircuitBreaker.CLOSED


def test_repeated_refusals_never_open_the_breaker_for_everyone_else() -> None:
    breaker = CircuitBreaker("warehouse-test", failure_threshold=2)
    resilient = _resilient(breaker)
    refused = _Calls(DatabricksSqlPermissionError(_LIVE_REFUSAL))
    for _ in range(10):
        with pytest.raises(DependencyDownError):
            resilient.call(refused)
    assert breaker.state == CircuitBreaker.CLOSED
    assert resilient.call(_Calls()) == "ok"


def test_a_refused_half_open_probe_closes_the_breaker_instead_of_stranding_it() -> None:
    clock = [0.0]
    breaker = CircuitBreaker("warehouse-test", failure_threshold=1, cooldown_s=5.0, now=lambda: clock[0])
    resilient = _resilient(breaker)
    with pytest.raises(DependencyDownError):
        resilient.call(_Calls(DatabricksSqlError("HTTP 503 from Databricks SQL API: busy")))
    assert breaker.state == CircuitBreaker.OPEN
    clock[0] = 6.0
    with pytest.raises(DependencyDownError) as raised:
        resilient.call(_Calls(DatabricksSqlPermissionError(_LIVE_REFUSAL)))
    assert raised.value.kind == DependencyDownError.KIND_PERMISSION_DENIED
    assert breaker.state == CircuitBreaker.CLOSED
    assert resilient.call(_Calls()) == "ok"


def test_transient_failures_keep_their_retry_and_breaker_behaviour() -> None:
    breaker = CircuitBreaker("warehouse-test", failure_threshold=2)
    resilient = _resilient(breaker)
    flaky = _Calls(DatabricksSqlError("HTTP 503 from Databricks SQL API: busy"))
    for expected_state in (CircuitBreaker.CLOSED, CircuitBreaker.OPEN):
        with pytest.raises(DependencyDownError) as raised:
            resilient.call(flaky)
        assert raised.value.kind == DependencyDownError.KIND_RETRIES_EXHAUSTED
        assert raised.value.retryable is True
        assert breaker.state == expected_state
    assert flaky.count == 6, "three attempts per call"


def test_other_kinds_stay_retryable() -> None:
    for kind in (
        DependencyDownError.KIND_WARMING_UP,
        DependencyDownError.KIND_BREAKER_OPEN,
        DependencyDownError.KIND_RETRIES_EXHAUSTED,
        "not-a-kind",
    ):
        assert DependencyDownError("warehouse", reason="x", kind=kind).retryable is True


class _RefusedRepo:
    def rate_sensitivity(self) -> Any:
        raise DependencyDownError(
            "warehouse",
            reason=f"DatabricksSqlPermissionError: {_LIVE_REFUSAL}",
            last_error=DatabricksSqlPermissionError(_LIVE_REFUSAL),
            kind=DependencyDownError.KIND_PERMISSION_DENIED,
        )


@pytest.fixture
def refused_route() -> Iterator[None]:
    prior = app.dependency_overrides.get(get_rate_sensitivity_repository)
    app.dependency_overrides[get_rate_sensitivity_repository] = lambda: _RefusedRepo()
    try:
        yield
    finally:
        if prior is None:
            app.dependency_overrides.pop(get_rate_sensitivity_repository, None)
        else:
            app.dependency_overrides[get_rate_sensitivity_repository] = prior


def test_the_503_body_is_non_retryable_and_leaks_nothing(refused_route: None) -> None:
    response = TestClient(app).get("/api/v1/geo/rate-sensitivity")

    assert response.status_code == 503
    body = response.json()
    assert body["retryable"] is False
    assert body["reason"] == "permission_denied"
    assert body["dependency"] == "warehouse"
    assert body["detail"] == "warehouse denied the app access to a required object"
    for leaked in ("mip.silver", "INSUFFICIENT_PERMISSIONS", "USE SCHEMA", "42501", "temporarily"):
        assert leaked not in response.text


def test_production_client_is_wired_to_fail_fast_on_refusals(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        type(settings),
        "require_databricks_creds",
        lambda _self: ("https://workspace.example", lambda: "test-token", "warehouse-id"),
    )
    _reset_sql_client_for_tests()
    try:
        client = get_sql_client()
        posts: list[str] = []

        def fake_post(url: str, body: dict[str, Any]) -> dict[str, Any]:
            posts.append(body["statement"])
            return {"status": {"state": "FAILED", "error": {"message": _LIVE_REFUSAL}}}

        monkeypatch.setattr(client._client, "_post", fake_post)  # type: ignore[attr-defined]
        breaker = client.resilient.breaker  # type: ignore[attr-defined]
        with pytest.raises(DependencyDownError) as raised:
            client.execute("SELECT 1")
        assert raised.value.kind == DependencyDownError.KIND_PERMISSION_DENIED
        assert posts == ["SELECT 1"], "one attempt, no retries"
        assert breaker.state == CircuitBreaker.CLOSED
    finally:
        _reset_sql_client_for_tests()

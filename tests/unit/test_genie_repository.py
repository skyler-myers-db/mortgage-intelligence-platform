"""Unit tests for ``DatabricksGenieRepository`` -- breaker-gated safe corpus.

Contract under test:

1. Breaker CLOSED + happy path: calls ``ResilientGenieClient.ask`` and
   adapts the response to ``GenieMessageResponse(source="genie")``.
2. Breaker OPEN + question in safe corpus: returns the honest "warming
   up" message with ``source="degraded"``. Prior behavior (`source=
   "fallback"` with a curated catalog answer body) was retired
   2026-05-04 — the catalog answers shipped hardcoded specific numbers
   that read as real Cotality data. CLAUDE.md prohibits mock fallback
   in the running app, so the only acceptable degraded response carries
   no fabricated content.
3. Breaker OPEN + unknown question: also returns the honest "warming
   up" message with ``source="degraded"``, never fabricated data.
4. ``DependencyDownError`` bubbled from the client with the breaker
   just opening: falls back to the safe corpus / degraded message.
5. ``GenieClientError`` from the live client (401, 500, malformed JSON)
   is re-raised -- it is NOT silently masked by a catalog answer.
"""
from __future__ import annotations

from typing import Any

import pytest

from backend.services.genie_answers import GenieMessageResponse
from backend.services.genie_client import GenieClientError, GenieResponse
from backend.services.repositories.databricks_repo import (
    DatabricksGenieRepository,
)
from backend.services.resilience import (
    CircuitBreaker,
    DependencyDownError,
    Resilient,
)


class _StubClient:
    """Minimal ``ResilientGenieClient`` shape for the repository.

    We don't use the real ``Resilient`` machinery here because these
    tests want to simulate the breaker state directly; the real
    wrapper is exercised in ``test_genie_client.py``.
    """

    def __init__(self, breaker: CircuitBreaker, response: Any) -> None:
        self._breaker = breaker
        self._response = response
        self.ask_calls: list[str] = []

    class _ResilientView:
        def __init__(self, breaker: CircuitBreaker) -> None:
            self.breaker = breaker

    @property
    def resilient(self) -> _StubClient._ResilientView:
        return _StubClient._ResilientView(self._breaker)

    def ask(self, question: str, conversation_id: str | None = None) -> Any:  # noqa: ARG002
        self.ask_calls.append(question)
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


def _make_breaker(state: str = "closed") -> CircuitBreaker:
    cb = CircuitBreaker(
        "genie",
        failure_threshold=1,
        cooldown_s=60.0,
    )
    if state == "open":
        cb.record_failure()
    return cb


# ---------------------------------------------------------------------------
# Happy path: breaker closed, real Genie returns a live answer.
# ---------------------------------------------------------------------------


def test_breaker_closed_calls_live_genie_and_stamps_source() -> None:
    live = GenieResponse(
        answer_text="12,840 borrowers are currently in the money.",
        sql_query="SELECT count(*) FROM mip.gold.lead_scores WHERE in_the_money",
        sql_result_rows=[{"count": 12840}],
        conversation_id="conv-abc",
        message_id="msg-1",
    )
    stub = _StubClient(_make_breaker("closed"), response=live)
    repo = DatabricksGenieRepository(stub)  # type: ignore[arg-type]

    result = repo.respond("How many borrowers are in the money?")

    assert isinstance(result, GenieMessageResponse)
    assert result.source == "genie"
    assert result.question == "How many borrowers are in the money?"
    assert "12,840" in result.answer
    assert result.table_rows == [{"count": 12840}]
    assert "mip.gold.lead_scores" in result.trusted_assets
    # Live call must have been made.
    assert stub.ask_calls == ["How many borrowers are in the money?"]


# ---------------------------------------------------------------------------
# Safe-corpus fallback -- only when breaker is open.
# ---------------------------------------------------------------------------


def test_breaker_open_with_catalog_match_returns_degraded() -> None:
    # Breaker open; even when the question matches a catalog intent we
    # now return the honest "Genie is warming up" message rather than
    # the curated catalog answer body. The catalog used to ship hard-
    # coded specific numbers (counts, dollar amounts, sample borrower
    # IDs) tagged source="fallback"; user feedback 2026-05-04 flagged
    # those as misleading because they read like real Cotality data.
    # CLAUDE.md prohibits mock fallback in the running app, so the
    # only acceptable degraded response carries no fabricated content.
    stub = _StubClient(_make_breaker("open"), response=None)
    repo = DatabricksGenieRepository(stub)  # type: ignore[arg-type]

    result = repo.respond("show me the in the money segment")

    assert result.source == "degraded"
    assert "warming up" in result.answer.lower()
    # No trusted assets on a degraded reply — we are not claiming a
    # source we cannot cite.
    assert result.trusted_assets == []
    # Follow-up suggestions still flow through (static UI hints, not
    # fabricated data).
    assert result.follow_up_questions
    assert result.question == "show me the in the money segment"
    # Live client must NOT have been called while the breaker was open.
    assert stub.ask_calls == []


def test_breaker_open_unknown_question_returns_degraded_message() -> None:
    stub = _StubClient(_make_breaker("open"), response=None)
    repo = DatabricksGenieRepository(stub)  # type: ignore[arg-type]

    result = repo.respond("what's the weather in tokyo")

    assert result.source == "degraded"
    assert "warming up" in result.answer.lower()
    # No trusted assets on a degraded reply -- we are not claiming a
    # source we cannot cite.
    assert result.trusted_assets == []
    assert stub.ask_calls == []


# ---------------------------------------------------------------------------
# Breaker transitions mid-call.
# ---------------------------------------------------------------------------


def test_dependency_down_error_returns_degraded() -> None:
    # Breaker closed at check time but the call itself raises
    # DependencyDownError (simulating the breaker opening during the
    # attempt). We now return the honest degraded message regardless
    # of whether the question matched the catalog (2026-05-04: see
    # `test_breaker_open_with_catalog_match_returns_degraded` for the
    # rationale — catalog answer bodies were misleading).
    stub = _StubClient(
        _make_breaker("closed"),
        response=DependencyDownError("genie", reason="circuit opened mid-call"),
    )
    repo = DatabricksGenieRepository(stub)  # type: ignore[arg-type]

    result = repo.respond("show me the in the money segment")
    assert result.source == "degraded"
    assert "warming up" in result.answer.lower()
    # The live client WAS attempted (breaker was closed at check time);
    # only the call itself raised.
    assert stub.ask_calls == ["show me the in the money segment"]


def test_dependency_down_error_unknown_question_returns_degraded() -> None:
    stub = _StubClient(
        _make_breaker("closed"),
        response=DependencyDownError("genie", reason="circuit opened mid-call"),
    )
    repo = DatabricksGenieRepository(stub)  # type: ignore[arg-type]

    result = repo.respond("random unrelated question about tokyo weather")
    assert result.source == "degraded"


# ---------------------------------------------------------------------------
# Non-dependency-down errors propagate -- no silent mock fallback.
# ---------------------------------------------------------------------------


def test_genie_client_error_propagates_without_silent_fallback() -> None:
    stub = _StubClient(
        _make_breaker("closed"),
        response=GenieClientError("HTTP 500 from Genie API", status_code=500),
    )
    repo = DatabricksGenieRepository(stub)  # type: ignore[arg-type]

    with pytest.raises(GenieClientError):
        repo.respond("how many borrowers are in the money")


# ---------------------------------------------------------------------------
# End-to-end resilient wrapper: verify the breaker trips after failures.
# ---------------------------------------------------------------------------


def test_end_to_end_with_real_resilient_wrapper() -> None:
    """Smoke test that the repository plugs into the real ``Resilient``
    + ``CircuitBreaker`` machinery and returns the degraded message
    after the breaker opens on repeated failures."""

    class _FlakyClient:
        def __init__(self) -> None:
            self.calls = 0

        def ask(self, question: str, conversation_id: str | None = None) -> Any:  # noqa: ARG002
            self.calls += 1
            raise GenieClientError("HTTP 503")

    breaker = CircuitBreaker("genie", failure_threshold=1, cooldown_s=60.0)
    resilient = Resilient[Any](
        breaker=breaker,
        dependency_name="genie",
        attempts=1,
        retry_on=(GenieClientError,),
    )

    class _Wired:
        def __init__(self) -> None:
            self._client = _FlakyClient()

        @property
        def resilient(self) -> Any:
            return resilient

        def ask(self, question: str, conversation_id: str | None = None) -> Any:
            return resilient.call(
                lambda: self._client.ask(question, conversation_id=conversation_id)
            )

    repo = DatabricksGenieRepository(_Wired())  # type: ignore[arg-type]

    # First call -- breaker closed; the flaky client raises and the
    # resilient wrapper translates to DependencyDownError. Question is
    # not in catalog -> degraded message.
    first = repo.respond("completely random query")
    assert first.source == "degraded"
    # After the failure, breaker is open.
    assert breaker.state == "open"

    # Next call with a catalog-matching question -> still degraded
    # message (no fabricated data), no live call attempted.
    second = repo.respond("show me the in the money segment")
    assert second.source == "degraded"
    assert "warming up" in second.answer.lower()

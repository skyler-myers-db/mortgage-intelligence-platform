"""One real Genie turn before the stop, always stubbed in tests (M0-PR5).

The workspace AI budget is a shared, metered resource, so no test may issue a
Genie turn: an autouse guard makes the App's Genie client fail on any request,
and every test passes its own scripted client.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from backend.services.genie_client import GenieClient
from tools.databricks import prestop_genie_budget as probe
from tools.databricks.prestop_refusals import GENIE_BUDGET

SPACE = "space-governed"


@pytest.fixture(autouse=True)
def _no_real_genie_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("a test reached the Genie API")

    monkeypatch.setattr(GenieClient, "_send", refuse)


class _Client:
    def __init__(self, outcome: Any, built: list[tuple[Any, ...]]) -> None:
        self.outcome = outcome
        self.built = built

    def factory(self, host: str, token: Any, space_id: str, *, timeout_s: int) -> Any:
        self.built.append((host, space_id, timeout_s))
        return self


def _answer(text: str, *, status: str = "COMPLETED") -> Any:
    return SimpleNamespace(genie_status=status, answer_text=text)


def _run(monkeypatch: pytest.MonkeyPatch, outcome: Any, space_id: str = SPACE) -> tuple[Any, ...]:
    built: list[tuple[Any, ...]] = []
    client = _Client(outcome, built)
    asked: list[str] = []

    def ask(target: _Client, question: str) -> Any:
        asked.append(question)
        if isinstance(target.outcome, Exception):
            raise target.outcome
        return target.outcome

    monkeypatch.setattr(probe, "ASSERT", ask)
    refusals, notes = probe.genie_budget_refusals(
        host="https://workspace.example.invalid",
        token_provider=lambda: "token-PLACEHOLDER",
        space_id=space_id,
        client_factory=client.factory,
    )
    return refusals, notes, built, asked


def test_a_completed_turn_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    refusals, notes, built, asked = _run(monkeypatch, _answer("12,345 borrowers"))

    assert refusals == []
    assert notes == [f"one Genie turn on the governed space {SPACE} completed"]
    assert built == [("https://workspace.example.invalid", SPACE, probe.TURN_TIMEOUT_S)]
    assert asked == [probe.QUESTION], "exactly one turn, the live smoke's question"


def test_a_budget_error_refuses_and_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    error = RuntimeError("HTTP 429 from Genie API: BUDGET_EXCEEDED for this workspace")

    refusals, _notes, _built, _asked = _run(monkeypatch, error)

    assert [refusal.code for refusal in refusals] == [GENIE_BUDGET]
    assert "the workspace Genie budget is exhausted" in refusals[0].message
    assert "after step 0a stops the App" in refusals[0].message


def test_any_other_failed_turn_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    error = RuntimeError("Genie message terminated in state 'FAILED': internal error")

    refusals, _notes, _built, _asked = _run(monkeypatch, error)

    assert len(refusals) == 1 and "the turn did not complete" in refusals[0].message


def test_an_unfinished_turn_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    refusals, _notes, _built, _asked = _run(monkeypatch, _answer("x", status="CANCELLED"))

    assert len(refusals) == 1 and "ended CANCELLED, not COMPLETED" in refusals[0].message


@pytest.mark.parametrize("answer", ("", "  ", probe.QUESTION.upper()))
def test_a_completed_turn_with_only_the_prompt_echo_refuses(
    monkeypatch: pytest.MonkeyPatch, answer: str
) -> None:
    refusals, _notes, _built, _asked = _run(monkeypatch, _answer(answer))

    assert len(refusals) == 1
    assert "completed with no answer beyond the prompt echo" in refusals[0].message


def test_no_pinned_space_skips_the_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    refusals, notes, built, asked = _run(monkeypatch, AssertionError("never asked"), space_id=" ")

    assert refusals == [] and built == [] and asked == []
    assert "the Genie budget turn is skipped" in notes[0]


def test_the_default_client_is_the_app_live_client_and_never_reached_here() -> None:
    client = GenieClient("https://workspace.example.invalid", lambda: "token-PLACEHOLDER", SPACE)

    with pytest.raises(AssertionError, match="a test reached the Genie API"):
        probe.ASSERT(client, probe.QUESTION)


def test_the_deployer_token_comes_from_the_sdk_auth_headers() -> None:
    workspace = SimpleNamespace(
        config=SimpleNamespace(authenticate=lambda: {"Authorization": "Bearer token-PLACEHOLDER"})
    )
    assert probe.deployer_token_provider(workspace)() == "token-PLACEHOLDER"

    basic = SimpleNamespace(config=SimpleNamespace(authenticate=lambda: {"Authorization": "Basic x"}))
    with pytest.raises(RuntimeError, match="no bearer token"):
        probe.deployer_token_provider(basic)()

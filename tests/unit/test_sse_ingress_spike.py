"""The SSE ingress spike's pure analysis on synthetic timelines (delivery-04).

The tool decides whether the Genie job-events stream is built, so its PASS
rule is pinned here without a network: an unbuffered timeline passes, a
late first event, a drifting cadence and a burst after a silence (an ingress
that buffers) fail; the disconnect rule; the idle survival read; the
decision; the SSE frame parser; and an auth failure is INCONCLUSIVE (2).
"""

from __future__ import annotations

from typing import Any

import pytest

from tools.databricks import sse_ingress_spike as spike


def _ticks(times: list[float]) -> list[spike.Arrival]:
    return [spike.Arrival(kind="tick", seq=index + 1, at_s=at) for index, at in enumerate(times)]


def test_an_unbuffered_cadence_passes() -> None:
    result = spike.analyse_flush(_ticks([0.4 + 0.5 * n + (0.03 if n % 2 else 0) for n in range(20)]), interval_s=0.5)

    assert result["verdict"] == "PASS"
    assert result["burst_after_silence"] is False


@pytest.mark.parametrize(
    ("times", "reason"),
    [
        ([3.5 + 0.5 * n for n in range(20)], "first_event_s"),
        ([0.2 + 0.8 * n for n in range(20)], "median_lag_ms"),
        ([0.2, 0.7, 1.2] + [3.6 + 0.01 * n for n in range(17)], "burst_after_silence"),
    ],
)
def test_a_late_drifting_or_buffered_stream_fails(times: list[float], reason: str) -> None:
    result = spike.analyse_flush(_ticks(times), interval_s=0.5)

    assert result["verdict"] == "FAIL", (reason, result)


def test_no_events_fail() -> None:
    assert spike.analyse_flush([], interval_s=0.5)["verdict"] == "FAIL"


@pytest.mark.parametrize(
    ("outcome", "verdict"),
    [
        ({"events_sent": 4, "disconnected_at_seq": 4, "completed": False}, "PASS"),
        ({"events_sent": 60, "disconnected_at_seq": None, "completed": True}, "FAIL"),
        ({"events_sent": 12, "disconnected_at_seq": 12, "completed": False}, "FAIL"),
        ({}, "FAIL"),
    ],
)
def test_the_disconnect_rule(outcome: dict[str, Any], verdict: str) -> None:
    assert spike.analyse_disconnect(outcome)["verdict"] == verdict


def test_idle_survival_needs_every_event_and_the_end() -> None:
    kept = [*_ticks([0.3, 45.3, 90.3]), spike.Arrival(kind="done", seq=None, at_s=90.4)]

    assert spike.analyse_idle(kept, events=3)["survived"] is True
    assert spike.analyse_idle(kept[:2], events=3)["survived"] is False


def test_the_stream_is_built_only_when_every_gate_passes() -> None:
    passing = {
        "flush": {"verdict": "PASS"},
        "pad": {"verdict": "PASS"},
        "disconnect": {"verdict": "PASS"},
        "idle_keepalive": {"survived": True},
        "idle_no_keepalive": {"survived": False},
    }

    assert spike.decide(passing) == "build"
    for gate, failing in (("pad", {"verdict": "FAIL"}), ("idle_keepalive", {"survived": False})):
        assert spike.decide({**passing, gate: failing}) == "keep_polling"
    assert "Decision: **build**" in spike.markdown(passing, "build")


def test_the_frame_parser_reads_ticks_keepalives_and_the_end() -> None:
    frames, rest = spike.parse_frames(
        'id: 1\nevent: tick\ndata: {"seq":1}\n: ' + "x" * 8 + "\n\n: keepalive\n\nevent: done\ndata: {}\n\nid: 2\nev"
    )

    assert frames == [("tick", 1), ("keepalive", None), ("done", None)]
    assert rest == "id: 2\nev"


def test_an_auth_failure_is_inconclusive(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    import databricks.sdk

    def broken(**_: Any) -> Any:
        raise ValueError("no such profile")

    monkeypatch.setattr(databricks.sdk, "WorkspaceClient", broken)

    assert spike.main(["--profile", "nope", "--base-url", "https://example.databricksapps.com"]) == 2
    assert "INCONCLUSIVE" in capsys.readouterr().out

"""SSE ingress spike against the deployed App (audit 2026-09-21 ``delivery-04``).

Decides whether the Genie job-events stream gets built: it drives the admin
probe (``GET /api/v1/admin/sse-probe``) through the Databricks Apps ingress
and measures what a browser would see. Scenarios:

* ``flush``: 20 events x 500 ms; ``pad``: the same with 2,048-byte padding.
  PASS when the first event arrives within 3 s, the median per-event lag
  stays within 250 ms of the server cadence, and no burst follows a silence
  of 2 s or more (an ingress that buffers releases events in bursts).
* ``idle``: 3 events x 45 s gaps, with keep-alive 0 and with keep-alive
  15 s; records whether the ingress kept the connection open.
* ``disconnect``: read 3 of 60 events, close, wait 3 s, then read the
  server's record. PASS when it stopped by sequence 6.

Decision rule (docs/load-baseline.md): build the stream only if flush, pad
and disconnect all PASS and the keep-alive idle run survived. Exit 0 PASS,
1 FAIL, 2 INCONCLUSIVE (auth or network). Headers are never printed.

Usage (an admin profile; the integrator runs it after the W5b deploy)::

    python tools/databricks/sse_ingress_spike.py --profile <admin-profile> \\
        --base-url https://mip-app-....databricksapps.com --out spike.json
"""

from __future__ import annotations

import argparse
import json
import secrets
import statistics
import sys
import time
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Any

PROBE_PATH = "/api/v1/admin/sse-probe"
FIRST_EVENT_MAX_S = 3.0
MEDIAN_LAG_MAX_S = 0.25
BURST_SILENCE_S = 2.0
BURST_WINDOW_S = 0.1
DISCONNECT_MAX_SEQ = 6
SCENARIOS = ("flush", "pad", "idle", "disconnect")


@dataclass(frozen=True)
class Arrival:
    """One SSE frame as the client saw it: ``at_s`` from request start."""

    kind: str  # tick | keepalive | done
    seq: int | None
    at_s: float


def parse_frames(buffer: str) -> tuple[list[tuple[str, int | None]], str]:
    """Complete frames in ``buffer`` (blank-line separated) and the remainder."""

    frames: list[tuple[str, int | None]] = []
    while "\n\n" in buffer:
        frame, buffer = buffer.split("\n\n", 1)
        lines = [line for line in frame.split("\n") if line]
        if lines and all(line.startswith(":") for line in lines):
            frames.append(("keepalive" if "keepalive" in frame else "pad", None))
            continue
        event = next((line[7:] for line in lines if line.startswith("event: ")), "")
        seq = next((int(line[4:]) for line in lines if line.startswith("id: ") and line[4:].isdigit()), None)
        frames.append((event or "message", seq))
    return frames, buffer


def analyse_flush(arrivals: Iterable[Arrival], *, interval_s: float) -> dict[str, Any]:
    """PASS: first event <= 3 s, median cadence lag <= 250 ms, no burst."""

    ticks = [a for a in arrivals if a.kind == "tick" and a.seq is not None]
    if not ticks:
        return {"verdict": "FAIL", "reason": "no events arrived"}
    first = ticks[0]
    lags = [abs((a.at_s - first.at_s) - (a.seq - first.seq) * interval_s) for a in ticks]  # type: ignore[operator]
    median_lag = statistics.median(lags)
    burst = _burst_after_silence(ticks)
    passed = first.at_s <= FIRST_EVENT_MAX_S and median_lag <= MEDIAN_LAG_MAX_S and not burst
    return {
        "verdict": "PASS" if passed else "FAIL",
        "first_event_s": round(first.at_s, 3),
        "median_lag_ms": round(median_lag * 1000, 1),
        "burst_after_silence": burst,
        "events": len(ticks),
    }


def _burst_after_silence(ticks: list[Arrival]) -> bool:
    """A silence of 2 s or more followed by several events within 100 ms."""

    for earlier, later in zip(ticks, ticks[1:], strict=False):
        if later.at_s - earlier.at_s >= BURST_SILENCE_S:
            landed = [tick for tick in ticks if 0 <= tick.at_s - later.at_s <= BURST_WINDOW_S]
            if len(landed) >= 2:
                return True
    return False


def analyse_disconnect(outcome: dict[str, Any]) -> dict[str, Any]:
    """PASS when the server stopped by sequence 6 after the client closed."""

    seq = outcome.get("disconnected_at_seq")
    sent = outcome.get("events_sent")
    passed = isinstance(seq, int) and seq <= DISCONNECT_MAX_SEQ and isinstance(sent, int) and sent <= DISCONNECT_MAX_SEQ
    return {"verdict": "PASS" if passed else "FAIL", "disconnected_at_seq": seq, "events_sent": sent}


def analyse_idle(arrivals: Iterable[Arrival], *, events: int) -> dict[str, Any]:
    """Whether the ingress kept the connection through the gaps."""

    seen = list(arrivals)
    ticks = sum(1 for a in seen if a.kind == "tick")
    survived = ticks == events and any(a.kind == "done" for a in seen)
    return {"survived": survived, "events": ticks, "keepalives": sum(1 for a in seen if a.kind == "keepalive")}


def decide(results: dict[str, dict[str, Any]]) -> str:
    """``build`` the job-events stream, or ``keep_polling``."""

    core = all(results.get(name, {}).get("verdict") == "PASS" for name in ("flush", "pad", "disconnect"))
    idle = results.get("idle_keepalive", {}).get("survived") is True
    return "build" if core and idle else "keep_polling"


def markdown(results: dict[str, dict[str, Any]], decision: str) -> str:
    rows = ["| Scenario | Result |", "| --- | --- |"]
    rows += [f"| {name} | {json.dumps(value, sort_keys=True)} |" for name, value in results.items()]
    return "\n".join([*rows, "", f"Decision: **{decision}**"])


# ------------------------------------------------------------- the network


def _stream(client: Any, url: str, params: dict[str, Any], headers: dict[str, str], *,
            stop_after: int | None = None) -> Iterator[Arrival]:
    started = time.monotonic()
    with client.stream("GET", url, params=params, headers=headers) as response:
        if response.status_code != 200:
            raise RuntimeError(f"probe returned HTTP {response.status_code}")
        buffer, ticks = "", 0
        for chunk in response.iter_text():
            buffer += chunk
            frames, buffer = parse_frames(buffer)
            now = time.monotonic() - started
            for event, seq in frames:
                kind = "tick" if event == "tick" else "done" if event == "done" else "keepalive"
                yield Arrival(kind=kind, seq=seq, at_s=now)
                ticks += kind == "tick"
                if stop_after is not None and ticks >= stop_after:
                    return


def run(client: Any, base: str, headers: dict[str, str], scenarios: list[str]) -> dict[str, dict[str, Any]]:
    url = base + PROBE_PATH
    results: dict[str, dict[str, Any]] = {}

    def probe(**params: Any) -> dict[str, Any]:
        return {"probe_id": secrets.token_hex(8), **params}

    if "flush" in scenarios:
        results["flush"] = analyse_flush(_stream(client, url, probe(events=20, interval_ms=500), headers), interval_s=0.5)
    if "pad" in scenarios:
        params = probe(events=20, interval_ms=500, pad_bytes=2048)
        results["pad"] = analyse_flush(_stream(client, url, params, headers), interval_s=0.5)
    if "idle" in scenarios:
        for name, keepalive in (("idle_no_keepalive", 0), ("idle_keepalive", 15_000)):
            params = probe(events=3, interval_ms=45_000, keepalive_ms=keepalive)
            try:
                results[name] = analyse_idle(_stream(client, url, params, headers), events=3)
            except Exception as exc:  # noqa: BLE001 - a dropped idle connection is the measurement
                results[name] = {"survived": False, "error": type(exc).__name__}
    if "disconnect" in scenarios:
        params = probe(events=60, interval_ms=500)
        list(_stream(client, url, params, headers, stop_after=3))
        time.sleep(3)
        outcome = client.get(f"{url}/{params['probe_id']}", headers=headers)
        results["disconnect"] = analyse_disconnect(outcome.json() if outcome.status_code == 200 else {})
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--profile", required=True)
    parser.add_argument("--app-name", default="mip-app")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--scenarios", default=",".join(SCENARIOS))
    parser.add_argument("--out")
    args = parser.parse_args(argv)
    scenarios = [name for name in args.scenarios.split(",") if name]
    if not set(scenarios) <= set(SCENARIOS):
        parser.error(f"scenarios must be among {','.join(SCENARIOS)}")
    try:
        import httpx

        from databricks.sdk import WorkspaceClient
        from tools.databricks.app_health_contract import canonical_workspace_app_url

        workspace = WorkspaceClient(profile=args.profile)
        base = canonical_workspace_app_url(workspace, app_name=args.app_name, base_url=args.base_url)
        headers = dict(workspace.config.authenticate())
        headers["Accept"] = "text/event-stream"
        with httpx.Client(timeout=httpx.Timeout(30.0, read=120.0), follow_redirects=False) as client:
            results = run(client, base, headers, scenarios)
    except Exception as exc:  # noqa: BLE001 - auth or network: inconclusive, never a verdict
        print(json.dumps({"verdict": "INCONCLUSIVE", "error": type(exc).__name__}))
        return 2
    decision = decide(results)
    summary = {"results": results, "decision": decision}
    print(json.dumps(summary, indent=2, sort_keys=True))
    print(markdown(results, decision))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(summary, handle, indent=2, sort_keys=True)
    failed = any(value.get("verdict") == "FAIL" for value in results.values())
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["Arrival", "analyse_disconnect", "analyse_flush", "analyse_idle", "decide", "markdown", "parse_frames"]

"""Pure analysis for the cold-cache Locust profile (audit delivery-09).

No Locust import: ``locust_cold_cache.py`` records one ``Sample`` per request
and hands the list here on quit, and the unit tests drive these functions
directly.

The question the profile answers: on a cold cache, do simultaneous users fill
the dependency semaphores with blocked single-flight followers until the
worker threads run out (429 ``dependency_saturated``) or the shell stalls
(/health or /session slow)? The verdict is 'reproduced' iff the cold window
holds any ``dependency_saturated`` 429, or the /health or /session p95 in the
cold window exceeds 3000 ms; otherwise 'not_reproduced'. ``rate_limited``
429s are reported but never counted: the profile runs as ONE bearer, so the
per-actor buckets (360/min expensive, 600/min default) are a property of the
harness, not of the App. W5d w5-genie-provenance-tiles builds the single-flight
slot release only on a 'reproduced' verdict.
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

COLD_WINDOW_S = 60.0
STALL_P95_MS = 3000.0
STALL_ROUTES: tuple[str, ...] = ("GET /api/v1/health", "GET /api/v1/session")


@dataclass(frozen=True)
class Sample:
    """One request: its templated name ('GET /api/v1/health'), never an id."""

    name: str
    started_s: float  # seconds since the run started
    elapsed_ms: float
    status: int
    body: Mapping[str, Any] | None = None  # parsed JSON, kept for 429 / 503 only


def classify_429(body: object) -> str:
    """``dependency_saturated:<dependency>``, ``rate_limited:<scope>`` or ``other``."""
    if not isinstance(body, Mapping):
        return "other"
    reason = body.get("reason")
    if reason == "dependency_saturated":
        dependency = body.get("dependency")
        return f"dependency_saturated:{dependency if isinstance(dependency, str) and dependency else 'unknown'}"
    if reason == "rate_limited":
        scope = body.get("scope")
        return f"rate_limited:{scope if isinstance(scope, str) and scope else 'unknown'}"
    return "other"


def _reason_503(body: object) -> str:
    if isinstance(body, Mapping):
        reason = body.get("reason")
        if isinstance(reason, str) and reason:
            return reason
    return "other"


def percentile(values: Iterable[float], q: float) -> float | None:
    """Nearest-rank percentile (q in 0..100); None for no values."""
    ordered = sorted(values)
    if not ordered:
        return None
    rank = max(1, math.ceil(q / 100.0 * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def _latency(values: list[float]) -> dict[str, float | int | None]:
    return {
        "count": len(values),
        "p50_ms": percentile(values, 50),
        "p95_ms": percentile(values, 95),
        "p99_ms": percentile(values, 99),
        "max_ms": max(values) if values else None,
    }


def summarize(
    samples: Iterable[Sample],
    cold_window_s: float = COLD_WINDOW_S,
    *,
    stall_routes: tuple[str, ...] = STALL_ROUTES,
) -> dict[str, Any]:
    """Per-route latency (cold window and whole run), 429s by class, 503s by reason."""
    rows = list(samples)
    cold = [sample for sample in rows if sample.started_s < cold_window_s]
    routes: dict[str, dict[str, list[float]]] = defaultdict(lambda: {"cold": [], "all": []})
    for sample in rows:
        routes[sample.name]["all"].append(sample.elapsed_ms)
    for sample in cold:
        routes[sample.name]["cold"].append(sample.elapsed_ms)

    def tally(window: list[Sample], status: int) -> dict[str, int]:
        classify = classify_429 if status == 429 else _reason_503
        return dict(sorted(Counter(classify(s.body) for s in window if s.status == status).items()))

    summary: dict[str, Any] = {
        "cold_window_s": cold_window_s,
        "requests": {"cold": len(cold), "all": len(rows)},
        "routes": {
            name: {"cold": _latency(windows["cold"]), "all": _latency(windows["all"])}
            for name, windows in sorted(routes.items())
        },
        "status_429": {"cold": tally(cold, 429), "all": tally(rows, 429)},
        "status_503": {"cold": tally(cold, 503), "all": tally(rows, 503)},
        "stall_p95_ms_cold": {
            name: percentile(routes[name]["cold"], 95) if name in routes else None
            for name in stall_routes
        },
    }
    summary["verdict"] = verdict(summary)
    return summary


def verdict(summary: Mapping[str, Any]) -> str:
    """'reproduced' iff a cold dependency_saturated 429 or a cold shell stall."""
    cold_429 = summary.get("status_429", {}).get("cold", {})
    if any(str(key).startswith("dependency_saturated:") and count for key, count in cold_429.items()):
        return "reproduced"
    for p95 in summary.get("stall_p95_ms_cold", {}).values():
        if p95 is not None and p95 > STALL_P95_MS:
            return "reproduced"
    return "not_reproduced"


__all__ = [
    "COLD_WINDOW_S",
    "STALL_P95_MS",
    "STALL_ROUTES",
    "Sample",
    "classify_429",
    "percentile",
    "summarize",
    "verdict",
]

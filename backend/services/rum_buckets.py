"""Fixed per-metric histogram buckets for the RUM day aggregates (D-platform-process-d2).

Every metric has 24 buckets: 23 finite, inclusive upper edges plus an
overflow bucket. The Core Web Vitals thresholds are EXACT edges, so a p75
interpolated inside its bucket can never cross a rating band:

* ``lcp``: 2500 and 4000 ms;
* ``inp`` and its three phase metrics (``inp_input_delay``,
  ``inp_processing``, ``inp_presentation``): 200 and 500 ms;
* ``cls``: 0.1 and 0.25 (unitless);
* ``navigation_load`` and ``route_change``: log-spaced ms edges that keep
  their 1000 / 2500 ms rating bands;
* ``api_call``: log-spaced ms edges up to the schema's 600000 ms ceiling;
* ``client_error``: a count, so every event falls in bucket 0.

The STORED rating is always derived here from the value; the browser's own
rating is never trusted. ``api_call`` and ``client_error`` rate ``info``.
"""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Sequence
from typing import Final

NUM_BUCKETS: Final = 24
OVERFLOW_BUCKET: Final = NUM_BUCKETS - 1

_INP_EDGES: Final[tuple[float, ...]] = (
    8, 16, 24, 32, 40, 50, 64, 80, 100, 125, 150, 175, 200,
    250, 300, 350, 400, 500, 650, 800, 1000, 2000, 5000,
)
_NAVIGATION_EDGES: Final[tuple[float, ...]] = (
    10, 25, 50, 75, 100, 150, 200, 300, 400, 500, 750, 1000,
    1250, 1500, 2000, 2500, 3000, 4000, 5000, 7500, 10000, 20000, 60000,
)

BUCKET_EDGES: Final[dict[str, tuple[float, ...]]] = {
    "lcp": (
        100, 200, 300, 400, 500, 600, 800, 1000, 1200, 1500, 1800, 2000,
        2200, 2500, 3000, 3500, 4000, 5000, 6000, 8000, 10000, 15000, 30000,
    ),
    "cls": (
        0.005, 0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.07, 0.08, 0.09, 0.1, 0.125,
        0.15, 0.175, 0.2, 0.25, 0.3, 0.4, 0.5, 0.75, 1.0, 1.5, 3.0,
    ),
    "inp": _INP_EDGES,
    "inp_input_delay": _INP_EDGES,
    "inp_processing": _INP_EDGES,
    "inp_presentation": _INP_EDGES,
    "navigation_load": _NAVIGATION_EDGES,
    "route_change": _NAVIGATION_EDGES,
    "api_call": (
        5, 10, 20, 35, 50, 75, 100, 150, 200, 300, 500, 750,
        1000, 1500, 2000, 3000, 5000, 10000, 20000, 30000, 60000, 120000, 600000,
    ),
}

#: (good upper edge, needs-improvement upper edge); a value above the second is poor.
RATING_THRESHOLDS: Final[dict[str, tuple[float, float]]] = {
    "lcp": (2500, 4000),
    "cls": (0.1, 0.25),
    "inp": (200, 500),
    "inp_input_delay": (200, 500),
    "inp_processing": (200, 500),
    "inp_presentation": (200, 500),
    "navigation_load": (1000, 2500),
    "route_change": (1000, 2500),
}

#: Every metric the day-aggregate table stores (lakebase/schema.sql CHECK).
ROLLUP_METRICS: Final = frozenset({*BUCKET_EDGES, "client_error"})


def zero_buckets() -> list[int]:
    return [0] * NUM_BUCKETS


def bucket_index(metric: str, value: float) -> int:
    """The bucket of ``value``: the first whose inclusive upper edge holds it."""
    if metric == "client_error":
        return 0
    return bisect_left(BUCKET_EDGES[metric], value)


def rating(metric: str, value: float) -> str:
    """``good`` / ``needs_improvement`` / ``poor`` from the bands, else ``info``."""
    bands = RATING_THRESHOLDS.get(metric)
    if bands is None:
        return "info"
    good, needs_improvement = bands
    if value <= good:
        return "good"
    if value <= needs_improvement:
        return "needs_improvement"
    return "poor"


def p75(metric: str, buckets: Sequence[int]) -> float | None:
    """The 75th percentile, interpolated linearly inside its bucket; None when empty.

    A value in the overflow bucket reads as its lower edge (the last finite
    edge): the histogram cannot say how far beyond it the tail runs.
    """
    if len(buckets) != NUM_BUCKETS:
        raise ValueError(f"expected {NUM_BUCKETS} buckets")
    total = sum(buckets)
    if total <= 0:
        return None
    edges = BUCKET_EDGES.get(metric)
    if edges is None:
        return None
    target = 0.75 * total
    before = 0
    for index, count in enumerate(buckets):
        if count <= 0 or before + count < target:
            before += count
            continue
        lower = 0.0 if index == 0 else float(edges[index - 1])
        if index == OVERFLOW_BUCKET:
            return lower
        upper = float(edges[index])
        return lower + (upper - lower) * (target - before) / count
    return float(edges[-1])


__all__ = [
    "BUCKET_EDGES",
    "NUM_BUCKETS",
    "RATING_THRESHOLDS",
    "ROLLUP_METRICS",
    "bucket_index",
    "p75",
    "rating",
    "zero_buckets",
]

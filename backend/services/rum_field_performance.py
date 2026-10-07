"""Field performance summary over the RUM day aggregates (D-platform-process-d2 step 11).

Reads ``mip_app.rum_daily`` for a 7- or 28-day window and aggregates in
Python: buckets are summed across days and ratings per (metric, route,
facet), then the p75 and its rating are computed from the summed histogram
(``rum_buckets``). A cell with fewer than :data:`SAMPLE_FLOOR` samples shows
no p75 and no rating: a statistical floor that also limits inference about
individual use in a small deployment. Client-error counts carry no floor.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import date
from typing import Any, Final, Literal

from backend.schemas.field_performance import (
    FieldPerformanceCell,
    FieldPerformanceClientErrorRow,
    FieldPerformanceInteractionRow,
    FieldPerformanceResponse,
    FieldPerformanceVitalsRow,
    FieldRating,
)
from backend.services.rum_buckets import NUM_BUCKETS, p75, rating, zero_buckets

SAMPLE_FLOOR: Final = 20
MAX_BUILDS: Final = 20
#: Bound on the rows one read pulls (every key of 28 days fits well inside it).
READ_LIMIT: Final = 100_000

FIELD_PERFORMANCE_SQL: Final = (
    "SELECT metric, route, facet, rating, builds, sample_count, buckets "
    "FROM mip_app.rum_daily WHERE day >= %(since)s::date AND sample_count > 0"
)

_VITALS: Final = ("lcp", "inp", "cls")
_PHASES: Final = (
    ("input_delay", "inp_input_delay"),
    ("processing", "inp_processing"),
    ("presentation", "inp_presentation"),
)
_INTERACTION_METRICS: Final = frozenset({"inp", *(metric for _, metric in _PHASES)})
_BANDS: Final[dict[str, FieldRating]] = {
    "good": "good",
    "needs_improvement": "needs_improvement",
    "poor": "poor",
}

Histogram = list[int]


def _cell(metric: str, buckets: Sequence[int] | None) -> FieldPerformanceCell:
    samples = sum(buckets) if buckets else 0
    if buckets is None or samples < SAMPLE_FLOOR:
        return FieldPerformanceCell(p75=None, rating=None, samples=samples, floor_applied=True)
    value = p75(metric, buckets)
    if value is None:
        return FieldPerformanceCell(p75=None, rating=None, samples=samples, floor_applied=True)
    return FieldPerformanceCell(
        p75=round(value, 4),
        rating=_BANDS.get(rating(metric, value)),
        samples=samples,
        floor_applied=False,
    )


def _sum_into(target: Histogram, buckets: Sequence[int]) -> None:
    for index in range(min(NUM_BUCKETS, len(buckets))):
        target[index] += int(buckets[index])


def summarize(
    rows: Iterable[Mapping[str, Any]],
    *,
    days: Literal[7, 28],
    since: date,
    enabled: bool,
) -> FieldPerformanceResponse:
    histograms: dict[tuple[str, str, str], Histogram] = defaultdict(zero_buckets)
    errors: dict[tuple[str, str], int] = defaultdict(int)
    builds: set[str] = set()
    for row in rows:
        metric, route, facet = str(row["metric"]), str(row["route"]), str(row["facet"] or "")
        builds.update(str(build) for build in row.get("builds") or ())
        if metric == "client_error":
            errors[(route, facet)] += int(row["sample_count"])
            continue
        if len(row["buckets"] or ()) != NUM_BUCKETS:
            continue
        _sum_into(histograms[(metric, route, facet)], row["buckets"])

    vitals_by_route: dict[str, dict[str, Histogram]] = defaultdict(dict)
    interactions: dict[tuple[str, str], dict[str, Histogram]] = defaultdict(dict)
    for (metric, route, facet), histogram in histograms.items():
        if metric in _VITALS:
            merged = vitals_by_route[route].setdefault(metric, zero_buckets())
            _sum_into(merged, histogram)
        if metric in _INTERACTION_METRICS:
            interactions[(route, facet or "other")][metric] = histogram

    vitals = [
        FieldPerformanceVitalsRow(
            route=route,
            lcp=_cell("lcp", by_metric.get("lcp")),
            inp=_cell("inp", by_metric.get("inp")),
            cls=_cell("cls", by_metric.get("cls")),
        )
        for route, by_metric in sorted(vitals_by_route.items())
    ]
    interaction_rows = [
        FieldPerformanceInteractionRow(
            route=route,
            interaction_target=target,
            samples=sum(by_metric.get("inp") or ()),
            inp=_cell("inp", by_metric.get("inp")),
            input_delay=_cell("inp_input_delay", by_metric.get("inp_input_delay")),
            processing=_cell("inp_processing", by_metric.get("inp_processing")),
            presentation=_cell("inp_presentation", by_metric.get("inp_presentation")),
        )
        for (route, target), by_metric in sorted(interactions.items())
    ]
    client_errors = [
        entry
        for (route, facet), count in errors.items()
        if (entry := _client_error_row(route, facet, count)) is not None
    ]
    client_errors.sort(key=lambda item: (-item.count, item.route, item.error_name, item.error_kind, item.boundary or ""))
    return FieldPerformanceResponse(
        days=days,
        since=since,
        browser_telemetry="on" if enabled else "off",
        builds=sorted(builds)[:MAX_BUILDS],
        vitals=vitals,
        interactions=interaction_rows,
        client_errors=client_errors,
    )


def _client_error_row(route: str, facet: str, count: int) -> FieldPerformanceClientErrorRow | None:
    parts = facet.split("|")
    if len(parts) != 3 or not parts[0] or not parts[1]:
        return None
    name, kind, boundary = parts
    return FieldPerformanceClientErrorRow(
        route=route,
        error_name=name,
        error_kind=kind,
        boundary=None if boundary in ("", "-") else boundary,
        count=count,
    )


__all__ = ["FIELD_PERFORMANCE_SQL", "READ_LIMIT", "SAMPLE_FLOOR", "summarize"]

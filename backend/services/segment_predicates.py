"""Canonical segment-predicate composition for Unity Catalog reads.

S8: every surface that filters borrowers by segment membership — the lead
repository (Lead Queue list/count), the geo repository (map rollups +
Segment Intelligence card cohorts), and the analytics repository — must
compose the SAME predicate per segment: ``array_contains(<column>,
:segment_i)`` over the gold ``segment_codes`` membership array, AND-joined
for ``all`` (intersection) and OR-joined for ``any`` (de-duplicated union).

This module is the single definition of that composition. Repositories
delegate here instead of hand-rolling the clause so the intersection the
Segment Intelligence cards preview is provably the intersection the Lead
Queue ranks. Segment values are ALWAYS emitted as named bind parameters —
never interpolated into SQL text — so a hostile "code" can only ever fail
an ``array_contains`` membership test, not escape the predicate.
"""

from __future__ import annotations

from collections.abc import Sequence

SEGMENT_MODE_ALL = "all"
SEGMENT_MODE_ANY = "any"


def normalise_segment_codes(codes: Sequence[str | None] | None) -> list[str]:
    """Strip, drop blanks, and de-duplicate while preserving caller order.

    Caller order is preserved (not sorted) so parameter names stay
    deterministic for a given request and repository cache keys remain
    stable across identical calls.
    """
    out: list[str] = []
    seen: set[str] = set()
    for code in codes or []:
        normalised = str(code or "").strip()
        if not normalised or normalised in seen:
            continue
        seen.add(normalised)
        out.append(normalised)
    return out


def compose_segment_predicate(
    codes: Sequence[str | None] | None,
    *,
    mode: str = SEGMENT_MODE_ANY,
    column: str = "segment_codes",
) -> tuple[str, dict[str, object]]:
    """Compose the canonical membership predicate for the given segments.

    Returns ``(clause, params)`` where ``clause`` references only named
    bind parameters. Empty/blank input composes to ``("", {})`` — callers
    treat that as "no segment filter", never as ``1=1``.

    Parameter-name contract (pinned by unit tests and warehouse result
    cache keys): a single code binds ``:segment``; multiple codes bind
    ``:segment_0..n`` in caller order. ``mode`` is ``all`` for the
    intersection (AND) and anything else composes the ``any`` union (OR),
    matching the fail-open-to-OR behaviour the routers already validate
    upstream.
    """
    normalised = normalise_segment_codes(codes)
    if not normalised:
        return "", {}
    if len(normalised) == 1:
        return f"array_contains({column}, :segment)", {"segment": normalised[0]}
    params: dict[str, object] = {
        f"segment_{i}": code for i, code in enumerate(normalised)
    }
    fragments = [
        f"array_contains({column}, :segment_{i})" for i in range(len(normalised))
    ]
    if mode == SEGMENT_MODE_ALL:
        return " AND ".join(fragments), params
    return "(" + " OR ".join(fragments) + ")", params

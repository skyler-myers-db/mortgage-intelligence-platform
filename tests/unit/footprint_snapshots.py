"""FootprintSnapshot builders shared by the footprint guard and config tests.

The Genie footprint guards and ``/api/config/*`` take ONE snapshot per
decision (decision record e2), so tests hand them a snapshot directly instead
of patching the resolver. Three shapes cover every status the resolver emits.
"""

from __future__ import annotations

from collections.abc import Iterable

from backend.schemas.usps import US_STATE_NAME_BY_CODE
from backend.services.state_footprint import FootprintSnapshot, FootprintState


def _rows(codes: Iterable[str]) -> tuple[FootprintState, ...]:
    return tuple(
        FootprintState(code, US_STATE_NAME_BY_CODE[code], idx, idx == 1)
        for idx, code in enumerate(codes, start=1)
    )


def live_snapshot(*codes: str) -> FootprintSnapshot:
    """Live gold coverage over ``codes`` (first code is the default state)."""
    return FootprintSnapshot(rows=_rows(codes), status="live_coverage")


def fallback_snapshot() -> FootprintSnapshot:
    """The generic 50-state outage fallback (degraded, no default state)."""
    rows = tuple(
        FootprintState(code, name, idx, False)
        for idx, (code, name) in enumerate(
            ((c, n) for c, n in US_STATE_NAME_BY_CODE.items() if c != "DC"),
            start=1,
        )
    )
    return FootprintSnapshot(rows=rows, status="fallback")


def metadata_only_snapshot(*codes: str) -> FootprintSnapshot:
    """``ref.state_footprint`` metadata rows only (degraded) over ``codes``."""
    return FootprintSnapshot(rows=_rows(codes), status="metadata_only")


def empty_fallback_snapshot() -> FootprintSnapshot:
    """The worst case: a degraded snapshot that covers no state at all."""
    return FootprintSnapshot(rows=(), status="fallback")

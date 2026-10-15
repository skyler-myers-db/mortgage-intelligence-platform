"""The Distribution Board's greedy fill (audit wow-power-5 step 2). Pure: no I/O.

``score_balanced`` used to be a label the store recorded over a plain
round-robin. This module is the allocation that label now names, and only
POST /sales/distribute runs it (a single assign refuses the strategy).

Rules, in order (the talk track repeats them for the manager):

1. Candidates go highest ``opportunity_score`` first; ties by borrower id.
2. An officer's remaining capacity is ``max(0, capacity_per_day - open_count)``:
   a day's capacity measured against the officer's whole open book, which is
   deliberately conservative. An officer with capacity 0 never receives a lead.
3. Each lead goes to an officer with remaining capacity, preferring, in order:
   an officer whose region IS the property's state (state grain; an empty or
   'National' region is a generalist and never counts as in-region); then the
   lowest projected utilisation ``(open + added) / capacity``; then the lowest
   score sum already added in this plan; then the email.
4. When no officer has room, the lead is left unplaced (``unplaced_no_capacity``).

The fingerprint is the SHA-256 of the canonical JSON of the sorted
``(borrower_id, email)`` placements plus the strategy and the assignment
duration, so a commit can prove it runs the plan the manager previewed. The
result is the same for any input order.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from fractions import Fraction

STRATEGY = "score_balanced"
_GENERALIST_REGIONS = frozenset({"", "NATIONAL"})


class DistributionPlanChanged(RuntimeError):
    """The commit re-planned on live counts and the fingerprint moved."""

    def __init__(self, fingerprint: str) -> None:
        super().__init__("distribution_plan_changed")
        self.fingerprint = fingerprint


@dataclass(frozen=True)
class Candidate:
    borrower_id: str
    score: int
    state: str | None


@dataclass(frozen=True)
class OfficerSlot:
    email: str
    display_label: str
    region: str | None
    capacity_per_day: int
    open_count: int


@dataclass(frozen=True)
class OfficerLoad:
    slot: OfficerSlot
    added_count: int
    added_score_sum: int
    in_region_count: int


@dataclass(frozen=True)
class DistributionPlan:
    placements: tuple[tuple[str, str], ...]
    unplaced_no_capacity: int
    placed_score_sum: int
    officers: tuple[OfficerLoad, ...]
    fingerprint: str


def _region_key(value: str | None) -> str:
    return (value or "").strip().upper()


def in_region(slot: OfficerSlot, state: str | None) -> bool:
    region = _region_key(slot.region)
    return region not in _GENERALIST_REGIONS and region == _region_key(state)


def plan_fingerprint(
    placements: Iterable[tuple[str, str]], *, expires_in_hours: int | None
) -> str:
    canonical = json.dumps(
        {
            "expires_in_hours": expires_in_hours,
            "placements": sorted([borrower_id, email] for borrower_id, email in placements),
            "strategy": STRATEGY,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def plan_score_balanced(
    candidates: Iterable[Candidate],
    officers: Iterable[OfficerSlot],
    *,
    expires_in_hours: int | None,
) -> DistributionPlan:
    unique: dict[str, Candidate] = {}
    for candidate in sorted(candidates, key=lambda c: (-c.score, c.borrower_id)):
        unique.setdefault(candidate.borrower_id, candidate)
    slots: dict[str, OfficerSlot] = {}
    for slot in sorted(officers, key=lambda s: s.email):
        slots.setdefault(slot.email, slot)
    added = {email: 0 for email in slots}
    score_sum = {email: 0 for email in slots}
    region_hits = {email: 0 for email in slots}

    placements: list[tuple[str, str]] = []
    unplaced = 0
    for candidate in unique.values():
        open_slots = [
            slot
            for slot in slots.values()
            if max(0, slot.capacity_per_day - slot.open_count) - added[slot.email] > 0
        ]
        if not open_slots:
            unplaced += 1
            continue
        chosen = min(
            open_slots,
            key=lambda slot: (
                0 if in_region(slot, candidate.state) else 1,
                Fraction(slot.open_count + added[slot.email], slot.capacity_per_day),
                score_sum[slot.email],
                slot.email,
            ),
        )
        added[chosen.email] += 1
        score_sum[chosen.email] += candidate.score
        if in_region(chosen, candidate.state):
            region_hits[chosen.email] += 1
        placements.append((candidate.borrower_id, chosen.email))

    loads = tuple(
        OfficerLoad(slot, added[slot.email], score_sum[slot.email], region_hits[slot.email])
        for slot in sorted(slots.values(), key=lambda s: (s.display_label, s.email))
    )
    return DistributionPlan(
        placements=tuple(placements),
        unplaced_no_capacity=unplaced,
        placed_score_sum=sum(score_sum.values()),
        officers=loads,
        fingerprint=plan_fingerprint(placements, expires_in_hours=expires_in_hours),
    )

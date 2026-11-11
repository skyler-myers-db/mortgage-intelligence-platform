"""Interim owner pairs: exact, expiring exceptions to the approved-owner policy (M2-PR8).

Spike S4 measured that a pipeline-published streaming table or materialized
view can never change owner by itself: the REST owner PATCH and every ``ALTER
... OWNER TO`` form are refused for any actor
(``CHANGE_OWNER_NOT_ALLOWED_FOR_PIPELINE_TABLE``). Its owner is always the
pipeline's run_as identity and flips the moment that setting changes. The
ownership handover (tools/clients/handover_uc_ownership.py) therefore leaves
those datasets with their current owner, the legacy identity the pipeline
runs as until the release, and its ``--plan`` writes one pair per dataset,
``(full_name, owner, expires_at)``, to the private ``--report-out`` report,
also as the single-line value
(``canonical_pairs``). The reviewed pairs are per-client configuration,
``MIP_UC_INTERIM_OWNER_PAIRS_JSON`` (identifying: a GitHub Environment secret
or ``.env.local``, never the tree); the client profile parses the field
(tools/clients/json_fields.InterimOwnerPair) and ``build_pairs`` applies the
rules below, at resolve as at every use.

A pair is an exception for exactly one object and one owner:

* only an exact match counts: the object's full name and its owner exactly as
  Unity Catalog returns them, character for character. A pair approves its
  owner for no other object, and never approves a grantee
  (``prestop_owner_equivalent`` does not read pairs);
* ``expires_at`` lies at most 14 days ahead whenever the pairs are read, so a
  pair cannot outlive the cutover by being re-read; and
* an expired pair refuses its object (``expired``). A pair is meant to cover
  the UC handover (cutover step 7) until the release's bundle apply moves the
  pipeline's run_as to the jobs identity, which re-owns every dataset at once;
  no journaled re-own exists. From then on the pairs match nothing and the
  field is unset. The jobs identity's ownership is not an exception to pair
  but the permanent rule: ``handover_uc_ownership --verify --released``
  asserts that every pipeline dataset is owned by the jobs identity, which
  its pipeline runs as live (tools/clients/handover_pipeline_owner.py).

Refusal lines name pair indexes and rules, never a value.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from tools.clients.json_fields import InterimOwnerPair as PairEntry

MAX_AHEAD = timedelta(days=14)
MATCH, EXPIRED, NONE = "match", "expired", "none"


class InterimPairRefusal(ValueError):
    """The pairs refuse; ``lines`` are value-free."""

    def __init__(self, lines: Iterable[str]) -> None:
        self.lines = tuple(lines)
        super().__init__("; ".join(self.lines))


def parse_expiry(value: str) -> datetime:
    """An ISO date (midnight UTC, the earliest reading) or an offset-qualified time."""

    text = value.strip()
    if len(text) == 10:
        return datetime.fromisoformat(text).replace(tzinfo=UTC)
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("a time needs Z or an offset")
    return parsed.astimezone(UTC)


@dataclass(frozen=True)
class InterimPair:
    full_name: str
    owner: str
    expires_at: datetime
    expires_text: str

    def canonical(self) -> dict[str, str]:
        return {"full_name": self.full_name, "owner": self.owner, "expires_at": self.expires_text}


@dataclass(frozen=True)
class InterimOwnerPairs:
    """The reviewed pairs as of ``now``; ``verdict`` matches exactly or not at all."""

    pairs: tuple[InterimPair, ...] = ()
    now: datetime = datetime.min.replace(tzinfo=UTC)

    def verdict(self, *, full_name: str, owner: str) -> str:
        for pair in self.pairs:
            if pair.full_name == full_name and pair.owner == owner:
                return EXPIRED if pair.expires_at <= self.now else MATCH
        return NONE

    def __bool__(self) -> bool:
        return bool(self.pairs)


def build_pairs(entries: Iterable[PairEntry], *, now: datetime) -> InterimOwnerPairs:
    """Refuse a pair more than 14 days ahead, a repeated object or an unreadable expiry."""

    lines: list[str] = []
    pairs: list[InterimPair] = []
    seen: dict[str, int] = {}
    for index, entry in enumerate(entries):
        first = seen.setdefault(entry.full_name, index)
        if first != index:
            lines.append(f"pair {index}: repeats pair {first}'s object; give each object one pair")
            continue
        try:
            expires_at = parse_expiry(entry.expires_at)
        except ValueError:
            lines.append(f"pair {index}: expires_at is not a date or an offset-qualified time")
            continue
        if expires_at - now > MAX_AHEAD:
            lines.append(f"pair {index}: an interim owner pair expires at most 14 days ahead")
        pairs.append(InterimPair(entry.full_name, entry.owner, expires_at, entry.expires_at))
    if lines:
        raise InterimPairRefusal(lines)
    return InterimOwnerPairs(tuple(pairs), now)


def canonical_pairs(rows: Iterable[Mapping[str, str]]) -> str:
    """Pairs as the single-line JSON value the profile field carries, sorted by object."""

    canonical = [{"full_name": row["full_name"], "owner": row["owner"], "expires_at": row["expires_at"]} for row in rows]
    return json.dumps(sorted(canonical, key=lambda row: row["full_name"]), sort_keys=True, separators=(",", ":"))


__all__ = [
    "EXPIRED",
    "MATCH",
    "MAX_AHEAD",
    "NONE",
    "InterimOwnerPairs",
    "InterimPair",
    "InterimPairRefusal",
    "build_pairs",
    "canonical_pairs",
    "parse_expiry",
]

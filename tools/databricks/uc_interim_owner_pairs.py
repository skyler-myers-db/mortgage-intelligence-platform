"""Interim owner pairs: exact, expiring exceptions to the approved-owner policy (M2-PR8).

Spike S4 has not shown that ``ALTER OWNER`` on a pipeline-published streaming
table or materialized view is accepted, nor that it survives the pipeline's
run_as change. The ownership handover (tools/clients/handover_uc_ownership.py)
therefore leaves those datasets with their current owner and prints one pair
per dataset: ``(full_name, owner, expires_at)``. The reviewed pairs are
per-client configuration, ``MIP_UC_INTERIM_OWNER_PAIRS_JSON`` (identifying: a
GitHub Environment secret or ``.env.local``, never the tree).

A pair is an exception for exactly one object and one owner:

* only an exact match counts: the object's full name and its owner exactly as
  Unity Catalog returns them, character for character. A pair approves its
  owner for no other object, and never approves a grantee
  (``prestop_owner_equivalent`` does not read pairs);
* ``expires_at`` lies at most 14 days ahead whenever the pairs are read, so a
  pair cannot outlive the cutover by being re-read; and
* an expired pair refuses its object (``expired``): the handover's journaled
  re-own, or the pipeline update after release, must have moved it by then.

Refusal lines name pair indexes and rules, never a value.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from pydantic import ValidationError

from tools.clients.json_fields import InterimOwnerPair as PairEntry
from tools.clients.shapes import DuplicateJsonKey, strict_json_loads

INTERIM_PAIRS_ENV = "MIP_UC_INTERIM_OWNER_PAIRS_JSON"
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


def parse_pairs(text: str, *, now: datetime | None = None) -> InterimOwnerPairs:
    """``MIP_UC_INTERIM_OWNER_PAIRS_JSON`` as read at ``now``; blank is no pairs."""

    moment = now or datetime.now(UTC)
    if not text.strip():
        return InterimOwnerPairs((), moment)
    try:
        raw = strict_json_loads(text)
    except DuplicateJsonKey:
        raise InterimPairRefusal(["the pairs repeat a key in one JSON object"]) from None
    except ValueError:
        raise InterimPairRefusal(["the pairs must be single-line JSON"]) from None
    if not isinstance(raw, list):
        raise InterimPairRefusal(["the pairs must be a JSON list"])
    entries: list[PairEntry] = []
    lines: list[str] = []
    for index, item in enumerate(raw):
        try:
            entries.append(PairEntry.model_validate(item))
        except ValidationError as exc:
            fields = sorted({".".join(str(part) for part in error["loc"]) or "pair" for error in exc.errors()})
            lines.append(f"pair {index}: {', '.join(fields)} must be full_name, owner and expires_at")
    if lines:
        raise InterimPairRefusal(lines)
    return build_pairs(entries, now=moment)


def canonical_pairs(pairs: InterimOwnerPairs) -> str:
    """The pairs as the single-line JSON the profile carries (sorted by object)."""

    rows = sorted((pair.canonical() for pair in pairs.pairs), key=lambda row: row["full_name"])
    return json.dumps(rows, sort_keys=True, separators=(",", ":"))


__all__ = [
    "EXPIRED",
    "INTERIM_PAIRS_ENV",
    "MATCH",
    "MAX_AHEAD",
    "NONE",
    "InterimOwnerPairs",
    "InterimPair",
    "InterimPairRefusal",
    "build_pairs",
    "canonical_pairs",
    "parse_expiry",
    "parse_pairs",
]

"""The live geo drill gate: state -> ZIP, plus the retired county grain.

``tools/verify_live.py`` drills the map the way the UI does (PR #182): from the
largest populated state straight to that state's ZIPs. The county grain is
retired by construction (``gold.county_rollup`` emits a NULL ``fips_5``, audit
C2 2026-08-07), so ``/geo/county-rollups`` answers an empty list for every
state; it is probed only as a shape contract.

Everything here fails closed. The checks hold whatever the data:

* the drill target is the state with the largest whole ``addressable`` count,
  ties broken by state code, and it is a 2-letter USPS code;
* the ZIP layer for that state is a non-empty list of in-state rows, each with
  a 5-digit ``zip`` and a whole ``addressable_borrowers``, at least one > 0;
* the drill reconciles exactly:
  ``sum(zip.addressable_borrowers) == state.addressable - state.zip_unassigned_count``
  (``zip_unassigned_count`` is the state's borrowers with no 5-digit ZIP);
* the county answer may be empty, and every row it does ship carries the
  drilled state and a 5-digit ``fips_5``.

The empty ``rollups`` red flag is waived for exactly one probe name,
``COUNTY_PROBE``, and only for the ``rollups`` key.
"""

from __future__ import annotations

import re
import urllib.parse
from collections.abc import Sequence
from typing import Any, Protocol, TypeGuard

STATE_PROBE = "geo.state_rollups.filtered"
ZIP_PROBE = "geo.zip_rollups.filtered"
COUNTY_PROBE = "geo.county_rollups.retired_grain"
RECONCILIATION = "geo.drill_reconciliation"
EMPTY_ALLOWED_PROBES = frozenset({COUNTY_PROBE})

_STATE_RE = re.compile(r"[A-Z]{2}")
_FIVE_DIGITS_RE = re.compile(r"[0-9]{5}")


class RecordedProbe(Protocol):
    """The fields of ``tools.verify_live.ProbeResult`` the drill checks read."""

    name: str
    path: str
    ok: bool
    sample: Any


def whole_count(value: Any) -> int | None:
    """A JSON count: a non-negative int (a bool or a float is not a count)."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def is_state_code(value: Any) -> TypeGuard[str]:
    return isinstance(value, str) and _STATE_RE.fullmatch(value) is not None


def is_five_digits(value: Any) -> TypeGuard[str]:
    return isinstance(value, str) and _FIVE_DIGITS_RE.fullmatch(value) is not None


def _rollups(sample: Any) -> list[Any] | None:
    rollups = sample.get("rollups") if isinstance(sample, dict) else None
    return rollups if isinstance(rollups, list) else None


def pick_drill_state(sample: Any) -> tuple[str | None, str | None]:
    """``(state, None)`` for the largest populated state, else ``(None, reason)``.

    Every row must be an object with a 2-letter ``state`` and a whole
    ``addressable``; one malformed row refuses the pick rather than being
    skipped, so a broken payload cannot move the target.
    """
    rows = _rollups(sample)
    if rows is None:
        return None, "filtered state rollups carried no `rollups` array"
    populated: list[tuple[int, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            return None, "a filtered state rollup row is not an object"
        state = row.get("state")
        addressable = whole_count(row.get("addressable"))
        if not is_state_code(state) or addressable is None:
            return None, (
                f"filtered state rollup row state={state!r} addressable={row.get('addressable')!r} "
                "lacks a 2-letter state or a whole addressable count"
            )
        if addressable > 0:
            populated.append((addressable, state))
    if not populated:
        return None, "no filtered state rollup with addressable borrowers"
    _, state = min(populated, key=lambda item: (-item[0], item[1]))
    return state, None


def pick_drill_zip(sample: Any, state: str) -> str | None:
    """The drilled state's largest populated 5-digit ZIP, ZIP as the tie-break."""
    candidates: list[tuple[int, str]] = []
    for row in _rollups(sample) or []:
        if not isinstance(row, dict) or row.get("state") != state:
            continue
        zip_code = row.get("zip")
        borrowers = whole_count(row.get("addressable_borrowers"))
        if is_five_digits(zip_code) and borrowers is not None and borrowers > 0:
            candidates.append((borrowers, zip_code))
    if not candidates:
        return None
    return min(candidates, key=lambda item: (-item[0], item[1]))[1]


def requested_state(path: str) -> str | None:
    """The ``state`` query parameter the probe actually sent."""
    values = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query).get("state") or []
    return values[0] if len(values) == 1 else None


def _offenders(prefix: str, label: str, bad: list[Any]) -> list[str]:
    """One flag per defect kind: how many rows, and the first offending value."""
    if not bad:
        return []
    return [f"{prefix}{label}: {len(bad)} row(s), first {bad[0]!r}"]


def zip_layer_flags(sample: Any, state: str | None) -> list[str]:
    """The ZIP probe's answer is a populated, in-state, 5-digit-only layer."""
    prefix = f"{ZIP_PROBE}: "
    if not is_state_code(state):
        return [prefix + f"probe path carries no single 2-letter state (got {state!r})"]
    rows = _rollups(sample)
    if rows is None:
        return [prefix + "`rollups` is not an array"]
    flags: list[str] = []
    if sample.get("state") != state:
        flags.append(prefix + f"envelope state {sample.get('state')!r} is not the drilled {state}")
    objects = [row for row in rows if isinstance(row, dict)]
    if len(objects) != len(rows):
        flags.append(prefix + "a ZIP rollup row is not an object")
    bad_zips = [row.get("zip") for row in objects if not is_five_digits(row.get("zip"))]
    flags += _offenders(prefix, "ZIP row without a 5-digit zip", bad_zips)
    other_states = [row.get("state") for row in objects if row.get("state") != state]
    flags += _offenders(prefix, f"ZIP row outside {state}", other_states)
    counts = [whole_count(row.get("addressable_borrowers")) for row in objects]
    bad_counts = [
        row.get("addressable_borrowers") for row, count in zip(objects, counts, strict=True) if count is None
    ]
    flags += _offenders(prefix, "ZIP row without a whole addressable_borrowers", bad_counts)
    if not any(count for count in counts):
        flags.append(prefix + f"no ZIP row with addressable borrowers for {state}")
    return flags


def reconciliation_flags(state_sample: Any, zip_sample: Any, state: str | None) -> list[str]:
    """``sum(zip.addressable_borrowers) == state.addressable - state.zip_unassigned_count``."""
    prefix = f"{RECONCILIATION}: "
    if not is_state_code(state):
        return [prefix + f"no drilled 2-letter state to reconcile (got {state!r})"]
    state_rows = [
        row for row in _rollups(state_sample) or [] if isinstance(row, dict) and row.get("state") == state
    ]
    if len(state_rows) != 1:
        return [prefix + f"expected exactly one filtered state rollup row for {state!r}, found {len(state_rows)}"]
    addressable = whole_count(state_rows[0].get("addressable"))
    unassigned = whole_count(state_rows[0].get("zip_unassigned_count"))
    if addressable is None or unassigned is None:
        return [
            prefix + f"{state} state row lacks a whole addressable or zip_unassigned_count "
            f"(addressable={state_rows[0].get('addressable')!r}, "
            f"zip_unassigned_count={state_rows[0].get('zip_unassigned_count')!r})"
        ]
    zip_rows = _rollups(zip_sample)
    counts = [
        whole_count(row.get("addressable_borrowers")) if isinstance(row, dict) else None for row in zip_rows or []
    ]
    if zip_rows is None or any(count is None for count in counts):
        return [prefix + f"cannot sum the {state} ZIP layer: a row lacks a whole addressable_borrowers"]
    total = sum(count for count in counts if count is not None)
    if total != addressable - unassigned:
        return [
            prefix + f"{state} does not reconcile: sum(zip.addressable_borrowers)={total}, "
            f"expected state.addressable {addressable} - state.zip_unassigned_count {unassigned} "
            f"= {addressable - unassigned}"
        ]
    return []


def county_contract_flags(sample: Any, state: str | None) -> list[str]:
    """Retired grain: any rows (none expected) carry this state and a 5-digit FIPS."""
    prefix = f"{COUNTY_PROBE}: "
    rows = _rollups(sample)
    if rows is None:
        return [prefix + "`rollups` is not an array"]
    flags: list[str] = []
    if not is_state_code(state) or sample.get("state") != state:
        flags.append(prefix + f"envelope state {sample.get('state')!r} is not the drilled {state!r}")
    objects = [row for row in rows if isinstance(row, dict)]
    if len(objects) != len(rows):
        flags.append(prefix + "a county rollup row is not an object")
    bad_fips = [row.get("fips_5") for row in objects if not is_five_digits(row.get("fips_5"))]
    flags += _offenders(prefix, "county row without a 5-digit fips_5", bad_fips)
    other_states = [row.get("state") for row in objects if row.get("state") != state]
    flags += _offenders(prefix, f"county row outside {state}", other_states)
    return flags


def geo_drill_flags(results: Sequence[RecordedProbe]) -> list[str]:
    """Red flags for the recorded drill. A failed probe is flagged by its own status."""

    def find(name: str) -> RecordedProbe | None:
        return next((r for r in results if r.name == name), None)

    flags: list[str] = []
    zip_probe = find(ZIP_PROBE)
    if zip_probe is not None and zip_probe.ok:
        state = requested_state(zip_probe.path)
        flags += zip_layer_flags(zip_probe.sample, state)
        state_probe = find(STATE_PROBE)
        state_sample = state_probe.sample if state_probe is not None and state_probe.ok else None
        flags += reconciliation_flags(state_sample, zip_probe.sample, state)
    county_probe = find(COUNTY_PROBE)
    if county_probe is not None and county_probe.ok:
        flags += county_contract_flags(county_probe.sample, requested_state(county_probe.path))
    return flags

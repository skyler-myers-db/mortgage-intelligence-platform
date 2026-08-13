"""Regenerate ``COUNTY_NAME_EXCLUSIONS`` for the reviewed-analytics location slot.

The shipped ``frontend/public/us-counties.json`` is a static, repo-committed
national artifact, so which of its names collide with a governed term changes
only when the DETECTOR BANKS change -- never at runtime. Screening all ~1.8k
names costs ~5s (each probe is ~2.8ms), which is fine once and far too slow
on a first guard call, so the answer is committed in
``backend/schemas/marketing_selection_reviewed_places.py`` and this script is
how it is re-derived.

Run it whenever a protected-class, health, or national-origin bank changes:

    PYTHONPATH=$PWD python tools/screen_county_place_vocabulary.py

It prints the exclusion tuple to paste back, and exits non-zero when the
committed set has drifted from what the current banks produce.
``test_reviewed_analytics_location.py::test_committed_county_exclusions_are_all_real_collisions``
pins the cheap direction of the same property on every CI run.
"""

from __future__ import annotations

import sys

from backend.schemas.marketing_selection_reviewed_places import (
    COUNTY_NAME_EXCLUSIONS,
    _collides_with_governed_term,
    shipped_county_names,
)


def main() -> int:
    names = sorted(shipped_county_names())
    if not names:
        print("no shipped county names -- is frontend/public/us-counties.json present?")
        return 2
    derived = {name.lower() for name in names if _collides_with_governed_term(name)}
    print(f"screened {len(names)} shipped county names, {len(derived)} collide\n")
    print("COUNTY_NAME_EXCLUSIONS: Final[frozenset[str]] = frozenset(")
    print("    {")
    for name in sorted(derived):
        print(f'        "{name}",')
    print("    }")
    print(")")
    missing = derived - COUNTY_NAME_EXCLUSIONS
    extra = COUNTY_NAME_EXCLUSIONS - derived
    if missing:
        print(f"\nDRIFT: {len(missing)} collide but are ADMITTED: {sorted(missing)}")
    if extra:
        print(f"\nDRIFT: {len(extra)} excluded but no longer collide: {sorted(extra)}")
    return 1 if (missing or extra) else 0


if __name__ == "__main__":
    sys.exit(main())

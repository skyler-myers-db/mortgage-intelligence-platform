#!/usr/bin/env python
"""Trim us-atlas counties-albers-10m.json to the 6-state Module 0
footprint and write the result to frontend/public/us-counties.json.

Module 0's USChoroplethMap renders state-level polygons from
@svg-maps/usa, then drills into county polygons read from the
trimmed TopoJSON shipped at `frontend/public/us-counties.json`. The
upstream us-atlas counties file weighs ~795 KB and contains every
US county; trimming to the footprint states (IL/CA/FL/TX/WA/CO) gets
us under 160 KB while still providing real polygons for every state
in the Cotality eval share.

USAGE
=====

  # one-time download of the upstream file (committed to /tmp, not the repo)
  curl -sL https://unpkg.com/us-atlas@3.0.1/counties-albers-10m.json \
    -o /tmp/counties-albers-10m.json

  # trim and write
  python tools/trim_us_counties.py

WHY ALBERS
==========

The frontend's choropleth (state level) uses @svg-maps/usa, which is
already projected. The county TopoJSON has to share that projection
or the polygons won't align with the state outline they drill from.
us-atlas ships counties-albers-10m.json with that exact projection.

WHY A SCRIPT (vs commit-only output)
====================================

The committed JSON is what the app ships, but a script makes the
provenance + the filter list explicit. Bumping us-atlas, adding a
new footprint state, or moving to a higher-res polygon set is now a
one-line edit + a re-run, not a manual JSON surgery.
"""
from __future__ import annotations

import json
from pathlib import Path

# Mirror CLAUDE.md "do not filter real data to a single metro. The product
# spans the full 6-state share footprint (IL/CA/FL/TX/WA/CO)". Add a state
# here when the Cotality share extends; the trimmer picks it up next run.
FOOTPRINT: dict[str, str] = {
    "06": "CA",
    "08": "CO",
    "12": "FL",
    "17": "IL",
    "48": "TX",
    "53": "WA",
}

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = Path("/tmp/counties-albers-10m.json")
OUT = REPO_ROOT / "frontend" / "public" / "us-counties.json"


def _collect_arcs(geom: dict, used: set[int]) -> None:
    """TopoJSON arcs are referenced by INDEX; ~i means the same arc
    traversed in reverse. Both forms reference the same underlying arc,
    so we normalise to the positive index when collecting the used set.
    """
    t = geom.get("type")
    if t == "Polygon":
        for ring in geom["arcs"]:
            for a in ring:
                used.add(a if a >= 0 else ~a)
    elif t == "MultiPolygon":
        for poly in geom["arcs"]:
            for ring in poly:
                for a in ring:
                    used.add(a if a >= 0 else ~a)


def _remap(geom: dict, old_to_new: dict[int, int]) -> None:
    """Translate arc indices in `geom` from old (full-file) to new
    (trimmed-file) positions, preserving the ~i orientation flag.
    """
    t = geom.get("type")
    if t == "Polygon":
        geom["arcs"] = [
            [
                old_to_new[a] if a >= 0 else ~old_to_new[~a]
                for a in ring
            ]
            for ring in geom["arcs"]
        ]
    elif t == "MultiPolygon":
        geom["arcs"] = [
            [
                [
                    old_to_new[a] if a >= 0 else ~old_to_new[~a]
                    for a in ring
                ]
                for ring in poly
            ]
            for poly in geom["arcs"]
        ]


def main() -> int:
    if not SRC.exists():
        print(
            f"error: source TopoJSON not found at {SRC}. "
            f"Run:\n  curl -sL https://unpkg.com/us-atlas@3.0.1/counties-albers-10m.json "
            f"-o {SRC}"
        )
        return 1

    with SRC.open() as f:
        topo = json.load(f)

    counties = topo["objects"]["counties"]["geometries"]
    keep = [
        g
        for g in counties
        if str(g.get("id", "")).startswith(tuple(FOOTPRINT.keys()))
    ]
    print(f"source counties: {len(counties)}")
    print(f"kept (footprint): {len(keep)}")

    used: set[int] = set()
    for g in keep:
        _collect_arcs(g, used)
    sorted_used = sorted(used)
    old_to_new = {old: new for new, old in enumerate(sorted_used)}
    for g in keep:
        _remap(g, old_to_new)

    new_topology = {
        "type": topo["type"],
        "transform": topo.get("transform"),
        "bbox": topo.get("bbox"),
        "arcs": [topo["arcs"][i] for i in sorted_used],
        "objects": {
            "counties": {
                "type": topo["objects"]["counties"]["type"],
                "geometries": keep,
            }
        },
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w") as f:
        json.dump(new_topology, f, separators=(",", ":"))
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes, {len(used)} arcs)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

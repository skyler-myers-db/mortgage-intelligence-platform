"""County FIPS -> display-name lookup.

The app ships a public-domain us-atlas county TopoJSON for map drill-downs.
Use the same artifact to label API responses when Cotality provides FIPS but
not a county-name column. This keeps display labels data-derived and national,
without adding a hand-maintained six-county lookup.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def _county_name_map() -> dict[str, str]:
    root = Path(__file__).resolve().parents[2]
    path = root / "frontend" / "public" / "us-counties.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    geometries = payload.get("objects", {}).get("counties", {}).get("geometries", [])
    out: dict[str, str] = {}
    for geometry in geometries:
        fips = str(geometry.get("id") or "")[:5]
        name = geometry.get("properties", {}).get("name")
        if len(fips) == 5 and name:
            out[fips] = str(name)
    return out


def county_name_for_fips(fips_5: str | None) -> str | None:
    fips = str(fips_5 or "")[:5]
    if len(fips) != 5:
        return None
    return _county_name_map().get(fips)

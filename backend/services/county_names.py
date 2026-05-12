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


def county_fips_for_name(term: str | None, *, limit: int = 25) -> list[str]:
    """Return FIPS codes whose public county name matches ``term``.

    This is intentionally backed by the shipped national TopoJSON instead of
    a demo-footprint lookup. It lets global search resolve county names even
    when the current Cotality rollup has FIPS codes but no county-name column.
    """
    normalized = str(term or "").strip().lower()
    if not normalized:
        return []
    normalized = " ".join(part for part in normalized.replace(".", " ").split() if part != "county")
    if len(normalized) < 2:
        return []
    matches: list[tuple[int, str]] = []
    for fips, name in _county_name_map().items():
        county = name.lower()
        if county == normalized:
            rank = 0
        elif county.startswith(normalized):
            rank = 1
        elif normalized in county:
            rank = 2
        else:
            continue
        matches.append((rank, fips))
    return [fips for _rank, fips in sorted(matches)[: max(1, min(limit, 50))]]

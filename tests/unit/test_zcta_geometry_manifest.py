"""The committed per-state ZCTA geometry and its operator-only tool (audit dataviz-01).

``tools/geo/build_zcta_topojson.mjs`` writes 51 ``<USPS>.topo.json`` files and a
``manifest.json`` under ``frontend/src/geo/zcta`` (D-dataviz-geo-e part A). This
suite is the only gate on the geometry bytes (the frontend budget gates JS and
CSS). Stdlib only. ``geometry_problems`` checks ANY directory, so a scratch
copy is checked the same way as the committed one:

* the manifest's sha256 and bytes match each file, and there are 51 files;
* each file is at most 1.5 MiB and 120,000 arc points, the set at most 20 MiB;
* each topology has exactly two objects: ``zcta`` (a GeometryCollection with
  sorted, unique 5-digit ids) and ``state`` (one geometry, 2-digit id);
* no ``properties`` member anywhere;
* ``manifest.sources`` equals the tool's three pinned URLs and hashes, and the
  manifest is not a fixture-pinned test build.

Until the operator build is approved and committed, the committed-geometry
case skips with 'geometry not committed: operator build pending'; every other
case (the checker on a synthetic scratch copy, its mutation controls, the tool
pins, the CI audit, the package isolation) runs now.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
GEO_DIR = ROOT / "frontend" / "src" / "geo" / "zcta"
TOOL = ROOT / "tools" / "geo" / "build_zcta_topojson.mjs"
TOOL_PACKAGE = ROOT / "tools" / "geo" / "package.json"
TOOL_LOCK = ROOT / "tools" / "geo" / "package-lock.json"

MIB = 1024 * 1024
MAX_FILE_BYTES = int(1.5 * MIB)
MAX_ARC_POINTS = 120_000
MAX_TOTAL_BYTES = 20 * MIB

USPS = (
    "AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV "
    "NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY"
).split()
EXPECTED_FILES = {f"{usps}.topo.json" for usps in USPS}
CENSUS_URLS = {
    "zcta": "https://www2.census.gov/geo/tiger/GENZ2020/shp/cb_2020_us_zcta520_500k.zip",
    "relationship": "https://www2.census.gov/geo/docs/maps-data/data/rel2020/zcta520/tab20_zcta520_county20_natl.txt",
    "state": "https://www2.census.gov/geo/tiger/GENZ2020/shp/cb_2020_us_state_500k.zip",
}
TOOL_ONLY_PACKAGES = ("d3-geo", "d3-geo-projection", "shapefile", "topojson-server", "topojson-simplify")
_SOURCE_RE = re.compile(r"name: '(\w+)',\s*url: '([^']+)',\s*sha256: (null|'[0-9a-f]{64}')")
_BLOCKED_LICENSE_TERMS = ("agpl", "gpl", "lgpl", "cc-by-nc", "noncommercial", "commons clause")
_SKIP_REASON = "geometry not committed: operator build pending"


def tool_sources() -> list[dict[str, str | None]]:
    """The tool's pinned SOURCES, in order: name, url and sha256 (None while unpinned)."""
    return [
        {"name": name, "url": url, "sha256": None if sha == "null" else sha.strip("'")}
        for name, url, sha in _SOURCE_RE.findall(TOOL.read_text(encoding="utf-8"))
    ]


def _has_properties(value: Any) -> bool:
    if isinstance(value, dict):
        if "properties" in value:
            return True
        return any(_has_properties(item) for key, item in value.items() if key != "arcs")
    if isinstance(value, list):
        return any(_has_properties(item) for item in value)
    return False


def topology_problems(name: str, topology: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    if topology.get("type") != "Topology":
        problems.append(f"{name}: not a Topology")
    objects = topology.get("objects", {})
    if set(objects) != {"zcta", "state"}:
        problems.append(f"{name}: objects are {sorted(objects)}, expected exactly zcta and state")
        return problems
    zcta, state = objects["zcta"], objects["state"]
    if zcta.get("type") != "GeometryCollection":
        problems.append(f"{name}: zcta is not a GeometryCollection")
    ids = [geometry.get("id") for geometry in zcta.get("geometries", [])]
    if not all(isinstance(zid, str) and re.fullmatch(r"\d{5}", zid) for zid in ids):
        problems.append(f"{name}: a zcta id is not a 5-digit string")
    elif ids != sorted(set(ids)):
        problems.append(f"{name}: zcta ids are not sorted and unique")
    if state.get("type") not in {"Polygon", "MultiPolygon"}:
        problems.append(f"{name}: state is not one polygon geometry")
    if not (isinstance(state.get("id"), str) and re.fullmatch(r"\d{2}", state["id"])):
        problems.append(f"{name}: state id is not a 2-digit string")
    if _has_properties(topology):
        problems.append(f"{name}: carries a properties member")
    return problems


def geometry_problems(
    directory: Path,
    pinned: list[dict[str, str | None]],
    expected_files: set[str] = EXPECTED_FILES,
) -> list[str]:
    """Every way ``directory`` fails the committed-geometry contract (empty when it passes)."""
    manifest_path = directory / "manifest.json"
    if not manifest_path.is_file():
        return [f"{directory}: no manifest.json"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    problems: list[str] = []
    if manifest.get("fixture_pins"):
        problems.append("manifest is a fixture-pinned test build")
    sources = [{key: source.get(key) for key in ("name", "url", "sha256")} for source in manifest.get("sources", [])]
    if sources != pinned or any(source["sha256"] is None for source in pinned):
        problems.append("manifest sources differ from the tool's pinned SOURCES (or a pin is missing)")
    files: dict[str, dict[str, Any]] = manifest.get("files", {})
    on_disk = {path.name for path in directory.glob("*.topo.json")}
    if set(files) != expected_files or on_disk != expected_files:
        problems.append(f"expected the {len(expected_files)} state files in the manifest and on disk")
    total = 0
    for name in sorted(set(files) & on_disk):
        raw = (directory / name).read_bytes()
        stats = files[name]
        total += len(raw)
        if stats.get("bytes") != len(raw) or stats.get("sha256") != hashlib.sha256(raw).hexdigest():
            problems.append(f"{name}: bytes or sha256 differ from the manifest")
        if len(raw) > MAX_FILE_BYTES:
            problems.append(f"{name}: {len(raw)} bytes > {MAX_FILE_BYTES}")
        topology = json.loads(raw)
        points = sum(len(arc) for arc in topology.get("arcs", []))
        if points > MAX_ARC_POINTS or stats.get("arc_point_count") != points:
            problems.append(f"{name}: {points} arc points (limit {MAX_ARC_POINTS}, manifest {stats.get('arc_point_count')})")
        geometries = topology.get("objects", {}).get("zcta", {}).get("geometries", [])
        if stats.get("zcta_count") != len(geometries):
            problems.append(f"{name}: zcta_count differs from the file")
        problems.extend(topology_problems(name, topology))
    if total > MAX_TOTAL_BYTES:
        problems.append(f"the geometry totals {total} bytes > {MAX_TOTAL_BYTES}")
    return problems


# --- a synthetic scratch copy ---------------------------------------------

_PINS = [{"name": name, "url": url, "sha256": f"{index:064x}"} for index, (name, url) in enumerate(CENSUS_URLS.items(), 1)]


def _square(arc: int) -> dict[str, Any]:
    return {"type": "Polygon", "arcs": [[arc]]}


def _write_copy(directory: Path, *, mutate: dict[str, Any] | None = None) -> None:
    """51 tiny valid topologies plus a manifest that describes them exactly."""
    directory.mkdir(parents=True, exist_ok=True)
    files: dict[str, dict[str, Any]] = {}
    for index, usps in enumerate(USPS):
        topology: dict[str, Any] = {
            "type": "Topology",
            "transform": {"scale": [0.001, 0.001], "translate": [100.0 + index, 200.0]},
            "objects": {
                "zcta": {"type": "GeometryCollection", "geometries": [
                    {**_square(0), "id": "60611"},
                    {**_square(1), "id": "60612"},
                ]},
                "state": {**_square(2), "id": f"{index + 1:02d}"},
            },
            "arcs": [[[0, 0], [0, 10], [10, 0], [0, -10], [-10, 0]]] * 3,
        }
        if mutate and usps in mutate:
            mutate[usps](topology)
        text = json.dumps(topology).encode()
        (directory / f"{usps}.topo.json").write_bytes(text)
        files[f"{usps}.topo.json"] = {
            "arc_point_count": 15,
            "bytes": len(text),
            "sha256": hashlib.sha256(text).hexdigest(),
            "zcta_count": 2,
            "zcta_bbox": [0, 0, 1, 1],
            "state_bbox": [0, 0, 1, 1],
        }
    manifest = {"sources": _PINS, "files": files, "quantization": 100000, "weight": 0.0001}
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def test_the_checker_passes_a_valid_scratch_copy(tmp_path: Path) -> None:
    _write_copy(tmp_path)
    assert geometry_problems(tmp_path, _PINS) == []


def _add_properties(topology: dict[str, Any]) -> None:
    topology["objects"]["zcta"]["geometries"][1]["properties"] = {"NAME20": "ZCTA5 60612"}


def _unsorted(topology: dict[str, Any]) -> None:
    topology["objects"]["zcta"]["geometries"].reverse()


def _third_object(topology: dict[str, Any]) -> None:
    topology["objects"]["county"] = _square(0)


def _collection_state(topology: dict[str, Any]) -> None:
    topology["objects"]["state"] = {"type": "GeometryCollection", "geometries": []}


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        (_add_properties, "TX.topo.json: carries a properties member"),
        (_unsorted, "TX.topo.json: zcta ids are not sorted and unique"),
        (_third_object, "TX.topo.json: objects are ['county', 'state', 'zcta'], expected exactly zcta and state"),
        (_collection_state, "TX.topo.json: state is not one polygon geometry"),
    ],
)
def test_the_checker_refuses_a_broken_topology(tmp_path: Path, mutation: Any, expected: str) -> None:
    # The manifest describes the mutated bytes, so only the topology check fires.
    _write_copy(tmp_path, mutate={"TX": mutation})
    assert expected in geometry_problems(tmp_path, _PINS)


def test_the_checker_refuses_drift_from_the_manifest_and_the_pins(tmp_path: Path) -> None:
    _write_copy(tmp_path)
    (tmp_path / "IL.topo.json").write_bytes((tmp_path / "IL.topo.json").read_bytes() + b" ")
    assert "IL.topo.json: bytes or sha256 differ from the manifest" in geometry_problems(tmp_path, _PINS)
    other = [{**source, "sha256": "f" * 64} for source in _PINS]
    assert any("pinned SOURCES" in problem for problem in geometry_problems(tmp_path, other))
    (tmp_path / "WY.topo.json").unlink()
    assert any("51 state files" in problem for problem in geometry_problems(tmp_path, _PINS))


def test_the_checker_refuses_a_fixture_pinned_build(tmp_path: Path) -> None:
    _write_copy(tmp_path)
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    (tmp_path / "manifest.json").write_text(json.dumps({**manifest, "fixture_pins": True}), encoding="utf-8")
    assert "manifest is a fixture-pinned test build" in geometry_problems(tmp_path, _PINS)


def test_the_checker_refuses_a_file_over_the_limits(tmp_path: Path) -> None:
    def oversize(topology: dict[str, Any]) -> None:
        topology["arcs"] = [[[1, 1]] * (MAX_ARC_POINTS + 1)]

    _write_copy(tmp_path, mutate={"CA": oversize})
    problems = geometry_problems(tmp_path, _PINS)
    assert any(problem.startswith("CA.topo.json:") and "arc points" in problem for problem in problems)


# --- the committed geometry ---------------------------------------------------


def test_the_committed_geometry_matches_its_manifest_and_the_tool_pins() -> None:
    if not (GEO_DIR / "manifest.json").is_file() and not list(GEO_DIR.glob("*.topo.json")):
        pytest.skip(_SKIP_REASON)
    assert geometry_problems(GEO_DIR, tool_sources()) == []


def test_the_geometry_dir_documents_provenance_and_the_overlay_boundary() -> None:
    readme = (GEO_DIR / "README.md").read_text(encoding="utf-8")
    assert (
        "This layer is shape-only. Demographic, ACS or composition overlays on it are out of "
        "scope and require a fair-lending review."
    ) in readme
    assert "17 U.S.C. 105" in readme
    for url in CENSUS_URLS.values():
        assert url in readme
    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines()
    assert "frontend/src/geo/zcta/*.topo.json linguist-generated=true" in attributes


# --- the tool, its pins and its isolation ---------------------------------------


def test_the_tool_pins_exactly_the_three_census_sources() -> None:
    sources = tool_sources()
    assert [(source["name"], source["url"]) for source in sources] == list(CENSUS_URLS.items())
    # Unpinned (None) until the first approved download; the tool refuses it then.
    for source in sources:
        assert source["sha256"] is None or re.fullmatch(r"[0-9a-f]{64}", str(source["sha256"]))


def test_ci_audits_the_geo_tool_lock_at_high() -> None:
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "run: npm --prefix tools/geo audit --audit-level=high" in ci


def test_the_geo_tool_is_private_with_exact_pins_and_no_license_blocker() -> None:
    package = json.loads(TOOL_PACKAGE.read_text(encoding="utf-8"))
    lock = json.loads(TOOL_LOCK.read_text(encoding="utf-8"))
    assert package["private"] is True
    assert package["type"] == "module"
    dependencies = {**package.get("dependencies", {}), **package.get("devDependencies", {})}
    assert set(dependencies) == {*TOOL_ONLY_PACKAGES, "topojson-client"}
    for name, version in dependencies.items():
        assert re.fullmatch(r"\d+\.\d+\.\d+", version), f"{name} must be an exact pin, got {version!r}"
        assert lock["packages"][f"node_modules/{name}"]["version"] == version
    assert lock["packages"][""]["dependencies"] == package["dependencies"]
    blockers = [
        f"{path}: {meta.get('license')}"
        for path, meta in lock["packages"].items()
        if path and any(term in str(meta.get("license") or "").lower() for term in _BLOCKED_LICENSE_TERMS)
    ]
    assert blockers == []


def test_the_frontend_never_takes_a_geo_tool_package() -> None:
    frontend = json.loads((ROOT / "frontend" / "package.json").read_text(encoding="utf-8"))
    frontend_deps = {**frontend.get("dependencies", {}), **frontend.get("devDependencies", {})}
    assert not set(TOOL_ONLY_PACKAGES) & set(frontend_deps)
    # topojson-client is the one shared package: the browser decodes the files with it.
    tool = json.loads(TOOL_PACKAGE.read_text(encoding="utf-8"))
    assert frontend_deps["topojson-client"] == tool["dependencies"]["topojson-client"]

"""Frontend dependency pins (audit 2026-09-21 stack-10, W5b dependency batch).

Exact pins everywhere, the TanStack query packages in lockstep (one
query-core in the lock), web-vitals as an exact production dependency (W5c
field vitals consume it), no stylelint until a patched braces exists (removed
2026-10-06, GHSA-vfj7-8cjw-p6xm), the held
majors (D-platform-process-a, review_by 2026-11-15; Playwright held to the VRT
image), and one gitleaks version across the CI workflow.

W5e (w5-deps-report): tools/dependency_holds.json is the source of truth for
every dated hold (the weekly report renders a held package as held). The
held majors below are derived from it, every hold must name a pinned package
whose current pin is below its bound, and an expired ``review_by`` is NOT a
failure here: the report owns that signal (exit 1, ``hold_review_passed``).
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE_JSON = ROOT / "frontend" / "package.json"
PACKAGE_LOCK = ROOT / "frontend" / "package-lock.json"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
HOLDS = ROOT / "tools" / "dependency_holds.json"
REQUIREMENTS_IN = ROOT / "requirements.in"
UV_LOCK = ROOT / "uv.lock"
EXACT = re.compile(r"^\d+\.\d+\.\d+$")
TANSTACK_PAIR = ("@tanstack/react-query", "@tanstack/react-query-persist-client")
TANSTACK_CORES = ("@tanstack/query-core", "@tanstack/query-persist-client-core")
HELD_MAJORS = {"typescript": 6, "vitest": 4, "@babel/core": 7}
MAJOR_HOLDS = ("typescript", "vitest", "@babel/core")
PLAYWRIGHT_PIN = "1.63.0"
WEB_VITALS_PIN = "6.2.2"


def _package() -> dict:
    return json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))


def _lock_packages() -> dict:
    return json.loads(PACKAGE_LOCK.read_text(encoding="utf-8"))["packages"]


def _holds() -> list[dict]:
    return json.loads(HOLDS.read_text(encoding="utf-8"))["holds"]


def _version(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.match(r"\d+(?:\.\d+)*", value).group(0).split("."))


def _python_pins(path: Path) -> dict[str, str]:
    pins: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?==([^\s;#]+)", line.strip())
        if match:
            pins[re.sub(r"[-_.]+", "-", match.group(1)).lower()] = match.group(2)
    return pins


def hold_problems(holds: list[dict], npm_pins: dict[str, str], pypi_pins: dict[str, str]) -> list[str]:
    """A hold must name a pinned package whose current pin is below its bound."""

    problems: list[str] = []
    for hold in holds:
        keys = {"ecosystem", "package", "literals" if hold.get("ecosystem") == "tool-literal" else "below", "reason", "review_by"}
        if set(hold) != keys:
            problems.append(f"{hold.get('package')}: keys {sorted(hold)} are not the schema {sorted(keys)}")
            continue
        date.fromisoformat(hold["review_by"])  # a date, never a test of whether it has passed
        if hold["ecosystem"] == "tool-literal":
            continue
        pins = npm_pins if hold["ecosystem"] == "npm" else pypi_pins
        pinned = pins.get(hold["package"])
        if pinned is None:
            problems.append(f"{hold['package']}: no {hold['ecosystem']} pin to hold")
        elif _version(pinned) >= _version(hold["below"]):
            problems.append(f"{hold['package']}: pin {pinned} is not below {hold['below']}")
    return problems


def _all_deps(package: dict) -> dict[str, str]:
    return {**package.get("dependencies", {}), **package.get("devDependencies", {})}


def gitleaks_versions(text: str) -> list[str]:
    return re.findall(r'GITLEAKS_VERSION="([^"]+)"', text)


def tanstack_problems(package: dict, lock: dict) -> list[str]:
    deps = package.get("dependencies", {})
    pins = {name: deps.get(name) for name in TANSTACK_PAIR}
    problems: list[str] = []
    if len(set(pins.values())) != 1 or None in pins.values():
        problems.append(f"TanStack pins differ: {pins}")
        return problems
    pin = pins[TANSTACK_PAIR[0]]
    for name in (*TANSTACK_PAIR, *TANSTACK_CORES):
        entries = [key for key in lock if key == f"node_modules/{name}" or key.endswith(f"/node_modules/{name}")]
        if entries != [f"node_modules/{name}"]:
            problems.append(f"{name}: expected exactly one top-level lock entry, found {entries}")
        elif lock[entries[0]].get("version") != pin:
            problems.append(f"{name}: lock has {lock[entries[0]].get('version')}, pin is {pin}")
    return problems


def test_every_frontend_dependency_is_an_exact_pin() -> None:
    loose = {name: spec for name, spec in _all_deps(_package()).items() if not EXACT.match(spec)}

    assert loose == {}


def test_the_tanstack_query_packages_move_in_lockstep() -> None:
    assert tanstack_problems(_package(), _lock_packages()) == []


def test_web_vitals_is_an_exact_production_dependency() -> None:
    package = _package()

    assert package["dependencies"].get("web-vitals") == WEB_VITALS_PIN
    assert "web-vitals" not in package.get("devDependencies", {})
    entry = _lock_packages()["node_modules/web-vitals"]
    assert entry["version"] == WEB_VITALS_PIN and entry.get("dev") is not True
    assert entry["license"] == "Apache-2.0"


def test_stylelint_waits_for_a_patched_braces() -> None:
    """stylelint 17 reaches braces through micromatch, and every braces release
    (<= 3.0.3) carries GHSA-vfj7-8cjw-p6xm (high, no patched version on
    2026-10-06), which turns CI's npm audit gate red. The W5b batch added
    stylelint with no script calling it, so it was removed rather than
    excused. W5e w5-compiler-lint re-adds it once braces is patched or the
    lint path avoids it, and updates this test with the patched pin."""
    assert "stylelint" not in _all_deps(_package())
    assert not [key for key in _lock_packages() if key.endswith("node_modules/braces")]


def test_the_held_majors_stay_held() -> None:
    deps = _all_deps(_package())

    for name, major in HELD_MAJORS.items():
        assert int(deps[name].split(".")[0]) == major, f"{name} {deps[name]} is held at {major}.x"
    assert deps["@playwright/test"] == PLAYWRIGHT_PIN


def test_the_held_majors_are_the_holds_file_rows() -> None:
    by_name = {hold["package"]: hold for hold in _holds()}

    for name in MAJOR_HOLDS:
        assert by_name[name]["ecosystem"] == "npm"
        assert _version(by_name[name]["below"]) == (HELD_MAJORS[name] + 1, 0, 0), name
    assert set(HELD_MAJORS) == set(MAJOR_HOLDS)


def test_the_holds_file_has_exactly_the_schema_keys() -> None:
    document = json.loads(HOLDS.read_text(encoding="utf-8"))

    assert set(document) == {"holds"}
    assert document["holds"], "the seeded holds are present"
    assert {hold["ecosystem"] for hold in document["holds"]} <= {"npm", "pypi", "tool-literal"}


def test_every_hold_names_a_pin_below_its_bound() -> None:
    npm_pins = _all_deps(_package())
    pypi_pins = {**_python_pins(REQUIREMENTS_IN), **_python_pins(UV_LOCK)}

    assert hold_problems(_holds(), npm_pins, pypi_pins) == []


def test_the_hold_rules_reject_planted_drift_and_accept_an_expired_date() -> None:
    hold = {"ecosystem": "npm", "package": "vitest", "below": "5.0.0", "reason": "r", "review_by": "2000-01-01"}

    assert hold_problems([hold], {"vitest": "4.1.11"}, {}) == [], "an expired review_by is the report's signal"
    assert hold_problems([hold], {"vitest": "5.0.4"}, {}) != [], "a pin at or above its bound"
    assert hold_problems([hold], {}, {}) != [], "a hold on an unpinned package"
    assert hold_problems([{**hold, "extra": 1}], {"vitest": "4.1.11"}, {}) != [], "an unknown key"


def test_both_gitleaks_version_literals_are_equal() -> None:
    versions = gitleaks_versions(CI_WORKFLOW.read_text(encoding="utf-8"))

    assert len(versions) == 2, versions
    assert len(set(versions)) == 1, f"ci.yml pins two gitleaks versions: {versions}"


def test_the_rules_reject_planted_drift() -> None:
    """Non-vacuity, in memory: each rule above fails on the drift it guards."""

    package = {"dependencies": {"@tanstack/react-query": "5.104.0", "@tanstack/react-query-persist-client": "5.103.0"}}
    assert tanstack_problems(package, {}) != []
    same = {"dependencies": dict.fromkeys(TANSTACK_PAIR, "5.104.0")}
    nested = {f"node_modules/{n}": {"version": "5.104.0"} for n in (*TANSTACK_PAIR, *TANSTACK_CORES)}
    nested["node_modules/x/node_modules/@tanstack/query-core"] = {"version": "5.100.10"}
    assert any("exactly one" in p for p in tanstack_problems(same, nested))
    assert not EXACT.match("^6.2.2")
    assert len(set(gitleaks_versions('GITLEAKS_VERSION="8.30.1"\nGITLEAKS_VERSION="8.28.0"'))) == 2

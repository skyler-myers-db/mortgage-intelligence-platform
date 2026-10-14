"""Gate: tests carry synthetic identities only (W5c fix round 1 NB-a).

Test rosters, request headers and fixture rows use ``@summit.example``
aliases. A real operator's address in a test leaks into CI logs, VRT
captures and fixture exports, and a fake roster that seeds it teaches
every reader that the real operator is the test admin.

This gate scans every Python test, every frontend unit test and
test-support module, and every Playwright spec or fixture module, and
fails on the operator's real mail domain. The only exemption is the
documented security-contact pin, which mirrors SECURITY.md.

Live tests (``tests/integration/*_live.py`` and the approve stress test)
act against the roster that ``lakebase/seed_campaigns.sql`` deploys, so
they never name an actor at all: they resolve it at test start through
``tests/integration/_live_actor.py`` (integrator correction C8).

Convert a red file with ``python3 tools/test_harness/h2_synthetic_identities.py``.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

# Spelled in two parts so this file does not match its own needle.
_REAL_DOMAIN = "entrada" + ".ai"
_NEEDLE_RE = re.compile(r"@" + re.escape(_REAL_DOMAIN), re.IGNORECASE)

SCAN_GLOBS: tuple[str, ...] = (
    "tests/**/*.py",
    "frontend/src/**/*.test.ts",
    "frontend/src/**/*.test.tsx",
    "frontend/src/**/*.test-support.ts",
    "frontend/src/**/*.test-support.tsx",
    "frontend/tests/**/*.ts",
)

# (file, exact address) pairs allowed to keep the real domain, with why.
EXEMPT: dict[tuple[str, str], str] = {
    (
        "tests/unit/test_documentation_contract.py",
        "security@" + _REAL_DOMAIN,
    ): "the documented security contact; SECURITY.md names this real address",
}

# Live tests that act as the target roster's admin: they must resolve it.
ROSTER_ACTING_LIVE_TESTS: tuple[str, ...] = (
    "tests/integration/test_approval_funnel_live.py",
    "tests/integration/test_loan_officer_assignment_live.py",
    "tests/integration/test_geo_assignment_overlay_live.py",
    "tests/integration/test_approve_stress.py",
)

_ADDRESS_RE = re.compile(r"[A-Za-z0-9._%+{}$-]*@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
_ACTOR_NAME_RE = re.compile(r"ACTOR|ADMIN|OPERATOR", re.IGNORECASE)


def _scanned_files() -> list[Path]:
    seen: dict[Path, None] = {}
    for pattern in SCAN_GLOBS:
        for path in sorted(ROOT.glob(pattern)):
            if "node_modules" in path.parts or not path.is_file():
                continue
            seen.setdefault(path, None)
    return list(seen)


def _real_domain_hits(rel: str, text: str) -> list[tuple[int, str]]:
    hits: list[tuple[int, str]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if not _NEEDLE_RE.search(line):
            continue
        addresses = [m.group(0) for m in _ADDRESS_RE.finditer(line) if _NEEDLE_RE.search(m.group(0))]
        for address in addresses or [line.strip()]:
            if (rel, address.lower()) not in EXEMPT:
                hits.append((lineno, address))
    return hits


def test_the_scan_is_not_vacuous() -> None:
    files = _scanned_files()
    assert len(files) >= 400, f"identity gate scanned only {len(files)} files"
    rels = {p.relative_to(ROOT).as_posix() for p in files}
    # Every root contributes: Python tests, frontend unit tests, Playwright specs.
    assert any(r.startswith("tests/") for r in rels)
    assert any(r.startswith("frontend/src/") for r in rels)
    assert any(r.startswith("frontend/tests/") for r in rels)


def test_no_test_file_carries_a_real_domain_identity() -> None:
    offenders: list[str] = []
    for path in _scanned_files():
        rel = path.relative_to(ROOT).as_posix()
        for lineno, address in _real_domain_hits(rel, path.read_text(encoding="utf-8")):
            offenders.append(f"{rel}:{lineno}: {address}")
    assert not offenders, (
        "real-domain identities in tests (use an @summit.example alias; "
        "convert with tools/test_harness/h2_synthetic_identities.py):\n"
        + "\n".join(offenders)
    )


@pytest.mark.parametrize(("rel", "address"), sorted(EXEMPT))
def test_each_exemption_is_still_needed(rel: str, address: str) -> None:
    text = (ROOT / rel).read_text(encoding="utf-8")
    assert address in text.lower(), f"stale exemption: {rel} no longer names {address}"


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ('    "email": "someone@' + _REAL_DOMAIN + '",', 1),
        ("actor = f\"integration-s4+{uuid4().hex[:12]}@" + _REAL_DOMAIN.upper() + '"', 1),
        ("created_by: 'operator@summit.example',", 0),
    ],
)
def test_the_detector_sees_a_planted_address(line: str, expected: int) -> None:
    assert len(_real_domain_hits("tests/unit/planted.py", line)) == expected


def _module_level_identity_constants(tree: ast.Module) -> list[str]:
    names: list[str] = []
    for node in tree.body:
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Constant):
            continue
        if not isinstance(node.value.value, str) or not _ADDRESS_RE.fullmatch(node.value.value):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and _ACTOR_NAME_RE.search(target.id):
                names.append(target.id)
    return names


def _live_test_files() -> list[Path]:
    return sorted((ROOT / "tests" / "integration").glob("*_live.py"))


def test_live_tests_name_no_actor_identity() -> None:
    files = _live_test_files()
    assert len(files) >= 10, "the live-test scan found too few *_live.py files"
    offenders: list[str] = []
    for path in [*files, ROOT / "tests" / "integration" / "test_approve_stress.py"]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for name in _module_level_identity_constants(tree):
            offenders.append(f"{path.relative_to(ROOT).as_posix()}: {name}")
    assert not offenders, (
        "live tests must resolve their actor via tests/integration/_live_actor.py, "
        "never a module-level identity constant:\n" + "\n".join(offenders)
    )


@pytest.mark.parametrize("rel", ROSTER_ACTING_LIVE_TESTS)
def test_roster_acting_live_tests_resolve_their_actor(rel: str) -> None:
    text = (ROOT / rel).read_text(encoding="utf-8")
    assert "from tests.integration._live_actor import resolve_live_admin_actor" in text
    assert "resolve_live_admin_actor(" in text.split("import resolve_live_admin_actor", 1)[1]


def test_the_constant_detector_sees_a_planted_actor() -> None:
    planted = ast.parse('_ADMIN_ACTOR = "someone@summit.example"\n_SEEDED_LO_01 = "5555"\n')
    assert _module_level_identity_constants(planted) == ["_ADMIN_ACTOR"]

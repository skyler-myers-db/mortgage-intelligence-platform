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
# Names an acting identity: an actor, admin, operator or approver variable,
# keyword or dict key, an assigner, or the edge identity header.
_ACTOR_SLOT_RE = re.compile(
    r"actor|admin|operator|approver|assigned_by|forwarded[-_]email", re.IGNORECASE
)


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


def _is_address_literal(node: ast.AST | None) -> bool:
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and _ADDRESS_RE.fullmatch(node.value) is not None
    )


def _slot_name(target: ast.expr) -> str | None:
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    return None


def _actor_identity_literals(tree: ast.AST) -> list[str]:
    """Every address literal bound to an actor slot, at any depth.

    A slot is an assignment target (module, function or attribute), a
    keyword argument or a string dict key whose name matches
    ``_ACTOR_SLOT_RE``. An f-string (a per-run synthetic actor) and an
    address in another slot (an assignee such as ``assigned_to_email``)
    are not actor identities.
    """
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign | ast.AnnAssign) and _is_address_literal(node.value):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                name = _slot_name(target)
                if name and _ACTOR_SLOT_RE.search(name):
                    found.append(f"{node.lineno}: {name}")
        elif isinstance(node, ast.keyword) and node.arg and _ACTOR_SLOT_RE.search(node.arg):
            if _is_address_literal(node.value):
                found.append(f"{node.value.lineno}: {node.arg}=")
        elif isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values, strict=True):
                if (
                    isinstance(key, ast.Constant)
                    and isinstance(key.value, str)
                    and _ACTOR_SLOT_RE.search(key.value)
                    and _is_address_literal(value)
                ):
                    found.append(f"{key.lineno}: {key.value!r}")
    return found


def _live_test_files() -> list[Path]:
    return sorted((ROOT / "tests" / "integration").glob("*_live.py"))


def test_live_tests_name_no_actor_identity() -> None:
    files = _live_test_files()
    assert len(files) >= 10, "the live-test scan found too few *_live.py files"
    offenders: list[str] = []
    for path in [*files, ROOT / "tests" / "integration" / "test_approve_stress.py"]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for slot in _actor_identity_literals(tree):
            offenders.append(f"{path.relative_to(ROOT).as_posix()}:{slot}")
    assert not offenders, (
        "live tests must resolve their actor via tests/integration/_live_actor.py, "
        "never an identity literal in an actor slot:\n" + "\n".join(offenders)
    )


@pytest.mark.parametrize("rel", ROSTER_ACTING_LIVE_TESTS)
def test_roster_acting_live_tests_resolve_their_actor(rel: str) -> None:
    text = (ROOT / rel).read_text(encoding="utf-8")
    assert "from tests.integration._live_actor import resolve_live_admin_actor" in text
    assert "resolve_live_admin_actor(" in text.split("import resolve_live_admin_actor", 1)[1]


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ('_ADMIN_ACTOR = "someone@summit.example"\n', ["1: _ADMIN_ACTOR"]),
        ('_SEEDED_LO_01 = "5555"\n', []),
        # Inline, inside a test function (the W5e fix-round gap).
        ('def test_x():\n    admin_actor = "someone@summit.example"\n', ["2: admin_actor"]),
        ('def test_x():\n    self.actor: str = "someone@summit.example"\n', ["2: actor"]),
        ('def test_x(c):\n    c.approve(actor="someone@summit.example")\n', ["2: actor="]),
        ('def test_x(c):\n    c.assign(assigned_by="someone@summit.example")\n', ["2: assigned_by="]),
        (
            'def test_x(c):\n    c.get("/", headers={"X-Forwarded-Email": "someone@summit.example"})\n',
            ["2: 'X-Forwarded-Email'"],
        ),
        ('def test_x(c):\n    c.run({"actor": "someone@summit.example"})\n', ["2: 'actor'"]),
        # Not actor identities: a per-run synthetic f-string, an assignee, a resolved actor.
        ('def test_x():\n    actor = f"integration+{uuid4().hex}@summit.example"\n', []),
        ('def test_x(c):\n    c.run({"assigned_to_email": "lo01@summit.example"})\n', []),
        ("def test_x(c):\n    admin_actor = resolve_live_admin_actor(c)\n", []),
    ],
)
def test_the_actor_slot_detector_sees_a_planted_actor(source: str, expected: list[str]) -> None:
    assert _actor_identity_literals(ast.parse(source)) == expected

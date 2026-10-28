"""The App runtime closure covers what the backend reads, by two nets.

tools/databricks/app_runtime_closure.py lists the repo paths a slim
per-release App source folder must carry. Several readers degrade silently
when their file is missing (county_names returns {}, the Genie space id
fallback returns None), so a miss is a quiet production degradation, not a
loud failure. Two independent scans of backend/ and the first-party modules it
imports guard the list:

* the flow scan (tests/fixtures/app_runtime_reads.py) follows path values from
  the roots it knows to the consumer that reads them, and counts anything it
  cannot follow as a read of the whole subtree;
* the literal sweep (tests/fixtures/app_runtime_literals.py) follows nothing
  and flags each string literal that names a repo path by its own text (a
  ``/`` path, or a dotted first-party module) or by the path call or ``/``
  join around it. A bare name such as ``"docs"`` passed through a parameter
  is the flow scan's to follow, not the sweep's.

Each read and literal the nets find must be covered by the closure (or, for
a literal, reviewed below with a reason), and every closure ``read`` entry must
still be read. tests/unit/test_app_runtime_read_shapes.py pins the shapes
each net catches; test_app_runtime_read_residuals.py pins the known residuals
neither does. The nets are a tripwire for accidental reads, not a proof: the
residual list is known, not complete.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from tests.fixtures.app_runtime_literals import (
    PathLiteral,
    module_paths,
    sweep,
    top_level_names,
)
from tests.fixtures.app_runtime_reads import ScanResult, outside_reads, scan
from tools.databricks import app_runtime_closure as closure

ROOT = Path(__file__).resolve().parents[2]

# (module, literal key) -> why the literal is not a read the closure must carry.
REVIEWED_LITERALS: dict[tuple[str, str], str] = {
    ("backend/api/health.py", "docs/security/GRANTS.md#10"): (
        "docs_ref string in the trust-boundary health payload; never opened"
    ),
    ("backend/config/settings.py", "docs/security/GRANTS.md#10"): (
        "docs_ref string in the trust-boundary startup log event; never opened"
    ),
    ("backend/services/capabilities.py", "tests/eval"): (
        "eval_dir, joined only with golden_agent_cases.jsonl and scorers.py, both "
        "closure entries; the flow scan follows it to those two files"
    ),
}


def _uncovered(leaves: dict[str, set[str]]) -> dict[str, set[str]]:
    return {
        path: origins
        for path, origins in leaves.items()
        if closure.owning_entry(path, closure.CLOSURE) is None
    }


def _unreferenced(
    entries: tuple[closure.ClosureEntry, ...], leaves: dict[str, set[str]]
) -> list[str]:
    return [
        entry.path
        for entry in entries
        if not any(ref == entry.path or ref.startswith(f"{entry.path}/") for ref in leaves)
    ]


def _unreviewed(literals: list[PathLiteral]) -> list[PathLiteral]:
    return [
        literal
        for literal in literals
        if closure.owning_entry(literal.key(), closure.CLOSURE) is None
        and (literal.origin.rsplit(":", 1)[0], literal.key()) not in REVIEWED_LITERALS
    ]


@pytest.fixture(scope="module")
def real_scan() -> ScanResult:
    return scan(ROOT)


@pytest.fixture(scope="module")
def real_literals(real_scan: ScanResult) -> list[PathLiteral]:
    return sweep(real_scan.trees, top_level_names(ROOT), modules=module_paths(ROOT))


def test_every_backend_read_outside_backend_is_in_the_closure(real_scan: ScanResult) -> None:
    assert real_scan.unhandled_file_uses == [], "teach tests/fixtures/app_runtime_reads.py"
    assert _uncovered(outside_reads(real_scan)) == {}


def test_every_closure_read_entry_is_still_read(real_scan: ScanResult) -> None:
    assert _unreferenced(closure.READS, outside_reads(real_scan)) == []


def test_every_path_literal_is_in_the_closure_or_reviewed(
    real_literals: list[PathLiteral],
) -> None:
    assert _unreviewed(real_literals) == []


def test_every_reviewed_literal_is_still_there(real_literals: list[PathLiteral]) -> None:
    present = {(literal.origin.rsplit(":", 1)[0], literal.key()) for literal in real_literals}

    assert sorted(set(REVIEWED_LITERALS) - present) == []


def test_the_scan_follows_the_backend_into_the_job_module_it_imports(
    real_scan: ScanResult,
) -> None:
    leaves = outside_reads(real_scan)

    assert "jobs/sync_lifecycle_state.py" in real_scan.scanned
    assert "backend/services/lifecycle_sync.py:24" in leaves["jobs/sync_lifecycle_state.py"]


def test_an_added_entry_nothing_reads_is_reported(real_scan: ScanResult) -> None:
    stale = closure.ClosureEntry("docs/never_read.md", "read", True, "nobody")

    assert _unreferenced((*closure.READS, stale), outside_reads(real_scan)) == [stale.path]


# Fake readers dropped into a copy of the real backend: a module-level path
# nothing in the module loads, the SOURCE_ROOT that dependency_lock_check
# exports, imported by another module, and a bare "docs" that reaches a path
# only through a parameter (the flow scan's alone: the sweep does not flag a
# bare name).
FAKE_READERS = {
    "zz_fake_constant.py": (
        'NOTES = Path(__file__).resolve().parents[2] / "docs" / "fake_constant.md"\n'
    ),
    "zz_fake_import.py": (
        "from backend.services.dependency_lock_check import SOURCE_ROOT\n\n"
        "def read():\n"
        '    return (SOURCE_ROOT / "docs" / "fake_import.md").read_text()\n'
    ),
    "zz_fake_param.py": (
        "def _read(folder, name):\n"
        "    return (Path(folder) / name).read_text()\n\n"
        "def load():\n"
        '    return _read("docs", "fake_param.md")\n'
    ),
}


def test_fake_reads_in_a_copy_of_the_real_backend_are_caught(
    tmp_path: Path,
) -> None:
    ignore = shutil.ignore_patterns("__pycache__")
    for name in ("backend", "jobs"):
        shutil.copytree(ROOT / name, tmp_path / name, ignore=ignore)
    (tmp_path / "docs").mkdir()
    clean = scan(tmp_path)
    assert _uncovered(outside_reads(clean)) == {}  # the control: a clean copy
    modules = module_paths(tmp_path)
    assert _unreviewed(sweep(clean.trees, top_level_names(tmp_path), modules=modules)) == []
    for name, source in FAKE_READERS.items():
        (tmp_path / "backend" / "services" / name).write_text(
            "from pathlib import Path\n\n" + source, encoding="utf-8"
        )

    result = scan(tmp_path)

    swept = {"docs/fake_constant.md", "docs/fake_import.md"}
    assert set(_uncovered(outside_reads(result))) == swept | {"docs/fake_param.md"}
    literals = _unreviewed(sweep(result.trees, top_level_names(tmp_path), modules=modules))
    assert {literal.key() for literal in literals} == swept

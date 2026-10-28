"""The App runtime closure matches what the backend reads, in both directions.

tools/databricks/app_runtime_closure.py lists the repo paths a slim
per-release App source folder must carry. tests/fixtures/app_runtime_reads.py
scans backend/ (and the first-party modules it imports) for every read
outside backend/. A read the closure misses would boot an App that fails at
request time; a closure ``read`` entry nothing reads any more is dead weight
that hides the next real omission. Both fail here.
"""

from __future__ import annotations

import shutil
import textwrap
from pathlib import Path

import pytest

from tests.fixtures.app_runtime_reads import ScanResult, outside_reads, scan
from tools.databricks import app_runtime_closure as closure

ROOT = Path(__file__).resolve().parents[2]


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


@pytest.fixture(scope="module")
def real_scan() -> ScanResult:
    return scan(ROOT)


def test_every_backend_read_outside_backend_is_in_the_closure(real_scan: ScanResult) -> None:
    assert real_scan.unhandled_file_uses == [], "teach tests/fixtures/app_runtime_reads.py"
    assert _uncovered(outside_reads(real_scan)) == {}


def test_every_closure_read_entry_is_still_read(real_scan: ScanResult) -> None:
    assert _unreferenced(closure.READS, outside_reads(real_scan)) == []


def test_the_scan_follows_the_backend_into_the_job_module_it_imports(
    real_scan: ScanResult,
) -> None:
    leaves = outside_reads(real_scan)

    assert "jobs/sync_lifecycle_state.py" in real_scan.scanned
    assert "backend/services/lifecycle_sync.py:24" in leaves["jobs/sync_lifecycle_state.py"]


def test_an_added_entry_nothing_reads_is_reported(real_scan: ScanResult) -> None:
    stale = closure.ClosureEntry("docs/never_read.md", "read", True, "nobody")

    assert _unreferenced((*closure.READS, stale), outside_reads(real_scan)) == [stale.path]


def test_a_fake_read_in_a_copy_of_the_real_backend_is_caught(tmp_path: Path) -> None:
    ignore = shutil.ignore_patterns("__pycache__")
    for name in ("backend", "jobs"):
        shutil.copytree(ROOT / name, tmp_path / name, ignore=ignore)
    assert _uncovered(outside_reads(scan(tmp_path))) == {}  # the control: a clean copy
    (tmp_path / "backend" / "services" / "zz_fake_read.py").write_text(
        "from pathlib import Path\n\n"
        'NOTES = Path(__file__).resolve().parents[2] / "docs" / "fake_read.md"\n',
        encoding="utf-8",
    )

    assert set(_uncovered(outside_reads(scan(tmp_path)))) == {"docs/fake_read.md"}


# Each shape the backend uses today, plus the import and escape shapes, in a
# minimal tree: the module is backend/services/fake.py.
SHAPES: dict[str, tuple[str, str]] = {
    "parents_chain": (
        'X = Path(__file__).resolve().parents[2] / "docs" / "a.md"', "docs/a.md"),
    "parent_parent": (
        'X = Path(__file__).resolve().parent.parent.parent / "docs" / "b.md"', "docs/b.md"),
    "local_root": (
        """
        def read():
            root = Path(__file__).resolve().parents[2]
            path = root / "docs" / "c.md"
            return path.read_text()
        """, "docs/c.md"),
    "function_return": (
        """
        def _repo_root():
            return Path(__file__).resolve().parents[2]

        def read():
            notes = _repo_root() / "docs"
            return (notes / "d.md").exists()
        """, "docs/d.md"),
    "joinpath": (
        'X = Path(__file__).resolve().parents[2].joinpath("docs", "e.md")', "docs/e.md"),
    "cwd_literal": ('X = Path("docs/f.md")', "docs/f.md"),
    "cwd_or_fallback": (
        """
        def read(path=None):
            chosen = path or Path(
                "docs/g.md"
            )
            return chosen.read_text()
        """, "docs/g.md"),
    "or_bound_root": (
        """
        def read(base=None):
            root = base or Path(__file__).resolve().parents[2]
            return (root / "docs" / "i.md").read_text()
        """, "docs/i.md"),
    "open_literal": ('X = open("docs/h.md")', "docs/h.md"),
    "tools_import": ("from tools.fake_tool import thing", "tools/fake_tool.py"),
    "import_module": (
        'import importlib\nX = importlib.import_module("tools.fake_tool")', "tools/fake_tool.py"),
    "transitive": ("from jobs.fake_job import run", "docs/job.md"),
    "escape": ('X = Path(__file__).resolve().parents[3] / "outside.txt"', "../outside.txt"),
}


def _minimal_tree(tmp_path: Path, source: str) -> Path:
    services = tmp_path / "backend" / "services"
    services.mkdir(parents=True)
    body = "from pathlib import Path\n\n" + textwrap.dedent(source).strip() + "\n"
    (services / "fake.py").write_text(body, encoding="utf-8")
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "fake_tool.py").write_text("thing = 1\n", encoding="utf-8")
    (tmp_path / "jobs").mkdir()
    (tmp_path / "jobs" / "fake_job.py").write_text(
        "from pathlib import Path\n\n"
        "def run():\n"
        '    return (Path(__file__).resolve().parents[1] / "docs" / "job.md").read_text()\n',
        encoding="utf-8",
    )
    return tmp_path


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_the_scanner_catches_each_read_shape(tmp_path: Path, shape: str) -> None:
    source, expected = SHAPES[shape]

    result = scan(_minimal_tree(tmp_path, source))

    assert result.unhandled_file_uses == []
    assert expected in _uncovered(outside_reads(result))


def test_the_minimal_tree_alone_reads_nothing_outside_backend(tmp_path: Path) -> None:
    result = scan(_minimal_tree(tmp_path, 'X = Path(__file__).with_name("sibling.json")'))

    assert outside_reads(result) == {}


def test_an_unrecognised_file_relative_shape_fails_loudly(tmp_path: Path) -> None:
    source = 'import os\nX = os.path.join(os.path.dirname(__file__), "..", "..", "docs")'

    result = scan(_minimal_tree(tmp_path, source))

    assert result.unhandled_file_uses == ["backend/services/fake.py:4"]

"""The read shapes each closure net catches, in a minimal tree.

The module under test is backend/services/fake.py, so ``parents[2]`` is the
repo root; backend/roots.py exports a root for the cross-module shapes. Each
shape reads something under docs/, which no closure entry covers, so a caught
read shows up as uncovered. The flow scan is tests/fixtures/app_runtime_reads.py
and the literal sweep is tests/fixtures/app_runtime_literals.py.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from tests.fixtures.app_runtime_literals import sweep, top_level_names
from tests.fixtures.app_runtime_reads import ScanResult, outside_reads, scan
from tools.databricks import app_runtime_closure as closure

ROOT_MODULE = "from pathlib import Path\n\nREPO_ROOT = Path(__file__).resolve().parents[1]\n"
JOB = (
    "from pathlib import Path\n\n"
    "def run():\n"
    '    return (Path(__file__).resolve().parents[1] / "docs" / "job.md").read_text()\n'
)


def _tree(tmp_path: Path, source: str) -> Path:
    files = {
        "backend/services/fake.py": "import os\nimport subprocess\nfrom pathlib import Path\n\n"
        + textwrap.dedent(source).strip() + "\n",
        "backend/roots.py": ROOT_MODULE,
        "tools/fake_tool.py": "thing = 1\n",
        "jobs/fake_job.py": JOB,
        "docs/README.md": "x\n",
    }
    for relpath, text in files.items():
        path = tmp_path / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return tmp_path


def _flow(result: ScanResult) -> set[str]:
    return {
        key for key in outside_reads(result) if closure.owning_entry(key, closure.CLOSURE) is None
    }


def _literals(root: Path, result: ScanResult) -> set[str]:
    return {literal.key() for literal in sweep(result.trees, top_level_names(root))}


# shape -> (source, what the flow scan reports, what the literal sweep reports)
SHAPES: dict[str, tuple[str, str, str | None]] = {
    # Shapes the backend uses today.
    "parents_chain": (
        'X = (Path(__file__).resolve().parents[2] / "docs" / "a.md").read_text()',
        "docs/a.md", "docs/a.md"),
    "parent_parent": (
        'X = Path(__file__).resolve().parent.parent.parent / "docs" / "b.md"',
        "docs/b.md", "docs/b.md"),
    "local_root": ("""
        def read():
            root = Path(__file__).resolve().parents[2]
            path = root / "docs" / "c.md"
            return path.read_text()
        """, "docs/c.md", "docs/c.md"),
    "function_return": ("""
        def _repo_root():
            return Path(__file__).resolve().parents[2]

        def read():
            notes = _repo_root() / "docs"
            return (notes / "d.md").exists()
        """, "docs/d.md", "docs"),
    "joinpath": (
        'X = Path(__file__).resolve().parents[2].joinpath("docs", "e.md").read_text()',
        "docs/e.md", "docs/e.md"),
    "cwd_literal": ('X = Path("docs/f.md").read_text()', "docs/f.md", "docs/f.md"),
    "cwd_or_fallback": ("""
        def read(path=None):
            chosen = path or Path(
                "docs/g.md"
            )
            return chosen.read_text()
        """, "docs/g.md", "docs/g.md"),
    "or_bound_root": ("""
        def read(base=None):
            root = base or Path(__file__).resolve().parents[2]
            return (root / "docs" / "i.md").read_text()
        """, "docs/i.md", "docs/i.md"),
    "open_literal": ('X = open("docs/h.md")', "docs/h.md", "docs/h.md"),
    "tools_import": ("from tools.fake_tool import thing", "tools/fake_tool.py", None),
    "import_module": (
        'import importlib\nX = importlib.import_module("tools.fake_tool")',
        "tools/fake_tool.py", None),
    "transitive": ("from jobs.fake_job import run", "docs/job.md", "docs/job.md"),
    "escape_above_root": (
        'X = Path(__file__).resolve().parents[3] / "outside.txt"', "../outside.txt", None),
    "os_path_dirname_file": (
        'X = os.path.join(os.path.dirname(__file__), "..", "..", "docs")', "docs", "docs"),
    # The review battery: shapes the first scanner missed.
    "module_constant_string": ("""
        SAMPLE = "docs/j.md"

        def read():
            return Path(SAMPLE).read_text()
        """, "docs/j.md", "docs/j.md"),
    "fstring_open": ("""
        def read(n):
            return open(f"docs/k_{n}.md").read()
        """, "docs/**", "docs/**"),
    "path_cwd": (
        'X = (Path.cwd() / "docs" / "l.md").read_text()', "docs/l.md", "docs/l.md"),
    "env_default": (
        'X = Path(os.environ.get("MIP_X", "docs/m.md")).read_text()', "docs/m.md", "docs/m.md"),
    "os_path_join_literals": (
        'X = open(os.path.join("docs", "n.md")).read()', "docs/n.md", "docs/n.md"),
    "cross_module_root": ("""
        from backend.roots import REPO_ROOT

        def read():
            return (REPO_ROOT / "docs" / "o.md").read_text()
        """, "docs/o.md", "docs/o.md"),
    "module_alias_root": ("""
        import backend.roots as roots

        def read():
            return (roots.REPO_ROOT / "docs" / "p.md").read_text()
        """, "docs/p.md", "docs/p.md"),
    "root_function_with_args": ("""
        def root_of(levels):
            return Path(__file__).resolve().parents[2]

        def read():
            return (root_of(1) / "docs" / "q.md").read_text()
        """, "docs/q.md", "docs/q.md"),
    "class_attribute_via_self": ("""
        class Reader:
            ROOT = Path(__file__).resolve().parents[2]

            def read(self):
                return (self.ROOT / "docs" / "r.md").read_text()
        """, "docs/r.md", "docs/r.md"),
    "class_attribute_via_class": ("""
        class Roots:
            ROOT = Path(__file__).resolve().parents[2]

        def read():
            return (Roots.ROOT / "docs" / "s.md").read_text()
        """, "docs/s.md", "docs/s.md"),
    "instance_attribute": ("""
        class Reader:
            def __init__(self):
                self.root = Path(__file__).resolve().parents[2]

            def read(self):
                return (self.root / "docs" / "t.md").read_text()
        """, "docs/t.md", "docs/t.md"),
    "root_as_parameter": ("""
        def read_under(root):
            return (root / "docs" / "u.md").read_text()

        def read():
            return read_under(Path(__file__).resolve().parents[2])
        """, "docs/u.md", "docs/u.md"),
    "root_as_keyword_into_method": ("""
        class Reader:
            def read_under(self, *, root):
                return (root / "docs" / "v.md").read_text()

            def read(self):
                return self.read_under(root=Path(__file__).resolve().parents[2])
        """, "docs/v.md", "docs/v.md"),
    "glob_from_root": (
        'X = [p.read_text() for p in Path(__file__).resolve().parents[2].glob("docs/*.md")]',
        "docs/**", "docs/**"),
    "tuple_assignment": ("""
        def read():
            root, name = Path(__file__).resolve().parents[2], "w.md"
            return (root / "docs" / "w.md").read_text()
        """, "docs/w.md", "docs/w.md"),
    "tuple_from_a_call": ("""
        def _pair():
            return Path(__file__).resolve().parents[2], "label"

        def read():
            root, label = _pair()
            return (root / "docs" / "wa.md").read_text()
        """, "docs/wa.md", "docs/wa.md"),
    "walrus": ("""
        def read():
            if (root := Path(__file__).resolve().parents[2]).exists():
                return (root / "docs" / "x.md").read_text()
            return None
        """, "docs/x.md", "docs/x.md"),
    "fstring_segment": ("""
        def read(n):
            return (Path(__file__).resolve().parents[2] / f"docs/{n}.md").read_text()
        """, "docs/**", "docs/**"),
    "augmented_join": ("""
        def read():
            root = Path(__file__).resolve().parents[2]
            root /= "docs"
            return (root / "y.md").read_text()
        """, "docs/y.md", "docs"),
    # A root that escapes into code the scan does not follow reads everything.
    "rglob_beside_a_deeper_read": ("""
        ROOT = Path(__file__).resolve().parents[2]

        def read():
            (ROOT / "sql" / "metric_views").exists()
            return [p.read_text() for p in ROOT.rglob("*.md")]
        """, "./**", None),
    "string_concatenation": (
        'X = open(str(Path(__file__).resolve().parents[2]) + "/docs/z.md").read()',
        "./**", None),
    "fstring_of_a_root": (
        'X = open(f"{Path(__file__).resolve().parents[2]}/docs/z.md").read()', "./**", None),
    "library_call": (
        'X = subprocess.run(["cat", "docs/z.md"], cwd=Path(__file__).resolve().parents[2])',
        "./**", "docs/z.md"),
    "root_into_a_container": (
        'X = {"root": Path(__file__).resolve().parents[2]}', "./**", None),
    "uncalled_root_function_passed_as_a_value": ("""
        def _root():
            return Path(__file__).resolve().parents[2]

        HOOKS = {"root": _root}
        """, "./**", None),
}


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_each_net_catches_each_read_shape(tmp_path: Path, shape: str) -> None:
    source, flow, literal = SHAPES[shape]
    root = _tree(tmp_path, source)

    result = scan(root)

    assert result.unhandled_file_uses == []
    assert flow in _flow(result)
    if literal is not None:
        assert literal in _literals(root, result)


@pytest.mark.parametrize(
    ("shape", "source"),
    [
        ("parents_iterated", "X = [p for p in Path(__file__).resolve().parents]"),
        ("parents_variable_index", "def f(n):\n    return Path(__file__).parents[n]"),
        ("file_into_a_helper", "import helper\nX = helper.locate(__file__)"),
        ("unknown_path_attribute", "X = Path(__file__).resolve().parents[2].frobnicate"),
        ("modulo_assign", 'def f():\n    root = Path(__file__).parent\n    root %= "x"'),
    ],
)
def test_a_flow_the_scan_cannot_bound_is_unhandled(tmp_path: Path, shape: str, source: str) -> None:
    result = scan(_tree(tmp_path, source))

    assert result.unhandled_file_uses, shape
    assert all(use.startswith("backend/services/fake.py:") for use in result.unhandled_file_uses)


def test_the_minimal_tree_alone_reads_nothing_outside_backend(tmp_path: Path) -> None:
    root = _tree(tmp_path, 'X = Path(__file__).with_name("sibling.json").read_text()')

    result = scan(root)

    assert outside_reads(result) == {}
    assert _literals(root, result) == set()


def test_a_path_value_inside_backend_may_escape(tmp_path: Path) -> None:
    source = 'X = subprocess.run(["ls"], cwd=Path(__file__).resolve().parent / "data")'

    result = scan(_tree(tmp_path, source))

    assert result.unhandled_file_uses == []
    assert outside_reads(result) == {}


def test_doc_strings_and_bare_strings_are_not_literals(tmp_path: Path) -> None:
    source = '"""See docs/guide.md."""\n\ndef f():\n    "docs/notes.md"\n    return 1'
    root = _tree(tmp_path, source)

    assert _literals(root, scan(root)) == set()

"""The read shapes each App read net catches, in a minimal tree.

tests/fixtures/app_runtime_shape_tree.py builds the tree (the module under
test is backend/services/fake.py). CAUGHT pins each shape at least one net
sees; tests/unit/test_app_runtime_read_residuals.py pins the flows the scan
reports as unhandled and the known residuals neither net sees.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.fixtures.app_runtime_paths import absolute_module, module_package
from tests.fixtures.app_runtime_reads import outside_reads, scan
from tests.fixtures.app_runtime_shape_tree import flow_reads, literal_reads, make_tree

# shape -> (source, what the flow scan reports, what the literal sweep reports);
# None: that net does not see the shape (at least one of the two must).
CAUGHT: dict[str, tuple[str, str | None, str | None]] = {
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
        "tools/fake_tool.py", "tools/fake_tool.py"),
    # Dotted first-party module strings: the flow scan loads the module a
    # literal names; the sweep flags the string wherever it sits.
    "dunder_import": (
        'X = __import__("tools.fake_tool")', "tools/fake_tool.py", "tools/fake_tool.py"),
    "find_spec": (
        'import importlib.util\nX = importlib.util.find_spec("tools.fake_tool")',
        "tools/fake_tool.py", "tools/fake_tool.py"),
    "run_module": (
        'import runpy\nX = runpy.run_module("tools.fake_tool")',
        "tools/fake_tool.py", "tools/fake_tool.py"),
    "python_dash_m": (
        'import sys\nX = subprocess.run([sys.executable, "-m", "tools.fake_tool"])',
        None, "tools/fake_tool.py"),
    "module_name_held_in_a_constant": ("""
        import importlib

        MODULE = "tools.fake_tool"

        def load():
            return importlib.import_module(MODULE)
        """, None, "tools/fake_tool.py"),
    "entry_point_string": ('X = "tools.fake_tool:main"', None, "tools/fake_tool.py"),
    "package_string": ('X = "tools.pkg.inner"', None, "tools/pkg/**"),
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
    # Attributes of an imported first-party object (round 4): a settings
    # instance, its class, a module attribute chain, an attribute set in
    # __init__ and a string attribute. "docs" sits in a name, so only the flow
    # scan can see these.
    "imported_instance_attribute": ("""
        from backend.services.paths import settings

        D = "docs"

        def read():
            return (settings.root / D / "a2.md").read_text()
        """, "docs/a2.md", None),
    "imported_class_attribute": ("""
        from backend.services.paths import Settings

        D = "docs"

        def read():
            return (Settings.root / D / "a3.md").read_text()
        """, "docs/a3.md", None),
    "imported_module_attribute_chain": ("""
        from backend.services import paths

        D = "docs"

        def read():
            return (paths.settings.root / D / "a4.md").read_text()
        """, "docs/a4.md", None),
    "imported_init_attribute": ("""
        from backend.services.paths import settings

        D = "docs"

        def read():
            return (settings.data_root / D / "a5.md").read_text()
        """, "docs/a5.md", None),
    "imported_string_attribute": ("""
        from backend.services.paths import settings

        def read():
            return (Path(settings.docs_dir) / "a6.md").read_text()
        """, "docs/a6.md", None),
    # os.path.abspath / realpath of a cwd string is the cwd path (round 4).
    "abspath_of_the_cwd": ("""
        DOCS = "docs"
        X = open(os.path.join(os.path.abspath("."), DOCS, "x1.md")).read()
        """, "docs/x1.md", None),
    "realpath_of_the_cwd": ("""
        DOCS = "docs"
        X = (Path(os.path.realpath(".")) / DOCS / "x2.md").read_text()
        """, "docs/x2.md", None),
    "normpath_of_the_cwd": ("""
        DOCS = "docs"
        X = open(os.path.join(os.path.normpath("."), DOCS, "x3.md")).read()
        """, "docs/x3.md", None),
    "expanduser_of_the_cwd": ("""
        DOCS = "docs"
        X = open(os.path.join(os.path.expanduser("."), DOCS, "x4.md")).read()
        """, "docs/x4.md", None),
    # A root bound through `global` or `nonlocal`, and names reached through
    # relative imports (round 4).
    "global_rebinding": ("""
        DOCS = "docs"
        ROOT = None

        def init():
            global ROOT
            ROOT = Path(__file__).resolve().parents[2]

        def read():
            return (ROOT / DOCS / "g1.md").read_text()
        """, "docs/g1.md", None),
    "nonlocal_rebinding": ("""
        DOCS = "docs"

        def read():
            root = None

            def bind():
                nonlocal root
                root = Path(__file__).resolve().parents[2]

            bind()
            return (root / DOCS / "n1.md").read_text()
        """, "docs/n1.md", None),
    "relative_import": ("""
        from .paths import DOCS, REPO_ROOT

        def read():
            return (REPO_ROOT / DOCS / "r1.md").read_text()
        """, "docs/r1.md", None),
    "relative_module_import": ("""
        from . import paths

        def read():
            return (paths.REPO_ROOT / paths.DOCS / "r2.md").read_text()
        """, "docs/r2.md", None),
    "relative_import_outside_backend": (
        "from jobs.rel_job import read", "docs/rel.md", "docs/rel.md"),
    # A loop variable over a literal tuple of names carries their strings
    # (round 4; a loop over a NAME bound to the tuple is a known residual).
    "loop_variable_over_literal_names": ("""
        def read():
            for folder in ("docs",):
                return (Path(folder) / "l1.md").read_text()
        """, "docs/l1.md", None),
    "comprehension_over_literal_names": (
        'X = [(Path(f) / "l2.md").read_text() for f in ["docs"]]', "docs/l2.md", None),
    # A bare top-level name reaching a path constructor only through a
    # first-party parameter: no '/' and no path context, so only the flow
    # scan can see it.
    "string_into_a_parameter": ("""
        def _read(folder, name):
            return (Path(folder) / name).read_text()

        def load():
            return _read("docs", "runbook.md")
        """, "docs/runbook.md", None),
    "string_keyword_into_a_method": ("""
        class Reader:
            def read(self, *, folder):
                return open(os.path.join(folder, "kw.md")).read()

            def load(self):
                return self.read(folder="docs")
        """, "docs/kw.md", None),
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


@pytest.mark.parametrize("shape", sorted(CAUGHT))
def test_each_net_catches_each_read_shape(tmp_path: Path, shape: str) -> None:
    source, flow, literal = CAUGHT[shape]
    root = make_tree(tmp_path, source)

    result = scan(root)

    assert result.unhandled_file_uses == []
    assert flow is not None or literal is not None
    if flow is not None:
        assert flow in flow_reads(result)
    if literal is not None:
        assert literal in literal_reads(root, result)


def test_the_minimal_tree_alone_reads_nothing_outside_backend(tmp_path: Path) -> None:
    root = make_tree(tmp_path, 'X = Path(__file__).with_name("sibling.json").read_text()')

    result = scan(root)

    assert result.unhandled_file_uses == []
    assert outside_reads(result) == {}
    assert literal_reads(root, result) == set()


@pytest.mark.parametrize(
    ("relfile", "level", "module", "expected"),
    [
        ("backend/services/fake.py", 1, "paths", "backend.services.paths"),
        ("backend/services/fake.py", 1, None, "backend.services"),
        ("backend/services/fake.py", 2, "roots", "backend.roots"),
        ("backend/services/__init__.py", 1, "paths", "backend.services.paths"),
        ("backend/services/fake.py", 0, "jobs.x", "jobs.x"),
        ("backend/services/fake.py", 4, "x", None),
    ],
)
def test_relative_imports_resolve_against_the_importing_package(
    relfile: str, level: int, module: str | None, expected: str | None
) -> None:
    dotted = relfile.removesuffix(".py").removesuffix("/__init__").replace("/", ".")

    assert absolute_module(module_package(relfile, dotted), level, module) == expected


@pytest.mark.parametrize(
    "import_line",
    ["from .docroot import DOCS_DIR", "from backend.services.docroot import DOCS_DIR"],
)
def test_a_root_imported_by_name_is_not_also_an_unused_binding(
    tmp_path: Path, import_line: str
) -> None:
    # docroot.py binds DOCS_DIR and never loads it. A path bound to a name
    # nothing loads counts as read where it is bound ("docs"), unless another
    # module imports that name; a relative import must count as that import
    # too, or the scan reports a "docs" read nobody makes. Exact equality,
    # because membership cannot see the extra read.
    root = make_tree(tmp_path, f'{import_line}\nX = (DOCS_DIR / "rr.md").read_text()')
    (root / "backend" / "services" / "docroot.py").write_text(
        'from pathlib import Path\n\nDOCS_DIR = Path(__file__).resolve().parents[2] / "docs"\n',
        encoding="utf-8",
    )

    result = scan(root)

    assert result.unhandled_file_uses == []
    assert flow_reads(result) == {"docs/rr.md"}


def test_the_home_folder_is_not_a_repo_read(tmp_path: Path) -> None:
    # The control for the os.path cwd rule: expanduser("~/...") names the
    # home folder, not a path under the source root.
    source = 'X = open(os.path.expanduser("~/.databrickscfg")).read()'

    result = scan(make_tree(tmp_path, source))

    assert result.unhandled_file_uses == []
    assert outside_reads(result) == {}


def test_a_path_value_inside_backend_may_escape(tmp_path: Path) -> None:
    source = 'X = subprocess.run(["ls"], cwd=Path(__file__).resolve().parent / "data")'

    result = scan(make_tree(tmp_path, source))

    assert result.unhandled_file_uses == []
    assert outside_reads(result) == {}


def test_dotted_strings_that_name_no_tracked_module_are_not_literals(tmp_path: Path) -> None:
    # The control for the dotted-module rule: event names under a top-level
    # name without that module (genie/ holds no Python here or in the repo),
    # a missing module, the package's own modules and a dotted file name.
    source = """
        EVENTS = ("genie.run_query", "docs.readme", "tools.missing_module", "uv.lock")
        OWN = "backend.roots"
        """
    root = make_tree(tmp_path, source)
    (root / "genie").mkdir()
    (root / "genie" / "sample_questions.md").write_text("- q\n", encoding="utf-8")

    assert literal_reads(root, scan(root)) == set()


def test_doc_strings_and_bare_strings_are_not_literals(tmp_path: Path) -> None:
    source = '"""See docs/guide.md."""\n\ndef f():\n    "docs/notes.md"\n    return 1'
    root = make_tree(tmp_path, source)

    assert literal_reads(root, scan(root)) == set()

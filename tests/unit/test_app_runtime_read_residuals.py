"""What the App read nets report as unhandled, and what neither sees.

UNHANDLED pins flows the flow scan cannot bound, and roots it does not follow,
which it reports wherever they appear. RESIDUALS pins KNOWN residuals that
neither net sees; the list is known, not complete. The nets are a tripwire for
accidental reads outside backend/, not a proof that none exists.
tests/fixtures/app_runtime_shape_tree.py builds the tree.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.fixtures.app_runtime_reads import scan
from tests.fixtures.app_runtime_shape_tree import flow_reads, literal_reads, make_tree

UNHANDLED: tuple[tuple[str, str], ...] = (
    ("parents_iterated", "X = [p for p in Path(__file__).resolve().parents]"),
    ("parents_variable_index", "def f(n):\n    return Path(__file__).parents[n]"),
    ("file_into_a_helper", "import helper\nX = helper.locate(__file__)"),
    ("unknown_path_attribute", "X = Path(__file__).resolve().parents[2].frobnicate"),
    ("modulo_assign", 'def f():\n    root = Path(__file__).parent\n    root %= "x"'),
    # Roots other than a bare __file__ or the cwd, with the top-level
    # segment held in a name, so the literal sweep cannot see it either.
    ("module_attribute_file", (
        'import backend\nDOCS = "docs"\n'
        'X = (Path(backend.__file__).parent / DOCS / "q1.md").read_text()')),
    ("sys_modules_file", (
        'import sys\nDOCS = "docs"\n'
        'X = Path(sys.modules[__name__].__file__).parents[2] / DOCS')),
    ("spec_origin", 'DOCS = "docs"\nX = Path(__spec__.origin).parents[2] / DOCS'),
    ("loader", "X = Path(__loader__.get_filename()).parents[2]"),
    ("package_path", "X = Path(__path__[0]).parent"),
    ("cached", "X = Path(__cached__).parents[3]"),
    ("inspect_getfile", (
        "import inspect\n\ndef f():\n    return 1\n\n"
        "X = Path(inspect.getfile(f)).parents[2]")),
    ("inspect_getsourcefile_imported", (
        "from inspect import getsourcefile\nX = Path(getsourcefile(Path)).parent")),
    ("sys_argv", 'import sys\nDOCS = "docs"\nX = Path(sys.argv[0]).parents[1] / DOCS'),
    ("sys_path", 'import sys\nDOCS = "docs"\nX = Path(sys.path[0]) / DOCS'),
    ("os_curdir", 'DOCS = "docs"\nX = Path(os.curdir) / DOCS'),
    ("importlib_resources_files", (
        'import importlib.resources\nDOCS = "docs"\n'
        'X = importlib.resources.files("backend") / ".." / DOCS')),
    ("resources_module_alias", (
        'from importlib import resources\nX = resources.files("backend")')),
    ("resources_function_import", (
        'from importlib.resources import as_file\nX = as_file')),
    ("pkgutil_get_data", 'import pkgutil\nX = pkgutil.get_data("backend", "x")'),
    # Round 4: a module's file by name, a frame's or code object's file, and a
    # find_spec result's paths.
    ("globals_file", 'DOCS = "docs"\nX = Path(globals()["__file__"]).parents[2] / DOCS'),
    ("vars_file", 'X = Path(vars()["__file__"]).parents[2]'),
    ("f_globals_get_file", (
        'import sys\nX = Path(sys.modules[__name__].__dict__.get("__file__")).parent')),
    ("getattr_file", 'import backend\nX = Path(getattr(backend, "__file__")).parent'),
    ("sys_getframe", (
        "import sys\nX = Path(sys._getframe().f_code.co_filename).parents[2]")),
    ("inspect_currentframe", (
        "import inspect\nX = Path(inspect.currentframe().f_code.co_filename).parents[2]")),
    ("inspect_stack", "import inspect\nX = Path(inspect.stack()[0].filename).parents[2]"),
    ("code_object_filename", (
        "def f():\n    return 1\n\nX = Path(f.__code__.co_filename).parents[2]")),
    ("find_spec_origin", (
        'import importlib.util\n'
        'X = Path(importlib.util.find_spec("backend").origin).parents[1]')),
    ("find_spec_bound_search_locations", (
        'import importlib.util\nSPEC = importlib.util.find_spec("backend")\n'
        "X = Path(SPEC.submodule_search_locations[0]).parent")),
)


@pytest.mark.parametrize(("shape", "source"), UNHANDLED)
def test_a_flow_the_scan_cannot_bound_is_unhandled(tmp_path: Path, shape: str, source: str) -> None:
    result = scan(make_tree(tmp_path, source))

    assert result.unhandled_file_uses, shape
    assert all(use.startswith("backend/services/fake.py:") for use in result.unhandled_file_uses)


def test_names_that_only_look_like_unfollowed_roots_are_not_reported(tmp_path: Path) -> None:
    # The control for the root rules: a local `files`, an `argv` attribute of
    # a first-party class and a `path` key are not the module roots they echo;
    # a find_spec presence test (capabilities._module_present) and an `origin`
    # or `loader` that is not on a find_spec result read no file.
    source = """
        import importlib.util

        files = ["a.md"]
        X = files[0]

        class Options:
            argv = []
            path = "x"
            origin = "https://example.invalid"
            loader = None

        Y = Options.argv, Options.path, {"__file__": 1}, Options.origin, Options.loader
        PRESENT = importlib.util.find_spec("json") is not None
        """

    assert scan(make_tree(tmp_path, source)).unhandled_file_uses == []


# The KNOWN residuals: shapes NEITHER net sees (tests/fixtures/app_runtime_reads.py,
# tools/databricks/app_runtime_closure.py and docs/deployment.md list them). The
# list is known, not complete. If a net learns one, move it to CAUGHT
# (test_app_runtime_read_shapes.py) and drop it from those three lists.
RESIDUALS: dict[str, str] = {
    "library_loader": (
        'from starlette.staticfiles import StaticFiles\nX = StaticFiles(directory="docs")'),
    "template_loader": 'import jinja2\nX = jinja2.FileSystemLoader("docs")',
    "str_format_from_the_cwd": (
        'def read(n):\n    return open("{}/{}".format("docs", n)).read()'),
    "percent_from_the_cwd": 'def read(n):\n    return open("%s/%s" % ("docs", n)).read()',
    "env_var_without_a_default": 'X = Path(os.environ["MIP_DOCS"]).read_text()',
    "computed_module_name": (
        'import importlib\nX = getattr(importlib.import_module("tools" + ".fake_tool"), "thing")'),
    # Round 4: a path joined with str.join, a starred argument, and a bare
    # top-level name held in a container or a named tuple a loop walks.
    "str_join_from_the_cwd": 'def read(n):\n    return open("/".join(["docs", n])).read()',
    "os_sep_join_from_the_cwd": 'X = open(os.sep.join(["docs", "x.md"])).read()',
    "starred_os_path_join": 'X = open(os.path.join(*["docs", "x.md"])).read()',
    "starred_name_into_path": 'PARTS = ("docs", "x.md")\nX = Path(*PARTS).read_text()',
    "starred_tuple_into_path": 'X = Path(*("docs", "x.md")).read_text()',
    "dict_value_held_name": 'X = (Path({"k": "docs"}["k"]) / "x.md").read_text()',
    "list_item_held_name": 'X = (Path(["docs"][0]) / "x.md").read_text()',
    "loop_over_a_named_tuple": (
        'NAMES = ("docs",)\nX = [(Path(n) / "x.md").read_text() for n in NAMES]'),
    "callee_reached_through_an_object": """
        class Reader:
            def read(self, folder):
                return (Path(folder) / "obj.md").read_text()

        READERS = {"docs": Reader()}

        def load():
            return READERS["docs"].read("docs")
        """,
}


@pytest.mark.parametrize("shape", sorted(RESIDUALS))
def test_each_documented_residual_is_still_missed_by_both_nets(tmp_path: Path, shape: str) -> None:
    root = make_tree(tmp_path, RESIDUALS[shape])

    result = scan(root)

    assert (result.unhandled_file_uses, flow_reads(result), literal_reads(root, result)) == ([], set(), set())

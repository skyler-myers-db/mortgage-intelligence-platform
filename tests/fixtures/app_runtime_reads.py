"""Static flow scan of the App backend for files it reads outside ``backend/``.

Feeds tests/unit/test_app_runtime_closure_reads.py, which checks the result
against tools/databricks/app_runtime_closure.py. It is one of two independent
nets; tests/fixtures/app_runtime_literals.py is the other.

A path value starts at ``__file__`` (through ``Path``, ``os.path.dirname``,
``abspath``, ``realpath``), at the cwd (``Path.cwd()``, ``os.getcwd()``, a
relative ``Path("...")``, ``open("...")``, ``os.path.join("...")`` or
``os.path.abspath(".")``, which the App resolves against its source root), or at a string constant, f-string or
``os.environ.get`` default fed to one of those. The scan follows it, to a fixed
point over every scanned module, through ``/``, ``.parent``, ``.parents[N]``,
``joinpath``, ``with_name``, ``resolve()``, ``str()``, ``or``/``if``, names
(a ``global`` or ``nonlocal`` name binds in its outer scope), tuple targets,
walrus and ``/=``, function returns, call arguments (path values
and strings) into first-party functions (including imported ones and
``self.method``), parameter defaults,
attributes assigned on ``self``, ``cls`` or a class body (also when read
through an imported first-party object, ``settings.root``), names and module
attributes imported from other first-party modules (relative imports
included), containers, and ``for`` loops (a loop over a literal tuple or list
of strings binds each string). Each value ends at a consumer, which decides what is read:

* a read or test (``read_text``, ``exists``, ``open(p)``, ``.name``, a
  comparison, a bare statement) reads exactly that path;
* ``glob``, ``rglob``, ``iterdir``, ``os.walk`` and a join with a non-constant
  segment read the subtree under the path (``p/**``);
* anything the scan does not follow (a library call, a method of an unknown
  object, an f-string, a dict, a lambda, a function returned to callers it
  cannot see) is an escape: the whole subtree under the value counts as read,
  so an escaping repo root (``./**``) can never be covered;
* a flow it cannot bound at all (``.parents`` without a constant index, an
  augmented assignment other than ``/=``, an unknown attribute of a path,
  calling a path, ``__file__`` outside the path constructors) is reported as
  unhandled, and its subtree counts as read.

The unfollowed roots in app_runtime_paths.ROOT_DUNDERS, ROOT_ATTRS, SPEC_ATTRS
and ROOT_REFS are reported as unhandled wherever they are referenced: a
module attribute ``__file__`` (``backend.__file__``,
``sys.modules[__name__].__file__``) or one looked up by name
(``globals()["__file__"]``, ``vars()["__file__"]``,
``getattr(m, "__file__")``), ``__spec__``, ``__loader__``, ``__path__``,
``__cached__``, any ``co_filename``, the frame helpers named in FRAME_REFS
(``sys._getframe``, ``inspect.currentframe``, ``inspect.stack``,
``traceback.extract_stack`` and the rest of that table, not every helper that
yields a frame), the ``origin``, ``submodule_search_locations``, ``cached`` or
``loader`` read directly on a ``find_spec`` call or on a name bound to one in
the same module, ``inspect.getfile`` and its siblings, ``sys.argv``,
``sys.path``, ``os.curdir``, and the ``importlib.resources``,
``pkgutil.get_data`` and ``pkg_resources`` data helpers.

A path value bound to a name nothing loads counts as read where it is bound.

Known residuals neither net sees (tests/unit/test_app_runtime_read_residuals.py
RESIDUALS pins each one). The list is KNOWN, NOT COMPLETE: the two nets are a
tripwire for accidental reads outside backend/, not a proof that none exists.
A new App read outside backend/ must use the shapes the scans follow (a
module-level ``Path(__file__)``-rooted constant plus literal segments) and be
added to READS in tools/databricks/app_runtime_closure.py. No backend module
uses any of these shapes today:

* a first-party callee the flow scan cannot name (a method reached through an
  object, a function passed as a value) is assumed to read only under the
  path it receives, and gets none of the strings passed to it;
* a bare top-level name with no ``/`` (``"docs"``) handed to a library loader
  (``StaticFiles(directory="docs")``, ``jinja2.FileSystemLoader("docs")``) or
  to any other call that is neither a path constructor nor a first-party
  function the scan can name. Bare names are not swept: the backend uses
  ``"lakebase"`` and ``"genie"`` as dependency labels hundreds of times;
* a bare top-level name held in a container (``{"k": "docs"}["k"]``,
  ``["docs"][0]``) or in a named tuple a loop walks (a loop over a literal
  tuple or list is followed);
* a working-directory path built with ``str.format``, ``%`` or ``str.join``
  (``"/".join(["docs", n])``, ``os.sep.join``) from parts that hold no ``/``;
  a ``__file__`` root formatted into a string still escapes, so it counts as
  ``./**``;
* a starred argument to a path constructor or ``os.path.join``
  (``Path(*PARTS)``, ``os.path.join(*["docs", "x.md"])``);
* a path read from an environment variable with no default;
* ``getattr``, ``exec``, ``eval`` and a module named by a computed string;
* a module or frame file reached through a helper outside ROOT_REFS
  (``traceback.extract_tb(...)[0].filename``,
  ``logging.getLogger().findCaller()``,
  ``pkgutil.get_loader(...).get_filename()``), or a ``find_spec`` result
  whose ``origin`` is read through an alias, a walrus, an attribute
  (``self.spec``) or a parameter;
* the working directory reached without ``os.getcwd``, ``Path.cwd`` or a
  string (``os.getcwdb()``, ``os.listdir()`` and ``os.scandir()`` with no
  argument);
* a ``nonlocal`` name bound two or more scopes up (the rebind lands one
  scope out);
* a folder name the scan cannot read as a ``str``: keywords unpacked with
  ``**{...}`` into a first-party function, a computed string
  (``"DOCS".lower()``), a bytes path (``open(b"docs/x.md")``), and a library
  keyword (``subprocess.run(..., cwd=DOCS)``).

The scan is split by responsibility: app_runtime_paths.py (path values and
pure helpers), app_runtime_flow.py (resolution and binding to a fixed point),
app_runtime_consumers.py (what each consumer reads) and this driver.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from tests.fixtures.app_runtime_consumers import consumer
from tests.fixtures.app_runtime_flow import ModuleFlow, World
from tests.fixtures.app_runtime_paths import (
    DIR_CALLS,
    OPEN_CALLS,
    ROOT_ATTRS,
    ROOT_DUNDERS,
    ROOT_REFS,
    SPEC_ATTRS,
    Ref,
    Scope,
    absolute_module,
    glob_prefix,
    join_ref,
    module_package,
    subtree,
)

__all__ = ["Ref", "ScanResult", "outside_reads", "scan"]


@dataclass
class ScanResult:
    references: dict[str, set[str]] = field(default_factory=dict)  # Ref.key() -> origins
    unhandled_file_uses: list[str] = field(default_factory=list)
    scanned: list[str] = field(default_factory=list)
    trees: dict[str, ast.Module] = field(default_factory=dict)  # relfile -> parsed module

    def add(self, key: str, origin: str) -> None:
        self.references.setdefault(key, set()).add(origin)



def _module_file(root: Path, module: str) -> str | None:
    base = module.replace(".", "/")
    for candidate in (f"{base}.py", f"{base}/__init__.py"):
        if (root / candidate).is_file():
            return candidate
    return base if (root / base).is_dir() else None


def _dotted_name(relfile: str) -> str:
    return relfile.removesuffix(".py").removesuffix("/__init__").replace("/", ".")


# Calls that load the module their literal first argument names.
MODULE_LOADERS = frozenset({"import_module", "__import__", "find_spec", "run_module"})


def _imported_names(tree: ast.Module, package: str) -> Iterator[tuple[str, int]]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from ((alias.name, node.lineno) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = absolute_module(package, node.level, node.module)
            if base:
                yield base, node.lineno
                yield from ((f"{base}.{a.name}", node.lineno) for a in node.names)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute | ast.Name):
            callee = node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
            first = node.args[0] if node.args else None
            literal = first.value if isinstance(first, ast.Constant) else None
            if callee in MODULE_LOADERS and isinstance(literal, str):
                yield literal, node.lineno


MAX_ROUNDS = 32


def scan(root: Path, package: str = "backend") -> ScanResult:
    """References from ``root/package`` and the first-party modules it imports."""

    result = ScanResult()
    world = World()
    top_levels = {p.name for p in root.iterdir() if p.is_dir()} - {package}
    queue = sorted(p.relative_to(root).as_posix() for p in (root / package).rglob("*.py"))
    seen = set(queue)
    while queue:
        relfile = queue.pop(0)
        tree = ast.parse((root / relfile).read_text(encoding="utf-8"), filename=relfile)
        result.scanned.append(relfile)
        result.trees[relfile] = tree
        dotted = _dotted_name(relfile)
        world.modules[dotted] = ModuleFlow(world, relfile, dotted, tree)
        for name, line in _imported_names(tree, module_package(relfile, dotted)):
            if name.split(".")[0] not in top_levels:
                continue
            target = _module_file(root, name)
            if target is None:
                continue
            result.add(target, f"{relfile}:{line}")
            if target.endswith(".py") and target not in seen:
                seen.add(target)
                queue.append(target)
    for module in world.modules.values():
        module.mark_calls()
    for _ in range(MAX_ROUNDS):
        world.changed = False
        for module in world.modules.values():
            module.bind_round()
        if not world.changed:
            break
    else:
        raise RuntimeError(f"no fixed point after {MAX_ROUNDS} rounds: teach the scanner")
    for module in world.modules.values():
        module.cache.clear()
        _classify(module, result)
    return result


def _classify(module: ModuleFlow, result: ScanResult) -> None:
    for node, scope in module.nodes:
        if isinstance(node, ast.AugAssign) and not isinstance(node.op, ast.Div):
            if module.resolve(node.target, scope):
                result.unhandled_file_uses.append(f"{module.file}:{node.lineno} (AugAssign)")
            continue
        if not isinstance(node, ast.expr):
            continue
        if not isinstance(getattr(node, "ctx", ast.Load()), ast.Load):
            continue
        if isinstance(node, ast.Call) and node.args:
            _record_string_read(module, node, scope, result)
        refs = module.resolve(node, scope)
        if not refs:
            continue
        verdict = consumer(module, node, scope)
        if verdict == "follow":
            continue
        origin = f"{module.file}:{getattr(node, 'lineno', 0)}"
        if verdict == "unhandled":
            parent = module.parents.get(id(node))
            result.unhandled_file_uses.append(f"{origin} ({type(parent).__name__})")
        for ref in refs if verdict == "sink" else subtree(refs):
            if not ref.path.startswith("/"):
                result.add(ref.key(), origin)
    result.unhandled_file_uses.extend(
        f"{module.file}:{node.lineno} (__file__)"
        for node, _ in module.nodes
        if isinstance(node, ast.Name) and node.id == "__file__"
        and id(node) not in module.handled_file_nodes
    )
    result.unhandled_file_uses.extend(_unfollowed_roots(module))


def _is_find_spec(module: ModuleFlow, node: ast.AST | None) -> bool:
    if not isinstance(node, ast.Call):
        return False
    return (module.canon(node.func) or "").rsplit(".", 1)[-1] == "find_spec"


def _spec_names(module: ModuleFlow) -> set[str]:
    """Names bound to a ``find_spec(...)`` result anywhere in the module."""

    names: set[str] = set()
    for node, _ in module.nodes:
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign | ast.NamedExpr):
            targets = [node.target]
        if targets and _is_find_spec(module, getattr(node, "value", None)):
            names.update(t.id for t in targets if isinstance(t, ast.Name))
    return names


def _root_label(module: ModuleFlow, node: ast.AST, specs: set[str]) -> str | None:  # noqa: PLR0911
    if isinstance(node, ast.Attribute):
        if node.attr in ROOT_DUNDERS:
            return f"module {node.attr}"
        if node.attr in ROOT_ATTRS:
            return node.attr
        receiver = node.value
        on_spec = _is_find_spec(module, receiver) or (
            isinstance(receiver, ast.Name) and receiver.id in specs)
        if node.attr in SPEC_ATTRS and on_spec:
            return f"find_spec {node.attr}"
    if isinstance(node, ast.Name) and node.id in ROOT_DUNDERS - {"__file__"}:
        return node.id
    if isinstance(node, ast.Name | ast.Attribute) and module.canon(node) in ROOT_REFS:
        return module.canon(node)
    if isinstance(node, ast.Constant) and node.value in ROOT_DUNDERS:
        # A module attribute looked up by name: globals()["__file__"],
        # vars(m)["__file__"], frame.f_globals.get("__file__"), getattr(m, ...).
        parent = module.parents.get(id(node))
        keyed = isinstance(parent, ast.Subscript) and parent.slice is node
        if keyed or (isinstance(parent, ast.Call) and node in parent.args):
            return f"{node.value} by name"
    return None


def _unfollowed_roots(module: ModuleFlow) -> Iterator[str]:
    """Each reference to a root the scan is known not to follow (paths.ROOT_*)."""

    specs = _spec_names(module)
    for node, _ in module.nodes:
        label = _root_label(module, node, specs)
        if label is not None:
            yield f"{module.file}:{getattr(node, 'lineno', 0)} ({label})"


def _record_string_read(module: ModuleFlow, call: ast.Call, scope: Scope, result: ScanResult) -> None:
    """``open("x")`` or ``os.listdir("x")``: a cwd-relative string read, not a path value."""

    name = module.canon(call.func)
    if name not in OPEN_CALLS and name not in DIR_CALLS:
        return
    origin = f"{module.file}:{call.lineno}"
    for text, dynamic in module.strings_of(call.args[0], scope):
        if name in ("glob.glob", "glob.iglob"):
            text, dynamic = glob_prefix(text), True
        ref = join_ref(Ref("."), text, dynamic)
        if ref is not None:
            result.add((ref if name in OPEN_CALLS else Ref(ref.path, True)).key(), origin)


def outside_reads(result: ScanResult, package: str = "backend") -> dict[str, set[str]]:
    """References outside ``package``; an exact read of the repo root itself drops out."""

    return {
        key: origins
        for key, origins in result.references.items()
        if key != "." and key != package and not key.startswith(f"{package}/")
    }

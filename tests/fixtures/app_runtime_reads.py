"""Static flow scan of the App backend for files it reads outside ``backend/``.

Feeds tests/unit/test_app_runtime_closure_reads.py, which checks the result
against tools/databricks/app_runtime_closure.py. It is one of two independent
nets; tests/fixtures/app_runtime_literals.py is the other.

A path value starts at ``__file__`` (through ``Path``, ``os.path.dirname``,
``abspath``, ``realpath``), at the cwd (``Path.cwd()``, ``os.getcwd()``, a
relative ``Path("...")``, ``open("...")`` or ``os.path.join("...")``, which the
App resolves against its source root), or at a string constant, f-string or
``os.environ.get`` default fed to one of those. The scan follows it, to a fixed
point over every scanned module, through ``/``, ``.parent``, ``.parents[N]``,
``joinpath``, ``with_name``, ``resolve()``, ``str()``, ``or``/``if``, names,
tuple targets, walrus and ``/=``, function returns, call arguments into first-party
functions (including imported ones and ``self.method``), parameter defaults,
attributes assigned on ``self``, ``cls`` or a class body, names and module
attributes imported from other first-party modules, containers, and ``for``
loops. Each value ends at a consumer, which decides what is read:

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

A root it does not follow is reported as unhandled wherever it is referenced
(app_runtime_paths.ROOT_DUNDERS and ROOT_REFS): a module attribute
``__file__`` (``backend.__file__``, ``sys.modules[__name__].__file__``),
``__spec__``, ``__loader__``, ``__path__``, ``__cached__``,
``inspect.getfile`` and its siblings, ``sys.argv``, ``sys.path``,
``os.curdir``, and the ``importlib.resources``, ``pkgutil.get_data`` and
``pkg_resources`` data helpers.

A path value bound to a name nothing loads counts as read where it is bound.

Known residuals, each a shape no backend module uses today: a first-party
callee the scan cannot name (a method reached through an object, a function
passed as a value) is assumed to read only under the path it receives, so a
callee that walks up from it is not seen; a string that reaches a path
constructor only through a function parameter is not followed (the literal
sweep is the net for those); ``getattr``, ``exec`` and ``importlib`` with a
computed name are invisible.

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
    ROOT_DUNDERS,
    ROOT_REFS,
    Ref,
    Scope,
    glob_prefix,
    join_ref,
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


def _imported_names(tree: ast.Module) -> Iterator[tuple[str, int]]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from ((alias.name, node.lineno) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.module, node.lineno
            yield from ((f"{node.module}.{a.name}", node.lineno) for a in node.names)
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
        world.modules[_dotted_name(relfile)] = ModuleFlow(world, relfile, _dotted_name(relfile), tree)
        for name, line in _imported_names(tree):
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


def _unfollowed_roots(module: ModuleFlow) -> Iterator[str]:
    """Every reference to a root the scan does not follow (paths.ROOT_*)."""

    for node, _ in module.nodes:
        label = None
        if isinstance(node, ast.Attribute) and node.attr in ROOT_DUNDERS:
            label = f"module {node.attr}"
        elif isinstance(node, ast.Name) and node.id in ROOT_DUNDERS - {"__file__"}:
            label = node.id
        elif isinstance(node, ast.Name | ast.Attribute) and module.canon(node) in ROOT_REFS:
            label = module.canon(node)
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

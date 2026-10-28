"""Static scan of the App backend for files it reads outside ``backend/``.

Feeds tests/unit/test_app_runtime_closure_reads.py, which checks the result
against tools/databricks/app_runtime_closure.py. The scanner is an AST
evaluator over the shapes the backend actually uses (read at each call site):

* ``Path(__file__)`` followed by ``.resolve()``, ``.parent``, ``.parents[N]``,
  ``.with_name(...)``, ``.joinpath(...)`` and ``/ "segment"``;
* the same path held in a local or module name (``root = ...; root / "x"``)
  or returned by a no-argument function (``_repo_root() / "sql"``);
* cwd-relative ``Path("sql/...")`` and ``open("...")`` literals (the App's
  cwd is its source root), including inside ``x or Path("...")``;
* first-party imports outside backend (``from jobs.x import``,
  ``importlib.import_module("tools.x")``), whose modules are scanned too.

Any other use of ``__file__`` is returned as unhandled, so a new shape fails
loudly instead of being silently missed.
"""

from __future__ import annotations

import ast
import posixpath
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

_PASSTHROUGH_METHODS = frozenset({"resolve", "absolute", "expanduser"})
_PATH_TYPES = frozenset({"Path", "PurePath", "PosixPath", "PurePosixPath"})
_PATH_CALLS = _PATH_TYPES | {"str", "fspath"}


@dataclass
class ScanResult:
    references: dict[str, set[str]] = field(default_factory=dict)  # path -> origins
    unhandled_file_uses: list[str] = field(default_factory=list)
    scanned: list[str] = field(default_factory=list)

    def add(self, path: str, origin: str) -> None:
        self.references.setdefault(path, set()).add(origin)


def _join(base: str, *segments: str) -> str:
    return posixpath.normpath(posixpath.join(base or ".", *segments))


def _up(path: str, levels: int) -> str:
    """``levels`` directories up; above the repo root stays visible as ``..``."""

    for _ in range(levels):
        if path in ("", "."):
            path = ".."
        elif posixpath.basename(path) == "..":
            path = f"{path}/.."
        else:
            path = posixpath.dirname(path)
    return path


def _const_strings(nodes: list[ast.expr]) -> list[str] | None:
    values = [n.value for n in nodes if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    return values if len(values) == len(nodes) and values else None


def _callee(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    return func.attr if isinstance(func, ast.Attribute) else None


class _ModuleScan:
    """Resolve path-valued expressions in one module, scope by scope."""

    def __init__(self, relfile: str, tree: ast.Module) -> None:
        self.file = relfile
        self.tree = tree
        self.bindings: dict[tuple[str, ...], dict[str, set[str]]] = {}
        self.returns: dict[str, set[str]] = {}
        self.handled_file_nodes: set[int] = set()

    def scoped(self) -> Iterator[tuple[ast.AST, tuple[str, ...]]]:
        stack: list[tuple[ast.AST, tuple[str, ...]]] = [(self.tree, ())]
        while stack:
            node, scope = stack.pop()
            yield node, scope
            inner = scope
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                inner = (*scope, node.name)
            stack.extend((child, inner) for child in ast.iter_child_nodes(node))

    def _lookup(self, name: str, scope: tuple[str, ...]) -> set[str]:
        for depth in range(len(scope), -1, -1):
            found = self.bindings.get(scope[:depth], {}).get(name)
            if found:
                return found
        return set()

    def resolve(self, node: ast.AST, scope: tuple[str, ...]) -> set[str]:  # noqa: C901
        if isinstance(node, ast.Name):
            return self._lookup(node.id, scope)
        if isinstance(node, ast.BoolOp):
            return set().union(*(self.resolve(value, scope) for value in node.values))
        if isinstance(node, ast.IfExp):
            return self.resolve(node.body, scope) | self.resolve(node.orelse, scope)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            right = _const_strings([node.right])
            return {_join(p, *right) for p in self.resolve(node.left, scope)} if right else set()
        if isinstance(node, ast.Attribute) and node.attr == "parent":
            return {_up(p, 1) for p in self.resolve(node.value, scope)}
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "parents"
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, int)
        ):
            return {_up(p, node.slice.value + 1) for p in self.resolve(node.value.value, scope)}
        if isinstance(node, ast.Call):
            return self._resolve_call(node, scope)
        return set()

    def _resolve_call(self, node: ast.Call, scope: tuple[str, ...]) -> set[str]:
        name = _callee(node)
        func = node.func
        if name in _PATH_CALLS and node.args:
            first = node.args[0]
            if isinstance(first, ast.Name) and first.id == "__file__":
                self.handled_file_nodes.add(id(first))
                return {self.file}
            literal = _const_strings(node.args) if name in _PATH_TYPES else None
            if literal:
                return set() if literal[0].startswith("/") else {_join("", *literal)}
            return self.resolve(first, scope) if len(node.args) == 1 else set()
        if name == "open" and node.args:
            literal = _const_strings(node.args[:1])
            return {_join("", literal[0])} if literal and not literal[0].startswith("/") else set()
        if isinstance(func, ast.Attribute):
            if func.attr in _PASSTHROUGH_METHODS and not node.args:
                return self.resolve(func.value, scope)
            segments = _const_strings(node.args)
            if func.attr == "with_name" and segments:
                return {_join(_up(p, 1), segments[0]) for p in self.resolve(func.value, scope)}
            if func.attr == "joinpath" and segments:
                return {_join(p, *segments) for p in self.resolve(func.value, scope)}
        if isinstance(func, ast.Name) and not node.args and not node.keywords:
            return set(self.returns.get(func.id, set()))
        return set()

    def bind(self) -> None:
        """Fixed point over assignments and function returns."""

        for _ in range(8):
            changed = False
            for node, scope in self.scoped():
                target: str | None = None
                values: set[str] = set()
                if isinstance(node, ast.Assign) and len(node.targets) == 1:
                    if isinstance(node.targets[0], ast.Name):
                        target, values = node.targets[0].id, self.resolve(node.value, scope)
                elif isinstance(node, ast.AnnAssign) and node.value is not None:
                    if isinstance(node.target, ast.Name):
                        target, values = node.target.id, self.resolve(node.value, scope)
                elif isinstance(node, ast.Return) and node.value is not None and scope:
                    returned = self.returns.setdefault(scope[-1], set())
                    new = self.resolve(node.value, scope) - returned
                    returned |= new
                    changed |= bool(new)
                if target is not None and values:
                    bound = self.bindings.setdefault(scope, {}).setdefault(target, set())
                    changed |= not values <= bound
                    bound |= values
            if not changed:
                return

    def imports(self) -> Iterator[tuple[str, int]]:
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                yield from ((alias.name, node.lineno) for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                yield node.module, node.lineno
                yield from ((f"{node.module}.{a.name}", node.lineno) for a in node.names)
            elif isinstance(node, ast.Call) and _callee(node) == "import_module":
                literal = _const_strings(node.args[:1])
                if literal:
                    yield literal[0], node.lineno


def _module_file(root: Path, module: str) -> str | None:
    base = module.replace(".", "/")
    for candidate in (f"{base}.py", f"{base}/__init__.py"):
        if (root / candidate).is_file():
            return candidate
    return base if (root / base).is_dir() else None


def scan(root: Path, package: str = "backend") -> ScanResult:
    """References from ``root/package`` (and first-party modules it imports)."""

    result = ScanResult()
    top_levels = {p.name for p in root.iterdir() if p.is_dir()} - {package}
    queue = sorted(p.relative_to(root).as_posix() for p in (root / package).rglob("*.py"))
    seen = set(queue)
    while queue:
        relfile = queue.pop(0)
        result.scanned.append(relfile)
        tree = ast.parse((root / relfile).read_text(encoding="utf-8"), filename=relfile)
        module = _ModuleScan(relfile, tree)
        module.bind()
        for node, scope in module.scoped():
            if isinstance(node, ast.expr):
                line = getattr(node, "lineno", 0)
                for path in module.resolve(node, scope):
                    result.add(path, f"{relfile}:{line}")
        for name, line in module.imports():
            if name.split(".")[0] not in top_levels:
                continue
            target = _module_file(root, name)
            if target is None:
                continue
            result.add(target, f"{relfile}:{line}")
            if target.endswith(".py") and target not in seen:
                seen.add(target)
                queue.append(target)
        result.unhandled_file_uses.extend(
            f"{relfile}:{node.lineno}"
            for node in ast.walk(tree)
            if isinstance(node, ast.Name)
            and node.id == "__file__"
            and id(node) not in module.handled_file_nodes
        )
    return result


def outside_reads(result: ScanResult, package: str = "backend") -> dict[str, set[str]]:
    """Leaf references outside ``package``: ancestors of a sibling read drop out."""

    outside = {
        path: origins
        for path, origins in result.references.items()
        if path not in ("", ".") and path != package and not path.startswith(f"{package}/")
    }
    leaves: dict[str, set[str]] = {}
    for path, origins in outside.items():
        files = {origin.rsplit(":", 1)[0] for origin in origins}
        shadowing = [
            other for other, other_origins in outside.items()
            if other.startswith(f"{path}/")
            and files & {o.rsplit(":", 1)[0] for o in other_origins}
        ]
        if not shadowing:
            leaves[path] = origins
    return leaves

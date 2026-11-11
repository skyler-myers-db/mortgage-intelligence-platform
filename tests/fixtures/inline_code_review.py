"""What inline Python the deploy interpreter runs may name (M3-PR4, review of #330, round 3).

tests/fixtures/role_ledger.py classes each call the deploy interpreter ran. A
tool is classed by a reviewed table; inline code (``-c``, or a heredoc on
stdin) has no table entry, so it is reviewed here by what it names. Round 2
refused inline code that "loads code by name or starts a process" by
matching literal names, so ``import os as _os; _os.system(...)`` was classed
workspace, and a planted heredoc ran the human bootstrap with the proof
green. The review is an allowlist instead, read from the inline code the plan
and release dry runs execute (measured 2026-11-11: 72 heredoc calls and up to
5 ``-c`` calls a dispatch, 11 distinct scripts):

* a module outside the repository is imported only when ``INLINE_MODULES``
  lists it, and only the attributes it lists are used (``None``: any public
  attribute that is not itself a module, read by importing it here);
* a repository module's attribute is one its own source defines, never a
  name its imports bind (``from tools.x import os`` re-exports a module);
  a submodule is held to the same rule, one attribute at a time;
* a name an import binds to a module is used only as the root of an
  attribute chain: never passed, assigned or rebound, so no alias escapes
  the chain rule;
* every builtin the code names is on ``SAFE_BUILTINS`` (no ``getattr``,
  ``__import__``, ``eval``, ``vars``), every other name it reads is bound in
  the code, and no attribute is private, a dunder or an interpreter frame's
  (``f_globals``, ``gi_frame``): the routes from an object back to a
  module's namespace or the builtins;
* no relative or star import.

So ``sys.path`` and ``sys.modules`` (not reviewed ``sys`` attributes),
``builtins``, ``pkgutil``, ``importlib``, ``asyncio`` and ``subprocess`` (not
reviewed modules), ``getattr(os, "system")`` (not a safe builtin) and every
``os`` call but the reviewed reads are refused, by whatever name an import
binds them to.

It reads what the code names, not what it writes: code that writes a file a
later process loads is not modelled here, and that later call is classed by
its own arguments.

Standard library only: role_ledger imports it, and the stub CLI imports
role_ledger from the sandbox clone.
"""

from __future__ import annotations

import ast
import builtins
import functools
import importlib
import inspect
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

# The modules outside the repository inline code may import, and the
# attributes it may use of each: None for any public attribute that is not
# itself a module. Read from the inline code the dry runs execute: json, sys,
# re, os (environ), pathlib (Path), yaml (safe_load), dotenv (dotenv_values)
# and pydantic (imported only, as an availability check), with a few reviewed
# siblings of the same kind.
INLINE_MODULES: Mapping[str, frozenset[str] | None] = {
    "json": None,
    "re": None,
    "pathlib": None,
    "os.path": None,
    # os starts processes and sys reaches the import system: reviewed reads only.
    "os": frozenset({"environ", "getenv", "fspath", "getcwd", "sep", "linesep", "pathsep"}),
    "sys": frozenset({"argv", "stdin", "stdout", "stderr", "exit", "version_info"}),
    # yaml.load and unsafe_load build arbitrary objects; dotenv's CLI runs a command.
    "yaml": frozenset({"safe_load", "safe_load_all", "safe_dump", "YAMLError"}),
    "dotenv": frozenset({"dotenv_values"}),
    "pydantic": frozenset(),
}
# The builtins inline code may name: values, containers and pure functions,
# and every exception class. Not reflection (getattr, setattr, vars, dir,
# globals, locals), code (__import__, eval, exec, compile, breakpoint) or the
# interactive helpers.
SAFE_BUILTINS = frozenset(
    {
        "abs", "all", "any", "ascii", "bin", "bool", "bytearray", "bytes", "callable", "chr", "classmethod",
        "complex", "dict", "divmod", "enumerate", "filter", "float", "format", "frozenset", "hash", "hex", "id",
        "int", "isinstance", "issubclass", "iter", "len", "list", "map", "max", "memoryview", "min", "next",
        "object", "oct", "open", "ord", "pow", "print", "property", "range", "repr", "reversed", "round", "set",
        "slice", "sorted", "staticmethod", "str", "sum", "super", "tuple", "type", "zip",
        "Ellipsis", "NotImplemented", "__name__",
    }
) | frozenset(name for name, value in vars(builtins).items() if isinstance(value, type) and issubclass(value, BaseException))
# The attributes that lead from a generator, coroutine, traceback or frame to
# a frame's globals and builtins (CPython's, a closed set).
FRAME_ATTRIBUTES = frozenset(
    {
        "f_back", "f_builtins", "f_code", "f_globals", "f_locals", "f_trace",
        "gi_code", "gi_frame", "gi_yieldfrom", "cr_await", "cr_code", "cr_frame", "cr_origin",
        "ag_await", "ag_code", "ag_frame", "tb_frame", "tb_next",
    }
)


_MISSING = object()


@dataclass(frozen=True)
class Repository:
    """The repository's modules as the review reads them: its packages, and each module's source files."""

    packages: frozenset[str]
    sources: Callable[[str], Sequence[Path]]
    is_package_dir: Callable[[str], bool]

    def owns(self, name: str) -> bool:
        return name.split(".")[0] in self.packages

    def is_module(self, name: str) -> bool:
        return bool(self.sources(name)) or self.is_package_dir(name)

    def defines(self, module: str, name: str) -> bool:
        """Whether ``module``'s own source binds ``name`` at its top level, and no import of it does."""

        found = [_top_level_bindings(path) for path in self.sources(module)]
        return any(name in defined for defined, _imported in found) and not any(name in imported for _defined, imported in found)


@functools.cache
def _top_level_bindings(path: Path) -> tuple[frozenset[str], frozenset[str]]:
    """The names a module binds at its top level: by definition or assignment, and by an import."""

    defined: set[str] = set()
    imported: set[str] = set()
    todo: list[ast.AST] = list(ast.parse(path.read_text(encoding="utf-8")).body)
    while todo:
        node = todo.pop()
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            defined.add(node.name)
        elif isinstance(node, ast.Import | ast.ImportFrom):
            imported.update((alias.asname or alias.name).split(".")[0] for alias in node.names)
        elif isinstance(node, ast.Assign | ast.AnnAssign | ast.AugAssign):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            defined.update(n.id for t in targets for n in ast.walk(t) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store))
        elif isinstance(node, ast.If | ast.Try | ast.TryStar | ast.With | ast.For | ast.While):
            for field in ("body", "orelse", "finalbody", "handlers"):
                todo.extend(getattr(node, field, []))
        elif isinstance(node, ast.ExceptHandler):
            todo.extend(node.body)
    return frozenset(defined), frozenset(imported)


class _Review:
    """One pass over inline code; ``refusals`` collects each thing it names that is not reviewed."""

    def __init__(self, repo: Repository) -> None:
        self.repo = repo
        self.refusals: list[str] = []
        self.modules: dict[str, str] = {}  # a name an import binds to a module -> that module

    def is_module(self, name: str) -> bool:
        return self.repo.is_module(name) if self.repo.owns(name) else name in INLINE_MODULES

    def attribute(self, module: str, name: str) -> bool:
        """Whether ``module.name`` (not itself a module) is reviewed."""

        if self.repo.owns(module):
            return self.repo.defines(module, name)
        allowed = INLINE_MODULES.get(module, frozenset())
        if allowed is not None:
            return name in allowed
        value = getattr(importlib.import_module(module), name, _MISSING)
        return not name.startswith("_") and value is not _MISSING and not inspect.ismodule(value)

    def importable(self, name: str) -> bool:
        parts = name.split(".")
        prefixes = [".".join(parts[: index + 1]) for index in range(len(parts))]
        return self.repo.is_module(name) if self.repo.owns(name) else all(prefix in INLINE_MODULES for prefix in prefixes)

    def bind(self, name: str, module: str) -> None:
        if self.modules.setdefault(name, module) != module:
            self.refusals.append(f"{name} bound to two modules")

    def imports(self, tree: ast.AST) -> None:
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if not self.importable(alias.name):
                        self.refusals.append(f"import {alias.name}")
                    elif alias.asname:
                        self.bind(alias.asname, alias.name)
                    else:
                        self.bind(alias.name.split(".")[0], alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if node.level or not self.importable(module):
                    self.refusals.append(f"from {'.' * node.level}{module} import")
                    continue
                for alias in node.names:
                    if self.is_module(f"{module}.{alias.name}"):
                        self.bind(alias.asname or alias.name, f"{module}.{alias.name}")
                    elif alias.name == "*" or not self.attribute(module, alias.name):
                        self.refusals.append(f"from {module} import {alias.name}")

    def chain(self, node: ast.Attribute) -> None:
        """An attribute chain from a module-bound name: each step a reviewed submodule, then one reviewed attribute."""

        attrs: list[str] = []
        root: ast.expr = node
        while isinstance(root, ast.Attribute):
            attrs.append(root.attr)
            root = root.value
        if not isinstance(root, ast.Name) or root.id not in self.modules:
            return
        module = self.modules[root.id]
        for attr in reversed(attrs):
            if self.is_module(f"{module}.{attr}"):
                module = f"{module}.{attr}"
            elif self.attribute(module, attr):
                return
            else:
                self.refusals.append(f"{module}.{attr}")
                return

    def names(self, tree: ast.AST) -> None:
        bound = _bound_names(tree)
        roots = {id(node.value) for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        refused = (set(dir(builtins)) - SAFE_BUILTINS) | {"__builtins__"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                if node.attr.startswith("_") or node.attr in FRAME_ATTRIBUTES:
                    self.refusals.append(f"attribute {node.attr}")
                self.chain(node)
            elif isinstance(node, ast.Name):
                if node.id in refused:
                    self.refusals.append(f"builtin {node.id}")
                elif node.id in self.modules and id(node) not in roots:
                    self.refusals.append(f"module name {node.id} used outside an attribute")
                elif isinstance(node.ctx, ast.Load) and node.id not in bound and node.id not in SAFE_BUILTINS:
                    self.refusals.append(f"unbound name {node.id}")


def _bound_names(tree: ast.AST) -> set[str]:
    """Every name the code binds anywhere: assignments, definitions, arguments, imports, handlers and patterns."""

    bound: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load):
            bound.add(node.id)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            bound.add(node.name)
        elif isinstance(node, ast.arg):
            bound.add(node.arg)
        elif isinstance(node, ast.alias):
            bound.add((node.asname or node.name).split(".")[0])
        elif isinstance(node, ast.ExceptHandler | ast.MatchAs | ast.MatchStar) and node.name:
            bound.add(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            bound.add(node.rest)
        elif isinstance(node, ast.Global | ast.Nonlocal):
            bound.update(node.names)
    return bound


def inline_refusals(tree: ast.AST, repo: Repository) -> list[str]:
    """Everything parsed inline code names that is not reviewed; empty when it is held to the allowlist."""

    review = _Review(repo)
    review.imports(tree)
    review.names(tree)
    return review.refusals

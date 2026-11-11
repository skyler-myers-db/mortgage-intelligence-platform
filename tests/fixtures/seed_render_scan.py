"""Where the raw seed template can go: the analysis behind tests/unit/test_seed_render_guard.py.

lakebase/seed_campaigns.sql carries ``{{mip_seed_operator_email}}``, so a raw
apply would seed the literal token as an operator address. These functions
read Python source and report every place the seed's path or text could reach
SQL without render_seed_sql; the guard runs them over the tracked tree. Each
is fail-closed: a shape it cannot follow is reported, not skipped.
"""

from __future__ import annotations

import ast
import fnmatch
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass

_RENDERERS = frozenset({"render_seed_sql", "_render_seed_sql"})
_EXECUTORS = frozenset({"execute", "executemany", "executescript", "_run_transaction"})
_READS = frozenset({"read_text", "read_bytes"})
# What a path may pass through between the file's name and its read or binding.
_PATH_STEPS = frozenset(
    {"Path", "PurePath", "PosixPath", "PurePosixPath", "joinpath", "resolve", "absolute", "expanduser", "str", "fspath"}
)
ENUMERATORS = frozenset({"glob", "rglob", "iglob", "iterdir", "listdir", "scandir", "walk"})
ENUMERATION = re.compile(r"\b(?:" + "|".join(sorted(ENUMERATORS)) + r")\b")
# The seed's path from its file name up: an enumeration reaches it only in a directory of this name.
_SEED_TAIL = ("seed_campaigns.sql", "lakebase")
# os.listdir(d), os.scandir(d), os.walk(d), glob.glob(pattern): the directory is the first argument.
_ENUMERATOR_MODULES = frozenset({"os", "glob"})
_PATH_TYPES = frozenset({"Path", "PurePath", "PosixPath", "PurePosixPath"})
_GLOB_CHARS = frozenset("*?[")


@dataclass(frozen=True)
class SeedScan:
    raw: list[int]  # lines where the seed's path or text goes anywhere but render_seed_sql
    reads: int  # seed reads the scan followed
    executes: bool  # whether the module executes SQL
    enumerations: list[tuple[int, str]]  # (line, call) of each enumeration that can list the seed
    exports: frozenset[str]  # top-level names that hand another module the raw seed's path or text


def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name):
            return node.func.id
        if isinstance(node.func, ast.Attribute):
            return node.func.attr
    return None


def _bound_names(node: ast.AST | None) -> list[str] | None:
    """The names an assignment of one value binds, or None when it binds anything else."""

    if isinstance(node, ast.Assign):
        targets = node.targets
    elif isinstance(node, ast.AnnAssign):
        targets = [node.target]
    else:
        return None
    if all(isinstance(target, ast.Name) for target in targets):
        return [target.id for target in targets]
    return None


def _parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    return {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}


def _prose(node: ast.Constant, parent: Mapping[ast.AST, ast.AST]) -> bool:
    """Whether a string is prose (a docstring, an assert message, an exception's text), not code."""

    if not (isinstance(node.value, str) and any(char.isspace() for char in node.value)):
        return False
    child, up = node, parent.get(node)
    while isinstance(up, ast.JoinedStr | ast.BinOp):
        child, up = up, parent.get(up)
    if isinstance(up, ast.Expr):
        return True
    if isinstance(up, ast.Assert):
        return child is up.msg
    return isinstance(up, ast.Call) and child in up.args and isinstance(parent.get(up), ast.Raise)


def _components(text: str) -> list[str]:
    return [part for part in text.split("/") if part not in ("", ".")]


def _pattern(expr: ast.AST | None) -> list[str] | None:
    """A glob pattern's components, each formatted value read as ``*``; None when the scan cannot tell."""

    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return _components(expr.value)
    if isinstance(expr, ast.JoinedStr) and all(
        isinstance(value, ast.FormattedValue) or (isinstance(value, ast.Constant) and not set("[]") & set(str(value.value)))
        for value in expr.values
    ):
        return _components("".join(str(v.value) if isinstance(v, ast.Constant) else "*" for v in expr.values))
    return None


def _reaches_seed(pattern: list[str] | None) -> bool:
    """Whether a pattern can match the seed under some directory (its last components against the seed's)."""

    if pattern is None or ".." in pattern:
        return True
    for target, component in zip(_SEED_TAIL, reversed(pattern), strict=False):
        if component == "**":
            return True
        if not fnmatch.fnmatchcase(target, component.lower()):
            return False
    return True


class _Directories:
    """What a module proves about a directory: the literal components that end it.

    A piece the scan cannot see (a parameter, an attribute, a helper's return,
    an import, a name bound more than one way) leaves only what follows it, so
    ``root / "wheelhouse"`` ends in ``wheelhouse`` whatever ``root`` is, and a
    bare parameter ends in nothing the scan knows.
    """

    def __init__(self, tree: ast.AST) -> None:
        self.assigned: dict[str, list[ast.expr]] = {}
        simple: dict[str, int] = {}
        stores: dict[str, int] = {}
        self.unbound: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign | ast.AnnAssign) and node.value is not None:
                for name in _bound_names(node) or []:
                    self.assigned.setdefault(name, []).append(node.value)
                    simple[name] = simple.get(name, 0) + 1
            if isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load):
                stores[node.id] = stores.get(node.id, 0) + 1
            elif isinstance(node, ast.arg):
                self.unbound.add(node.arg)
            elif isinstance(node, ast.alias):
                self.unbound.add((node.asname or node.name).split(".")[0])
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                self.unbound.add(node.name)
            elif isinstance(node, ast.Global | ast.Nonlocal):
                self.unbound.update(node.names)
            elif isinstance(node, ast.ExceptHandler | ast.MatchAs | ast.MatchStar) and node.name:
                self.unbound.add(node.name)
        # A name bound any other way (a loop, a with, a walrus, a parameter, an import) is not provable.
        self.unbound |= {name for name, count in stores.items() if count != simple.get(name, 0)}

    def tail(self, expr: ast.AST | None, seen: frozenset[str] = frozenset()) -> list[str]:
        """The literal components ``expr``'s path ends in; [] when the scan knows none."""

        if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
            return _join([], expr.value.split("/"))
        if isinstance(expr, ast.Name):
            values = self.assigned.get(expr.id)
            if not values or expr.id in self.unbound or expr.id in seen:
                return []
            found = [self.tail(value, seen | {expr.id}) for value in values]
            return found[0] if all(each == found[0] for each in found) else []
        if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Div):
            return self._join(self.tail(expr.left, seen), [expr.right], seen)
        if isinstance(expr, ast.Attribute) and expr.attr == "parent":
            return self.tail(expr.value, seen)[:-1]
        if (
            isinstance(expr, ast.Subscript)
            and isinstance(expr.value, ast.Attribute)
            and expr.value.attr == "parents"
            and isinstance(expr.slice, ast.Constant)
            and isinstance(expr.slice.value, int)
        ):
            base = self.tail(expr.value.value, seen)
            return base[: max(len(base) - expr.slice.value - 1, 0)]
        if isinstance(expr, ast.Call) and not expr.keywords:
            func = expr.func
            if (isinstance(func, ast.Name) and func.id in _PATH_TYPES | {"str"}) or (
                isinstance(func, ast.Attribute) and func.attr in {"fspath", "join"}
            ):
                return self._join([], expr.args, seen)  # Path(a, b), str(p), os.fspath(p), os.path.join(a, b)
            if isinstance(func, ast.Attribute) and func.attr == "joinpath":
                return self._join(self.tail(func.value, seen), expr.args, seen)
            if isinstance(func, ast.Attribute) and func.attr in {"resolve", "absolute", "expanduser"} and not expr.args:
                return self.tail(func.value, seen)
        return []

    def _join(self, base: list[str], pieces: list[ast.expr], seen: frozenset[str]) -> list[str]:
        for piece in pieces:
            if isinstance(piece, ast.Constant) and isinstance(piece.value, str):
                base = _join(base, piece.value.split("/"))
            else:  # what the scan cannot see replaces everything before it
                base = self.tail(piece, seen)
        return base


def _join(base: list[str], parts: list[str]) -> list[str]:
    for part in parts:
        if part == "..":
            base = base[:-1]
        elif part not in ("", "."):
            base = [*base, part]
    return base


def _enumeration(node: ast.Call, directories: _Directories) -> tuple[list[str], list[str] | None] | None:
    """(the listed directory's last components, the pattern's) for an enumerator call, else None."""

    name = _call_name(node)
    owner = node.func.value if isinstance(node.func, ast.Attribute) else None
    if name not in ENUMERATORS or (isinstance(owner, ast.Name) and owner.id == "ast"):
        return None  # ast.walk walks a syntax tree, not a directory
    first = node.args[0] if node.args else None
    module_form = owner is None or (isinstance(owner, ast.Name) and owner.id in _ENUMERATOR_MODULES)
    if name in {"glob", "iglob"} and module_form:  # glob.glob(pattern): the directory leads the pattern
        whole = directories.tail(first)
        cut = next((at for at, part in enumerate(whole) if _GLOB_CHARS & set(part)), max(len(whole) - 1, 0))
        return whole[:cut], whole[cut:]
    if name in {"listdir", "scandir"} or (name == "walk" and module_form):
        return directories.tail(first), ["**" if name == "walk" else "*"]  # no argument lists the cwd
    directory = directories.tail(owner)
    if name in {"iterdir", "walk"}:
        return directory, ["**" if name == "walk" else "*"]
    pattern = _pattern(first)
    return directory, (["**", *pattern] if pattern is not None and name == "rglob" else pattern)


def _lists_the_seed(directory: list[str], pattern: list[str] | None) -> bool:
    """Whether an enumeration can list the seed: its pattern can match it, in a directory not proven another."""

    clear = (
        bool(directory)
        and not any(part.lower() == "lakebase" for part in directory)
        and (pattern is None or ".." not in pattern)
    )
    return _reaches_seed(pattern) and not clear


def _names_lakebase_as_a_path(node: ast.Constant, parent: Mapping[ast.AST, ast.AST]) -> bool:
    """Whether a string names the lakebase directory as a path, not as a dependency's label.

    A path component of a ``/``-joined string, a piece of a path (``/``, ``+``,
    Path(), joinpath, os.path.join) or a value bound to a name is a path; a
    label in a dict, a set, a comparison or any other call is not.
    """

    parts = [part.lower() for part in node.value.split("/")] if isinstance(node.value, str) else []
    if "lakebase" not in parts:
        return False
    if len(parts) > 1:
        return True
    up = parent.get(node)
    if isinstance(up, ast.Call):
        return node in up.args and _call_name(up) in _PATH_STEPS | {"join"}
    return isinstance(up, ast.BinOp | ast.Assign | ast.AnnAssign | ast.arguments)


def scan_seed_uses(source: str) -> SeedScan:
    """Every place in ``source`` the seed's path or text can go other than render_seed_sql.

    Fail-closed: every string in code that names ``seed_campaigns`` must be a
    path, built only by ``/``, ``+``, f-strings and Path steps, that is either
    bound to a name inside a function or read by ``.read_text()`` /
    ``.read_bytes()`` (``Path.read_text(p)`` and
    ``importlib.resources.read_text(pkg, name)`` count too). A read must be
    render_seed_sql's first argument, or be bound inside a function to a name
    whose every load is; a bound path's every load must be such a read.
    Anything else is a raw use: open(), an alias, a helper taking the name, a
    subprocess argument, a return, a module- or class-level binding another
    module could import, or a ``global`` / ``nonlocal`` one. Prose (a
    docstring, an assert message, an exception's text) may name the file.
    Comments are not code.

    An enumeration (glob, rglob, iterdir, listdir, scandir, os.walk) can list
    the seed unless its pattern cannot match it or the directory it lists is
    proven, from literals in this module, to be one not named lakebase; a
    directory from a helper, a parameter, an attribute or an import is not
    proven. Those land in ``enumerations``, for the guard's reviewed set.

    ``exports`` is what an inspector (a module allowed to read the raw text)
    hands any module that imports it: each top-level binding whose value holds
    the seed's path or text, a ``global`` one, and each function or class whose
    ``return`` / ``yield`` or class body holds it.
    """

    tree = ast.parse(source)
    parent = _parents(tree)
    loads: dict[str, list[ast.Name]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            loads.setdefault(node.id, []).append(node)
    escaping = {name for node in ast.walk(tree) if isinstance(node, ast.Global | ast.Nonlocal) for name in node.names}

    def rendered(node: ast.AST) -> bool:
        call = parent.get(node)
        return isinstance(call, ast.Call) and _call_name(call) in _RENDERERS and call.args[:1] == [node]

    def local(node: ast.AST, names: list[str] | None) -> bool:
        """Whether binding ``names`` at ``node`` stays inside a function call, not a module, class or global."""

        if names is None or escaping.intersection(names):
            return False
        up = parent.get(node)
        while up is not None:
            if isinstance(up, ast.ClassDef):
                return False
            if isinstance(up, ast.FunctionDef | ast.AsyncFunctionDef):
                return True
            up = parent.get(up)
        return False

    def method(up: ast.AST | None, names: frozenset[str]) -> ast.Call | None:
        """The call ``up`` makes when it is ``<receiver>.<one of names>``, else None."""

        call = parent.get(up) if isinstance(up, ast.Attribute) and up.attr in names else None
        return call if isinstance(call, ast.Call) and call.func is up else None

    def climb(node: ast.AST) -> tuple[ast.AST, ast.AST | None]:
        """The whole path expression ``node`` is part of, and what holds it."""

        child, up = node, parent.get(node)
        while True:
            if (step := method(up, _PATH_STEPS)) is not None:
                child = step  # p.resolve(), p.joinpath(...)
            elif isinstance(up, ast.BinOp | ast.JoinedStr | ast.FormattedValue) or (
                isinstance(up, ast.Call) and _call_name(up) in _PATH_STEPS and child in up.args
            ):
                child = up
            else:
                return child, up
            up = parent.get(child)

    raw: list[int] = []
    reads = 0
    values: set[ast.AST] = set()  # every expression that holds the raw seed's path or text
    path_names: set[str] = set()
    text_names: set[str] = set()
    pending: list[ast.AST] = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and "seed_campaigns" in node.value
        and not _prose(node, parent)
    ]
    while pending:
        anchor = pending.pop()
        path, up = climb(anchor)
        read = method(up, _READS)
        if read is None and isinstance(up, ast.Call) and _call_name(up) in _READS and path in up.args:
            read = up  # Path.read_text(p), importlib.resources.read_text(pkg, name)
        if read is None:
            values.add(path)
            names = _bound_names(up)
            if not local(path, names):
                raw.append(path.lineno)
            for name in set(names or []) - path_names:
                path_names.add(name)
                pending.extend(loads.get(name, []))
            continue
        reads += 1
        if rendered(read):
            continue
        values.add(read)
        names = _bound_names(parent.get(read))
        if not local(read, names):
            raw.append(read.lineno)
        text_names.update(names or [])
    for name in text_names:
        for node in loads.get(name, []):
            if not rendered(node):
                values.add(node)
                raw.append(node.lineno)

    # Enumerations count in a module that names the seed in code, or the lakebase directory as a path.
    aware = reads > 0 or bool(values) or any(
        isinstance(node, ast.Constant) and not _prose(node, parent) and _names_lakebase_as_a_path(node, parent)
        for node in ast.walk(tree)
    )
    directories = _Directories(tree)
    enumerations: list[tuple[int, str]] = []
    for node in ast.walk(tree) if aware else ():
        listed = _enumeration(node, directories) if isinstance(node, ast.Call) else None
        if listed is not None and _lists_the_seed(*listed):
            values.add(node)
            enumerations.append((node.lineno, ast.unparse(node)))

    executes = any(_call_name(node) in _EXECUTORS for node in ast.walk(tree))
    return SeedScan(sorted(raw), reads, executes, enumerations, _exports(tree, parent, values, escaping, rendered))


def raw_seed_uses(source: str) -> tuple[list[int], int, bool]:
    """(lines where the seed can go anywhere but render_seed_sql, seed reads, whether it executes SQL).

    The lines include each enumeration that can list the seed; the guard's
    tree test reads ``scan_seed_uses`` instead, to admit its reviewed ones.
    """

    scan = scan_seed_uses(source)
    return sorted(scan.raw + [line for line, _call in scan.enumerations]), scan.reads, scan.executes


def _exports(
    tree: ast.Module,
    parent: Mapping[ast.AST, ast.AST],
    values: set[ast.AST],
    escaping: set[str],
    rendered: Callable[[ast.AST], bool],
) -> frozenset[str]:
    """The top-level names through which the seed's raw path or text leaves the module."""

    exports: set[str] = set()

    def carries(node: ast.AST) -> bool:
        return any(
            inner in values
            or (
                isinstance(inner, ast.Name)
                and isinstance(inner.ctx, ast.Load)
                and inner.id in exports
                and not rendered(inner)
            )
            for inner in ast.walk(node)
        )

    while True:
        found: set[str] = set()
        for statement in tree.body:
            if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                handed = [
                    node.value
                    for node in ast.walk(statement)
                    if isinstance(node, ast.Return | ast.Yield | ast.YieldFrom) and node.value is not None
                ]
                if isinstance(statement, ast.ClassDef):
                    handed += [
                        node.value
                        for node in statement.body
                        if isinstance(node, ast.Assign | ast.AnnAssign | ast.AugAssign) and node.value is not None
                    ]
                if any(carries(value) for value in handed):
                    found.add(statement.name)
            elif carries(statement):  # a binding, or a with / for / if whose body binds module names
                found |= _module_names(statement)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign | ast.AnnAssign) and node.value is not None and carries(node.value):
                found |= escaping.intersection(_bound_names(node) or [])
        if found <= exports:
            return frozenset(exports)
        exports |= found


def _module_names(statement: ast.stmt) -> set[str]:
    """The module-level names a top-level statement binds (not those inside a function, class or comprehension)."""

    names: set[str] = set()
    pending: list[ast.AST] = [statement]
    while pending:
        node = pending.pop()
        if isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load):
            names.add(node.id)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.add(node.name)
            continue
        if isinstance(node, ast.Lambda | ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp):
            continue
        pending.extend(ast.iter_child_nodes(node))
    return names


def inspector_reaches(source: str, exports: Mapping[str, frozenset[str]]) -> list[int]:
    """Lines of ``source`` that can reach an inspector's raw seed: ``exports`` maps each inspector's stem to its own.

    A from-import of one of its exports, of ``*`` or of a dunder is a reach;
    so is any use of the inspector's module object other than reading an
    attribute it does not export, and any string that is the module's dotted
    name or file path (importlib, runpy, sys.modules, pytest_plugins). Text
    that only mentions the module (a comment, prose, an embedded script's
    comment) is not.
    """

    tree = ast.parse(source)
    parent = _parents(tree)

    def denied(stem: str, name: str) -> bool:
        return name == "*" or name.startswith("__") or name in exports[stem]

    hits: list[int] = []
    modules: dict[str, str] = {}  # how this source spells an inspector's module object -> the inspector's stem
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            stem = (node.module or "").rsplit(".", 1)[-1]
            if stem in exports:
                hits.extend(node.lineno for alias in node.names if denied(stem, alias.name))
            else:  # from tests.unit import <inspector>
                modules.update({alias.asname or alias.name: alias.name for alias in node.names if alias.name in exports})
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if (stem := alias.name.rsplit(".", 1)[-1]) in exports:
                    modules[alias.asname or alias.name] = stem
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if any(re.fullmatch(rf"[\w./\\:-]*\b{re.escape(stem)}(?:\.py)?", node.value.strip()) for stem in exports):
                hits.append(node.lineno)
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Name | ast.Attribute) and isinstance(node.ctx, ast.Load)):
            continue
        inspector = modules.get(ast.unparse(node))
        if inspector is None:
            continue
        up = parent.get(node)
        if not (isinstance(up, ast.Attribute) and up.value is node and not denied(inspector, up.attr)):
            hits.append(node.lineno)
    return sorted(hits)

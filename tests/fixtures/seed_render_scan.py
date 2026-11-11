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

_RENDERERS = frozenset({"render_seed_sql", "_render_seed_sql"})
_EXECUTORS = frozenset({"execute", "executemany", "executescript", "_run_transaction"})
_READS = frozenset({"read_text", "read_bytes"})
# What a path may pass through between the file's name and its read or binding.
_PATH_STEPS = frozenset(
    {"Path", "PurePath", "PosixPath", "PurePosixPath", "joinpath", "resolve", "absolute", "expanduser", "str", "fspath"}
)
_ENUMERATORS = frozenset({"glob", "rglob", "iglob", "iterdir", "listdir", "scandir", "walk"})
_LAKEBASE_DIR = re.compile(r"(?:^|/)lakebase(?:/|$)")
ENUMERATION = re.compile(r"\b(?:" + "|".join(sorted(_ENUMERATORS)) + r")\b")


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


def raw_seed_uses(source: str) -> tuple[list[int], int, bool]:
    """(lines where the seed goes anywhere but render_seed_sql, seed reads, whether it executes SQL).

    Fail-closed: every string in code that names ``seed_campaigns`` must be a
    path, built only by ``/``, ``+``, f-strings and Path steps, that is either
    bound to a name inside a function or read by ``.read_text()`` /
    ``.read_bytes()`` (``Path.read_text(p)`` and
    ``importlib.resources.read_text(pkg, name)`` count too). A read must be
    render_seed_sql's first argument, or be bound inside a function to a name
    whose every load is; a bound path's every load must be such a read.
    Anything else is a raw use: open(), an alias, a helper taking the name, a
    subprocess argument, a return, or a module- or class-level binding another
    module could import. Prose (a docstring, an assert message, an exception's
    text) may name the file. An enumeration of the lakebase directory that can
    match the seed is a raw use too. Comments are not code.
    """

    tree = ast.parse(source)
    parent = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    loads: dict[str, list[ast.Name]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            loads.setdefault(node.id, []).append(node)

    def rendered(node: ast.AST) -> bool:
        call = parent.get(node)
        return isinstance(call, ast.Call) and _call_name(call) in _RENDERERS and call.args[:1] == [node]

    def in_function(node: ast.AST) -> bool:
        up = parent.get(node)
        while up is not None:
            if isinstance(up, ast.ClassDef):
                return False
            if isinstance(up, ast.FunctionDef | ast.AsyncFunctionDef):
                return True
            up = parent.get(up)
        return False

    def prose(node: ast.Constant) -> bool:
        if not any(char.isspace() for char in node.value):
            return False
        child, up = node, parent.get(node)
        while isinstance(up, ast.JoinedStr | ast.BinOp):
            child, up = up, parent.get(up)
        if isinstance(up, ast.Expr):
            return True
        if isinstance(up, ast.Assert):
            return child is up.msg
        return isinstance(up, ast.Call) and child in up.args and isinstance(parent.get(up), ast.Raise)

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
    path_names: set[str] = set()
    text_names: set[str] = set()
    pending: list[ast.AST] = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and "seed_campaigns" in node.value
        and not prose(node)
    ]
    while pending:
        anchor = pending.pop()
        path, up = climb(anchor)
        read = method(up, _READS)
        if read is None and isinstance(up, ast.Call) and _call_name(up) in _READS and path in up.args:
            read = up  # Path.read_text(p), importlib.resources.read_text(pkg, name)
        if read is None:
            names = _bound_names(up)
            if names is None or not in_function(path):
                raw.append(path.lineno)
            for name in set(names or []) - path_names:
                path_names.add(name)
                pending.extend(loads.get(name, []))
            continue
        reads += 1
        if rendered(read):
            continue
        names = _bound_names(parent.get(read))
        if names is None or not in_function(read):
            raw.append(read.lineno)
        text_names.update(names or [])
    raw.extend(node.lineno for name in text_names for node in loads.get(name, []) if not rendered(node))

    lakebase_names: set[str] = set()

    def names_lakebase(expr: ast.AST) -> bool:
        return any(
            (isinstance(inner, ast.Constant) and isinstance(inner.value, str) and _LAKEBASE_DIR.search(inner.value))
            or (isinstance(inner, ast.Name) and inner.id in lakebase_names)
            for inner in ast.walk(expr)
        )

    bindings = [node for node in ast.walk(tree) if isinstance(node, ast.Assign | ast.AnnAssign) and node.value]
    while new := {
        name
        for node in bindings
        if node.value is not None and names_lakebase(node.value)
        for name in _bound_names(node) or []
    } - lakebase_names:
        lakebase_names |= new
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and _call_name(node) in _ENUMERATORS and names_lakebase(node)):
            continue
        first = node.args[0] if node.args else None
        pattern = first.value if isinstance(first, ast.Constant) and isinstance(first.value, str) else None
        if pattern is None or any(
            fnmatch.fnmatchcase(seed, pattern) for seed in ("seed_campaigns.sql", "lakebase/seed_campaigns.sql")
        ):
            raw.append(node.lineno)
    executes = any(_call_name(node) in _EXECUTORS for node in ast.walk(tree))
    return sorted(raw), reads, executes

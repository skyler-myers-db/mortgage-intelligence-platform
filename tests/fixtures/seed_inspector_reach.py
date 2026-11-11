"""What an inspector's raw seed text can reach: the export and import half of tests/fixtures/seed_render_scan.py.

An inspector (a test that only reads the seed template's text) may hold it
raw. ``exported_names`` computes every top-level name through which that text
leaves the inspector, following its data flow; ``inspector_reaches`` reports
every place another module takes one of them, imports the inspector some way
the scan cannot read, or names it to a loader. Both are fail-closed.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Callable, Mapping, Set

_SCOPES = (ast.Lambda, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
# Builtins whose answer is a number or a bool: their arguments do not flow out.
_ANSWERS = frozenset({"len", "bool", "isinstance", "issubclass", "hasattr", "callable", "id", "hash", "any", "all"})
# Calls that put a value into the container they are called on.
_MUTATORS = frozenset({"append", "appendleft", "extend", "extendleft", "insert", "add", "update", "setdefault", "__setitem__"})
# What loads code by name. A string naming an inspector is a reach only in a module that spells one:
# a bare name (a builtin, a module, an imported function), or an attribute.
_LOADER_NAMES = frozenset(
    {"__import__", "__builtins__", "builtins", "importlib", "import_module", "runpy", "run_path", "run_module",
     "pkgutil", "resolve_name", "zipimport", "exec", "eval", "compile", "spec_from_file_location",
     "spec_from_loader", "module_from_spec", "SourceFileLoader", "SourcelessFileLoader", "load_source",
     "importorskip", "pytest_plugins"}
)
_LOADER_ATTRS = frozenset(
    {"__import__", "import_module", "run_path", "run_module", "resolve_name", "exec", "eval",
     "spec_from_file_location", "spec_from_loader", "module_from_spec", "exec_module", "load_module",
     "SourceFileLoader", "SourcelessFileLoader", "load_source", "importorskip", "modules"}
)


def parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    return {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}


def parameter_defaults(arguments: ast.arguments) -> list[tuple[str, ast.expr]]:
    """(parameter, default) for each parameter that has one."""

    positional = [*arguments.posonlyargs, *arguments.args]
    pairs = list(zip(positional[len(positional) - len(arguments.defaults) :], arguments.defaults, strict=True))
    pairs += [(arg, default) for arg, default in zip(arguments.kwonlyargs, arguments.kw_defaults, strict=True) if default]
    return [(arg.arg, default) for arg, default in pairs]


def exported_names(tree: ast.Module, values: Set[ast.AST], rendered: Callable[[ast.AST], bool]) -> frozenset[str]:
    """The top-level names through which the raw seed leaves an inspector (``SeedScan.exports``).

    ``values`` are the expressions the scan found holding the seed's path or
    text, and ``rendered`` says whether a name's load is render_seed_sql's
    template. An export is each top-level name a statement holding one binds
    or stores it into (``SQL["seed"] = ...``, ``TEXTS.append(...)``), and each
    function or class that returns or yields it, defaults a parameter to it,
    or stores it into a parameter or a module name (``global`` included),
    through any chain of its own locals. ``*`` is a store the scan cannot name
    (``globals()[...]``): every name then counts.
    """

    exports: set[str] = set()

    def carries(node: ast.AST, tainted: Set[str] = frozenset()) -> bool:
        return any(
            inner in values
            or (
                isinstance(inner, ast.Name)
                and isinstance(inner.ctx, ast.Load)
                and (inner.id in exports or inner.id in tainted)
                and not rendered(inner)
            )
            for inner in _flowing(node)
        )

    while True:
        found: set[str] = set()
        for statement in tree.body:
            if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                tainted = _tainted(statement, carries)
                found |= tainted - _local_names(statement)  # a module name the def stores the raw value into
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
                parameters = {node.arg for node in ast.walk(statement) if isinstance(node, ast.arg)}
                if any(carries(value, tainted) for value in handed) or tainted & parameters:
                    found.add(statement.name)
            elif carries(statement):  # a binding, or a with / for / if whose body binds module names
                found |= _module_names(statement) | (_tainted(statement, carries) - _inner_scope_names(statement))
        if found <= exports:
            return frozenset(exports)
        exports |= found


def _flowing(node: ast.AST) -> list[ast.AST]:
    """``node`` and the parts of it whose value can become its own, or a binding's within it.

    Not a comparison, a comprehension's filter, a conditional's test, an index
    read, nor the argument of a builtin that answers with a number or a bool:
    ``[n for n in nodes if "seed_campaigns" in n]`` holds no seed text.
    """

    found: list[ast.AST] = []
    pending = [node]
    while pending:
        current = pending.pop()
        found.append(current)
        if isinstance(current, ast.Compare) or (
            isinstance(current, ast.Call) and isinstance(current.func, ast.Name) and current.func.id in _ANSWERS
        ):
            continue
        if isinstance(current, ast.comprehension):
            pending += [current.target, current.iter]
        elif isinstance(current, ast.IfExp):
            pending += [current.body, current.orelse]
        elif isinstance(current, ast.Subscript) and isinstance(current.ctx, ast.Load):
            pending.append(current.value)
        else:
            pending.extend(ast.iter_child_nodes(current))
    return found


def _tainted(statement: ast.stmt, carries: Callable[[ast.AST, Set[str]], bool]) -> set[str]:
    """The names ``statement`` binds, or stores into, a value that carries the raw seed (``*``: a dynamic scope).

    A binding taints its targets (an assignment, a loop, a with, a walrus, a
    parameter's default); a store taints the container's base name
    (``SQL["seed"] = v``, ``NS.seed = v``, ``TEXTS.append(v)``,
    ``setattr(NS, "seed", v)``). Names carry once tainted, to a fixed point.
    """

    tainted: set[str] = set()
    while True:
        grew: set[str] = set()
        for node in ast.walk(statement):
            for targets, value in _flows(node):
                if carries(value, tainted):
                    for target in targets:
                        grew |= _stored_names(target)
        if grew <= tainted:
            return tainted
        tainted |= grew


def _flows(node: ast.AST) -> list[tuple[list[ast.AST], ast.AST]]:
    """(targets, value) for each way ``node`` puts a value into a name or a container (a key counts: ``SQL[text] = 1``)."""

    if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store):
        return [([node], node.slice)]
    if isinstance(node, ast.Assign):
        return [(list(node.targets), node.value)]
    if isinstance(node, ast.AnnAssign | ast.AugAssign | ast.NamedExpr) and node.value is not None:
        return [([node.target], node.value)]
    if isinstance(node, ast.For | ast.AsyncFor | ast.comprehension):
        return [([node.target], node.iter)]
    if isinstance(node, ast.withitem) and node.optional_vars is not None:
        return [([node.optional_vars], node.context_expr)]
    if isinstance(node, ast.arguments):
        return [([ast.Name(id=arg, ctx=ast.Store())], default) for arg, default in parameter_defaults(node)]
    if isinstance(node, ast.Call):
        given = [*node.args, *(keyword.value for keyword in node.keywords)]
        if isinstance(node.func, ast.Attribute) and node.func.attr in _MUTATORS:
            return [([node.func.value], value) for value in given]
        if isinstance(node.func, ast.Name) and node.func.id == "setattr" and node.args:
            return [([node.args[0]], value) for value in given[1:]]
    return []


def _stored_names(target: ast.AST) -> set[str]:
    """The names a store into ``target`` changes: a name, or a container's base name (``*`` when it has none)."""

    if isinstance(target, ast.Tuple | ast.List):
        return {name for element in target.elts for name in _stored_names(element)}
    if isinstance(target, ast.Starred):
        return _stored_names(target.value)
    node = target
    while True:
        if isinstance(node, ast.Name):
            return {node.id}
        if isinstance(node, ast.Attribute) and node.attr in {"modules", "__dict__"}:
            return {"*"}  # sys.modules[__name__].X, NS.__dict__["X"]
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {"globals", "vars", "locals"}:
            return {"*"}
        if isinstance(node, ast.Attribute | ast.Subscript):
            node = node.value
        elif isinstance(node, ast.Call):
            node = node.func  # registry().update(...), CACHE.get("k").append(...)
        else:
            return {"*"}


def _local_names(statement: ast.stmt) -> set[str]:
    """The names a def or class binds for itself: its parameters, its locals, its nested defs (not ``global`` ones)."""

    declared = {name for node in ast.walk(statement) if isinstance(node, ast.Global) for name in node.names}
    local: set[str] = set()
    for node in ast.walk(statement):
        if isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load):
            local.add(node.id)
        elif isinstance(node, ast.arg):
            local.add(node.arg)
        elif isinstance(node, ast.alias):
            local.add((node.asname or node.name).split(".")[0])
        elif (
            isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.ExceptHandler | ast.MatchAs | ast.MatchStar)
            and node.name
            and node is not statement
        ):
            local.add(node.name)
    return local - declared


def _inner_scope_names(statement: ast.stmt) -> set[str]:
    """The names a top-level statement binds inside its own functions, classes, lambdas and comprehensions."""

    names: set[str] = set()
    for scope in ast.walk(statement):
        if isinstance(scope, (*_SCOPES, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names |= {node.id for node in ast.walk(scope) if isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load)}
            names |= {node.arg for node in ast.walk(scope) if isinstance(node, ast.arg)}
    return names - {name for node in ast.walk(statement) if isinstance(node, ast.Global) for name in node.names}


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
        if isinstance(node, _SCOPES):
            continue
        pending.extend(ast.iter_child_nodes(node))
    return names


def inspector_reaches(source: str, exports: Mapping[str, frozenset[str]]) -> list[int]:
    """Lines of ``source`` that can reach an inspector's raw seed: ``exports`` maps each inspector's stem to its own.

    A from-import of one of its exports, of ``*`` or of a dunder is a reach
    (of anything, when the inspector stores into a scope the scan cannot
    name); so is any use of the inspector's module object other than reading
    an attribute it does not export. In a module that can load code by name
    (``_LOADER_NAMES`` / ``_LOADER_ATTRS``: importlib, runpy, pkgutil, exec,
    sys.modules, pytest_plugins), so is any string that is the module's dotted
    name or file path, alone or with an export after it
    (``pkg.module:TEMPLATE``). Elsewhere such a string only names a file (a
    meta-test's registry), and text that only mentions the module (a comment,
    prose, an embedded script's comment) never reaches it. What it cannot see:
    a loader in another module handed the name.
    """

    tree = ast.parse(source)
    parent = parents(tree)

    def denied(stem: str, name: str) -> bool:
        return name == "*" or name.startswith("__") or name in exports[stem] or "*" in exports[stem]

    def named(text: str) -> bool:
        for stem in exports:
            match = re.fullmatch(rf"[\w./\\:-]*\b{re.escape(stem)}(?:\.py)?(?:[.:](\w+))?", text)
            if match is not None and (match.group(1) is None or denied(stem, match.group(1))):
                return True
        return False

    loads_code = _loads_code_by_name(tree)

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
        elif loads_code and isinstance(node, ast.Constant) and isinstance(node.value, str) and named(node.value.strip()):
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


def _loads_code_by_name(tree: ast.AST) -> bool:
    """Whether a module spells anything that loads code by a name or a path held in a string."""

    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in _LOADER_NAMES:
            return True
        if isinstance(node, ast.Attribute) and node.attr in _LOADER_ATTRS:
            return True
        if isinstance(node, ast.alias) and {node.name.split(".")[0], node.name.rsplit(".", 1)[-1]} & _LOADER_NAMES:
            return True
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] in _LOADER_NAMES:
            return True
        if isinstance(node, ast.Constant) and node.value in (_LOADER_NAMES | _LOADER_ATTRS) - {"modules"}:
            return True  # getattr(module, "import_module")
    return False

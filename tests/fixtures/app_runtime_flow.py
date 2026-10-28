"""Path-value resolution and binding for the App read scan.

One ModuleFlow per scanned module resolves expressions to path values (Ref)
and binds them, to a fixed point over every module (World), through names,
imports, returns, call arguments, attributes, tuple targets, walrus, ``/=``,
containers and loops. tests/fixtures/app_runtime_reads.py documents the scan.
"""

from __future__ import annotations

import ast
import posixpath
from collections.abc import Iterator
from typing import TypeVar

from tests.fixtures.app_runtime_paths import (
    CWD_CALLS,
    ENV_GETS,
    FOLLOW_METHODS,
    OS_PATH_DIRNAME,
    OS_PATH_JOIN,
    OS_PATH_SAME,
    PATH_CTORS,
    SAME_METHODS,
    STR_CALLS,
    Ref,
    Scope,
    StrValue,
    absolute_module,
    attr_chain,
    bounded,
    const_int,
    glob_prefix,
    join,
    join_ref,
    module_package,
    subtree,
    up,
)

T = TypeVar("T")


class World:
    def __init__(self) -> None:
        self.modules: dict[str, ModuleFlow] = {}  # dotted module name -> module
        self.attrs: dict[str, set[Ref]] = {}
        self.attr_strings: dict[str, set[StrValue]] = {}
        self.attr_loads: set[str] = set()
        self.imported: set[tuple[str, str]] = set()  # (module, name) imported by name
        self.called: set[tuple[str, str]] = set()  # (module, function) called visibly
        self.changed = False

    def grow(self, table: dict[str, set[T]], key: str, values: set[T]) -> None:
        values = {bounded(v) if isinstance(v, Ref) else v for v in values}  # type: ignore[misc]
        bound = table.setdefault(key, set())
        if not values <= bound:
            bound |= values
            self.changed = True

    def split(self, dotted: str) -> tuple[ModuleFlow, str] | None:
        """A first-party ``module.attr`` name, as (module, attr)."""

        head, _, attr = dotted.rpartition(".")
        module = self.modules.get(head)
        return (module, attr) if module is not None and attr else None


class ModuleFlow:
    def __init__(self, world: World, relfile: str, dotted: str, tree: ast.Module) -> None:
        self.world, self.file, self.name, self.tree = world, relfile, dotted, tree
        self.parents: dict[int, ast.AST] = {}
        self.nodes: list[tuple[ast.AST, Scope]] = []
        self.defs: dict[str, list[tuple[ast.FunctionDef | ast.AsyncFunctionDef, Scope]]] = {}
        self.bindings: dict[Scope, dict[str, set[Ref]]] = {}
        self.strings: dict[Scope, dict[str, set[StrValue]]] = {}
        self.returns: dict[str, set[Ref]] = {}
        self.imports: dict[str, str] = {}  # local alias -> canonical dotted name
        self.package = module_package(relfile, dotted)
        # (function scope, name) -> the scope a `global` / `nonlocal` name binds in
        self.rebound: dict[Scope, dict[str, Scope]] = {}
        self.loaded: set[str] = set()
        self.handled_file_nodes: set[int] = set()
        self.cache: dict[int, set[Ref]] = {}
        self._index()

    # -- static index -----------------------------------------------------------------
    def _index(self) -> None:
        stack: list[tuple[ast.AST, Scope]] = [(self.tree, ())]
        while stack:
            node, scope = stack.pop()
            self.nodes.append((node, scope))
            inner = scope
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                inner = (*scope, node.name)
                self.defs.setdefault(node.name, []).append((node, inner))
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    local = alias.asname or alias.name.split(".")[0]
                    self.imports[local] = alias.name if alias.asname else local
            elif isinstance(node, ast.ImportFrom):
                base = absolute_module(self.package, node.level, node.module)
                for alias in node.names if base else ():
                    self.imports[alias.asname or alias.name] = f"{base}.{alias.name}"
            elif isinstance(node, ast.Global | ast.Nonlocal):
                outer = () if isinstance(node, ast.Global) else scope[:-1]
                self.rebound.setdefault(scope, {}).update(dict.fromkeys(node.names, outer))
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                self.loaded.add(node.id)
            elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
                self.world.attr_loads.add(node.attr)
            for child in ast.iter_child_nodes(node):
                self.parents[id(child)] = node
                stack.append((child, inner))

    def _is_import(self, node: ast.Attribute) -> bool:
        head = attr_chain(node)
        return head is not None and head.split(".")[0] in self.imports

    def _first_party(self, node: ast.AST) -> bool:
        """``node`` names something inside a scanned module (``settings.root``)."""

        dotted = self.canon(node)
        if dotted is None:
            return False
        parts = dotted.split(".")
        return any(".".join(parts[:depth]) in self.world.modules for depth in range(1, len(parts)))

    def canon(self, node: ast.AST) -> str | None:
        dotted = attr_chain(node)
        if dotted is None:
            return None
        head, dot, rest = dotted.partition(".")
        full = self.imports.get(head, head)
        return f"{full}{dot}{rest}"

    # -- values -----------------------------------------------------------------------
    def _lookup(self, name: str, scope: Scope) -> set[Ref]:
        for depth in range(len(scope), -1, -1):
            found = self.bindings.get(scope[:depth], {}).get(name)
            if found:
                return found
        target = self.world.split(self.imports.get(name, ""))
        return set(target[0].bindings.get((), {}).get(target[1], set())) if target else set()

    def _lookup_strings(self, name: str, scope: Scope) -> set[StrValue]:
        for depth in range(len(scope), -1, -1):
            found = self.strings.get(scope[:depth], {}).get(name)
            if found:
                return found
        target = self.world.split(self.imports.get(name, ""))
        return set(target[0].strings.get((), {}).get(target[1], set())) if target else set()

    def resolve(self, node: ast.AST, scope: Scope) -> set[Ref]:
        key = id(node)
        if key not in self.cache:
            self.cache[key] = set()  # recursion guard
            self.cache[key] = self._resolve(node, scope)
        return self.cache[key]

    def _resolve(self, node: ast.AST, scope: Scope) -> set[Ref]:  # noqa: C901, PLR0911
        if isinstance(node, ast.Name):
            return self._lookup(node.id, scope)
        if isinstance(node, ast.BoolOp | ast.Tuple | ast.List | ast.Set):
            values = node.values if isinstance(node, ast.BoolOp) else node.elts
            return set().union(*(self.resolve(value, scope) for value in values))
        if isinstance(node, ast.IfExp):
            return self.resolve(node.body, scope) | self.resolve(node.orelse, scope)
        if isinstance(node, ast.NamedExpr | ast.Starred):
            return self.resolve(node.value, scope)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            lefts = self.resolve(node.left, scope)
            return self._join_node(lefts, node.right, scope) if lefts else set()
        if isinstance(node, ast.Attribute):
            receiver = self.resolve(node.value, scope)
            if node.attr == "parent":
                return {Ref(up(ref.path, 1), ref.subtree) for ref in receiver}
            if receiver:
                return set()  # a property of a path (.name, .stem, ...) is not a path
            dotted = self.canon(node)
            target = self.world.split(dotted) if dotted else None
            if target is not None:
                return set(target[0].bindings.get((), {}).get(target[1], set()))
            if self._is_import(node) and not self._first_party(node):
                return set()  # an attribute of a library module or class: sys.path, ...
            return set(self.world.attrs.get(node.attr, set()))
        if isinstance(node, ast.Subscript):
            if isinstance(node.value, ast.Attribute) and node.value.attr == "parents":
                levels = const_int(node.slice)
                if levels is None:
                    return set()
                bases = self.resolve(node.value.value, scope)
                return {Ref(up(ref.path, levels + 1), ref.subtree) for ref in bases}
            return self.resolve(node.value, scope)
        if isinstance(node, ast.Call):
            return self._resolve_call(node, scope)
        return set()

    def _file_ref(self, node: ast.AST) -> set[Ref]:
        if isinstance(node, ast.Name) and node.id == "__file__":
            self.handled_file_nodes.add(id(node))
            return {Ref(self.file)}
        return set()

    def _as_refs(self, node: ast.AST, scope: Scope) -> set[Ref]:
        """A path constructor argument: a path value, ``__file__`` or a cwd string."""

        refs = self._file_ref(node) | self.resolve(node, scope)
        for text, dynamic in self.strings_of(node, scope):
            ref = join_ref(Ref("."), text, dynamic)
            if ref is not None:
                refs.add(ref)
        return refs

    def _join_node(self, bases: set[Ref], segment: ast.AST, scope: Scope) -> set[Ref]:
        replaced = self.resolve(segment, scope)  # joining an absolute path replaces the base
        texts = self.strings_of(segment, scope)
        if not replaced and not texts:
            return subtree(bases)
        joined = {join_ref(ref, text, dyn) for ref in bases for text, dyn in texts}
        return replaced | {ref for ref in joined if ref is not None}

    def _join_args(self, args: list[ast.expr], scope: Scope) -> set[Ref]:
        if not args:
            return {Ref(".")}
        refs = self._as_refs(args[0], scope)
        for segment in args[1:]:
            if not refs:
                break
            refs = self._join_node(refs, segment, scope)
        return refs

    def _resolve_call(self, node: ast.Call, scope: Scope) -> set[Ref]:  # noqa: C901, PLR0911
        name = self.canon(node.func)
        args = node.args
        if name in PATH_CTORS or name == OS_PATH_JOIN:
            return self._join_args(args, scope)
        if name in CWD_CALLS:
            return {Ref(".")}
        if args and name in STR_CALLS:
            return self._file_ref(args[0]) | self.resolve(args[0], scope)
        if args and name in OS_PATH_SAME:
            # abspath(".") or realpath("docs") is a cwd path; "~" is the home folder.
            return {ref for ref in self._as_refs(args[0], scope) if not ref.path.startswith("~")}
        if args and name == OS_PATH_DIRNAME:
            bases = self._file_ref(args[0]) | self.resolve(args[0], scope)
            return {Ref(up(ref.path, 1), ref.subtree) for ref in bases}
        if name in ENV_GETS:
            default = self.env_default(node)
            return self.resolve(default, scope) if default is not None else set()
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in FOLLOW_METHODS:
            bases = self.resolve(func.value, scope)
            if bases:
                return self._path_method(func.attr, bases, args, scope)
        refs: set[Ref] = set()
        for module, function in self._callees(node):
            refs |= module.returns.get(function, set())
        return refs

    def _path_method(self, attr: str, bases: set[Ref], args: list[ast.expr],
                     scope: Scope) -> set[Ref]:
        if attr in SAME_METHODS:
            return bases
        if attr == "joinpath":
            refs = bases
            for segment in args:
                refs = self._join_node(refs, segment, scope)
            return refs
        if attr in ("glob", "rglob"):
            texts = self.strings_of(args[0], scope) if args else set()
            prefixes = {glob_prefix(text) for text, _ in texts} or {""}
            joined = {join_ref(ref, prefix, False) for ref in bases for prefix in prefixes}
            return subtree(ref for ref in joined if ref is not None)
        if attr == "iterdir":
            return subtree(bases)
        # with_name / with_suffix / with_stem: a sibling of the path.
        texts = self.strings_of(args[0], scope) if args else set()
        if attr == "with_name" and texts and not any(dyn for _, dyn in texts):
            return {Ref(join(up(ref.path, 1), text), ref.subtree)
                    for ref in bases for text, _ in texts}
        return subtree(Ref(up(ref.path, 1)) for ref in bases)

    @staticmethod
    def env_default(node: ast.Call) -> ast.expr | None:
        if len(node.args) >= 2:
            return node.args[1]
        return next((kw.value for kw in node.keywords if kw.arg == "default"), None)

    def strings_of(self, node: ast.AST, scope: Scope) -> set[StrValue]:  # noqa: PLR0911
        if isinstance(node, ast.Constant):
            return {(node.value, False)} if isinstance(node.value, str) else set()
        if isinstance(node, ast.JoinedStr):
            head = node.values[0] if node.values else None
            text = head.value if isinstance(head, ast.Constant) and isinstance(head.value, str) else ""
            return {(text, True)}
        if isinstance(node, ast.Name):
            return self._lookup_strings(node.id, scope)
        if isinstance(node, ast.Attribute):
            dotted = self.canon(node)
            target = self.world.split(dotted) if dotted else None
            if target is not None:
                return set(target[0].strings.get((), {}).get(target[1], set()))
            if self._is_import(node) and not self._first_party(node):
                return set()
            return set(self.world.attr_strings.get(node.attr, set()))
        if isinstance(node, ast.BoolOp):
            return set().union(*(self.strings_of(value, scope) for value in node.values))
        if isinstance(node, ast.IfExp):
            return self.strings_of(node.body, scope) | self.strings_of(node.orelse, scope)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            # Only literal + literal folds; anything else keeps the left head and
            # turns dynamic, so a self-referencing `s = s + x` cannot grow forever.
            if isinstance(node.left, ast.Constant) and isinstance(node.right, ast.Constant):
                left, right = node.left.value, node.right.value
                if isinstance(left, str) and isinstance(right, str):
                    return {(left + right, False)}
            return {(text, True) for text, _ in self.strings_of(node.left, scope)}
        if isinstance(node, ast.Call):
            name = self.canon(node.func)
            if name in ENV_GETS:
                default = self.env_default(node)
                return self.strings_of(default, scope) if default is not None else set()
            if name == OS_PATH_JOIN and node.args:
                literals = [arg.value for arg in node.args
                            if isinstance(arg, ast.Constant) and isinstance(arg.value, str)]
                if len(literals) == len(node.args):
                    return {(posixpath.join(*literals), False)}
                return {(text, True) for text, _ in self.strings_of(node.args[0], scope)}
        return set()

    # -- calls into first-party functions ---------------------------------------------
    def _callees(self, call: ast.Call) -> Iterator[tuple[ModuleFlow, str]]:
        func = call.func
        if isinstance(func, ast.Name):
            if func.id in self.defs:
                yield self, func.id
            target = self.world.split(self.imports.get(func.id, ""))
            if target is not None and target[1] in target[0].defs:
                yield target
        elif isinstance(func, ast.Attribute):
            if isinstance(func.value, ast.Name) and func.value.id in ("self", "cls"):
                if func.attr in self.defs:
                    yield self, func.attr
                return
            dotted = self.canon(func)
            target = self.world.split(dotted) if dotted else None
            if target is not None and target[1] in target[0].defs:
                yield target

    def param_targets(
        self, call: ast.Call, arg: ast.AST
    ) -> list[tuple[ModuleFlow, Scope, str]] | None:
        """Where ``arg`` of ``call`` lands in each first-party callee; None: unknown."""

        targets: list[tuple[ModuleFlow, Scope, str]] = []
        keyword = next((kw.arg for kw in call.keywords if kw.value is arg), "")
        position = next((i for i, value in enumerate(call.args) if value is arg), None)
        for module, function in self._callees(call):
            for definition, inner in module.defs[function]:
                params = [a.arg for a in (*definition.args.posonlyargs, *definition.args.args)]
                if params[:1] in (["self"], ["cls"]) and isinstance(call.func, ast.Attribute):
                    params = params[1:]
                if position is not None:
                    vararg = definition.args.vararg
                    name = params[position] if position < len(params) else (
                        vararg.arg if vararg else None)
                elif keyword:
                    names = {*params, *(a.arg for a in definition.args.kwonlyargs)}
                    kwarg = definition.args.kwarg
                    name = keyword if keyword in names else (kwarg.arg if kwarg else None)
                else:
                    name = None
                if name is None:
                    return None
                targets.append((module, inner, name))
        return targets or None

    def mark_calls(self) -> None:
        """Record every first-party function some scanned call reaches by name."""

        for node, _ in self.nodes:
            if isinstance(node, ast.Call):
                self.world.called.update((module.name, fn) for module, fn in self._callees(node))

    # -- binding rounds ---------------------------------------------------------------
    def _bind(self, target: ast.AST, scope: Scope, refs: set[Ref],
              strings: set[StrValue], *, in_class: bool = False) -> None:
        world = self.world
        if isinstance(target, ast.Name):
            scope = self.rebound.get(scope, {}).get(target.id, scope)
            if refs:
                world.grow(self.bindings.setdefault(scope, {}), target.id, refs)
            if strings:
                world.grow(self.strings.setdefault(scope, {}), target.id, strings)
            if in_class:
                self._bind_attr(target.id, refs, strings)
        elif isinstance(target, ast.Attribute):
            self._bind_attr(target.attr, refs, strings)
        elif isinstance(target, ast.Tuple | ast.List):
            for element in target.elts:
                self._bind(element, scope, refs, strings, in_class=in_class)

    def _bind_attr(self, attr: str, refs: set[Ref], strings: set[StrValue]) -> None:
        if refs:
            self.world.grow(self.world.attrs, attr, refs)
        if strings:
            self.world.grow(self.world.attr_strings, attr, strings)

    def _bind_assign(self, targets: list[ast.expr], value: ast.expr, scope: Scope,
                     in_class: bool) -> None:
        for target in targets:
            pairs = (
                zip(target.elts, value.elts, strict=True)
                if isinstance(target, ast.Tuple | ast.List)
                and isinstance(value, ast.Tuple | ast.List)
                and len(target.elts) == len(value.elts)
                and not any(isinstance(e, ast.Starred) for e in (*target.elts, *value.elts))
                else [(target, value)]
            )
            for sub_target, sub_value in pairs:
                self._bind(sub_target, scope, self.resolve(sub_value, scope),
                           self.strings_of(sub_value, scope), in_class=in_class)

    def bind_round(self) -> None:  # noqa: C901
        self.cache.clear()
        for node, scope in self.nodes:
            in_class = isinstance(self.parents.get(id(node)), ast.ClassDef)
            if isinstance(node, ast.Assign):
                self._bind_assign(node.targets, node.value, scope, in_class)
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                self._bind_assign([node.target], node.value, scope, in_class)
            elif isinstance(node, ast.AugAssign) and isinstance(node.op, ast.Div):
                bases = self.resolve(node.target, scope)
                if bases:
                    self._bind(node.target, scope, self._join_node(bases, node.value, scope),
                               set(), in_class=in_class)
            elif isinstance(node, ast.NamedExpr):
                self._bind(node.target, scope, self.resolve(node.value, scope),
                           self.strings_of(node.value, scope))
            elif isinstance(node, ast.For | ast.AsyncFor | ast.comprehension):
                self._bind(node.target, scope, self.resolve(node.iter, scope), set())
            elif isinstance(node, ast.Return) and node.value is not None and scope:
                self.world.grow(self.returns, scope[-1], self.resolve(node.value, scope))
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                self._bind_defaults(node, (*scope, node.name), scope)
            elif isinstance(node, ast.Call):
                self._bind_call(node, scope)
            elif isinstance(node, ast.ImportFrom):
                base = absolute_module(self.package, node.level, node.module)
                if base:
                    self.world.imported.update((base, alias.name) for alias in node.names)

    def _bind_defaults(self, node: ast.FunctionDef | ast.AsyncFunctionDef, inner: Scope,
                       outer: Scope) -> None:
        positional = [*node.args.posonlyargs, *node.args.args]
        pairs = list(zip(positional[len(positional) - len(node.args.defaults):],
                         node.args.defaults, strict=True))
        pairs += [(arg, default) for arg, default
                  in zip(node.args.kwonlyargs, node.args.kw_defaults, strict=True) if default]
        for arg, default in pairs:
            self._bind(ast.Name(arg.arg), inner, self.resolve(default, outer),
                       self.strings_of(default, outer))

    def _bind_call(self, call: ast.Call, scope: Scope) -> None:
        for arg in (*call.args, *(kw.value for kw in call.keywords)):
            value = arg.value if isinstance(arg, ast.Starred) else arg
            refs = self.resolve(value, scope)
            strings = self.strings_of(value, scope)
            if not refs and not strings:
                continue
            for module, inner, param in self.param_targets(call, arg) or []:
                if refs:
                    self.world.grow(module.bindings.setdefault(inner, {}), param, refs)
                if strings:
                    self.world.grow(module.strings.setdefault(inner, {}), param, strings)

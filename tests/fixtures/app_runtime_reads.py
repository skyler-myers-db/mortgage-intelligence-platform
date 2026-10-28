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

A path value bound to a name nothing loads counts as read where it is bound.

Known residuals, each a shape no backend module uses today: a first-party
callee the scan cannot name (a method reached through an object, a function
passed as a value) is assumed to read only under the path it receives, so a
callee that walks up from it is not seen; a string that reaches a path
constructor only through a function parameter is not followed (the literal
sweep is the net for those); ``getattr``, ``exec`` and ``importlib`` with a
computed name are invisible.
"""

from __future__ import annotations

import ast
import posixpath
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, TypeVar

T = TypeVar("T")
Verdict = Literal["follow", "sink", "subtree", "unhandled"]
Scope = tuple[str, ...]
StrValue = tuple[str, bool]  # (text or constant head, has a non-constant tail)

_PATH_CTORS = frozenset(
    f"pathlib.{name}" for name in ("Path", "PurePath", "PosixPath", "PurePosixPath")
)
_STR_CALLS = frozenset({"str", "os.fspath"})
_OPEN_CALLS = frozenset({"open", "io.open", "builtins.open", "codecs.open"})
_DIR_CALLS = frozenset({"os.walk", "os.listdir", "os.scandir", "glob.glob", "glob.iglob",
                        "shutil.copytree"})
_ENV_GETS = frozenset({"os.environ.get", "os.getenv", "os.environ.setdefault"})
_CWD_CALLS = frozenset({"pathlib.Path.cwd", "os.getcwd"})
_OS_PATH_SAME = frozenset({"os.path.abspath", "os.path.realpath", "os.path.normpath",
                           "os.path.expanduser"})
_OS_PATH_DIRNAME = "os.path.dirname"
_OS_PATH_JOIN = "os.path.join"
_SAME_METHODS = frozenset({"resolve", "absolute", "expanduser", "as_posix", "__fspath__"})
_FOLLOW_METHODS = _SAME_METHODS | {"with_name", "with_suffix", "with_stem", "joinpath",
                                   "glob", "rglob", "iterdir"}
_SINK_ATTRS = frozenset({
    "read_text", "read_bytes", "open", "exists", "is_file", "is_dir", "is_symlink", "stat",
    "lstat", "samefile", "readlink", "owner", "group", "write_text", "write_bytes", "touch",
    "mkdir", "unlink", "rmdir", "chmod", "name", "stem", "suffix", "suffixes", "parts",
    "anchor", "drive", "root", "is_absolute", "is_relative_to", "relative_to", "match",
    "as_uri",
})
_SUBTREE_ATTRS = frozenset({"walk"})
_GLOB_CHARS = frozenset("*?[")


@dataclass(frozen=True)
class Ref:
    """A repo-relative POSIX path (``.`` is the root); ``subtree`` adds ``/**``."""

    path: str
    subtree: bool = False

    def key(self) -> str:
        return f"{self.path}/**" if self.subtree else self.path


@dataclass
class ScanResult:
    references: dict[str, set[str]] = field(default_factory=dict)  # Ref.key() -> origins
    unhandled_file_uses: list[str] = field(default_factory=list)
    scanned: list[str] = field(default_factory=list)
    trees: dict[str, ast.Module] = field(default_factory=dict)  # relfile -> parsed module

    def add(self, key: str, origin: str) -> None:
        self.references.setdefault(key, set()).add(origin)


MAX_DEPTH = 12  # a deeper path, or one more than two levels above the root, is folded


def _norm(path: str) -> str:
    return posixpath.normpath(path or ".")


def _bounded(ref: Ref) -> Ref:
    """Keep the path lattice finite, so ``p = p.parent`` in a loop reaches a fixed point."""

    parts = ref.path.split("/")
    if parts[:3] == ["..", "..", ".."]:
        return Ref("../..", True)
    if len(parts) > MAX_DEPTH:
        return Ref("/".join(parts[:MAX_DEPTH]), True)
    return ref


def _join(base: str, *segments: str) -> str:
    return _norm(posixpath.join(base, *segments))


def _up(path: str, levels: int) -> str:
    """``levels`` directories up; above the repo root stays visible as ``..``."""

    for _ in range(levels):
        path = f"{path}/.." if posixpath.basename(path) in ("", ".", "..") else posixpath.dirname(path)
        path = _norm(path)
    return path


def _join_ref(ref: Ref, text: str, dynamic: bool) -> Ref | None:
    if text.startswith("/"):
        return None
    if ref.subtree:
        ups = text.split("/").count("..")
        return Ref(_up(ref.path, ups) if ups else ref.path, True)
    if dynamic:
        return Ref(_join(ref.path, posixpath.dirname(text)), True)
    return Ref(_join(ref.path, text))


def _subtree(refs: Iterable[Ref]) -> set[Ref]:
    return {Ref(ref.path, True) for ref in refs}


def _const_int(node: ast.AST) -> int | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, int):
        return node.value
    return None


def _dotted(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        head = _dotted(node.value)
        return f"{head}.{node.attr}" if head else None
    return None


def _glob_prefix(pattern: str) -> str:
    parts: list[str] = []
    for part in pattern.split("/"):
        if _GLOB_CHARS & set(part):
            break
        parts.append(part)
    return "/".join(parts)


class _World:
    def __init__(self) -> None:
        self.modules: dict[str, _Module] = {}  # dotted module name -> module
        self.attrs: dict[str, set[Ref]] = {}
        self.attr_strings: dict[str, set[StrValue]] = {}
        self.attr_loads: set[str] = set()
        self.imported: set[tuple[str, str]] = set()  # (module, name) imported by name
        self.called: set[tuple[str, str]] = set()  # (module, function) called visibly
        self.changed = False

    def grow(self, table: dict[str, set[T]], key: str, values: set[T]) -> None:
        values = {_bounded(v) if isinstance(v, Ref) else v for v in values}  # type: ignore[misc]
        bound = table.setdefault(key, set())
        if not values <= bound:
            bound |= values
            self.changed = True

    def split(self, dotted: str) -> tuple[_Module, str] | None:
        """A first-party ``module.attr`` name, as (module, attr)."""

        head, _, attr = dotted.rpartition(".")
        module = self.modules.get(head)
        return (module, attr) if module is not None and attr else None


class _Module:
    def __init__(self, world: _World, relfile: str, dotted: str, tree: ast.Module) -> None:
        self.world, self.file, self.name, self.tree = world, relfile, dotted, tree
        self.parents: dict[int, ast.AST] = {}
        self.nodes: list[tuple[ast.AST, Scope]] = []
        self.defs: dict[str, list[tuple[ast.FunctionDef | ast.AsyncFunctionDef, Scope]]] = {}
        self.bindings: dict[Scope, dict[str, set[Ref]]] = {}
        self.strings: dict[Scope, dict[str, set[StrValue]]] = {}
        self.returns: dict[str, set[Ref]] = {}
        self.imports: dict[str, str] = {}  # local alias -> canonical dotted name
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
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                for alias in node.names:
                    self.imports[alias.asname or alias.name] = f"{node.module}.{alias.name}"
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                self.loaded.add(node.id)
            elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
                self.world.attr_loads.add(node.attr)
            for child in ast.iter_child_nodes(node):
                self.parents[id(child)] = node
                stack.append((child, inner))

    def _is_import(self, node: ast.Attribute) -> bool:
        head = _dotted(node)
        return head is not None and head.split(".")[0] in self.imports

    def canon(self, node: ast.AST) -> str | None:
        dotted = _dotted(node)
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
                return {Ref(_up(ref.path, 1), ref.subtree) for ref in receiver}
            if receiver:
                return set()  # a property of a path (.name, .stem, ...) is not a path
            dotted = self.canon(node)
            target = self.world.split(dotted) if dotted else None
            if target is not None:
                return set(target[0].bindings.get((), {}).get(target[1], set()))
            if self._is_import(node):
                return set()  # an attribute of an imported module or class: sys.path, ...
            return set(self.world.attrs.get(node.attr, set()))
        if isinstance(node, ast.Subscript):
            if isinstance(node.value, ast.Attribute) and node.value.attr == "parents":
                levels = _const_int(node.slice)
                if levels is None:
                    return set()
                bases = self.resolve(node.value.value, scope)
                return {Ref(_up(ref.path, levels + 1), ref.subtree) for ref in bases}
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
            ref = _join_ref(Ref("."), text, dynamic)
            if ref is not None:
                refs.add(ref)
        return refs

    def _join_node(self, bases: set[Ref], segment: ast.AST, scope: Scope) -> set[Ref]:
        replaced = self.resolve(segment, scope)  # joining an absolute path replaces the base
        texts = self.strings_of(segment, scope)
        if not replaced and not texts:
            return _subtree(bases)
        joined = {_join_ref(ref, text, dyn) for ref in bases for text, dyn in texts}
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
        if name in _PATH_CTORS or name == _OS_PATH_JOIN:
            return self._join_args(args, scope)
        if name in _CWD_CALLS:
            return {Ref(".")}
        if args and (name in _STR_CALLS or name in _OS_PATH_SAME):
            return self._file_ref(args[0]) | self.resolve(args[0], scope)
        if args and name == _OS_PATH_DIRNAME:
            bases = self._file_ref(args[0]) | self.resolve(args[0], scope)
            return {Ref(_up(ref.path, 1), ref.subtree) for ref in bases}
        if name in _ENV_GETS:
            default = self._env_default(node)
            return self.resolve(default, scope) if default is not None else set()
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in _FOLLOW_METHODS:
            bases = self.resolve(func.value, scope)
            if bases:
                return self._path_method(func.attr, bases, args, scope)
        refs: set[Ref] = set()
        for module, function in self._callees(node):
            refs |= module.returns.get(function, set())
        return refs

    def _path_method(self, attr: str, bases: set[Ref], args: list[ast.expr],
                     scope: Scope) -> set[Ref]:
        if attr in _SAME_METHODS:
            return bases
        if attr == "joinpath":
            refs = bases
            for segment in args:
                refs = self._join_node(refs, segment, scope)
            return refs
        if attr in ("glob", "rglob"):
            texts = self.strings_of(args[0], scope) if args else set()
            prefixes = {_glob_prefix(text) for text, _ in texts} or {""}
            joined = {_join_ref(ref, prefix, False) for ref in bases for prefix in prefixes}
            return _subtree(ref for ref in joined if ref is not None)
        if attr == "iterdir":
            return _subtree(bases)
        # with_name / with_suffix / with_stem: a sibling of the path.
        texts = self.strings_of(args[0], scope) if args else set()
        if attr == "with_name" and texts and not any(dyn for _, dyn in texts):
            return {Ref(_join(_up(ref.path, 1), text), ref.subtree)
                    for ref in bases for text, _ in texts}
        return _subtree(Ref(_up(ref.path, 1)) for ref in bases)

    @staticmethod
    def _env_default(node: ast.Call) -> ast.expr | None:
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
            if self._is_import(node):
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
            if name in _ENV_GETS:
                default = self._env_default(node)
                return self.strings_of(default, scope) if default is not None else set()
            if name == _OS_PATH_JOIN and node.args:
                literals = [arg.value for arg in node.args
                            if isinstance(arg, ast.Constant) and isinstance(arg.value, str)]
                if len(literals) == len(node.args):
                    return {(posixpath.join(*literals), False)}
                return {(text, True) for text, _ in self.strings_of(node.args[0], scope)}
        return set()

    # -- calls into first-party functions ---------------------------------------------
    def _callees(self, call: ast.Call) -> Iterator[tuple[_Module, str]]:
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
    ) -> list[tuple[_Module, Scope, str]] | None:
        """Where ``arg`` of ``call`` lands in each first-party callee; None: unknown."""

        targets: list[tuple[_Module, Scope, str]] = []
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
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                self.world.imported.update((node.module, alias.name) for alias in node.names)

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
            if not refs:
                continue
            for module, inner, param in self.param_targets(call, arg) or []:
                self.world.grow(module.bindings.setdefault(inner, {}), param, refs)

    # -- consumers --------------------------------------------------------------------
    def consumer(self, node: ast.AST, scope: Scope) -> Verdict:  # noqa: C901, PLR0911, PLR0912
        parent = self.parents.get(id(node))
        if isinstance(parent, ast.Attribute):
            return self._attribute_consumer(parent)
        if isinstance(parent, ast.Subscript):
            return "follow" if parent.value is node else "sink"
        if isinstance(parent, ast.BinOp):
            return "follow" if isinstance(parent.op, ast.Div) and parent.left is node else "subtree"
        if isinstance(parent, ast.Compare | ast.UnaryOp):
            return "sink"
        if isinstance(parent, ast.BoolOp | ast.NamedExpr | ast.Tuple | ast.List | ast.Set):
            return "follow"
        if isinstance(parent, ast.IfExp):
            return "sink" if parent.test is node else "follow"
        if isinstance(parent, ast.keyword):
            call = self.parents.get(id(parent))
            if isinstance(call, ast.Call):
                return self._call_consumer(call, node, scope)
            return "unhandled"
        if isinstance(parent, ast.Call):
            return "unhandled" if parent.func is node else self._call_consumer(parent, node, scope)
        if isinstance(parent, ast.Assign | ast.AnnAssign):
            targets = parent.targets if isinstance(parent, ast.Assign) else [parent.target]
            return self._binding_consumer(targets)
        if isinstance(parent, ast.Return):
            called = scope and (self.name, scope[-1]) in self.world.called
            return "follow" if called else "subtree"
        if isinstance(parent, ast.For | ast.AsyncFor | ast.comprehension):
            if parent.iter is node:
                return self._binding_consumer([parent.target])
            return "sink"
        if isinstance(parent, ast.arguments):
            owner = self.parents.get(id(parent))
            return "subtree" if isinstance(owner, ast.Lambda) else "follow"
        if isinstance(parent, ast.Expr | ast.If | ast.While | ast.Assert | ast.Raise):
            return "sink"
        if isinstance(parent, ast.AugAssign):
            # p /= q rebinds p (bind_round); _classify reports any other operator.
            return "follow" if isinstance(parent.op, ast.Div) else "unhandled"
        if parent is None or isinstance(parent, ast.stmt):
            return "sink"
        return "subtree"  # f-string, dict, lambda, yield, await, starred, with-item, ...

    def _attribute_consumer(self, parent: ast.Attribute) -> Verdict:
        grand = self.parents.get(id(parent))
        called = isinstance(grand, ast.Call) and grand.func is parent
        if parent.attr == "parent":
            return "follow"
        if parent.attr == "parents":
            constant = isinstance(grand, ast.Subscript) and _const_int(grand.slice) is not None
            return "follow" if constant else "unhandled"
        if parent.attr in _FOLLOW_METHODS and called:
            return "follow"
        if parent.attr in _SINK_ATTRS:
            return "sink"
        return "subtree" if parent.attr in _SUBTREE_ATTRS else "unhandled"

    def _call_consumer(self, call: ast.Call, arg: ast.AST, scope: Scope) -> Verdict:  # noqa: PLR0911
        name = self.canon(call.func)
        if name in _PATH_CTORS or name in _STR_CALLS or name in _OS_PATH_SAME:
            return "follow"
        if name in (_OS_PATH_JOIN, _OS_PATH_DIRNAME):
            return "follow"
        if name in _ENV_GETS:
            return "follow" if self._env_default(call) is arg else "sink"
        if name in _OPEN_CALLS:
            return "sink"
        if name in _DIR_CALLS:
            return "subtree"
        if isinstance(call.func, ast.Attribute) and self.resolve(call.func.value, scope):
            # joinpath(path) yields that path; relative_to, samefile, ... read nothing.
            return "follow" if call.func.attr == "joinpath" else "sink"
        return "follow" if self.param_targets(call, arg) else "subtree"

    def _binding_consumer(self, targets: list[ast.expr]) -> Verdict:
        names: list[ast.expr] = []
        for target in targets:
            names.extend(target.elts if isinstance(target, ast.Tuple | ast.List) else [target])
        if not all(isinstance(t, ast.Name | ast.Attribute) for t in names):
            return "subtree"  # stored into a subscript or a starred target
        world = self.world
        used = all(
            t.id in self.loaded or (self.name, t.id) in world.imported or t.id in world.attr_loads
            if isinstance(t, ast.Name) else t.attr in world.attr_loads
            for t in names
        )
        return "follow" if used else "sink"


def _module_file(root: Path, module: str) -> str | None:
    base = module.replace(".", "/")
    for candidate in (f"{base}.py", f"{base}/__init__.py"):
        if (root / candidate).is_file():
            return candidate
    return base if (root / base).is_dir() else None


def _dotted_name(relfile: str) -> str:
    return relfile.removesuffix(".py").removesuffix("/__init__").replace("/", ".")


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
            if callee == "import_module" and isinstance(literal, str):
                yield literal, node.lineno


MAX_ROUNDS = 32


def scan(root: Path, package: str = "backend") -> ScanResult:
    """References from ``root/package`` and the first-party modules it imports."""

    result = ScanResult()
    world = _World()
    top_levels = {p.name for p in root.iterdir() if p.is_dir()} - {package}
    queue = sorted(p.relative_to(root).as_posix() for p in (root / package).rglob("*.py"))
    seen = set(queue)
    while queue:
        relfile = queue.pop(0)
        tree = ast.parse((root / relfile).read_text(encoding="utf-8"), filename=relfile)
        result.scanned.append(relfile)
        result.trees[relfile] = tree
        world.modules[_dotted_name(relfile)] = _Module(world, relfile, _dotted_name(relfile), tree)
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


def _classify(module: _Module, result: ScanResult) -> None:
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
        verdict = module.consumer(node, scope)
        if verdict == "follow":
            continue
        origin = f"{module.file}:{getattr(node, 'lineno', 0)}"
        if verdict == "unhandled":
            parent = module.parents.get(id(node))
            result.unhandled_file_uses.append(f"{origin} ({type(parent).__name__})")
        for ref in refs if verdict == "sink" else _subtree(refs):
            if not ref.path.startswith("/"):
                result.add(ref.key(), origin)
    result.unhandled_file_uses.extend(
        f"{module.file}:{node.lineno} (__file__)"
        for node, _ in module.nodes
        if isinstance(node, ast.Name) and node.id == "__file__"
        and id(node) not in module.handled_file_nodes
    )


def _record_string_read(module: _Module, call: ast.Call, scope: Scope, result: ScanResult) -> None:
    """``open("x")`` or ``os.listdir("x")``: a cwd-relative string read, not a path value."""

    name = module.canon(call.func)
    if name not in _OPEN_CALLS and name not in _DIR_CALLS:
        return
    origin = f"{module.file}:{call.lineno}"
    for text, dynamic in module.strings_of(call.args[0], scope):
        if name in ("glob.glob", "glob.iglob"):
            text, dynamic = _glob_prefix(text), True
        ref = _join_ref(Ref("."), text, dynamic)
        if ref is not None:
            result.add((ref if name in _OPEN_CALLS else Ref(ref.path, True)).key(), origin)


def outside_reads(result: ScanResult, package: str = "backend") -> dict[str, set[str]]:
    """References outside ``package``; an exact read of the repo root itself drops out."""

    return {
        key: origins
        for key, origins in result.references.items()
        if key != "." and key != package and not key.startswith(f"{package}/")
    }

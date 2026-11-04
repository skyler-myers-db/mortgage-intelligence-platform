"""The static control behind an elevated App exposure: one Genie client, one space.

An App entry of ``privilege_class: elevated`` accepts that the App's service
principal can reach another team's Genie space with more than view or run (the
default ``users`` ``CAN_MANAGE`` on ``/Workspace/Shared``, which the no-revoke
ruling leaves in place). What keeps that reach unused is the backend's shape:
every Genie API call goes through ``backend/services/genie_client.py``, whose
URLs all address ``self._space_id``, and the only ``GenieClient`` is built by
``backend/services/genie_client_registry.py`` from the pinned governed space id.
This module checks that shape on the backend's source with ``ast``, so the
accepted-exposure policy can refuse an elevated entry the moment it stops
holding (tests/unit/test_genie_single_space_ast.py pins it on this tree):

1. A Genie API path (``api/2.0/genie`` or ``genie/spaces``) appears in a string
   only in the client module, and there only in a plain method of
   ``GenieClient`` (no nested function, no static or class method), as
   ``.../api/2.0/genie/spaces/{self._space_id}``.
2. ``_space_id`` is named nowhere in the backend but as ``self._space_id`` read
   in a ``GenieClient`` method, and assigned once, ``self._space_id =
   space_id``, in ``GenieClient.__init__``. No other class can carry a space
   id into a Genie URL, and ``self`` is never rebound in the client module.
3. ``GenieClient`` is one top-level class of the client module. Outside its
   annotations it is named only by the registry, imported there under its own
   name, and called there with ``space_id=<name>`` bound once, by
   ``<name> = _resolve_space_id()``. No alias, subclass, attribute access or
   other reference reaches it anywhere else.
4. No backend module reaches the SDK's Genie API (an attribute ``.genie``).

It is a closed shape over names, not a sandbox: code assembling a Genie path
or an identifier from fragments at run time is out of its reach, and review
owns that.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
CLIENT_MODULE = "backend/services/genie_client.py"
REGISTRY_MODULE = "backend/services/genie_client_registry.py"
CLIENT_CLASS = "GenieClient"
_GENIE_PATHS = ("api/2.0/genie", "genie/spaces")
_SPACE_PREFIX = "/api/2.0/genie/spaces/"
_SPACE_ATTRIBUTE = "_space_id"
_RESOLVER = "_resolve_space_id"
# A star import from either module binds GenieClient without naming it.
_CLIENT_MODULES = frozenset({"backend.services.genie_client", "backend.services.genie_client_registry"})
_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)


def _strings(tree: ast.AST) -> Iterator[tuple[int, ast.AST, str]]:
    """Every string literal and f-string once, with its constant text."""

    parts = {id(part) for node in ast.walk(tree) if isinstance(node, ast.JoinedStr) for part in node.values}
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            text = "".join(
                part.value for part in node.values if isinstance(part, ast.Constant) and isinstance(part.value, str)
            )
            yield node.lineno, node, text
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in parts:
            yield node.lineno, node, node.value


def _parents(tree: ast.AST) -> dict[int, ast.AST]:
    return {id(child): node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}


def _scope(node: ast.AST, parents: dict[int, ast.AST]) -> ast.AST | None:
    """The nearest function, lambda or class around ``node``."""

    current = parents.get(id(node))
    while current is not None and not isinstance(current, _SCOPES):
        current = parents.get(id(current))
    return current


def _client_class(tree: ast.Module) -> ast.ClassDef | None:
    found = [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == CLIENT_CLASS]
    return found[0] if len(found) == 1 else None


def _is_method(function: ast.AST | None, client: ast.ClassDef | None, parents: dict[int, ast.AST]) -> bool:
    """A plain method defined directly in the client class's body."""

    return (
        client is not None
        and isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef)
        and parents.get(id(function)) is client
        and not any(
            isinstance(decorator, ast.Name) and decorator.id in {"staticmethod", "classmethod"}
            for decorator in function.decorator_list
        )
    )


def _is_self_space_id(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == _SPACE_ATTRIBUTE
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
    )


def _addresses_own_space(node: ast.JoinedStr) -> bool:
    """``.../api/2.0/genie/spaces/`` directly followed by ``{self._space_id}``."""

    parts = node.values
    for index, part in enumerate(parts[:-1]):
        if isinstance(part, ast.Constant) and str(part.value).endswith(_SPACE_PREFIX):
            following = parts[index + 1]
            return isinstance(following, ast.FormattedValue) and _is_self_space_id(following.value)
    return False


def _is_pinned_assignment(node: ast.AST, client: ast.ClassDef | None, parents: dict[int, ast.AST]) -> bool:
    """``self._space_id = space_id`` as a statement of ``GenieClient.__init__``'s own body."""

    init = parents.get(id(node))
    return (
        isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and _is_self_space_id(node.targets[0])
        and isinstance(node.value, ast.Name)
        and node.value.id == "space_id"
        and isinstance(init, ast.FunctionDef)
        and init.name == "__init__"
        and node in init.body
        and "space_id" in {arg.arg for arg in init.args.args + init.args.kwonlyargs}
        and not any(
            isinstance(inner, ast.Name) and inner.id == "space_id" and not isinstance(inner.ctx, ast.Load)
            for inner in ast.walk(init)
        )
        and _is_method(init, client, parents)
    )


def _identifier(node: ast.AST) -> object:
    """The identifier a node binds, reads or names, or the value of a constant."""

    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.keyword | ast.arg):
        return node.arg
    if isinstance(node, ast.alias):
        return node.asname or node.name
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
        return node.name
    if isinstance(node, ast.Constant):
        return node.value
    return None


def _space_id_mentions(tree: ast.AST) -> Iterator[ast.AST]:
    """Every node that names the ``_space_id`` identifier, in any role."""

    return (node for node in ast.walk(tree) if _identifier(node) == _SPACE_ATTRIBUTE)


def _space_id_violations(tree: ast.Module, *, path: str) -> list[str]:
    client = _client_class(tree) if path == CLIENT_MODULE else None
    parents = _parents(tree)
    found: list[str] = []
    pinned = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign) and _is_pinned_assignment(node, client, parents)
    ]
    pinned_targets = {id(node.targets[0]) for node in pinned}
    for node in _space_id_mentions(tree):
        if id(node) in pinned_targets and len(pinned) == 1:
            continue
        if (
            _is_self_space_id(node)
            and isinstance(node.ctx, ast.Load)  # type: ignore[attr-defined]
            and _is_method(_scope(node, parents), client, parents)
        ):
            continue
        found.append(
            f"{path}:{getattr(node, 'lineno', 0)}: {_SPACE_ATTRIBUTE} is named outside a "
            f"{CLIENT_CLASS} method, or assigned other than once in {CLIENT_CLASS}.__init__"
        )
    if path == CLIENT_MODULE and len(pinned) != 1:
        found.append(
            f"{path}: {CLIENT_CLASS}.__init__ must assign self.{_SPACE_ATTRIBUTE} = space_id exactly once"
        )
    return found


def _client_violations(tree: ast.Module, *, path: str) -> list[str]:
    found: list[str] = []
    client = _client_class(tree)
    if client is None:
        found.append(f"{path}: {CLIENT_CLASS} must be exactly one top-level class")
    parents = _parents(tree)
    for lineno, node, text in _strings(tree):
        if not any(marker in text for marker in _GENIE_PATHS):
            continue
        if not isinstance(node, ast.JoinedStr) or not _addresses_own_space(node):
            found.append(f"{path}:{lineno}: a Genie API path that does not address self._space_id")
        elif not _is_method(_scope(node, parents), client, parents):
            found.append(f"{path}:{lineno}: a Genie API path outside a plain {CLIENT_CLASS} method")
    found.extend(
        f"{path}:{node.lineno}: self is rebound in the client module"
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and node.id == "self" and not isinstance(node.ctx, ast.Load)
    )
    return found


def _resolved_name(function: ast.AST, name: str) -> bool:
    """``name`` is bound once in ``function``, by ``name = _resolve_space_id()``."""

    bindings = [
        node
        for node in ast.walk(function)
        if (isinstance(node, ast.Name) and node.id == name and not isinstance(node.ctx, ast.Load))
        or (isinstance(node, ast.arg) and node.arg == name)
    ]
    resolved = [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == name
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == _RESOLVER
        and not node.value.args
        and not node.value.keywords
    ]
    return len(bindings) == 1 and len(resolved) == 1


def _annotation_ids(tree: ast.AST) -> set[int]:
    """Every node inside an annotation, which names a type and never calls it."""

    roots: list[ast.AST] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.arg) and node.annotation is not None:
            roots.append(node.annotation)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.returns is not None:
            roots.append(node.returns)
        elif isinstance(node, ast.AnnAssign):
            roots.append(node.annotation)
    return {id(inner) for root in roots for inner in ast.walk(root)}


def _governed_call(node: ast.Call, *, path: str, parents: dict[int, ast.AST]) -> str | None:
    """Why a registry ``GenieClient(...)`` call is not the governed one, or ``None``."""

    owner = _scope(node, parents)
    space = [keyword.value for keyword in node.keywords if keyword.arg == "space_id"]
    if (
        not isinstance(owner, ast.FunctionDef | ast.AsyncFunctionDef)
        or node.args
        or any(keyword.arg is None for keyword in node.keywords)
        or len(space) != 1
        or not isinstance(space[0], ast.Name)
        or not _resolved_name(owner, space[0].id)
    ):
        return f"{path}:{node.lineno}: {CLIENT_CLASS} must take space_id= from {_RESOLVER}()"
    return None


def _reference_violations(tree: ast.Module, *, path: str) -> list[str]:
    """Every reference to ``GenieClient``, judged by where it is and what it does."""

    parents = _parents(tree)
    annotations = _annotation_ids(tree)
    calls = {
        id(node.func): node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == CLIENT_CLASS
    }
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "*" and node.module in _CLIENT_MODULES:
                    found.append(f"{path}:{node.lineno}: star-imports a module that binds {CLIENT_CLASS}")
                if alias.name != CLIENT_CLASS:
                    continue
                if (
                    path != REGISTRY_MODULE
                    or node.module != "backend.services.genie_client"
                    or alias.asname not in {None, CLIENT_CLASS}
                ):
                    found.append(
                        f"{path}:{node.lineno}: imports {CLIENT_CLASS} outside the registry, "
                        "or under an alias"
                    )
        elif isinstance(node, ast.alias) and (node.asname or node.name).split(".")[-1] == CLIENT_CLASS:
            if not isinstance(parents.get(id(node)), ast.ImportFrom):
                found.append(f"{path}:{getattr(node, 'lineno', 0)}: binds the name {CLIENT_CLASS} by an import")
        elif isinstance(node, ast.ClassDef) and node.name == CLIENT_CLASS:
            if path != CLIENT_MODULE or parents.get(id(node)) is not tree:
                found.append(f"{path}:{node.lineno}: defines another {CLIENT_CLASS}")
        elif isinstance(node, ast.Attribute) and node.attr == CLIENT_CLASS:
            found.append(f"{path}:{node.lineno}: reaches {CLIENT_CLASS} as an attribute")
        elif isinstance(node, ast.Name) and node.id == CLIENT_CLASS:
            found.extend(
                _name_violation(
                    node,
                    path=path,
                    call=calls.get(id(node)),
                    annotated=id(node) in annotations,
                    parents=parents,
                )
            )
    return found


def _name_violation(
    node: ast.Name, *, path: str, call: ast.Call | None, annotated: bool, parents: dict[int, ast.AST]
) -> list[str]:
    if not isinstance(node.ctx, ast.Load):
        return [f"{path}:{node.lineno}: rebinds the name {CLIENT_CLASS}"]
    if annotated and path in {CLIENT_MODULE, REGISTRY_MODULE}:
        return []
    if call is not None and path == REGISTRY_MODULE:
        reason = _governed_call(call, path=path, parents=parents)
        return [reason] if reason else []
    if call is not None:
        return [f"{path}:{node.lineno}: {CLIENT_CLASS} is built outside the registry"]
    return [
        f"{path}:{node.lineno}: {CLIENT_CLASS} is referenced outside a governed call or an annotation"
    ]


def _resolver_violations(tree: ast.Module, *, path: str) -> list[str]:
    """The registry defines ``_resolve_space_id`` once, at module level, and never rebinds it."""

    defined = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == _RESOLVER
    ]
    rebound = [
        node
        for node in ast.walk(tree)
        if (isinstance(node, ast.Name) and node.id == _RESOLVER and not isinstance(node.ctx, ast.Load))
        or (isinstance(node, ast.alias) and (node.asname or node.name) == _RESOLVER)
        or (
            isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            and node.name == _RESOLVER
            and node not in defined
        )
    ]
    if len(defined) == 1 and not rebound:
        return []
    return [f"{path}: {_RESOLVER} must be one module-level function the registry never rebinds"]


def _sdk_violations(tree: ast.AST, *, path: str) -> list[str]:
    return [
        f"{path}:{node.lineno}: reaches the SDK Genie API (.genie) instead of the pinned client"
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr == "genie"
    ]


def violations(root: Path = REPO) -> list[str]:
    """Every breach of the single-space shape under ``root/backend``; empty when it holds.

    The policy loads once per consumer process, so this rescans the tree each
    time an elevated entry is judged; the deploy's source does not change
    underneath a run.
    """

    backend = root / "backend"
    files = sorted(backend.rglob("*.py"))
    if not (root / CLIENT_MODULE).is_file() or not (root / REGISTRY_MODULE).is_file():
        return ["the Genie client or its registry is missing, so the control cannot hold"]
    found: list[str] = []
    for file in files:
        path = file.relative_to(root).as_posix()
        source = file.read_text(encoding="utf-8")
        # Every rule needs one of the words: a Genie path, GenieClient, a
        # .genie attribute or _space_id. A module without them cannot breach.
        if "genie" not in source.casefold() and _SPACE_ATTRIBUTE not in source:
            continue
        try:
            tree = ast.parse(source, filename=path)
        except SyntaxError:
            found.append(f"{path}: does not parse")
            continue
        if path == CLIENT_MODULE:
            found.extend(_client_violations(tree, path=path))
        else:
            found.extend(
                f"{path}:{lineno}: a Genie API path outside {CLIENT_MODULE}"
                for lineno, _node, text in _strings(tree)
                if any(marker in text for marker in _GENIE_PATHS)
            )
        if path == REGISTRY_MODULE:
            found.extend(_resolver_violations(tree, path=path))
        found.extend(_space_id_violations(tree, path=path))
        found.extend(_reference_violations(tree, path=path))
        found.extend(_sdk_violations(tree, path=path))
    return found


__all__ = ["CLIENT_CLASS", "CLIENT_MODULE", "REGISTRY_MODULE", "violations"]

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
   only in the client module.
2. Every such path there is ``.../api/2.0/genie/spaces/{self._space_id}``,
   and ``self._space_id`` is assigned only in ``GenieClient.__init__``.
3. ``GenieClient(...)`` is called only in the registry, with
   ``space_id=<name>`` bound by ``<name> = _resolve_space_id()``.
4. No backend module reaches the SDK's Genie API (an attribute ``.genie``).
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
CLIENT_MODULE = "backend/services/genie_client.py"
REGISTRY_MODULE = "backend/services/genie_client_registry.py"
_GENIE_PATHS = ("api/2.0/genie", "genie/spaces")
_SPACE_PREFIX = "/api/2.0/genie/spaces/"
_RESOLVER = "_resolve_space_id"


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


def _is_self_space_id(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "_space_id"
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


def _client_violations(tree: ast.AST, *, path: str) -> list[str]:
    found: list[str] = []
    for lineno, node, text in _strings(tree):
        if not any(marker in text for marker in _GENIE_PATHS):
            continue
        if not isinstance(node, ast.JoinedStr) or not _addresses_own_space(node):
            found.append(f"{path}:{lineno}: a Genie API path that does not address self._space_id")
    for function in ast.walk(tree):
        if not isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef) or function.name == "__init__":
            continue
        for node in ast.walk(function):
            if isinstance(node, ast.Assign):
                targets = list(node.targets)
            elif isinstance(node, ast.AugAssign | ast.AnnAssign):
                targets = [node.target]
            else:
                continue
            if any(_is_self_space_id(target) for target in targets):
                found.append(f"{path}:{node.lineno}: self._space_id is reassigned outside __init__")
    return found


def _called_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _resolved_names(function: ast.AST) -> set[str]:
    """Names a function binds with ``name = _resolve_space_id()``."""

    return {
        target.id
        for node in ast.walk(function)
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Call)
        and _called_name(node.value) == _RESOLVER
        for target in node.targets
        if isinstance(target, ast.Name)
    }


def _construction_violations(tree: ast.AST, *, path: str) -> list[str]:
    found: list[str] = []
    functions = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)]
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _called_name(node) != "GenieClient":
            continue
        if path != REGISTRY_MODULE:
            found.append(f"{path}:{node.lineno}: GenieClient is built outside the registry")
            continue
        owner = next(
            (function for function in functions if node in ast.walk(function)),
            None,
        )
        space = next((keyword.value for keyword in node.keywords if keyword.arg == "space_id"), None)
        if (
            owner is None
            or node.args
            or not isinstance(space, ast.Name)
            or space.id not in _resolved_names(owner)
        ):
            found.append(
                f"{path}:{node.lineno}: GenieClient must take space_id= from {_RESOLVER}()"
            )
    return found


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
        # Every rule needs the word: a Genie path, GenieClient or a .genie
        # attribute. A module without it cannot breach the shape.
        if "genie" not in source.casefold():
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
        found.extend(_construction_violations(tree, path=path))
        found.extend(_sdk_violations(tree, path=path))
    return found


__all__ = ["CLIENT_MODULE", "REGISTRY_MODULE", "violations"]

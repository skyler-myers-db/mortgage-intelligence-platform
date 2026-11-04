"""The second static control behind an elevated App exposure: governed SQL FQNs only.

Plan 4.12 makes an elevated App entry depend on two compensating controls.
``genie_single_space`` is the first: the App talks to one Genie space. This is
the second: the SQL trust policy the App applies to Genie's SQL before it runs
or discloses it admits only governed FQNs, so an elevated grant on another
team's object cannot be reached through trusted Genie SQL either. It checks the
policy's shape on the backend's source with ``ast``
(tests/unit/test_sql_trust_scope_ast.py pins it on this tree):

1. ``backend/services/genie_trusted_assets.py``: ``_TRUSTED_ASSET_PAIRS`` is
   one module-level tuple of ``(schema, table)`` string pairs, each schema a
   governed one (``audit``, ``gold``, ``ref``, ``semantics``), and
   ``trusted_assets()`` is exactly ``[qualify(schema, table) for schema, table
   in _TRUSTED_ASSET_PAIRS]``, with ``qualify`` imported from the SQL helpers.
2. ``backend/services/databricks_sql_helpers.py``: ``qualify`` returns
   ``f"{cat}.{schema}.{table}"`` with ``cat`` bound once to ``catalog if
   catalog is not None else settings.mip_default_catalog``: called without a
   catalog, as above, every admitted FQN is in the install's own catalog.
3. ``backend/services/repositories/databricks_genie_trust.py``:
   ``_trusted_genie_asset_names()`` returns exactly
   ``frozenset(trusted_assets())``, and ``_trusted_sql_policy_core`` requires
   ``bool(refs)``, every ref of ``refs = _extract_asset_refs(sql)`` in
   ``allowed_assets = _trusted_genie_asset_names()``, and no unqualified
   relation; its two wrappers return its verdict.

No configured Cotality source FQN is admitted today; admitting one must extend
this check (it refuses any other schema). A refactor of these functions fails
the control closed: elevated App entries then refuse until it is updated.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
ASSETS_MODULE = "backend/services/genie_trusted_assets.py"
HELPERS_MODULE = "backend/services/databricks_sql_helpers.py"
TRUST_MODULE = "backend/services/repositories/databricks_genie_trust.py"
GOVERNED_SCHEMAS = frozenset({"audit", "gold", "ref", "semantics"})
_PAIRS = "_TRUSTED_ASSET_PAIRS"


def _functions(tree: ast.Module) -> dict[str, list[ast.FunctionDef]]:
    found: dict[str, list[ast.FunctionDef]] = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            found.setdefault(node.name, []).append(node)
    return found


def _one_function(tree: ast.Module, name: str) -> ast.FunctionDef | None:
    """The module-level function ``name``, when it is defined once and never rebound."""

    defined = _functions(tree).get(name, [])
    rebound = [
        node
        for node in ast.walk(tree)
        if (isinstance(node, ast.Name) and node.id == name and not isinstance(node.ctx, ast.Load))
        or (isinstance(node, ast.alias) and (node.asname or node.name) == name)
    ]
    return defined[0] if len(defined) == 1 and not rebound else None


def _body(function: ast.FunctionDef) -> list[ast.stmt]:
    """The statements after an optional docstring."""

    body = list(function.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        body = body[1:]
    return body


def _only_return(function: ast.FunctionDef) -> ast.expr | None:
    body = _body(function)
    if len(body) == 1 and isinstance(body[0], ast.Return):
        return body[0].value
    return None


def _imports(tree: ast.Module, name: str, module: str) -> bool:
    """``name`` is imported once, unaliased, from ``module``, and never otherwise bound."""

    sources = [
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
        if (alias.asname or alias.name) == name or alias.name == "*"
    ]
    stores = [
        node
        for node in ast.walk(tree)
        if (isinstance(node, ast.Name) and node.id == name and not isinstance(node.ctx, ast.Load))
        or (isinstance(node, ast.FunctionDef | ast.ClassDef) and node.name == name)
    ]
    return sources == [module] and not stores


def _is_call(node: ast.AST | None, name: str, *, arity: int | None = None) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == name
        and not node.keywords
        and (arity is None or len(node.args) == arity)
    )


def _bound_once(function: ast.FunctionDef, name: str) -> ast.expr | None:
    """The value ``name`` is bound to by its single plain assignment in ``function``."""

    stores = [
        node
        for node in ast.walk(function)
        if (isinstance(node, ast.Name) and node.id == name and not isinstance(node.ctx, ast.Load))
        or (isinstance(node, ast.arg) and node.arg == name)
    ]
    assigns = [
        node.value
        for node in ast.walk(function)
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == name
    ]
    return assigns[0] if len(stores) == 1 and len(assigns) == 1 else None


def _pair_violations(tree: ast.Module, *, path: str) -> list[str]:
    assigns = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign | ast.AnnAssign | ast.AugAssign)
        and any(
            isinstance(target, ast.Name) and target.id == _PAIRS
            for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
        )
    ]
    if len(assigns) != 1 or assigns[0] not in tree.body or not isinstance(assigns[0], ast.Assign):
        return [f"{path}: {_PAIRS} must be assigned once, at module level"]
    value = assigns[0].value
    if not isinstance(value, ast.Tuple) or not value.elts:
        return [f"{path}:{value.lineno}: {_PAIRS} must be a literal tuple of (schema, table) pairs"]
    found: list[str] = []
    for pair in value.elts:
        texts = [
            item.value
            for item in (pair.elts if isinstance(pair, ast.Tuple) else [])
            if isinstance(item, ast.Constant) and isinstance(item.value, str)
        ]
        if len(texts) != 2 or len(pair.elts) != 2:  # type: ignore[attr-defined]
            found.append(f"{path}:{pair.lineno}: a trusted asset is not a (schema, table) string pair")
        elif texts[0] not in GOVERNED_SCHEMAS:
            found.append(f"{path}:{pair.lineno}: a trusted asset outside the governed schemas")
    return found


def _assets_violations(tree: ast.Module, *, path: str) -> list[str]:
    found = _pair_violations(tree, path=path)
    if not _imports(tree, "qualify", "backend.services.databricks_sql_helpers"):
        found.append(f"{path}: qualify must be imported once, unaliased, from the SQL helpers")
    function = _one_function(tree, "trusted_assets")
    value = _only_return(function) if function is not None else None
    generators = value.generators if isinstance(value, ast.ListComp) else []
    target = generators[0].target if len(generators) == 1 else None
    names = [item.id for item in target.elts if isinstance(item, ast.Name)] if isinstance(target, ast.Tuple) else []
    element = value.elt if isinstance(value, ast.ListComp) else None
    if not (
        len(names) == 2
        and _is_call(element, "qualify", arity=2)
        and [arg.id for arg in element.args if isinstance(arg, ast.Name)] == names  # type: ignore[union-attr]
        and isinstance(generators[0].iter, ast.Name)
        and generators[0].iter.id == _PAIRS
        and not generators[0].ifs
        and not generators[0].is_async
    ):
        found.append(
            f"{path}: trusted_assets() must be [qualify(schema, table) for schema, table in {_PAIRS}]"
        )
    return found


def _part(part: ast.expr) -> object:
    """An f-string part as text: a ``{name}`` field by its name, a literal by its value."""

    if isinstance(part, ast.FormattedValue) and isinstance(part.value, ast.Name):
        return part.value.id
    return part.value if isinstance(part, ast.Constant) else None


def _helpers_violations(tree: ast.Module, *, path: str) -> list[str]:
    function = _one_function(tree, "qualify")
    bad = [f"{path}: qualify must return f'{{cat}}.{{schema}}.{{table}}' in the configured catalog"]
    if function is None:
        return bad
    returns = [node.value for node in ast.walk(function) if isinstance(node, ast.Return)]
    joined = returns[0] if len(returns) == 1 else None
    parts = joined.values if isinstance(joined, ast.JoinedStr) else []
    shape = [_part(part) for part in parts]
    catalog = _bound_once(function, "cat")
    default = (
        isinstance(catalog, ast.IfExp)
        and isinstance(catalog.body, ast.Name)
        and catalog.body.id == "catalog"
        and ast.unparse(catalog.test) == "catalog is not None"
        and ast.unparse(catalog.orelse) == "settings.mip_default_catalog"
    )
    return [] if shape == ["cat", ".", "schema", ".", "table"] and default else bad


def _trust_violations(tree: ast.Module, *, path: str) -> list[str]:
    found: list[str] = []
    if not _imports(tree, "trusted_assets", "backend.services.genie_trusted_assets"):
        found.append(f"{path}: trusted_assets must be imported once, unaliased, from genie_trusted_assets")
    names = _one_function(tree, "_trusted_genie_asset_names")
    value = _only_return(names) if names is not None else None
    if not (_is_call(value, "frozenset", arity=1) and _is_call(value.args[0], "trusted_assets", arity=0)):  # type: ignore[union-attr]
        found.append(f"{path}: _trusted_genie_asset_names() must return frozenset(trusted_assets())")
    core = _one_function(tree, "_trusted_sql_policy_core")
    if core is None or not _core_admits_only_allowed(core):
        found.append(
            f"{path}: _trusted_sql_policy_core must require bool(refs), every ref in "
            "_trusted_genie_asset_names() and no unqualified relation"
        )
    for wrapper in ("_trusted_sql_policy", "_trusted_sql_policy_allowing_stale_evidence_enum"):
        function = _one_function(tree, wrapper)
        returned = _only_return(function) if function is not None else None
        if not (
            isinstance(returned, ast.Call)
            and isinstance(returned.func, ast.Name)
            and returned.func.id == "_trusted_sql_policy_core"
        ):
            found.append(f"{path}: {wrapper} must return _trusted_sql_policy_core(...)")
    return found


def _core_admits_only_allowed(core: ast.FunctionDef) -> bool:
    if not (
        _is_call(_bound_once(core, "refs"), "_extract_asset_refs", arity=1)
        and _is_call(_bound_once(core, "allowed_assets"), "_trusted_genie_asset_names", arity=0)
    ):
        return False
    returns = [node for node in ast.walk(core) if isinstance(node, ast.Return)]
    if len(returns) != 1 or _body(core)[-1] is not returns[0]:
        return False
    value = returns[0].value
    clauses = value.values if isinstance(value, ast.BoolOp) and isinstance(value.op, ast.And) else []
    rendered = {ast.unparse(clause) for clause in clauses}
    return {
        "bool(refs)",
        "all((asset in allowed_assets for asset in refs))",
    } <= rendered and any(
        isinstance(clause, ast.UnaryOp)
        and isinstance(clause.op, ast.Not)
        and _is_call(clause.operand, "_sql_has_unqualified_relations", arity=1)
        for clause in clauses
    )


_CHECKS = (
    (ASSETS_MODULE, _assets_violations),
    (HELPERS_MODULE, _helpers_violations),
    (TRUST_MODULE, _trust_violations),
)


def violations(root: Path = REPO) -> list[str]:
    """Every breach of the governed SQL trust scope under ``root``; empty when it holds."""

    found: list[str] = []
    for path, check in _CHECKS:
        file = root / path
        if not file.is_file():
            found.append(f"{path}: is missing, so the SQL trust scope cannot hold")
            continue
        try:
            tree = ast.parse(file.read_text(encoding="utf-8"), filename=path)
        except SyntaxError:
            found.append(f"{path}: does not parse")
            continue
        found.extend(check(tree, path=path))
    return found


__all__ = ["ASSETS_MODULE", "GOVERNED_SCHEMAS", "HELPERS_MODULE", "TRUST_MODULE", "violations"]

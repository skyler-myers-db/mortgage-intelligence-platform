"""The running App never names an ETL-only schema in SQL it executes.

The App's service principal reads ``mip.gold`` / ``mip.ref`` (plus the
governed semantics and audit surfaces) and holds no grant on ``mip.silver``,
``mip.raw``, ``mip.first_party`` or the Cotality provider catalog (``docs/security/GRANTS.md``
sections 5 and 7). A statement that names one of them passes every unit test
and fails live with ``[INSUFFICIENT_PERMISSIONS] ... USE SCHEMA on Schema
'mip.silver'`` -- the 2026-09-25 Rate Lever 503, whose live contactable CTE
LEFT JOINed ``mip.silver.lien_current``.

Three checks over every importable ``backend`` module (a superset of the
repositories and the ``*_sql*`` helpers). A statement keyword is an upper-case
``FROM`` / ``JOIN`` / ``DESCRIBE [DETAIL|TABLE|EXTENDED]``:

1. Rendered statements. Every module-level ``str`` -- and every ``str`` held
   in a module-level tuple / list / set / dict, three levels deep -- whose
   text carries an upper-case SQL ``FROM`` / ``JOIN`` keyword is a statement
   or a statement fragment, rendered exactly as the App sends it
   (``qualify("silver", ...)`` has already become ``mip.silver.x``). None may
   name an ETL-only schema. Provenance labels such as the Rate Lever's
   ``_BOOK_SOURCE`` (``"mip.gold.borrower_360 + mip.silver.lien_current"``)
   or the Admin rules' ``uc_table`` values carry no ``FROM`` / ``JOIN`` and
   are excluded by construction, not by a list.
2. Statement builders. A function that assembles SQL at call time is not
   rendered by check 1, so the source is read: an f-string whose literal text
   carries ``FROM`` / ``JOIN`` may not interpolate ``qualify("silver" | "raw",
   ...)`` nor a module-level name whose rendered value names an ETL-only
   schema, and no plain string literal outside a docstring may carry both a
   ``FROM`` / ``JOIN`` keyword and an ETL-only reference.

3. Table slots. Every f-string interpolation that directly follows a
   statement keyword names the relation read, and it must RESOLVE: (i)
   ``qualify(<literal gold|ref|semantics|audit>, ...)``; (ii) a Name /
   Attribute that resolves to a module attribute whose rendered value names
   no ETL-only schema (a module constant built from (i) qualifies); (iii) a
   function-local name assigned only from (i) or (ii); or (iv) a
   hand-verified ``REVIEWED_RUNTIME_TABLE_SLOTS`` entry (shrink-only). This
   closes the runtime-name hole the 2026-09 Admin readiness probes used
   (``f"DESCRIBE DETAIL {fqtn}"`` over ``mip.silver.*`` / ``mip.first_party.*``).

Residual (stated, not hidden): SQL spliced from pieces that each lack the
keyword (``" ".join([...])``, ``+=`` of a bare ``qualify("silver", ...)``) is
not proven here, nor is a relation in a slot that is not an f-string
interpolation (``%`` / ``.format`` templating, or a relation built in one
function and interpolated in another after the keyword was split off). No
backend module does that today; the reviewed slots are verified by hand.
"""

from __future__ import annotations

import ast
import importlib
import re
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

from backend.services.repositories.databricks_rate_sensitivity import RATE_SENSITIVITY_SQL
from backend.services.repositories.databricks_rate_window import RATE_WINDOW_SQL

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "backend"

# The statement shape: an upper-case SQL keyword followed by whitespace.
# Upper case only, so prose such as "read from silver" is not a statement.
STATEMENT_KEYWORD = re.compile(r"\b(?:FROM|JOIN|DESCRIBE(?:\s+(?:DETAIL|TABLE|EXTENDED))?)\s")
# A reference into an ETL-only layer: ``silver.x`` / ``raw.x``, optionally
# catalog-qualified and back-ticked, or anything in the Cotality provider
# catalog. Case-insensitive, because Spark identifiers are.
ETL_ONLY_REFERENCE = re.compile(
    r"(?i)(?<![\w$])(?:[\w`]+\.)?`?(?:silver|raw|first_party)`?\.`?[a-z_]|\bcotality_mortgage_data\."
)
ETL_ONLY_SCHEMAS = frozenset({"silver", "raw", "first_party"})
_MAX_DEPTH = 3


def _module_name(path: Path) -> str:
    parts = list(path.relative_to(REPO_ROOT).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _backend_modules() -> list[tuple[str, Path]]:
    return [
        (_module_name(path), path)
        for path in sorted(BACKEND.rglob("*.py"))
        if "__pycache__" not in path.parts and path.name != "__main__.py"
    ]


def _strings(value: object, depth: int = 0) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif depth < _MAX_DEPTH and isinstance(value, tuple | list | set | frozenset):
        for item in value:
            yield from _strings(item, depth + 1)
    elif depth < _MAX_DEPTH and isinstance(value, dict):
        for item in value.values():
            yield from _strings(item, depth + 1)


def _module_level_strings(module: ModuleType) -> Iterator[tuple[str, str]]:
    for name, value in vars(module).items():
        if name.startswith("__"):
            continue
        for text in _strings(value):
            yield name, text


def _statements(module: ModuleType) -> Iterator[tuple[str, str]]:
    for name, text in _module_level_strings(module):
        if STATEMENT_KEYWORD.search(text):
            yield name, text


def _tainted_names(module: ModuleType) -> frozenset[str]:
    """Module-level names whose rendered value names an ETL-only schema."""
    return frozenset(name for name, text in _module_level_strings(module) if ETL_ONLY_REFERENCE.search(text))


def _docstring_ids(tree: ast.Module) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                ids.add(id(body[0].value))
    return ids


def _is_etl_only_qualify(node: ast.expr) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
    if name != "qualify":
        return False
    schema: ast.expr | None = node.args[0] if node.args else None
    for keyword in node.keywords:
        if keyword.arg == "schema":
            schema = keyword.value
    return isinstance(schema, ast.Constant) and str(schema.value).lower() in ETL_ONLY_SCHEMAS


def _builder_scan(source: str, tainted: frozenset[str]) -> tuple[list[str], int]:
    """Return (findings, number of statement-shaped f-strings inspected)."""
    tree = ast.parse(source)
    docstrings = _docstring_ids(tree)
    findings: list[str] = []
    inspected = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            literal = "".join(
                part.value for part in node.values if isinstance(part, ast.Constant) and isinstance(part.value, str)
            )
            if not STATEMENT_KEYWORD.search(literal):
                continue
            inspected += 1
            for part in node.values:
                if not isinstance(part, ast.FormattedValue):
                    continue
                value = part.value
                if _is_etl_only_qualify(value):
                    findings.append(f"line {node.lineno}: interpolates {ast.unparse(value)}")
                elif isinstance(value, ast.Name) and value.id in tainted:
                    findings.append(f"line {node.lineno}: interpolates {value.id} (renders an ETL-only schema)")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            if STATEMENT_KEYWORD.search(node.value) and ETL_ONLY_REFERENCE.search(node.value):
                findings.append(f"line {node.lineno}: literal statement names an ETL-only schema")
    return findings, inspected


def _builder_findings(source: str, tainted: frozenset[str]) -> list[str]:
    return _builder_scan(source, tainted)[0]


def _import(name: str) -> ModuleType:
    return importlib.import_module(name)


def test_the_detector_flags_etl_only_references_and_nothing_else() -> None:
    for flagged in (
        "mip.silver.lien_current",
        "`mip`.`silver`.`lien_current`",
        "acme_mip.silver.lien_current",
        "silver.lien_current",
        "mip.raw.cotality_lien",
        "MIP.SILVER.LIEN_CURRENT",
        "mip.first_party.loan_applications",
        "cotality_mortgage_data.corelogic.entrada_eval_voluntary_lien_status_marketing_v2",
    ):
        assert ETL_ONLY_REFERENCE.search(f"SELECT * FROM {flagged} AS x"), flagged
    for clean in (
        "mip.gold.borrower_360",
        "mip.gold.rate_sensitivity_book",
        "mip.ref.refresh_run_state",
        "b.raw_value",
        "lc.silver_flag",
        "b.first_party_flag",
        "mip.semantics.lead_generation_metric_view",
    ):
        assert not ETL_ONLY_REFERENCE.search(f"SELECT * FROM {clean} AS x"), clean


def test_every_rendered_app_statement_reads_governed_schemas_only() -> None:
    statements: list[tuple[str, str, str]] = []
    for module_name, _path in _backend_modules():
        module = _import(module_name)
        statements.extend((module_name, name, text) for name, text in _statements(module))

    # Non-vacuity: the collector sees the repositories' real statements.
    collected = {text for _module, _name, text in statements}
    assert RATE_SENSITIVITY_SQL in collected
    assert RATE_WINDOW_SQL in collected
    assert len(statements) >= 200, len(statements)

    offenders = sorted(
        f"{module_name}.{name}: {hit.group(0)!r}"
        for module_name, name, text in statements
        if (hit := ETL_ONLY_REFERENCE.search(text))
    )
    assert not offenders, (
        "App-executed SQL names an ETL-only schema (the App holds no grant; "
        "docs/security/GRANTS.md section 5). Precompute the value into a gold "
        f"table in the refresh job instead: {offenders}"
    )


def test_statement_builders_never_splice_an_etl_only_schema() -> None:
    offenders: dict[str, list[str]] = {}
    inspected = 0
    for module_name, path in _backend_modules():
        findings, count = _builder_scan(path.read_text(encoding="utf-8"), _tainted_names(_import(module_name)))
        inspected += count
        if findings:
            offenders[module_name] = findings
    # Non-vacuity: the scan reached the statement f-strings the repositories build.
    assert inspected >= 100, inspected
    assert not offenders, f"App SQL builders splice an ETL-only schema: {offenders}"


def test_the_builder_check_catches_the_pre_fix_rate_lever_shape() -> None:
    """Mutation proof: the 2026-09-25 statement shape fails the builder check."""
    pre_fix = (
        "from backend.services.databricks_sql_helpers import qualify\n"
        '_LIEN_CURRENT = qualify("silver", "lien_current")\n'
        "SQL = (\n"
        '    "SELECT b.state FROM b "\n'
        '    f"LEFT JOIN {_LIEN_CURRENT} AS lc ON lc.clip = b.clip "\n'
        ")\n"
        "def build():\n"
        '    return f"SELECT * FROM {qualify(\'raw\', \'x\')}"\n'
    )
    findings = _builder_findings(pre_fix, frozenset({"_LIEN_CURRENT"}))
    assert len(findings) == 2, findings
    literal = 'X = "SELECT * FROM mip.silver.lien_current"\n'
    assert _builder_findings(literal, frozenset()) == ["line 1: literal statement names an ETL-only schema"]
    provenance = '"""Reads FROM mip.silver.lien_current."""\nLABEL = f"{qualify(\'silver\', \'x\')} + gold"\n'
    assert _builder_findings(provenance, frozenset()) == []


# ---------------------------------------------------------------------------
# Check 3: every table slot resolves.
# ---------------------------------------------------------------------------

# An interpolation that FOLLOWS one of these (upper case, then whitespace, at
# the end of the preceding literal) names the relation the statement reads.
TABLE_SLOT_PREFIX = re.compile(r"\b(?:FROM|JOIN|DESCRIBE(?:\s+(?:DETAIL|TABLE|EXTENDED))?)\s+$")
GOVERNED_SCHEMAS = frozenset({"gold", "ref", "semantics", "audit"})

# Table slots whose relation is a runtime value the resolver cannot render,
# each verified by hand to read only a governed schema (or system metadata).
# Shrink-only: an entry that no longer occurs fails the check, so a reviewed
# site that moves or goes away must be removed here, never renamed silently.
REVIEWED_RUNTIME_TABLE_SLOTS: dict[tuple[str, str], str] = {
    (
        "backend.services.asset_metadata",
        "_quoted_fqn(descriptor)",
    ): "DESCRIBE DETAIL / COUNT(*) of a registry asset; _load_detail and _load_count "
    "run only for the App catalog's gold / ref / semantics descriptors (_app_readable)",
    (
        "backend.services.capabilities_live_probes",
        "asset",
    ): "loop over three qualify('semantics', ...) certified metric views",
    (
        "backend.services.capabilities_live_probes",
        "relation",
    ): "{mip_lakebase_sync_catalog}.{mip_lakebase_sync_schema}.{table}: the MIP-owned "
    "Lakebase synced serving tables (default mip_app_state.mip_sync), identifiers validated",
    (
        "backend.services.capability_serving_probes",
        "catalog",
    ): "{catalog}.{schema}.{table} of the configured AI Gateway inference table "
    "(mip_ai_gateway_inference_table), names listed from system.information_schema",
    (
        "backend.services.growth_agent_metrics",
        "primary_table",
    ): "_snapshot_validation_ctes(primary_table=...) is called only with the "
    "qualify('gold', ...) BORROWER_360 / BORROWER_DOSSIER constants",
    (
        "backend.services.lifecycle_sync",
        "funnel_table",
    ): "_qualified_uc_table(resolved_catalog, 'gold', 'funnel_snapshot_daily')",
    (
        "backend.services.repositories.databricks_campaign_treatment_materialize",
        "table",
    ): "_manifest_select_sql(table, ...) callers pass only "
    "qualify('audit', 'campaign_treatment_snapshot')",
}


class _Scope:
    """Names bound in one function / class body: params and assignments."""

    def __init__(self, params: frozenset[str]) -> None:
        self.params = params
        # name -> the assigned value expressions (None: bound some other way)
        self.assigned: dict[str, list[ast.expr | None]] = {}

    def bind(self, name: str, value: ast.expr | None) -> None:
        self.assigned.setdefault(name, []).append(value)


def _bind_targets(scope: _Scope, target: ast.expr, value: ast.expr | None) -> None:
    if isinstance(target, ast.Name):
        scope.bind(target.id, value)
    elif isinstance(target, ast.Tuple | ast.List):
        for element in target.elts:
            _bind_targets(scope, element, None)
    elif isinstance(target, ast.Starred):
        _bind_targets(scope, target.value, None)


def _function_params(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda) -> frozenset[str]:
    args = node.args
    every = [*args.posonlyargs, *args.args, *args.kwonlyargs]
    if args.vararg:
        every.append(args.vararg)
    if args.kwarg:
        every.append(args.kwarg)
    return frozenset(arg.arg for arg in every)


def _scope_of(node: ast.AST) -> _Scope:
    """Collect the bindings made directly in ``node``'s body (not nested scopes)."""
    params = (
        _function_params(node)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda)
        else frozenset()
    )
    scope = _Scope(params)
    stack: list[ast.AST] = [node.body] if isinstance(node, ast.Lambda) else list(getattr(node, "body", []))
    while stack:
        current = stack.pop()
        if isinstance(current, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            scope.bind(current.name, None)
            continue
        if isinstance(current, ast.Lambda):
            continue
        if isinstance(current, ast.Assign):
            for target in current.targets:
                _bind_targets(scope, target, current.value)
        elif isinstance(current, ast.AnnAssign) and current.value is not None:
            _bind_targets(scope, current.target, current.value)
        elif isinstance(current, ast.AugAssign | ast.For | ast.AsyncFor | ast.NamedExpr):
            _bind_targets(scope, current.target, None)
        elif isinstance(current, ast.withitem) and current.optional_vars is not None:
            _bind_targets(scope, current.optional_vars, None)
        elif isinstance(current, ast.ExceptHandler) and current.name:
            scope.bind(current.name, None)
        elif isinstance(current, ast.Import | ast.ImportFrom):
            for alias in current.names:
                scope.bind((alias.asname or alias.name).split(".")[0], None)
        stack.extend(ast.iter_child_nodes(current))
    return scope


def _is_governed_qualify(node: ast.expr) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
    if name != "qualify":
        return False
    schema: ast.expr | None = node.args[0] if node.args else None
    for keyword in node.keywords:
        if keyword.arg == "schema":
            schema = keyword.value
    return isinstance(schema, ast.Constant) and str(schema.value).lower() in GOVERNED_SCHEMAS


def _module_value(module: ModuleType, node: ast.expr) -> tuple[bool, object]:
    """Resolve a Name / Attribute chain rooted at a module attribute."""
    attrs: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        attrs.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name) or not hasattr(module, current.id):
        return False, None
    value: object = getattr(module, current.id)
    for attr in reversed(attrs):
        if not hasattr(value, attr):
            return False, None
        value = getattr(value, attr)
    return True, value


def _renders_governed(module: ModuleType, node: ast.expr) -> bool:
    """Rule (ii): an imported-module attribute whose rendered value is clean."""
    found, value = _module_value(module, node)
    return found and isinstance(value, str) and not ETL_ONLY_REFERENCE.search(value)


def _slot_resolves(module: ModuleType, node: ast.expr, scopes: list[_Scope]) -> bool:
    if _is_governed_qualify(node):  # (i)
        return True
    root = node
    while isinstance(root, ast.Attribute):
        root = root.value
    if isinstance(root, ast.Name) and scopes:
        local = scopes[-1]
        if root.id in local.params:
            return False
        if root.id in local.assigned:
            if root is not node:
                return False
            values = local.assigned[root.id]  # (iii)
            return all(
                value is not None
                and (_is_governed_qualify(value) or _renders_governed(module, value))
                for value in values
            )
    return _renders_governed(module, node)  # (ii)


class _SlotScanner(ast.NodeVisitor):
    def __init__(self, module: ModuleType) -> None:
        self.module = module
        self.scopes: list[_Scope] = []
        self.slots: list[tuple[int, str, bool]] = []  # (line, unparsed expr, resolves)

    def _visit_scope(self, node: ast.AST) -> None:
        self.scopes.append(_scope_of(node))
        self.generic_visit(node)
        self.scopes.pop()

    visit_FunctionDef = _visit_scope
    visit_AsyncFunctionDef = _visit_scope
    visit_ClassDef = _visit_scope
    visit_Lambda = _visit_scope

    def visit_JoinedStr(self, node: ast.JoinedStr) -> None:
        previous = ""
        for part in node.values:
            if isinstance(part, ast.Constant) and isinstance(part.value, str):
                previous = part.value
                continue
            if isinstance(part, ast.FormattedValue) and TABLE_SLOT_PREFIX.search(previous):
                self.slots.append(
                    (node.lineno, ast.unparse(part.value), _slot_resolves(self.module, part.value, self.scopes))
                )
            previous = ""
        self.generic_visit(node)


def _table_slots(module: ModuleType, source: str) -> list[tuple[int, str, bool]]:
    scanner = _SlotScanner(module)
    scanner.visit(ast.parse(source))
    return scanner.slots


def _all_table_slots() -> list[tuple[str, int, str, bool]]:
    slots: list[tuple[str, int, str, bool]] = []
    for module_name, path in _backend_modules():
        module = _import(module_name)
        slots.extend(
            (module_name, line, expr, ok)
            for line, expr, ok in _table_slots(module, path.read_text(encoding="utf-8"))
        )
    return slots


def test_every_table_slot_resolves_to_a_governed_relation() -> None:
    """Check 3: the relation after FROM / JOIN / DESCRIBE is provably governed.

    A slot resolves when it is (i) ``qualify(<literal governed schema>)``,
    (ii) a module attribute whose rendered value names no ETL-only schema,
    (iii) a function-local name assigned only from (i) or (ii), or (iv) a
    hand-verified ``REVIEWED_RUNTIME_TABLE_SLOTS`` entry.
    """
    slots = _all_table_slots()
    # Non-vacuity: the scan reaches the repositories' table slots.
    assert len(slots) >= 180, len(slots)
    unresolved = sorted(
        f"{module}:{line}: {{{expr}}}"
        for module, line, expr, ok in slots
        if not ok and (module, expr) not in REVIEWED_RUNTIME_TABLE_SLOTS
    )
    assert not unresolved, (
        "A table slot reads a relation this check cannot prove governed. Use "
        "qualify(<gold|ref|semantics|audit>, ...) or a module constant built from "
        f"it; never allowlist an ETL-only read: {unresolved}"
    )


def test_reviewed_runtime_table_slots_are_shrink_only() -> None:
    live = {(module, expr) for module, _line, expr, ok in _all_table_slots() if not ok}
    stale = sorted(set(REVIEWED_RUNTIME_TABLE_SLOTS) - live)
    assert not stale, f"reviewed table slots no longer occur; delete them: {stale}"


def test_the_slot_check_catches_the_pre_fix_admin_probe_shapes() -> None:
    """Mutation proof: the removed Admin readiness probes fail check 3."""
    module = ModuleType("pre_fix_admin_rules")
    pre_fix = (
        "def _describe_detail(sql, fqtn):\n"
        '    detail = sql.execute(f"DESCRIBE DETAIL {fqtn}")\n'
        '    count = sql.execute(f"SELECT COUNT(*) AS row_count FROM {fqtn}")\n'
        "    return detail, count\n"
    )
    assert [(expr, ok) for _line, expr, ok in _table_slots(module, pre_fix)] == [
        ("fqtn", False),
        ("fqtn", False),
    ]
    governed = (
        "def read(sql):\n"
        '    table = qualify("gold", "borrower_360")\n'
        '    sql.execute(f"SELECT 1 FROM {table}")\n'
        '    sql.execute(f"DESCRIBE TABLE {qualify(\'ref\', \'offer_rules_config\')}")\n'
        '    sql.execute(f"SELECT 1 FROM {qualify(\'silver\', \'lien_current\')}")\n'
    )
    assert [(expr, ok) for _line, expr, ok in _table_slots(module, governed)] == [
        ("table", True),
        ("qualify('ref', 'offer_rules_config')", True),
        ("qualify('silver', 'lien_current')", False),
    ]
    assert STATEMENT_KEYWORD.search("DESCRIBE DETAIL mip.silver.lien_current")

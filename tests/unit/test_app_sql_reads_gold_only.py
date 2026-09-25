"""The running App never names an ETL-only schema in SQL it executes.

The App's service principal reads ``mip.gold`` / ``mip.ref`` (plus the
governed semantics and audit surfaces) and holds no grant on ``mip.silver``,
``mip.raw`` or the Cotality provider catalog (``docs/security/GRANTS.md``
sections 5 and 7). A statement that names one of them passes every unit test
and fails live with ``[INSUFFICIENT_PERMISSIONS] ... USE SCHEMA on Schema
'mip.silver'`` -- the 2026-09-25 Rate Lever 503, whose live contactable CTE
LEFT JOINed ``mip.silver.lien_current``.

Two checks over every importable ``backend`` module (a superset of the
repositories and the ``*_sql*`` helpers):

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

Residual (stated, not hidden): SQL spliced from pieces that each lack the
keyword (``" ".join([...])``, ``+=`` of a bare ``qualify("silver", ...)``) is
not proven here. No backend module does that today.
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
STATEMENT_KEYWORD = re.compile(r"\b(?:FROM|JOIN)\s")
# A reference into an ETL-only layer: ``silver.x`` / ``raw.x``, optionally
# catalog-qualified and back-ticked, or anything in the Cotality provider
# catalog. Case-insensitive, because Spark identifiers are.
ETL_ONLY_REFERENCE = re.compile(
    r"(?i)(?<![\w$])(?:[\w`]+\.)?`?(?:silver|raw)`?\.`?[a-z_]|\bcotality_mortgage_data\."
)
ETL_ONLY_SCHEMAS = frozenset({"silver", "raw"})
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
        "cotality_mortgage_data.corelogic.entrada_eval_voluntary_lien_status_marketing_v2",
    ):
        assert ETL_ONLY_REFERENCE.search(f"SELECT * FROM {flagged} AS x"), flagged
    for clean in (
        "mip.gold.borrower_360",
        "mip.gold.rate_sensitivity_book",
        "mip.ref.refresh_run_state",
        "b.raw_value",
        "lc.silver_flag",
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

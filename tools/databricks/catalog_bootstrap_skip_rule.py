#!/usr/bin/env python3
"""Skip the catalog bootstrap only when the live catalog already is it (attack O12, M0-PR8).

``mip_init_catalog_schemas`` runs the rendered catalog, ref and gold DDL and
republishes the three reviewed Growth Agent functions with ``CREATE OR
REPLACE FUNCTION``. With blue serving, that republish opens an EXECUTE gap: a
replaced function's grants are proven again only by the reconcile that
follows (``reconcile_reviewed_function_execute_grants``), and blue's calls in
between fail. A repo-diff skip rule would trust the previous run's outcome;
this one reads the live catalog instead.

``check`` parses the job's rendered files (the task list is read from
``databricks.yml``) into a contract: the catalogs and schemas created, every
``CREATE TABLE IF NOT EXISTS`` column (name, type, nullability, in order), the
``ALTER TABLE ... SET TBLPROPERTIES`` values and every function's parameters,
return type, determinism, comment and body. It reads the same surface from
``information_schema`` (catalogs, schemata, columns, routines, parameters)
and ``SHOW TBLPROPERTIES``, read-only through the SQL warehouse, normalizes
both alike and compares their sha256. Equal exits 0 (skip: running the job
would change nothing); anything else exits 3 (run), and the report predicts
the EXECUTE-gap interval. A statement kind the parser does not know, or a
live read that fails, can never skip.

The live ``routine_definition`` text shape is derived, not captured: until a
governed run records it, a reformatted body reads as a difference, which only
ever means "run", the behaviour before this rule.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from tools.databricks.check_exit_codes import CHECK_DRIFT, CHECK_EXACT

BOOTSTRAP_JOB = "mip_init_catalog_schemas"
# Budgets (seconds), not measurements: the job, then the grant reconcile.
EXECUTE_GAP_BUDGET_S = {"job": 900, "reconcile": 600}
_IDENT = re.compile(r"[a-z0-9_]+\Z")
_NAME = r"`?[A-Za-z0-9_]+`?(?:\.`?[A-Za-z0-9_]+`?)*"
_TYPE_ALIASES = {
    "integer": "int",
    "long": "bigint",
    "short": "smallint",
    "byte": "tinyint",
    "real": "float",
    "dec": "decimal",
    "numeric": "decimal",
}
_COLUMN_STOP = frozenset({"NOT", "NULL", "COMMENT", "DEFAULT", "GENERATED", "MASK", "PRIMARY"})
_TABLE_CONSTRAINTS = ("CONSTRAINT", "PRIMARY", "FOREIGN", "CHECK", "UNIQUE")


@dataclass
class Contract:
    catalogs: set[str] = field(default_factory=set)
    schemas: set[str] = field(default_factory=set)
    tables: dict[str, list[list[Any]]] = field(default_factory=dict)
    table_properties: dict[str, dict[str, str]] = field(default_factory=dict)
    functions: dict[str, dict[str, Any]] = field(default_factory=dict)
    unknown: list[str] = field(default_factory=list)

    def canonical(self) -> dict[str, Any]:
        return {
            "catalogs": sorted(self.catalogs),
            "schemas": sorted(self.schemas),
            "tables": {name: self.tables[name] for name in sorted(self.tables)},
            "table_properties": {
                name: dict(sorted(self.table_properties[name].items()))
                for name in sorted(self.table_properties)
            },
            "functions": {name: self.functions[name] for name in sorted(self.functions)},
        }

    def sha256(self) -> str:
        body = json.dumps(self.canonical(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(b"mip-catalog-bootstrap-contract\0" + body.encode()).hexdigest()


def _unquote(name: str) -> str:
    return ".".join(part.strip("`").lower() for part in name.split("."))


def normalize_type(raw: str) -> str:
    text = re.sub(r"\s+", "", raw).replace("`", "").lower()
    base = re.match(r"[a-z]+", text)
    if base and base.group(0) in _TYPE_ALIASES:
        text = _TYPE_ALIASES[base.group(0)] + text[base.end() :]
    return "decimal(10,0)" if text == "decimal" else text


def _normalize_body(body: str) -> str:
    return re.sub(r"\s+", " ", body).strip().rstrip(";").strip()


def split_statements(text: str) -> list[str]:
    """Statements at ``;`` outside quotes, backticks and comments; comments dropped."""

    statements: list[str] = []
    current: list[str] = []
    index, length = 0, len(text)
    while index < length:
        char = text[index]
        if char == "-" and text.startswith("--", index):
            end = text.find("\n", index)
            index = length if end < 0 else end
            continue
        if char == "/" and text.startswith("/*", index):
            end = text.find("*/", index + 2)
            index = length if end < 0 else end + 2
            continue
        if char in "'`\"":
            end = index + 1
            while end < length:
                if text[end] == "\\" and char != "`":
                    end += 2
                    continue
                if text[end] == char:
                    if end + 1 < length and text[end + 1] == char:
                        end += 2
                        continue
                    break
                end += 1
            current.append(text[index : end + 1])
            index = end + 1
            continue
        if char == ";":
            statements.append("".join(current).strip())
            current = []
        else:
            current.append(char)
        index += 1
    statements.append("".join(current).strip())
    return [statement for statement in statements if statement]


def _matching(text: str, open_index: int) -> int:
    depth, index, quote = 0, open_index, ""
    while index < len(text):
        char = text[index]
        if quote:
            if char == quote:
                quote = ""
        elif char in "'`\"":
            quote = char
        elif char in "(<":
            depth += 1
        elif char in ")>":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    raise ValueError("unbalanced parentheses in a DDL statement")


def _top_level_split(text: str) -> list[str]:
    parts, depth, quote, start = [], 0, "", 0
    for index, char in enumerate(text):
        if quote:
            if char == quote:
                quote = ""
        elif char in "'`\"":
            quote = char
        elif char in "(<":
            depth += 1
        elif char in ")>":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(text[start:index].strip())
            start = index + 1
    parts.append(text[start:].strip())
    return [part for part in parts if part]


def _type_and_rest(tokens: str) -> tuple[str, str]:
    """Split ``TYPE rest...`` at the first top-level keyword that ends a type."""

    depth = 0
    for match in re.finditer(r"[<>()]|[A-Za-z_]+", tokens):
        token = match.group(0)
        if token in "<(":
            depth += 1
        elif token in ">)":
            depth -= 1
        elif depth == 0 and match.start() > 0 and token.upper() in _COLUMN_STOP:
            return tokens[: match.start()].strip(), tokens[match.start() :]
    return tokens.strip(), ""


def _parse_table(statement: str, contract: Contract) -> None:
    match = re.match(rf"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+({_NAME})\s*\(", statement, re.I)
    if match is None:
        contract.unknown.append("create_table_shape")
        return
    close = _matching(statement, match.end() - 1)
    columns = []
    for part in _top_level_split(statement[match.end() : close]):
        if part.split()[0].upper() in _TABLE_CONSTRAINTS:
            continue
        name, _, rest = part.partition(" ")
        column_type, tail = _type_and_rest(rest.strip())
        nullable = re.search(r"\bNOT\s+NULL\b", tail, re.I) is None
        columns.append([name.strip("`").lower(), normalize_type(column_type), nullable])
    contract.tables[_unquote(match.group(1))] = columns


def _parse_function(statement: str, contract: Contract) -> None:
    match = re.match(rf"CREATE\s+OR\s+REPLACE\s+FUNCTION\s+({_NAME})\s*\(", statement, re.I)
    if match is None:
        contract.unknown.append("create_function_shape")
        return
    close = _matching(statement, match.end() - 1)
    parameters = []
    for part in _top_level_split(statement[match.end() : close]):
        name, _, rest = part.partition(" ")
        parameters.append([name.strip("`").lower(), normalize_type(_type_and_rest(rest)[0])])
    tail = statement[close + 1 :]
    returns = re.match(
        r"\s*RETURNS\s+(.+?)\s+(?=NOT\s+DETERMINISTIC|DETERMINISTIC|COMMENT|RETURN)",
        tail,
        re.I | re.S,
    )
    body = re.search(r"\bRETURN\b(.*)\Z", tail, re.I | re.S)
    comment = re.search(
        r"\bCOMMENT\s+'((?:[^']|'')*)'", tail[: body.start()] if body else tail, re.I
    )
    if returns is None or body is None:
        contract.unknown.append("function_signature_shape")
        return
    head = tail[: body.start()]
    deterministic = (
        False
        if re.search(r"\bNOT\s+DETERMINISTIC\b", head, re.I)
        else True
        if re.search(r"\bDETERMINISTIC\b", head, re.I)
        else None
    )
    if deterministic is None:
        contract.unknown.append("function_determinism_unstated")
    contract.functions[_unquote(match.group(1))] = {
        "parameters": parameters,
        "returns": normalize_type(returns.group(1)),
        "deterministic": deterministic,
        "comment": comment.group(1).replace("''", "'") if comment else "",
        "body": _normalize_body(body.group(1)),
    }


def _parse_properties(text: str) -> dict[str, str]:
    pairs = re.findall(r"'((?:[^']|'')*)'\s*=\s*'((?:[^']|'')*)'", text)
    return {key.replace("''", "'"): value.replace("''", "'") for key, value in pairs}


def rendered_contract(files: Iterable[Path]) -> Contract:
    contract = Contract()
    for path in files:
        for statement in split_statements(path.read_text(encoding="utf-8")):
            head = " ".join(statement.split()[:4]).upper()
            if head.startswith("CREATE CATALOG IF NOT"):
                contract.catalogs.add(_unquote(statement.split()[5]))
            elif head.startswith("CREATE SCHEMA IF NOT"):
                contract.schemas.add(_unquote(statement.split()[5]))
            elif head.startswith("CREATE TABLE IF NOT"):
                _parse_table(statement, contract)
            elif head.startswith("CREATE OR REPLACE FUNCTION"):
                _parse_function(statement, contract)
            elif re.match(rf"ALTER\s+TABLE\s+{_NAME}\s+SET\s+TBLPROPERTIES\s*\(", statement, re.I):
                name = _unquote(statement.split()[2])
                contract.table_properties.setdefault(name, {}).update(_parse_properties(statement))
            else:
                contract.unknown.append(head.split(" ")[0].lower() or "empty")
    return contract


def bootstrap_files(bundle: Path, repo_root: Path) -> list[Path]:
    """The job's SQL task files, in task order, from ``databricks.yml``."""

    body = yaml.safe_load(bundle.read_text(encoding="utf-8")) or {}
    job = ((body.get("resources") or {}).get("jobs") or {}).get(BOOTSTRAP_JOB) or {}
    paths = [
        repo_root / str(((task.get("sql_task") or {}).get("file") or {}).get("path") or "")
        for task in job.get("tasks") or []
    ]
    if not paths or any(not path.is_file() for path in paths):
        raise ValueError(f"{BOOTSTRAP_JOB} names no rendered SQL file, or one is missing")
    return paths


def _rows(
    workspace: Any, warehouse_id: str, statement: str, parameters: Mapping[str, str]
) -> list[list[Any]]:
    from databricks.sdk.service.sql import StatementParameterListItem
    from tools.databricks.ensure_campaign_treatment_table import execute_sql

    response = execute_sql(
        workspace,
        warehouse_id=warehouse_id,
        statement=statement,
        parameters=[StatementParameterListItem(name=k, value=v) for k, v in parameters.items()],
    )
    rows = list(getattr(getattr(response, "result", None), "data_array", None) or [])
    chunks = int(getattr(getattr(response, "manifest", None), "total_chunk_count", 1) or 1)
    for index in range(1, chunks):
        chunk = workspace.statement_execution.get_statement_result_chunk_n(
            str(response.statement_id), index
        )
        rows.extend(getattr(chunk, "data_array", None) or [])
    return [list(row) for row in rows]


def live_contract(workspace: Any, *, warehouse_id: str, rendered: Contract) -> Contract:
    """The same surface, read-only from information_schema and SHOW TBLPROPERTIES."""

    live = Contract()
    catalogs = {
        name.split(".")[0] for name in (*rendered.schemas, *rendered.tables, *rendered.functions)
    }
    catalogs |= rendered.catalogs
    if any(_IDENT.fullmatch(catalog) is None for catalog in catalogs):
        raise ValueError("a bootstrap catalog name is not a plain identifier")
    for catalog in sorted(catalogs):
        query = (
            "SELECT catalog_name FROM system.information_schema.catalogs WHERE catalog_name = :c"
        )
        if _rows(workspace, warehouse_id, query, {"c": catalog}):
            live.catalogs.add(catalog)
        else:
            continue
        info = f"`{catalog}`.information_schema"
        for (schema,) in _rows(
            workspace, warehouse_id, f"SELECT schema_name FROM {info}.schemata", {}
        ):
            live.schemas.add(f"{catalog}.{str(schema).lower()}")
        columns = _rows(
            workspace,
            warehouse_id,
            f"SELECT table_schema, table_name, column_name, ordinal_position, full_data_type, "
            f"is_nullable FROM {info}.columns ORDER BY table_schema, table_name, ordinal_position",
            {},
        )
        for schema, table, column, _position, data_type, nullable in columns:
            name = f"{catalog}.{str(schema).lower()}.{str(table).lower()}"
            if name in rendered.tables:
                live.tables.setdefault(name, []).append(
                    [
                        str(column).lower(),
                        normalize_type(str(data_type)),
                        str(nullable).upper() != "NO",
                    ]
                )
        routines = _rows(
            workspace,
            warehouse_id,
            f"SELECT routine_schema, routine_name, full_data_type, is_deterministic, comment, "
            f"routine_definition FROM {info}.routines",
            {},
        )
        parameters = _rows(
            workspace,
            warehouse_id,
            f"SELECT specific_schema, specific_name, parameter_name, full_data_type FROM "
            f"{info}.parameters WHERE is_result = 'NO' ORDER BY specific_schema, specific_name, "
            f"ordinal_position",
            {},
        )
        for schema, routine, data_type, deterministic, comment, body in routines:
            name = f"{catalog}.{str(schema).lower()}.{str(routine).lower()}"
            if name not in rendered.functions:
                continue
            live.functions[name] = {
                "parameters": [
                    [str(param).lower(), normalize_type(str(param_type))]
                    for p_schema, p_name, param, param_type in parameters
                    if f"{catalog}.{str(p_schema).lower()}.{str(p_name).lower()}" == name
                ],
                "returns": normalize_type(str(data_type)),
                "deterministic": str(deterministic).upper() == "YES",
                "comment": str(comment or ""),
                "body": _normalize_body(str(body or "")),
            }
    for name, expected in rendered.table_properties.items():
        if name not in live.tables:
            continue
        quoted = ".".join(f"`{part}`" for part in name.split("."))
        rows = _rows(workspace, warehouse_id, f"SHOW TBLPROPERTIES {quoted}", {})
        actual = {str(row[0]): str(row[1]) for row in rows if len(row) >= 2}
        live.table_properties[name] = {key: actual.get(key, "") for key in expected}
    live.schemas &= rendered.schemas
    return live


def _short(name: str) -> str:
    return ".".join(name.split(".")[1:]) or name


def decide(rendered: Contract, live: Contract | None, *, live_error: str = "") -> dict[str, Any]:
    """The skip decision and its EXECUTE-gap prediction; names are ``<schema>.<object>``."""

    differing: dict[str, list[str]] = {}
    if live is not None:
        mine, theirs = rendered.canonical(), live.canonical()
        for kind in ("catalogs", "schemas"):
            differing[kind] = sorted(set(mine[kind]) ^ set(theirs[kind]))
        for kind in ("tables", "table_properties", "functions"):
            differing[kind] = sorted(
                _short(name)
                for name in set(mine[kind]) | set(theirs[kind])
                if mine[kind].get(name) != theirs[kind].get(name)
            )
        differing["catalogs"] = [str(len(differing["catalogs"]))] if differing["catalogs"] else []
        differing["schemas"] = [_short(name) for name in differing["schemas"]]
    skip = live is not None and not rendered.unknown and not any(differing.values())
    functions = sorted(_short(name) for name in rendered.functions)
    return {
        "schema": "mip.catalog-bootstrap-skip-rule.v1",
        "decision": "skip" if skip else "run",
        "code": "skip" if skip else "gap",
        "rendered_sha256": rendered.sha256(),
        "live_sha256": "" if live is None else live.sha256(),
        "live_error": live_error,
        "unknown_statements": sorted(set(rendered.unknown)),
        "differing": differing,
        "functions_republished": [] if skip else functions,
        "execute_gap": {
            "starts_at": f"{BOOTSTRAP_JOB} function publish tasks",
            "ends_at": "reconcile_reviewed_function_execute_grants",
            "predicted_seconds": 0 if skip else sum(EXECUTE_GAP_BUDGET_S.values()),
        },
    }


def main(argv: Sequence[str] | None = None, *, workspace_factory: Any = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check")
    check.add_argument("--warehouse-id", required=True)
    check.add_argument("--bundle", type=Path, default=Path("databricks.yml"))
    check.add_argument("--repo-root", type=Path, default=Path("."))
    check.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    rendered = rendered_contract(bootstrap_files(args.bundle, args.repo_root))
    live: Contract | None = None
    error = ""
    try:
        if workspace_factory is None:
            from tools.databricks.workspace_auth import deployment_workspace_client

            workspace_factory = deployment_workspace_client
        live = live_contract(workspace_factory(), warehouse_id=args.warehouse_id, rendered=rendered)
    except Exception as exc:  # noqa: BLE001 -- any unreadable live fact can only mean "run"
        error = type(exc).__name__
    report = decide(rendered, live, live_error=error)
    descriptor = os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, sort_keys=True)
        handle.write("\n")
    counts = {kind: len(names) for kind, names in report["differing"].items()}
    print(
        f"[mip-catalog-bootstrap] {report['decision']}: rendered {report['rendered_sha256'][:12]}, "
        f"live {report['live_sha256'][:12] or 'unread'}; differing {json.dumps(counts, sort_keys=True)}; "
        f"{len(report['unknown_statements'])} unknown statement kinds"
        + (f"; live read failed ({error})" if error else "")
    )
    if report["decision"] == "run":
        gap = report["execute_gap"]
        print(
            f"[mip-catalog-bootstrap] predicted EXECUTE gap: {len(report['functions_republished'])} "
            f"republished functions, from {gap['starts_at']} to {gap['ends_at']}, "
            f"at most {gap['predicted_seconds']} s",
            file=sys.stderr,
        )
    return CHECK_EXACT if report["decision"] == "skip" else CHECK_DRIFT


if __name__ == "__main__":
    raise SystemExit(main())

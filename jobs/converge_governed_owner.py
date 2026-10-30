"""Hand what a job run created in the governed schemas to the data-owner group (M2-PR5).

The ``converge_governed_owner`` task ends every App-triggerable job that creates
or replaces governed Unity Catalog objects (mip_refresh_silver,
mip_refresh_scores, mip_fred_rates_ingest; databricks.yml). Those jobs run as
the non-admin jobs identity, so whatever they create or replace is owned by it;
this task moves exactly those objects to the install's data-owner group
(``<MIP_IDENTITY_PREFIX>-data-owners``, plan section 4.3), which the deploy-time
owner policy (tools/databricks/uc_owner_policy.ApprovedOwnerPolicy) approves.

What it may touch, and nothing else:

* the pinned catalog only (jobs/install_pins.json; ``--catalog`` must equal it),
  and only its governed schemas, :data:`GOVERNED_SCHEMAS`, the schema segments
  tools/render_sql.py governs (tests/unit/test_converge_governed_owner.py pins
  the two equal). Never the catalog, a schema itself, or another catalog;
* only tables, views and functions whose owner is this run's own identity
  (``current_user()``). An object anyone else owns (the deployer, which owns
  the reviewed Growth Agent functions under MIP_REVIEWED_FUNCTION_OWNER;
  another team; a person) is never touched;
* the pinned group as the new owner, never a parameter.

Pipeline-managed datasets (``MATERIALIZED_VIEW``, ``STREAMING_TABLE``) stay with
the pipeline, whose run_as owns them; their handover is spike S4's and M2-PR8's.
Any other table type the identity owns refuses before any ALTER.

Exit codes: 0 converged (or nothing to hand over); 2 an unexpected inventory
row, nothing altered; 3 an ALTER failed (the others were attempted); 4 no Spark
session; 5 the install pins refused the run, before any SQL.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

PROG = "converge_governed_owner"
GOVERNED_SCHEMAS = ("gold", "silver", "ref", "semantics", "raw", "first_party", "audit", "app")
# information_schema.tables.table_type -> the ALTER statement's object keyword.
TABLE_KINDS = {"MANAGED": "TABLE", "EXTERNAL": "TABLE", "VIEW": "VIEW", "METRIC_VIEW": "VIEW"}
PIPELINE_KINDS = frozenset({"MATERIALIZED_VIEW", "STREAMING_TABLE"})

Execute = Callable[[str], list[dict[str, Any]]]


def _quote(identifier: str) -> str:
    return "`" + identifier.replace("`", "``") + "`"


def _in_list() -> str:
    return ", ".join(f"'{schema}'" for schema in GOVERNED_SCHEMAS)


def inventory_statements(catalog: str) -> tuple[str, str]:
    """The two read-only inventory queries, scoped to the governed schemas of ``catalog``."""

    quoted = _quote(catalog)
    return (
        "SELECT table_catalog, table_schema, table_name, table_type, table_owner "
        f"FROM {quoted}.information_schema.tables WHERE table_schema IN ({_in_list()})",
        "SELECT routine_catalog, routine_schema, routine_name, routine_owner "
        f"FROM {quoted}.information_schema.routines WHERE routine_schema IN ({_in_list()})",
    )


class InventoryRefused(RuntimeError):
    pass


def plan(
    execute: Execute,
    *,
    catalog: str,
    owner_group: str,
) -> tuple[list[str], int]:
    """(the ALTER OWNER statements, the pipeline datasets left alone); read-only."""

    identity_rows = execute("SELECT current_user() AS identity")
    if len(identity_rows) != 1 or not str(identity_rows[0].get("identity") or "").strip():
        raise InventoryRefused("current_user() did not return exactly one identity")
    identity = str(identity_rows[0]["identity"]).strip().casefold()
    tables, routines = inventory_statements(catalog)
    statements: list[str] = []
    pipeline_datasets = 0
    candidates: list[tuple[str, str, str, str]] = []
    for row in execute(tables):
        candidates.append(
            (str(row.get("table_catalog")), str(row.get("table_schema")), str(row.get("table_name")),
             f"table:{row.get('table_type')}:{row.get('table_owner')}")
        )
    for row in execute(routines):
        candidates.append(
            (str(row.get("routine_catalog")), str(row.get("routine_schema")), str(row.get("routine_name")),
             f"function:FUNCTION:{row.get('routine_owner')}")
        )
    group = _quote(owner_group)
    for row_catalog, schema, name, described in candidates:
        source, kind, owner = described.split(":", 2)
        if row_catalog != catalog or schema not in GOVERNED_SCHEMAS:
            raise InventoryRefused(f"the {source} inventory returned a row outside the governed schemas")
        if owner.strip().casefold() != identity:
            continue
        fqn = ".".join(_quote(part) for part in (catalog, schema, name))
        if source == "function":
            statements.append(f"ALTER FUNCTION {fqn} OWNER TO {group}")
        elif kind in PIPELINE_KINDS:
            pipeline_datasets += 1
        elif kind in TABLE_KINDS:
            statements.append(f"ALTER {TABLE_KINDS[kind]} {fqn} OWNER TO {group}")
        else:
            raise InventoryRefused(f"the identity owns a {kind} object this task has no reviewed ALTER for")
    return sorted(statements), pipeline_datasets


def converge(execute: Execute, *, catalog: str, owner_group: str) -> int:
    try:
        statements, pipeline_datasets = plan(execute, catalog=catalog, owner_group=owner_group)
    except InventoryRefused as exc:
        print(f"[{PROG}] REFUSED: {exc}; nothing was altered", file=sys.stderr)
        return 2
    failed = 0
    for statement in statements:
        try:
            execute(statement)
        except Exception as exc:  # noqa: BLE001 - counted; every other ALTER still runs
            failed += 1
            print(f"[{PROG}] an ALTER OWNER failed ({type(exc).__name__})", file=sys.stderr)
    print(
        f"[{PROG}] handed {len(statements) - failed} of {len(statements)} governed object(s) to the "
        f"data-owner group; {pipeline_datasets} pipeline dataset(s) left with the pipeline"
    )
    return 3 if failed else 0


def _get_spark() -> Any:
    try:
        from pyspark.sql import SparkSession
    except ImportError:
        print(f"[{PROG}] pyspark unavailable; must run on Databricks.", file=sys.stderr)
        sys.exit(4)
    spark = SparkSession.getActiveSession() or SparkSession.builder.getOrCreate()
    if spark is None:
        print(f"[{PROG}] no active SparkSession.", file=sys.stderr)
        sys.exit(4)
    return spark


def _spark_executor(spark: Any) -> Execute:
    def execute(statement: str) -> list[dict[str, Any]]:
        result = spark.sql(statement)
        if result is None:
            return []
        return [row.asDict(recursive=True) for row in result.collect()]

    return execute


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=PROG, description=__doc__.splitlines()[0])
    parser.add_argument("--catalog", required=True, help="the governed catalog; must equal the install pin")
    return parser


def main(argv: list[str] | None = None, *, owner_group: str) -> int:
    args = build_parser().parse_args(argv)
    return converge(_spark_executor(_get_spark()), catalog=args.catalog, owner_group=owner_group)


def _install_pins() -> ModuleType:
    """jobs/_install_pins.py from this script's own folder, by exact path (M2-PR5).

    Never a search and never ``sys.path``: Databricks' exec() path can drop
    ``__file__``, and its working directory is then this folder or the bundle
    files root above it. Anything else refuses (exit 5) before any SQL.
    """
    try:
        folder = Path(__file__).resolve().parent
    except NameError:
        cwd = Path.cwd()
        folder = cwd if (cwd / "converge_governed_owner.py").is_file() else cwd / "jobs"
    source = folder / "_install_pins.py"
    spec = importlib.util.spec_from_file_location("mip_install_pins", source)
    if spec is None or spec.loader is None or not source.is_file():
        print("[install-pins] converge_governed_owner REFUSED: jobs/_install_pins.py is not beside this job", file=sys.stderr)
        sys.exit(5)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(argv: list[str] | None = None) -> int:
    """The job's entrypoint: the install pins before any SQL, then :func:`main` with the pinned group."""

    pins = _install_pins().enforce(build_parser(), argv, expected=lambda pins: {"catalog": pins.catalog})
    return main(argv, owner_group=pins.data_owner_group)


if __name__ == "__main__":
    # Only a non-zero return raises: the Databricks exec wrapper fails a task on
    # any SystemExit, SystemExit(0) included (jobs/fred_rates_ingest.py).
    _rc = run()
    if _rc != 0:
        sys.exit(_rc)

#!/usr/bin/env python3
"""Publish the App's least-privilege observed-lineage view (optional evidence).

``<catalog>.audit.asset_lineage`` (``sql/ddl/audit_asset_lineage_view.sql``)
projects four columns of ``system.access.table_lineage`` for the edges that
touch the install catalog. Unity Catalog evaluates a view with its owner's
privileges on what it reads, so the deploying identity that creates the view
must be able to read the system table; the App then needs ``SELECT`` on the
view only, never a grant on the metastore-wide system schema.

Exit status (``scripts/deploy.sh`` maps it, between the
``observed lineage view: publish`` markers):

* 0 -- the view is published and its four-column projection verified; the
  deploy grants the App ``SELECT`` on it next.
* 3 -- the deploying identity cannot read ``system.access.table_lineage``.
  Nothing is created, replaced or granted; the deploy warns, naming the
  missing privilege, and the App keeps its explicit "Observed lineage
  unavailable" gap. A view an earlier owner published stays as it was.
* 4 -- the probe passed but publishing or verifying the view failed (for
  example the deployer does not own a view another identity published).
  Fatal, like every other UC grant failure in the deploy.
* 2 -- invalid arguments.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Literal

from tools import render_sql

REPO = Path(__file__).resolve().parents[2]
VIEW_SQL = REPO / "sql" / "ddl" / "audit_asset_lineage_view.sql"
VIEW_COLUMNS = (
    "source_table_full_name",
    "target_table_full_name",
    "event_time",
    "event_date",
)
SYSTEM_TABLE = "system.access.table_lineage"
REQUIRED_PRIVILEGES = (
    "USE CATALOG on system, USE SCHEMA on system.access and SELECT on "
    "system.access.table_lineage (with the access system schema enabled)"
)
# Statement failures that mean the deployer cannot read the system table (no
# privilege, or the access system schema is not enabled on the metastore).
MISSING_ACCESS_MARKERS = (
    "INSUFFICIENT_PERMISSIONS",
    "PERMISSION_DENIED",
    "TABLE_OR_VIEW_NOT_FOUND",
    "SCHEMA_NOT_FOUND",
    "CATALOG_NOT_FOUND",
)
EXIT_PUBLISHED = 0
EXIT_SKIPPED = 3
EXIT_FAILED = 4

Outcome = Literal["published", "skipped"]
StatementRunner = Callable[[str], object]


class LineageViewError(RuntimeError):
    """The probe passed but the view could not be published or verified."""


def view_name(catalog: str) -> str:
    return f"{catalog}.audit.asset_lineage"


def probe_statement() -> str:
    """Read nothing, but resolve every column the view selects or filters on."""
    return (
        f"SELECT {', '.join(VIEW_COLUMNS)}, source_table_catalog, target_table_catalog "
        f"FROM {SYSTEM_TABLE} LIMIT 0"
    )


def view_statement(catalog: str, *, source: Path = VIEW_SQL) -> str:
    """The rendered CREATE OR REPLACE VIEW, comment lines and terminator removed."""
    rendered = render_sql.render_for_catalog(source.read_text(encoding="utf-8"), catalog=catalog)
    body = "\n".join(
        line for line in rendered.splitlines() if not line.lstrip().startswith("--")
    ).strip()
    if not body.endswith(";") or body.count(";") != 1:
        raise ValueError(f"{source.name} must hold exactly one ';'-terminated statement")
    statement = body[:-1].rstrip()
    if not statement.upper().startswith(f"CREATE OR REPLACE VIEW {view_name(catalog).upper()} "):
        raise ValueError(f"{source.name} must create {view_name(catalog)}")
    return statement


def missing_access_marker(error: BaseException) -> str | None:
    text = str(error).upper()
    return next((marker for marker in MISSING_ACCESS_MARKERS if marker in text), None)


def _rows(response: object) -> Sequence[Sequence[object]]:
    rows = getattr(getattr(response, "result", None), "data_array", None) or []
    if not isinstance(rows, Sequence) or isinstance(rows, str | bytes):
        raise LineageViewError("DESCRIBE TABLE returned an invalid result")
    return rows


def described_columns(response: object) -> tuple[str, ...]:
    """Column names from DESCRIBE TABLE, stopping at its first section break."""
    names: list[str] = []
    for row in _rows(response):
        name = str(row[0] if row else "").strip()
        if not name or name.startswith("#"):
            break
        names.append(name)
    return tuple(names)


def ensure_asset_lineage_view(run: StatementRunner, *, catalog: str) -> Outcome:
    """Probe, publish and verify the view; ``skipped`` when the probe is denied.

    ``run`` executes one statement and raises on any non-success.
    """
    statement = view_statement(catalog)
    try:
        run(probe_statement())
    except RuntimeError as exc:
        marker = missing_access_marker(exc)
        if marker is None:
            raise LineageViewError(f"{SYSTEM_TABLE} probe failed: {exc}") from exc
        print(
            f"  skipped: the deploying identity cannot read {SYSTEM_TABLE} ({marker}); "
            f"{view_name(catalog)} needs an owner with {REQUIRED_PRIVILEGES}.",
            file=sys.stderr,
        )
        return "skipped"
    try:
        run(statement)
        columns = described_columns(run(f"DESCRIBE TABLE {view_name(catalog)}"))
    except RuntimeError as exc:
        raise LineageViewError(f"{view_name(catalog)} could not be published: {exc}") from exc
    if columns != VIEW_COLUMNS:
        raise LineageViewError(
            f"{view_name(catalog)} exposes {list(columns)}, expected exactly {list(VIEW_COLUMNS)}"
        )
    print(
        f"  published: {view_name(catalog)} ({len(columns)} columns, edges touching "
        f"catalog {catalog}), evaluated with its owner's {SYSTEM_TABLE} access"
    )
    return "published"


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--warehouse-id", required=True)
    parser.add_argument("--catalog", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    warehouse_id = args.warehouse_id.strip()
    catalog = args.catalog.strip()
    if not warehouse_id or render_sql._UC_IDENTIFIER.fullmatch(catalog) is None:
        print("ensure_asset_lineage_view: invalid --warehouse-id or --catalog", file=sys.stderr)
        return 2

    from tools.databricks.ensure_campaign_treatment_table import execute_sql  # noqa: PLC0415
    from tools.databricks.workspace_auth import deployment_workspace_client  # noqa: PLC0415

    workspace = deployment_workspace_client()

    def run(statement: str) -> object:
        return execute_sql(workspace, warehouse_id=warehouse_id, statement=statement)

    try:
        outcome = ensure_asset_lineage_view(run, catalog=catalog)
    except LineageViewError as exc:
        print(f"ensure_asset_lineage_view: {exc}", file=sys.stderr)
        return EXIT_FAILED
    return EXIT_PUBLISHED if outcome == "published" else EXIT_SKIPPED


if __name__ == "__main__":
    sys.exit(main())

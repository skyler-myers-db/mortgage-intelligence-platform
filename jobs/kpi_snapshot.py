"""Snapshot the S1 headline KPIs into Lakebase ``mip_app.kpi_snapshots``.

Runs as a Databricks Jobs Spark-Python task (``mip_kpi_snapshot`` in
``databricks.yml``). Aggregates the S1 headline measures over
``<catalog>.semantics.portfolio_headline_metric_view`` -- the named
semantic home for every demoed headline KPI -- and upserts ONE row per
day into ``mip_app.kpi_snapshots`` so the app can answer "what did the
headline numbers look like near a past timestamp" (S4's "since your
last login" deltas) without a warehouse time-travel query.

Idempotency contract:
    The write is ``INSERT ... ON CONFLICT (snapshot_date) DO UPDATE``.
    Re-running on the same day (scheduled run + deploy backfill, or a
    retry after a network flap) refreshes the day's row; it never
    duplicates. The deploy script (``./scripts/deploy.sh -t dev``) runs
    this job once after the gold refresh, which doubles as the
    first-install backfill -- a fresh deploy always leaves at least one
    snapshot row behind.

Measure parity:
    The aggregate SELECT below must stay in lockstep with the headline
    measures documented in sql/metric_views/portfolio_headline_metric_
    view.sql and consumed by backend/services/kpi_deltas.py
    (tests/unit/test_kpi_snapshot_contract.py pins the parity).

Auth model (identical to jobs/lakebase_migrate.py):
    On Databricks the task runs under the workspace identity. We fetch a
    short-lived Postgres credential via ``POST /api/2.0/database/
    credentials``. ``LAKEBASE_*`` env-var overrides are honored for
    local development.

Exit codes:
    0 -- snapshot upserted.
    2 -- psycopg / Postgres error (run mip_lakebase_migrate first if the
         kpi_snapshots table is missing).
    3 -- SDK / auth error resolving connection parameters.
    4 -- Spark session not available (not running on Databricks).
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from typing import Any, cast

_UC_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# The S1 headline aggregates. Column aliases match the mip_app.kpi_snapshots
# columns 1:1 so the upsert below is a straight dict pass-through.
_HEADLINE_AGGREGATE_SQL = """
SELECT
  COUNT(*)                                             AS marketable_population,
  COALESCE(SUM(CASE WHEN in_the_money THEN 1 ELSE 0 END), 0)
                                                       AS refi_economics_screen,
  COALESCE(SUM(CASE WHEN is_high_opportunity THEN 1 ELSE 0 END), 0)
                                                       AS high_opportunity,
  COALESCE(SUM(CASE WHEN offer_available THEN 1 ELSE 0 END), 0)
                                                       AS offers_available,
  COALESCE(SUM(CASE WHEN offer_recommended THEN 1 ELSE 0 END), 0)
                                                       AS offers_recommended,
  AVG(opportunity_score)                               AS avg_opportunity_score
FROM {metric_view}
"""

# Per-day upsert: snapshot_date/snapshot_at come from the Lakebase clock so
# a single authority orders visits vs snapshots. ON CONFLICT keys on the
# idx_kpi_snapshots_snapshot_date unique index declared in lakebase/schema.sql.
_UPSERT_SQL = """
INSERT INTO mip_app.kpi_snapshots (
    snapshot_date,
    snapshot_at,
    source_view,
    marketable_population,
    refi_economics_screen,
    high_opportunity,
    offers_available,
    offers_recommended,
    avg_opportunity_score
) VALUES (
    (now() AT TIME ZONE 'utc')::date,
    now(),
    'portfolio_headline_metric_view',
    %(marketable_population)s,
    %(refi_economics_screen)s,
    %(high_opportunity)s,
    %(offers_available)s,
    %(offers_recommended)s,
    %(avg_opportunity_score)s
)
ON CONFLICT (snapshot_date) DO UPDATE SET
    snapshot_at           = EXCLUDED.snapshot_at,
    source_view           = EXCLUDED.source_view,
    marketable_population = EXCLUDED.marketable_population,
    refi_economics_screen = EXCLUDED.refi_economics_screen,
    high_opportunity      = EXCLUDED.high_opportunity,
    offers_available      = EXCLUDED.offers_available,
    offers_recommended    = EXCLUDED.offers_recommended,
    avg_opportunity_score = EXCLUDED.avg_opportunity_score
RETURNING snapshot_id, snapshot_date, snapshot_at
"""


def _catalog_default() -> str:
    return (os.environ.get("MIP_DEFAULT_CATALOG") or "mip").strip() or "mip"


def _quote_uc_identifier(value: str, *, field: str) -> str:
    """Return a backtick-quoted Unity Catalog identifier, failing closed."""
    normalized = value.strip()
    if not _UC_IDENTIFIER_RE.fullmatch(normalized):
        print(
            f"[kpi-snapshot] invalid {field} identifier {value!r}; "
            "expected letters, digits, and underscores.",
            file=sys.stderr,
        )
        sys.exit(3)
    return f"`{normalized}`"


def _resolve_connection(
    *,
    instance_name: str | None = None,
    database_name: str | None = None,
) -> dict:
    """Return psycopg.connect kwargs. Mirrors jobs/lakebase_migrate.py."""
    instance_name = instance_name or os.environ.get("LAKEBASE_INSTANCE_NAME", "mip-app-state")
    database_name = database_name or os.environ.get("LAKEBASE_DATABASE", "mip_app_state")
    host = os.environ.get("LAKEBASE_HOST")
    user = os.environ.get("LAKEBASE_USER")
    password = os.environ.get("LAKEBASE_PASSWORD")

    if host and user and password:
        return {
            "host": host,
            "port": int(os.environ.get("LAKEBASE_PORT", "5432")),
            "dbname": database_name,
            "user": user,
            "password": password,
            "sslmode": os.environ.get("LAKEBASE_SSLMODE", "require"),
        }

    try:
        from databricks.sdk import WorkspaceClient
    except ImportError:
        print(
            "[kpi-snapshot] databricks-sdk is not installed; install it or "
            "set LAKEBASE_* env vars explicitly.",
            file=sys.stderr,
        )
        sys.exit(3)

    try:
        w = WorkspaceClient()
        me = w.current_user.me()
        identity = me.user_name or me.display_name
        if not identity:
            print(
                "[kpi-snapshot] could not resolve workspace identity.",
                file=sys.stderr,
            )
            sys.exit(3)

        inst = cast(
            dict[str, Any],
            w.api_client.do("GET", f"/api/2.0/database/instances/{instance_name}"),
        )
        resolved_host = host or inst.get("read_write_dns")
        if not resolved_host:
            print(
                f"[kpi-snapshot] instance {instance_name!r} has no "
                f"read_write_dns; check provisioning state.",
                file=sys.stderr,
            )
            sys.exit(3)

        cred = cast(
            dict[str, Any],
            w.api_client.do(
                "POST",
                "/api/2.0/database/credentials",
                body={
                    "request_id": (
                        f"mip-kpi-snapshot-"
                        f"{os.environ.get('DATABRICKS_JOB_RUN_ID','local')}"
                    ),
                    "instance_names": [instance_name],
                },
            ),
        )
        cred_token = cred.get("token")
        if not cred_token:
            print(
                f"[kpi-snapshot] credential response missing token: {cred}",
                file=sys.stderr,
            )
            sys.exit(3)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 -- operator-facing
        print(f"[kpi-snapshot] SDK auth/resolution failed: {exc}", file=sys.stderr)
        sys.exit(3)

    return {
        "host": resolved_host,
        "port": int(os.environ.get("LAKEBASE_PORT", "5432")),
        "dbname": database_name,
        "user": user or identity,
        "password": password or cred_token,
        "sslmode": os.environ.get("LAKEBASE_SSLMODE", "require"),
    }


def _get_spark() -> Any:
    """Return the active SparkSession. Exits 4 when unavailable."""
    try:
        from pyspark.sql import SparkSession
    except ImportError:
        print("[kpi-snapshot] pyspark unavailable; must run on Databricks.", file=sys.stderr)
        sys.exit(4)
    spark = SparkSession.getActiveSession() or SparkSession.builder.getOrCreate()
    if spark is None:
        print("[kpi-snapshot] no active SparkSession.", file=sys.stderr)
        sys.exit(4)
    return spark


def _read_headline_aggregates(catalog: str) -> dict[str, Any]:
    """Aggregate the headline measures from the S1 metric view (real UC read)."""
    spark = _get_spark()
    metric_view = ".".join(
        [
            _quote_uc_identifier(catalog, field="catalog"),
            _quote_uc_identifier("semantics", field="schema"),
            _quote_uc_identifier("portfolio_headline_metric_view", field="view"),
        ]
    )
    row = spark.sql(_HEADLINE_AGGREGATE_SQL.format(metric_view=metric_view)).collect()[0]
    aggregates = row.asDict()
    return {
        "marketable_population": int(aggregates["marketable_population"] or 0),
        "refi_economics_screen": int(aggregates["refi_economics_screen"] or 0),
        "high_opportunity": int(aggregates["high_opportunity"] or 0),
        "offers_available": int(aggregates["offers_available"] or 0),
        "offers_recommended": int(aggregates["offers_recommended"] or 0),
        "avg_opportunity_score": (
            float(aggregates["avg_opportunity_score"])
            if aggregates["avg_opportunity_score"] is not None
            else None
        ),
    }


def _upsert_snapshot(conn_kwargs: dict, aggregates: dict[str, Any]) -> None:
    import psycopg

    try:
        with psycopg.connect(**conn_kwargs, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(_UPSERT_SQL, aggregates)
            written = cur.fetchone()
    except psycopg.errors.UndefinedTable:
        print(
            "[kpi-snapshot] mip_app.kpi_snapshots does not exist; run the "
            "mip_lakebase_migrate job (deploy step 4b) first.",
            file=sys.stderr,
        )
        sys.exit(2)
    except psycopg.Error as exc:
        print(f"[kpi-snapshot] Lakebase upsert failed: {exc}", file=sys.stderr)
        sys.exit(2)
    print(f"[kpi-snapshot] upserted snapshot {written}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kpi_snapshot",
        description=(
            "Upsert today's S1 headline KPI aggregates from "
            "semantics.portfolio_headline_metric_view into "
            "mip_app.kpi_snapshots."
        ),
    )
    parser.add_argument(
        "--catalog",
        default=_catalog_default(),
        help=(
            "Unity Catalog catalog owning the semantics schema "
            "(default: MIP_DEFAULT_CATALOG or mip)."
        ),
    )
    parser.add_argument(
        "--lakebase-instance",
        default=os.environ.get("LAKEBASE_INSTANCE_NAME", "mip-app-state"),
    )
    parser.add_argument(
        "--lakebase-database",
        default=os.environ.get("LAKEBASE_DATABASE", "mip_app_state"),
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    aggregates = _read_headline_aggregates(args.catalog)
    print(
        "[kpi-snapshot] headline aggregates: "
        + ", ".join(f"{k}={v}" for k, v in aggregates.items())
    )
    conn_kwargs = _resolve_connection(
        instance_name=args.lakebase_instance,
        database_name=args.lakebase_database,
    )
    _upsert_snapshot(conn_kwargs, aggregates)


if __name__ == "__main__":
    main()

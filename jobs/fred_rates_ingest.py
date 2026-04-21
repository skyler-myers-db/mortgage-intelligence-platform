"""FRED MORTGAGE30US ingest for mip.silver.market_rates_weekly.

This file is executed as a Databricks Jobs Python task in three modes, each
a separate task in the `mip_fred_rates_ingest` bundle job:

    --mode=init     Runs the DDL bootstrap (catalog/schemas + silver table).
                    Declared as a SQL task in the bundle for idempotency, but
                    we keep an `init` Python branch here so the script is
                    self-sufficient when run locally.

    --mode=seed     Loads data/seeds/fred_mortgage30us_seed.csv INTO
                    mip.silver.market_rates_weekly IF the table is
                    empty. This is the "first boot works offline" guarantee
                    -- the committed seed ships with the repo so
                    `databricks bundle deploy -t dev` lands a populated
                    silver table before the first FRED fetch runs.
                    Re-runs against a non-empty table are a no-op.

    --mode=fred     Pulls https://fred.stlouisfed.org/graph/fredgraph.csv?id=
                    MORTGAGE30US&cosd=2021-01-01 and MERGEs into the silver
                    table on (series_id, observation_week). Newly-ingested
                    rows are tagged source='fred'. Re-running the same day
                    is a no-op. On network failure, logs a WARNING and
                    exits success if there is already "sufficient history"
                    in the silver table (i.e. at least one row within the
                    last 21 days -- FRED's own lag is ~7 days); exits
                    failure only if the table has drifted stale.

In every mode, the script re-computes `is_latest` as a final MERGE step
so metric views that join on `is_latest = TRUE` always point at the most
recent observation_week per series_id.

Contract references:
    docs/data-contract-module0.md  -- §2.5 column spec + grain
    sql/ddl/silver_market_rates_weekly.sql -- Delta DDL
    sql/uc_functions/fn_rate_spread.sql -- fractional-rate consumer

Runtime assumptions:
    - Databricks Runtime with PySpark + `spark` session in scope.
    - For local `--dry-run` execution, only stdlib + urllib.request is used.
    - `--table` defaults to `mip.silver.market_rates_weekly`; override
      in a non-default catalog by passing `--table <catalog>.<schema>.<name>`.
    - `--seed-path` defaults to the repo-relative
      `data/seeds/fred_mortgage30us_seed.csv`.
"""

from __future__ import annotations

import argparse
import csv
import io
import logging
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

FRED_CSV_URL = (
    "https://fred.stlouisfed.org/graph/fredgraph.csv"
    "?id=MORTGAGE30US&cosd=2021-01-01"
)
DEFAULT_TABLE = "mip.silver.market_rates_weekly"
DEFAULT_SEED_PATH = "data/seeds/fred_mortgage30us_seed.csv"
DEFAULT_SERIES = "MORTGAGE30US"
STALENESS_DAYS = 21  # FRED publishes weekly; two missed weeks -> fail loud.

log = logging.getLogger("fred_rates_ingest")


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RateRow:
    """One row of the target silver table, pre-MERGE."""

    series_id: str
    observation_week: date  # Monday of the week (see `to_week_monday`).
    rate_pct: float
    rate_fraction: float
    source: str

    def as_tuple(self) -> tuple[str, date, float, float, str]:
        return (
            self.series_id,
            self.observation_week,
            self.rate_pct,
            self.rate_fraction,
            self.source,
        )


# ---------------------------------------------------------------------------
# Pure helpers -- unit-testable, no Spark dependency
# ---------------------------------------------------------------------------


def to_week_monday(d: date) -> date:
    """Return the Monday of the ISO week containing `d`.

    FRED publishes MORTGAGE30US with a Thursday observation date, but the
    data contract (docs/data-contract-module0.md §2.5) names the grain
    "week-starting Monday". Normalizing here keeps the MERGE idempotent
    across any date-format drift upstream.
    """
    return d - timedelta(days=d.weekday())


def parse_fred_csv(text: str, series_id: str = DEFAULT_SERIES) -> list[RateRow]:
    """Parse the FRED fredgraph.csv endpoint payload into RateRows.

    Skips rows where the value column is missing or the FRED sentinel '.'.
    Computes rate_fraction = rate_pct / 100 with 6 decimal places of
    precision (matches the seed CSV format).
    """
    rows: list[RateRow] = []
    reader = csv.reader(io.StringIO(text))
    header = next(reader, None)
    if header is None or len(header) < 2 or header[0].lower() != "observation_date":
        raise ValueError(
            f"FRED CSV header unexpected: {header!r}. "
            "Expected ['observation_date', <series>]."
        )
    for raw in reader:
        if len(raw) < 2:
            continue
        obs_str, val = raw[0].strip(), raw[1].strip()
        if not val or val == ".":
            continue
        try:
            obs = datetime.strptime(obs_str, "%Y-%m-%d").date()
            rate_pct = float(val)
        except ValueError:
            log.warning("skipping malformed FRED row: %r", raw)
            continue
        rate_fraction = round(rate_pct / 100.0, 6)
        rows.append(
            RateRow(
                series_id=series_id,
                observation_week=to_week_monday(obs),
                rate_pct=rate_pct,
                rate_fraction=rate_fraction,
                source="fred",
            )
        )
    return rows


def parse_seed_csv(path: Path) -> list[RateRow]:
    """Parse the committed seed CSV at `path`.

    Seed CSV uses a human-friendly header
    (observation_date,series_id,rate_pct,rate_fraction,source) for easy
    review in PRs. The ingest normalizes observation_date -> observation_week
    on load just like parse_fred_csv, so the seed CSV does not need to be
    edited when the data contract flips grain names.
    """
    rows: list[RateRow] = []
    with path.open() as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None or "observation_date" not in reader.fieldnames:
            raise ValueError(f"seed CSV header malformed: {reader.fieldnames!r}")
        for rec in reader:
            obs = datetime.strptime(rec["observation_date"], "%Y-%m-%d").date()
            rows.append(
                RateRow(
                    series_id=rec["series_id"],
                    observation_week=to_week_monday(obs),
                    rate_pct=float(rec["rate_pct"]),
                    rate_fraction=float(rec["rate_fraction"]),
                    source=rec.get("source", "seed") or "seed",
                )
            )
    return rows


def fetch_fred_csv(url: str = FRED_CSV_URL, timeout: int = 30) -> str:
    """Fetch the FRED graph CSV. Raises urllib.error.URLError on network failure."""
    req = urllib.request.Request(url, headers={"User-Agent": "mip-ingest/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return resp.read().decode("utf-8")


# ---------------------------------------------------------------------------
# Spark-backed helpers -- only reachable when running on Databricks
# ---------------------------------------------------------------------------


def _get_spark():  # pragma: no cover - Spark is a runtime dependency
    """Return the active SparkSession; raise if not on Databricks.

    We import pyspark lazily so `--dry-run` and `python -c "import ..."`
    work from a plain Python environment with no pyspark installed.
    """
    try:
        from pyspark.sql import SparkSession
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "pyspark is not available. This script runs on Databricks; "
            "use --dry-run for local parsing smoke tests."
        ) from exc
    spark = SparkSession.getActiveSession() or SparkSession.builder.getOrCreate()
    return spark


def _table_row_count(spark, table: str) -> int:  # pragma: no cover
    return spark.sql(f"SELECT COUNT(*) AS n FROM {table}").first()["n"]


def _table_latest_week(spark, table: str) -> date | None:  # pragma: no cover
    row = spark.sql(
        f"SELECT MAX(observation_week) AS w FROM {table}"
    ).first()
    return row["w"] if row and row["w"] else None


def _rows_to_df(spark, rows: Iterable[RateRow], batch_id: str):  # pragma: no cover
    """Build a Spark DataFrame shaped like the silver table."""
    from pyspark.sql.types import (
        BooleanType,
        DateType,
        DoubleType,
        StringType,
        StructField,
        StructType,
        TimestampType,
    )

    schema = StructType(
        [
            StructField("series_id", StringType(), False),
            StructField("observation_week", DateType(), False),
            StructField("rate_pct", DoubleType(), False),
            StructField("rate_fraction", DoubleType(), False),
            StructField("vintage_ts", TimestampType(), False),
            StructField("is_latest", BooleanType(), False),
            StructField("source", StringType(), False),
            StructField("_meta_batch_id", StringType(), True),
        ]
    )
    now = datetime.now(tz=UTC).replace(tzinfo=None)
    materialized = [
        (
            r.series_id,
            r.observation_week,
            r.rate_pct,
            r.rate_fraction,
            now,
            False,  # is_latest recomputed after MERGE
            r.source,
            batch_id,
        )
        for r in rows
    ]
    return spark.createDataFrame(materialized, schema=schema)


def _merge_rows(spark, table: str, rows: list[RateRow], batch_id: str) -> tuple[int, int]:  # pragma: no cover
    """MERGE rows into `table` on (series_id, observation_week). Returns (inserted, updated) counts."""
    if not rows:
        return 0, 0

    staging = f"_staging_market_rates_{batch_id.replace('-', '_')}"
    df = _rows_to_df(spark, rows, batch_id)
    df.createOrReplaceTempView(staging)

    before = _table_row_count(spark, table)

    spark.sql(
        f"""
        MERGE INTO {table} AS t
        USING {staging} AS s
          ON t.series_id = s.series_id
         AND t.observation_week = s.observation_week
        WHEN MATCHED AND (
              t.rate_pct != s.rate_pct
           OR t.rate_fraction != s.rate_fraction
           OR t.source != s.source
        ) THEN UPDATE SET
            rate_pct = s.rate_pct,
            rate_fraction = s.rate_fraction,
            vintage_ts = s.vintage_ts,
            source = s.source,
            _meta_batch_id = s._meta_batch_id
        WHEN NOT MATCHED THEN INSERT (
            series_id, observation_week, rate_pct, rate_fraction,
            vintage_ts, is_latest, source, _meta_batch_id
        ) VALUES (
            s.series_id, s.observation_week, s.rate_pct, s.rate_fraction,
            s.vintage_ts, s.is_latest, s.source, s._meta_batch_id
        )
        """
    )

    after = _table_row_count(spark, table)
    inserted = max(0, after - before)
    # Updated count is harder to read back portably; for run summary we
    # approximate as (staged - inserted). A future slice can swap in the
    # Delta operationMetrics from `DESCRIBE HISTORY` if the number matters.
    updated = max(0, len(rows) - inserted)
    return inserted, updated


def _refresh_is_latest(spark, table: str) -> None:  # pragma: no cover
    """Recompute the is_latest flag across all rows in the silver table."""
    spark.sql(
        f"""
        MERGE INTO {table} AS t
        USING (
          SELECT series_id, observation_week,
                 row_number() OVER (
                   PARTITION BY series_id
                   ORDER BY observation_week DESC
                 ) = 1 AS is_latest_new
          FROM {table}
        ) AS s
          ON t.series_id = s.series_id
         AND t.observation_week = s.observation_week
        WHEN MATCHED AND t.is_latest != s.is_latest_new
          THEN UPDATE SET is_latest = s.is_latest_new
        """
    )


# ---------------------------------------------------------------------------
# Mode entrypoints
# ---------------------------------------------------------------------------


def run_init(table: str, dry_run: bool) -> int:
    """Run the DDL bootstrap. Mirrors the SQL task in the bundle."""
    repo_root = Path(__file__).resolve().parents[1]
    ddl_files = [
        repo_root / "sql" / "ddl" / "001_catalogs_schemas.sql",
        repo_root / "sql" / "ddl" / "silver_market_rates_weekly.sql",
    ]
    log.info("init mode: applying %d DDL file(s) to %s", len(ddl_files), table)
    if dry_run:
        for p in ddl_files:
            log.info("[dry-run] would apply %s", p)
        return 0
    spark = _get_spark()  # pragma: no cover
    for p in ddl_files:  # pragma: no cover
        sql_text = p.read_text()
        log.info("applying %s (%d bytes)", p.name, len(sql_text))
        for stmt in _split_sql_statements(sql_text):
            spark.sql(stmt)
    return 0


def _split_sql_statements(sql_text: str) -> list[str]:
    """Split a DDL file into individual statements for spark.sql().

    Databricks spark.sql() executes ONE statement per call. We strip SQL
    line-comments and split on semicolons; we do NOT try to parse nested
    CASE / BEGIN blocks because the DDL files in this slice are plain
    CREATE statements. If a future DDL file needs procedural blocks, swap
    this for a %run of the SQL file via notebook_task or a sql_task.
    """
    # Strip line comments.
    cleaned_lines = []
    for line in sql_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("--"):
            continue
        cleaned_lines.append(line)
    cleaned = "\n".join(cleaned_lines)
    return [s.strip() for s in cleaned.split(";") if s.strip()]


def run_seed(table: str, seed_path: Path, batch_id: str, dry_run: bool) -> int:
    """Load the seed CSV IF the table is empty. No-op otherwise."""
    rows = parse_seed_csv(seed_path)
    log.info("seed mode: parsed %d rows from %s", len(rows), seed_path)
    if not rows:
        log.error("seed CSV produced zero rows; refusing to load empty seed")
        return 2

    if dry_run:
        log.info("[dry-run] would check table is empty; if so insert %d seed rows", len(rows))
        log.info("[dry-run] first: %s", rows[0])
        log.info("[dry-run] last:  %s", rows[-1])
        return 0

    spark = _get_spark()  # pragma: no cover
    count = _table_row_count(spark, table)  # pragma: no cover
    if count > 0:  # pragma: no cover
        log.info("table %s already has %d rows; skipping seed", table, count)
        return 0
    inserted, updated = _merge_rows(spark, table, rows, batch_id)  # pragma: no cover
    _refresh_is_latest(spark, table)  # pragma: no cover
    log.info(  # pragma: no cover
        "seed mode: inserted=%d updated=%d latest=%s",
        inserted,
        updated,
        _table_latest_week(spark, table),
    )
    return 0  # pragma: no cover


def run_fred(table: str, batch_id: str, dry_run: bool, staleness_days: int) -> int:
    """Fetch FRED, MERGE into silver. Resilient to FRED outage if silver is fresh."""
    try:
        payload = fetch_fred_csv()
    except (urllib.error.URLError, TimeoutError) as exc:
        log.warning("FRED fetch failed: %s", exc)
        if dry_run:
            log.info("[dry-run] would check staleness and exit 0 or 1")
            return 0
        spark = _get_spark()  # pragma: no cover
        latest = _table_latest_week(spark, table)  # pragma: no cover
        cutoff = (datetime.now(tz=UTC).date() - timedelta(days=staleness_days))  # pragma: no cover
        if latest and latest >= cutoff:  # pragma: no cover
            log.warning(  # pragma: no cover
                "FRED unreachable but silver has %s (>= %s cutoff); exit success",
                latest,
                cutoff,
            )
            return 0  # pragma: no cover
        log.error(  # pragma: no cover
            "FRED unreachable AND silver latest (%s) older than %d days; failing",
            latest,
            staleness_days,
        )
        return 1  # pragma: no cover

    rows = parse_fred_csv(payload)
    log.info("fred mode: parsed %d rows from FRED", len(rows))
    if dry_run:
        log.info("[dry-run] would MERGE %d FRED rows into %s", len(rows), table)
        if rows:
            log.info("[dry-run] first: %s", rows[0])
            log.info("[dry-run] last:  %s", rows[-1])
        return 0
    if not rows:  # pragma: no cover
        log.error("FRED returned zero usable rows; not touching silver")
        return 1
    spark = _get_spark()  # pragma: no cover
    inserted, updated = _merge_rows(spark, table, rows, batch_id)  # pragma: no cover
    _refresh_is_latest(spark, table)  # pragma: no cover
    latest = _table_latest_week(spark, table)  # pragma: no cover
    log.info(  # pragma: no cover
        "fred mode: fetched=%d inserted=%d updated=%d latest=%s",
        len(rows),
        inserted,
        updated,
        latest,
    )
    return 0  # pragma: no cover


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="fred_rates_ingest",
        description="Ingest FRED MORTGAGE30US into mip.silver.market_rates_weekly.",
    )
    p.add_argument(
        "--mode",
        required=True,
        choices=("init", "seed", "fred"),
        help="init: run DDL; seed: load committed CSV if table empty; fred: pull + MERGE live.",
    )
    p.add_argument(
        "--table",
        default=DEFAULT_TABLE,
        help=f"Target table (default: {DEFAULT_TABLE}).",
    )
    p.add_argument(
        "--seed-path",
        default=None,
        help=f"Override seed CSV path (default: repo-relative {DEFAULT_SEED_PATH}).",
    )
    p.add_argument(
        "--staleness-days",
        type=int,
        default=STALENESS_DAYS,
        help="FRED-outage tolerance window (default: %(default)s days).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and validate inputs without writing to the silver table.",
    )
    p.add_argument(
        "--log-level",
        default="INFO",
        help="Logging level (default: INFO).",
    )
    return p


def _repo_root() -> Path:
    """Return the repo root.

    Tolerates Databricks ipykernel exec contexts where ``__file__`` is
    not defined (the exec() path strips the module's ``__file__`` attr).
    Fallback: search for ``data/seeds/fred_mortgage30us_seed.csv``
    starting at cwd.
    """
    try:
        return Path(__file__).resolve().parents[1]
    except NameError as exc:
        for base in (Path.cwd(), Path.cwd() / "..", Path("/Workspace/Users")):
            for probe in base.rglob("data/seeds/fred_mortgage30us_seed.csv"):
                return probe.parents[2]
        raise RuntimeError(
            "Cannot locate repo root — __file__ undefined and no data/"
            "seeds/fred_mortgage30us_seed.csv found under cwd."
        ) from exc


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    repo_root = _repo_root()
    seed_path = Path(args.seed_path) if args.seed_path else repo_root / DEFAULT_SEED_PATH
    batch_id = os.environ.get(
        "DATABRICKS_JOB_RUN_ID",
        f"local-{datetime.now(tz=UTC).strftime('%Y%m%dT%H%M%SZ')}",
    )

    if args.mode == "init":
        return run_init(args.table, args.dry_run)
    if args.mode == "seed":
        return run_seed(args.table, seed_path, batch_id, args.dry_run)
    if args.mode == "fred":
        return run_fred(args.table, batch_id, args.dry_run, args.staleness_days)
    # argparse choices guards this, but mypy/ruff appreciate the fallback.
    log.error("unknown mode: %s", args.mode)
    return 2


if __name__ == "__main__":
    # Only raise SystemExit on a non-zero return. Databricks ipykernel
    # exec wrappers treat *any* SystemExit (including SystemExit(0)) as
    # a task failure, so a successful run must fall through cleanly.
    _rc = main()
    if _rc != 0:
        sys.exit(_rc)

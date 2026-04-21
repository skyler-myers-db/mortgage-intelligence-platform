"""Unit tests for `jobs/fred_rates_ingest.py`.

Covers every pure helper + CLI dry-run path. Spark-backed helpers are
marked `# pragma: no cover` in the script itself; they're exercised
by the live Databricks job, not here.

The committed seed CSV at `data/seeds/fred_mortgage30us_seed.csv` is
also validated structurally — if the quarterly seed-refresh changes
the file shape, these tests fail loud before the bundle deploy does.
"""
from __future__ import annotations

import importlib
import sys
import urllib.error
from datetime import date
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
JOBS_DIR = REPO_ROOT / "jobs"
SEED_PATH = REPO_ROOT / "data" / "seeds" / "fred_mortgage30us_seed.csv"

# The `jobs/` directory isn't on `sys.path` by default; pytest discovers tests
# under tests/ but the job script lives outside the backend package.
if str(JOBS_DIR) not in sys.path:
    sys.path.insert(0, str(JOBS_DIR))

fred_ingest = importlib.import_module("fred_rates_ingest")


# ---------------------------------------------------------------------------
# to_week_monday — normalizes any weekday to the Monday of that ISO week
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("given", "expected_monday"),
    [
        (date(2026, 4, 20), date(2026, 4, 20)),  # Monday -> itself
        (date(2026, 4, 21), date(2026, 4, 20)),  # Tuesday
        (date(2026, 4, 22), date(2026, 4, 20)),  # Wednesday
        (date(2026, 4, 23), date(2026, 4, 20)),  # Thursday (FRED's publish day)
        (date(2026, 4, 24), date(2026, 4, 20)),  # Friday
        (date(2026, 4, 25), date(2026, 4, 20)),  # Saturday
        (date(2026, 4, 26), date(2026, 4, 20)),  # Sunday
        (date(2020, 1, 1), date(2019, 12, 30)),  # year boundary Wednesday
    ],
)
def test_to_week_monday(given: date, expected_monday: date) -> None:
    assert fred_ingest.to_week_monday(given) == expected_monday


# ---------------------------------------------------------------------------
# parse_fred_csv — FRED fredgraph.csv payload shape
# ---------------------------------------------------------------------------

def test_parse_fred_csv_valid_rows() -> None:
    payload = (
        "observation_date,MORTGAGE30US\n"
        "2026-04-09,6.370\n"
        "2026-04-16,6.300\n"
    )
    rows = fred_ingest.parse_fred_csv(payload)
    assert len(rows) == 2
    first = rows[0]
    assert first.series_id == "MORTGAGE30US"
    assert first.observation_week == date(2026, 4, 6)  # Monday of week of 2026-04-09
    assert first.rate_pct == pytest.approx(6.370)
    assert first.rate_fraction == pytest.approx(0.0637)
    assert first.source == "fred"


def test_parse_fred_csv_skips_sentinel_and_blank_values() -> None:
    payload = (
        "observation_date,MORTGAGE30US\n"
        "2026-04-02,.\n"            # FRED sentinel for missing
        "2026-04-09,\n"             # blank
        "2026-04-16,6.300\n"
    )
    rows = fred_ingest.parse_fred_csv(payload)
    assert len(rows) == 1
    assert rows[0].observation_week == date(2026, 4, 13)


def test_parse_fred_csv_rejects_bad_header() -> None:
    payload = "date,rate\n2026-04-16,6.300\n"
    with pytest.raises(ValueError, match="FRED CSV header unexpected"):
        fred_ingest.parse_fred_csv(payload)


def test_parse_fred_csv_skips_malformed_rows() -> None:
    payload = (
        "observation_date,MORTGAGE30US\n"
        "not-a-date,6.300\n"
        "2026-04-16,not-a-float\n"
        "2026-04-23,5.900\n"
    )
    rows = fred_ingest.parse_fred_csv(payload)
    assert len(rows) == 1
    assert rows[0].rate_pct == pytest.approx(5.900)


# ---------------------------------------------------------------------------
# parse_seed_csv — the committed bootstrap file
# ---------------------------------------------------------------------------

def test_seed_csv_exists_and_shape_valid() -> None:
    assert SEED_PATH.exists(), f"committed seed missing at {SEED_PATH}"
    rows = fred_ingest.parse_seed_csv(SEED_PATH)
    assert len(rows) >= 200, "seed should cover several years of weekly history"
    assert all(r.series_id == "MORTGAGE30US" for r in rows)
    assert all(r.source == "seed" for r in rows)


def test_seed_csv_rate_range_sane() -> None:
    rows = fred_ingest.parse_seed_csv(SEED_PATH)
    rates = [r.rate_pct for r in rows]
    assert min(rates) >= 2.0, "mortgage rates below 2% would be anomalous"
    assert max(rates) <= 10.0, "mortgage rates above 10% would be anomalous"


def test_seed_csv_weeks_strictly_ascending() -> None:
    rows = fred_ingest.parse_seed_csv(SEED_PATH)
    weeks = [r.observation_week for r in rows]
    assert weeks == sorted(weeks), "seed weeks must be chronological"
    assert len(set(weeks)) == len(weeks), "seed weeks must be unique"


def test_seed_csv_rate_fraction_matches_pct() -> None:
    rows = fred_ingest.parse_seed_csv(SEED_PATH)
    for r in rows:
        assert r.rate_fraction == pytest.approx(r.rate_pct / 100.0, abs=1e-6)


# ---------------------------------------------------------------------------
# _split_sql_statements — DDL bootstrap splitter
# ---------------------------------------------------------------------------

def test_split_sql_statements_strips_comments_and_splits() -> None:
    sql = (
        "-- header comment\n"
        "CREATE CATALOG IF NOT EXISTS mip;\n"
        "-- another comment\n"
        "CREATE SCHEMA IF NOT EXISTS mip.silver;\n"
    )
    stmts = fred_ingest._split_sql_statements(sql)
    assert len(stmts) == 2
    assert stmts[0].startswith("CREATE CATALOG")
    assert stmts[1].startswith("CREATE SCHEMA")


def test_split_sql_statements_ignores_trailing_whitespace() -> None:
    assert fred_ingest._split_sql_statements("   \n-- just comments\n  ") == []


def test_split_sql_statements_on_real_ddl_files() -> None:
    """Both bundled DDL files produce non-empty statement lists.

    The splitter is a naive comment-strip + semicolon-split — it does NOT
    understand string literals, so a COMMENT column that contains a ';'
    inside its quoted text will get broken across two pseudo-statements.
    This is fine for the booth-demo path because the DDL runs via
    Databricks `sql_task` (native parser), not through this splitter.
    `run_init` using this splitter is a dev-only escape hatch.

    Follow-up ticket: replace the splitter with a tiny quote-aware
    tokenizer if we ever need to rely on `run_init` in production.
    """
    for name in ("001_catalogs_schemas.sql", "silver_market_rates_weekly.sql"):
        path = REPO_ROOT / "sql" / "ddl" / name
        stmts = fred_ingest._split_sql_statements(path.read_text())
        assert stmts, f"{name} produced no statements"
        # At minimum, the first statement per file opens a top-level DDL.
        assert stmts[0].upper().startswith(("CREATE", "ALTER", "USE"))


# ---------------------------------------------------------------------------
# CLI dry-run smoke — every mode returns 0 without touching network or Spark
# ---------------------------------------------------------------------------

def test_cli_init_dry_run_returns_zero(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("INFO")
    rc = fred_ingest.main(["--mode=init", "--dry-run"])
    assert rc == 0
    assert any("would apply" in m for m in caplog.messages)


def test_cli_seed_dry_run_returns_zero(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("INFO")
    rc = fred_ingest.main(["--mode=seed", "--dry-run"])
    assert rc == 0
    assert any("parsed" in m and "rows from" in m for m in caplog.messages)


def test_cli_fred_dry_run_no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub fetch_fred_csv so the dry-run never touches the network."""
    stub = (
        "observation_date,MORTGAGE30US\n"
        "2026-04-16,6.300\n"
    )
    monkeypatch.setattr(fred_ingest, "fetch_fred_csv", lambda *a, **kw: stub)
    rc = fred_ingest.main(["--mode=fred", "--dry-run"])
    assert rc == 0


def test_cli_fred_dry_run_survives_network_outage(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """FRED unreachable + --dry-run should log warning and exit 0."""
    def raise_urlerror(*a: object, **kw: object) -> str:
        raise urllib.error.URLError("simulated FRED outage")

    monkeypatch.setattr(fred_ingest, "fetch_fred_csv", raise_urlerror)
    caplog.set_level("WARNING")
    rc = fred_ingest.main(["--mode=fred", "--dry-run"])
    assert rc == 0
    assert any("FRED fetch failed" in m for m in caplog.messages)


def test_cli_requires_mode() -> None:
    with pytest.raises(SystemExit):
        fred_ingest.main([])


def test_cli_rejects_unknown_mode() -> None:
    with pytest.raises(SystemExit):
        fred_ingest.main(["--mode=bogus"])

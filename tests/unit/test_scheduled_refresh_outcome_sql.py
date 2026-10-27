"""The mip_scheduled_refresh leaf gate (data-freshness design 2026-10-26, §5.2 B).

``sql/transformations/assert_scheduled_refresh_outcome.sql`` fails the weekly
chain when gold scored against a rate other than silver's ``is_latest``
MORTGAGE30US row (audit C1), or when that row is not a FRED print inside the
14-day window ``gold_source_readiness.sql`` uses for FRED ``live``. It must stay
SELECT-only: the orchestrator runs it unattended every week.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from tools import render_sql
from tools.databricks import verify_market_rate_alignment

ROOT = Path(__file__).resolve().parents[2]
GATE = ROOT / "sql" / "transformations" / "assert_scheduled_refresh_outcome.sql"
SOURCE_READINESS = ROOT / "sql" / "transformations" / "gold_source_readiness.sql"
RENDERED_PATH = "sql/_rendered/transformations/assert_scheduled_refresh_outcome.sql"
_WINDOW = re.compile(r"current_date\(\)\s*-\s*INTERVAL\s+(\d+)\s+DAYS", re.IGNORECASE)


def _code(sql: str) -> str:
    """The SQL without ``--`` comments or string literals (the error messages)."""

    without_comments = "\n".join(line.split("--", 1)[0] for line in sql.splitlines())
    return re.sub(r"'[^']*'", "''", without_comments)


def _gate() -> str:
    return GATE.read_text(encoding="utf-8")


def test_the_gate_is_select_only() -> None:
    code = _code(_gate())
    assert code.lstrip().upper().startswith("WITH "), "one WITH ... SELECT statement"
    assert code.count(";") == 1 and code.rstrip().endswith(";"), "exactly one statement"
    mutating = re.findall(
        r"\b(INSERT|UPDATE|DELETE|MERGE|CREATE|REPLACE|DROP|ALTER|GRANT|REVOKE|TRUNCATE|"
        r"OPTIMIZE|VACUUM|COPY|CALL|SET|USE|RESTORE|COMMENT|REFRESH|ANALYZE|CACHE|UNCACHE)\b",
        code,
        re.IGNORECASE,
    )
    assert mutating == [], mutating


def test_the_gate_reads_the_latest_mortgage30us_row_and_gold_borrower_360() -> None:
    code = _code(_gate())
    assert "FROM mip.silver.market_rates_weekly" in code
    assert re.search(r"WHERE series_id = ''\s+AND is_latest = TRUE", code), "series and is_latest filter"
    assert "series_id = 'MORTGAGE30US' AND is_latest = TRUE" in _gate()
    assert "FROM mip.gold.borrower_360" in code
    assert "MIN(market_rate_fraction)" in code and "MAX(market_rate_fraction)" in code
    assert "l.latest_rows <> 1" in code, "exactly one is_latest row"


def test_the_gate_uses_the_alignment_tolerance_on_both_ends() -> None:
    code = _code(_gate())
    assert verify_market_rate_alignment._TOLERANCE == 1e-9
    assert "ABS(g.max_rate_fraction - l.rate_fraction) > 1e-9" in code
    assert "ABS(g.min_rate_fraction - l.rate_fraction) > 1e-9" in code


def test_the_fred_window_equals_the_source_readiness_live_window() -> None:
    gate_windows = set(_WINDOW.findall(_code(_gate())))
    readiness_windows = set(_WINDOW.findall(_code(SOURCE_READINESS.read_text(encoding="utf-8"))))
    assert gate_windows == {"14"}
    assert readiness_windows == gate_windows, "gold_source_readiness marks FRED live within the same window"
    assert "COALESCE(l.source, '') <> 'fred'" in _gate()


def test_every_branch_raises_and_nulls_fail_closed() -> None:
    gate = _gate()
    code = _code(gate)
    assert code.count("RAISE_ERROR(") == 3
    assert re.search(r"ELSE 1\s+END AS ok", code)
    for null_guard in (
        "l.rate_fraction IS NULL",
        "g.min_rate_fraction IS NULL",
        "g.max_rate_fraction IS NULL",
        "l.observation_week IS NULL",
    ):
        assert null_guard in code, null_guard
    assert "SELECT rate_fraction FROM" not in code, "no scalar subquery: a duplicate row must reach the named error"


def test_the_orchestrator_runs_the_rendered_gate_and_the_renderer_emits_it(tmp_path: Path) -> None:
    bundle = yaml.safe_load((ROOT / "databricks.yml").read_text(encoding="utf-8"))
    task = next(
        task
        for task in bundle["resources"]["jobs"]["mip_scheduled_refresh"]["tasks"]
        if task["task_key"] == "assert_refresh_outcome"
    )
    assert task["sql_task"]["file"]["path"] == RENDERED_PATH
    assert task["sql_task"]["warehouse_id"] == "${resources.sql_warehouses.mip_serverless_sql.id}"

    out = tmp_path / "rendered"
    render_sql.render(catalog="lender_uc", source_root=ROOT / "sql", dest_root=out, demo_first_party_enabled=False)
    rendered = (out / "transformations" / GATE.name).read_text(encoding="utf-8")
    assert "lender_uc.silver.market_rates_weekly" in rendered
    assert "lender_uc.gold.borrower_360" in rendered
    assert "mip.silver." not in rendered and "mip.gold." not in rendered

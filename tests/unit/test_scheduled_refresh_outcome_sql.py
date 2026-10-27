"""The mip_scheduled_refresh leaf gate (data-freshness design 2026-10-26, §5.2 B).

``sql/transformations/assert_scheduled_refresh_outcome.sql`` fails the weekly
chain when gold scored against a rate other than silver's ``is_latest``
MORTGAGE30US row (audit C1), or when that row is not a FRED print inside the
14-day window ``gold_source_readiness.sql`` uses for FRED ``live``. It must stay
SELECT-only: the orchestrator runs it unattended every week.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable
from pathlib import Path

import pytest
import yaml

from tools import render_sql
from tools.databricks import verify_market_rate_alignment

ROOT = Path(__file__).resolve().parents[2]
GATE = ROOT / "sql" / "transformations" / "assert_scheduled_refresh_outcome.sql"
SOURCE_READINESS = ROOT / "sql" / "transformations" / "gold_source_readiness.sql"
RENDERED_PATH = "sql/_rendered/transformations/assert_scheduled_refresh_outcome.sql"
_WINDOW = re.compile(r"current_date\(\)\s*-\s*INTERVAL\s+(\d+)\s+DAYS", re.IGNORECASE)
_RAISE = re.compile(r"RAISE_ERROR\('[^']*'\)")

#: The whole gate, comments stripped, whitespace collapsed and each RAISE_ERROR
#: message replaced by <message>. Every predicate, connective, operator and
#: tolerance is load-bearing: the orchestrator runs this unattended every week,
#: so a deliberate change to the gate must change this pin too.
PINNED_STATEMENT = (
    "WITH latest_rate AS ( SELECT COUNT(*) AS latest_rows, MAX(rate_fraction) AS rate_fraction, "
    "MAX(observation_week) AS observation_week, MAX(source) AS source "
    "FROM mip.silver.market_rates_weekly WHERE series_id = 'MORTGAGE30US' AND is_latest = TRUE ), "
    "gold_rate AS ( SELECT MIN(market_rate_fraction) AS min_rate_fraction, "
    "MAX(market_rate_fraction) AS max_rate_fraction FROM mip.gold.borrower_360 ) "
    "SELECT CASE "
    "WHEN l.latest_rows <> 1 THEN <message> "
    "WHEN l.rate_fraction IS NULL OR g.min_rate_fraction IS NULL OR g.max_rate_fraction IS NULL "
    "OR ABS(g.max_rate_fraction - l.rate_fraction) > 1e-9 "
    "OR ABS(g.min_rate_fraction - l.rate_fraction) > 1e-9 THEN <message> "
    "WHEN COALESCE(l.source, '') <> 'fred' OR l.observation_week IS NULL "
    "OR l.observation_week < current_date() - INTERVAL 14 DAYS THEN <message> "
    "ELSE 1 END AS ok FROM latest_rate AS l CROSS JOIN gold_rate AS g;"
)


def _code(sql: str) -> str:
    """The SQL without ``--`` comments or string literals (the error messages)."""

    without_comments = "\n".join(line.split("--", 1)[0] for line in sql.splitlines())
    return re.sub(r"'[^']*'", "''", without_comments)


def _without_comments(sql: str) -> str:
    return "\n".join(line.split("--", 1)[0] for line in sql.splitlines())


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


def test_the_whole_gate_statement_is_pinned_exactly() -> None:
    """Substring pins let an inverted comparison or an OR -> AND survive; the full statement does not."""

    statement = " ".join(_RAISE.sub("<message>", _without_comments(_gate())).split())

    assert statement == PINNED_STATEMENT
    messages = _RAISE.findall(_gate())
    assert [message.split(": ", 1)[0] for message in messages] == [
        "RAISE_ERROR('assert_scheduled_refresh_outcome"
    ] * 3, "each error names the gate"


# ------------------------------------------------ the gate's branches, executed
#
# The verbatim statement runs in SQLite with three mechanical substitutions:
# two-part table names, a fixed "today" for current_date(), and RAISE_ERROR as
# a function that returns its message (CASE evaluates only the chosen branch,
# as Databricks does). Every predicate, connective and operator is the file's
# own, so a logic change shows up here as a wrong outcome.

TODAY = "2026-10-27"
_EXACTLY_ONE = "expected exactly one is_latest MORTGAGE30US row"
_SKEW = "(stale-rate skew)"
_NOT_FRED = "is not a FRED print within 14 days"
_FRESH_WEEK = "2026-10-19"
_RATE = 0.0732


def _sqlite_gate(today: str) -> str:
    sql = _without_comments(_gate())
    substitutions = (
        (r"\bmip\.silver\.market_rates_weekly\b", "market_rates_weekly", 1),
        (r"\bmip\.gold\.borrower_360\b", "borrower_360", 1),
        (r"current_date\(\)\s*-\s*INTERVAL\s+(\d+)\s+DAYS", rf"date('{today}', '-\1 days')", 1),
        (r"\bRAISE_ERROR\(", "raise_error(", 3),
    )
    for pattern, replacement, expected in substitutions:
        sql, count = re.subn(pattern, replacement, sql)
        assert count == expected, (pattern, count)
    return sql


def _silver(**latest: object) -> list[tuple[object, ...]]:
    """series_id, observation_week, rate_fraction, source, is_latest: one latest row plus noise."""

    row = {"series_id": "MORTGAGE30US", "observation_week": _FRESH_WEEK, "rate_fraction": _RATE, "source": "fred"}
    row.update(latest)
    return [
        (row["series_id"], row["observation_week"], row["rate_fraction"], row["source"], 1),
        ("MORTGAGE30US", "2026-10-12", 0.0740, "fred", 0),
        ("MORTGAGE15US", "2026-10-19", 0.0650, "fred", 1),
    ]


def _run(silver: list[tuple[object, ...]], gold: list[object], *, today: str = TODAY) -> object:
    connection = sqlite3.connect(":memory:")
    try:
        connection.create_function("raise_error", 1, lambda message: f"raised: {message}")
        connection.execute(
            "CREATE TABLE market_rates_weekly (series_id TEXT, observation_week TEXT, "
            "rate_fraction REAL, source TEXT, is_latest INTEGER)"
        )
        connection.execute("CREATE TABLE borrower_360 (market_rate_fraction REAL)")
        connection.executemany("INSERT INTO market_rates_weekly VALUES (?, ?, ?, ?, ?)", silver)
        connection.executemany("INSERT INTO borrower_360 VALUES (?)", [(value,) for value in gold])
        rows = connection.execute(_sqlite_gate(today)).fetchall()
    finally:
        connection.close()
    assert len(rows) == 1, rows
    return rows[0][0]


_SCENARIOS: dict[str, tuple[Callable[[], list[tuple[object, ...]]], list[object], str | None]] = {
    "fresh and aligned": (_silver, [_RATE] * 3, None),
    "within the 1e-9 tolerance": (_silver, [_RATE + 5e-10, _RATE], None),
    "exactly 14 days old (the source-readiness boundary)": (lambda: _silver(observation_week="2026-10-13"), [_RATE], None),
    "two is_latest rows": (lambda: [*_silver(), ("MORTGAGE30US", "2026-10-12", _RATE, "fred", 1)], [_RATE], _EXACTLY_ONE),
    "no is_latest row": (
        lambda: [(*row[:4], 0) if row[0] == "MORTGAGE30US" else row for row in _silver()],
        [_RATE],
        _EXACTLY_ONE,
    ),
    "gold all at the old rate": (_silver, [0.0669] * 2, _SKEW),
    "gold mixed (MAX drifts)": (_silver, [_RATE, _RATE + 1e-6], _SKEW),
    "gold mixed (MIN drifts)": (_silver, [_RATE - 1e-6, _RATE], _SKEW),
    "gold empty": (_silver, [], _SKEW),
    "gold rate NULL": (_silver, [None, None], _SKEW),
    "silver rate NULL": (lambda: _silver(rate_fraction=None), [_RATE], _SKEW),
    "a seed row": (lambda: _silver(source="seed"), [_RATE], _NOT_FRED),
    "a NULL source": (lambda: _silver(source=None), [_RATE], _NOT_FRED),
    "15 days old": (lambda: _silver(observation_week="2026-10-12"), [_RATE], _NOT_FRED),
    "a NULL week": (lambda: _silver(observation_week=None), [_RATE], _NOT_FRED),
}


@pytest.mark.parametrize("scenario", list(_SCENARIOS))
def test_each_gate_branch_decides_its_case(scenario: str) -> None:
    silver, gold, error = _SCENARIOS[scenario]

    outcome = _run(silver(), gold)

    if error is None:
        assert outcome == 1, outcome
    else:
        assert isinstance(outcome, str) and outcome.startswith("raised: ") and error in outcome, outcome


def test_the_freshness_comparison_moves_with_today() -> None:
    """The same row passes on the day it is 14 days old and fails the next day."""

    assert _run(_silver(observation_week="2026-10-13"), [_RATE], today="2026-10-27") == 1
    late = _run(_silver(observation_week="2026-10-13"), [_RATE], today="2026-10-28")
    assert isinstance(late, str) and _NOT_FRED in late


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

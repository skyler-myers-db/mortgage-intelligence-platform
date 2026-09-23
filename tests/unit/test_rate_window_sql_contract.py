"""SQL text contract for the why-now rate window (dataviz-08 / dataviz-06).

``sql/transformations/gold_rate_window_weekly.sql`` builds
``mip.gold.rate_window_weekly`` once per gold refresh. These pins keep the
table honest about what it is:

* the in-the-money count REUSES ``fn_rate_spread`` / ``fn_in_the_money``
  with the per-refresh thresholds carried on borrower_360 -- the rule is
  never forked into literal ``>= 75`` / ``>= 15`` predicates;
* it charts the whole FRED history (every MORTGAGE30US week), not the
  ``is_latest`` row the scoring path reads;
* the book is fixed-rate first liens behind the same active-lien and
  1%..15% clamp gates borrower_360 applies to ``rate_spread_bps``;
* the book is disclosed as-of the shared refresh anchor (``book_as_of``);
* the bundle wires the CTAS behind the freshness sentinel through the
  rendered path, so ``./scripts/deploy.sh -t dev`` builds it;
* the app-side read never computes the percentile per request.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TRANSFORM = REPO_ROOT / "sql" / "transformations" / "gold_rate_window_weekly.sql"
DDL = REPO_ROOT / "sql" / "ddl" / "gold_rate_window_weekly.sql"
MANIFEST = REPO_ROOT / "sql" / "ddl" / "003_gold_tables.sql"
BUNDLE = REPO_ROOT / "databricks.yml"
REPOSITORY = REPO_ROOT / "backend" / "services" / "repositories" / "databricks_rate_window.py"


def _strip_line_comments(sql_text: str) -> str:
    out: list[str] = []
    for line in sql_text.splitlines():
        idx = line.find("--")
        if idx >= 0:
            line = line[:idx]
        out.append(line)
    return "\n".join(out)


def _body() -> str:
    return _strip_line_comments(TRANSFORM.read_text(encoding="utf-8"))


def test_itm_count_reuses_the_canonical_primitives_with_governed_thresholds() -> None:
    body = _body()
    call = re.search(
        r"mip\.gold\.fn_in_the_money\(\s*"
        r"mip\.gold\.fn_rate_spread\(\s*k\.note_rate_fraction\s*,\s*w\.rate_fraction\s*\)\s*,\s*"
        r"k\.equity_pct\s*,\s*"
        r"k\.min_spread_bps_applied\s*,\s*"
        r"k\.min_equity_pct_applied\s*\)",
        body,
    )
    assert call, (
        "itm_count must be fn_in_the_money(fn_rate_spread(note_rate, week rate), equity_pct, "
        "min_spread_bps_applied, min_equity_pct_applied) -- the rule is reused, not forked."
    )
    # No literal threshold predicates anywhere in the executable body.
    assert not re.search(r">=\s*75\b", body), "rate-spread threshold must not be a literal"
    assert not re.search(r">=\s*15\b", body), "equity threshold must not be a literal"
    assert "min_spread_bps_applied" in body and "min_equity_pct_applied" in body


def test_series_is_the_whole_fred_history_not_the_latest_row() -> None:
    body = _body()
    assert "mip.silver.market_rates_weekly" in body
    assert re.search(r"series_id\s*=\s*'MORTGAGE30US'", body)
    assert not re.search(r"is_latest\s*=\s*TRUE", body, re.IGNORECASE), (
        "the rate window charts every observed week; an is_latest filter collapses it to one row."
    )
    # is_latest is CARRIED (the current print is governed, not inferred).
    assert re.search(r"\bw\.is_latest\b", body)


def test_weeks_without_a_print_are_dropped_not_charted_as_zero() -> None:
    body = _body()
    weeks = re.search(r"\bweeks\s+AS\s*\((.*?)\n\),", body, re.DOTALL)
    assert weeks, "could not locate the weeks CTE"
    cte = weeks.group(1)
    # A CTAS does not enforce silver's NOT NULL; a NULL print would read as a
    # 0 bps spread (nobody in the money) and as a dip to 0% on the chart.
    assert re.search(r"\brate_pct\s+IS\s+NOT\s+NULL\b", cte, re.IGNORECASE)
    assert re.search(r"\brate_fraction\s+IS\s+NOT\s+NULL\b", cte, re.IGNORECASE)


def test_ddl_cites_only_contracts_that_exist() -> None:
    ddl = DDL.read_text(encoding="utf-8")
    cited_paths = re.findall(r"\b((?:docs|sql|tests)/[\w./-]+\.(?:md|sql|py))\b", ddl)
    assert cited_paths, "the DDL header names the contract that pins it"
    for cited in cited_paths:
        assert (REPO_ROOT / cited).exists(), f"{DDL.name} cites {cited}, which does not exist"
    assert "data-contract-module0.md §3 (rate window)" not in ddl


def test_book_is_fixed_rate_active_liens_behind_the_borrower_360_gates() -> None:
    body = _body()
    assert "mip.gold.borrower_360" in body
    assert "mip.silver.lien_current" in body
    assert re.search(r"first_pos_rate_type\)?\)?\s*=\s*'FIX'", body), "fixed-rate first liens only"
    assert re.search(r"\bb\.current_rate\s*>\s*0\b", body), "active-lien gate must match borrower_360"
    assert "mip.gold.fn_bounded_mortgage_rate(lc.first_pos_rate)" in body
    assert re.search(r"note_rate_fraction\s*>\s*0\.01", body)
    assert re.search(r"note_rate_fraction\s*<\s*0\.15", body)
    # Band + as-of disclosure.
    assert re.search(r"PERCENTILE_APPROX\(\s*note_rate_fraction\s*,\s*ARRAY\(0\.25,\s*0\.5,\s*0\.75\)", body)
    assert re.search(r"AS\s+book_as_of\b", body)
    assert "mip.ref.refresh_run_state" in body
    assert not re.search(r"CURRENT_TIMESTAMP\s*\(", body, re.IGNORECASE)


def test_ddl_declares_the_as_of_disclosure_and_the_manifest_carries_the_table() -> None:
    ddl = DDL.read_text(encoding="utf-8")
    manifest = MANIFEST.read_text(encoding="utf-8")
    for text in (ddl, manifest):
        assert "CREATE TABLE IF NOT EXISTS mip.gold.rate_window_weekly" in text
        assert re.search(r"^\s*book_as_of\s+TIMESTAMP\s+NOT NULL", text, re.MULTILINE)
        assert re.search(r"^\s*itm_count\s+BIGINT\s+NOT NULL", text, re.MULTILINE)
        assert re.search(r"^\s*book_median_rate_pct\s+DOUBLE", text, re.MULTILINE)


def test_bundle_builds_the_table_behind_the_freshness_sentinel() -> None:
    bundle = BUNDLE.read_text(encoding="utf-8")
    block = re.search(
        r"mip_refresh_scores:(.*?)(?:\n    [a-zA-Z_]+:|\Z)",
        bundle,
        re.DOTALL,
    )
    assert block, "could not locate mip_refresh_scores block in databricks.yml."
    job = block.group(1)
    task = re.search(
        r"- task_key:\s*ctas_rate_window_weekly\s*.*?depends_on:\s*\n\s*- task_key:\s*([A-Za-z0-9_]+)"
        r".*?path:\s*(\S+)",
        job,
        re.DOTALL,
    )
    assert task, "ctas_rate_window_weekly task is not wired into mip_refresh_scores."
    assert task.group(1) == "assert_borrower_360_fresh"
    assert task.group(2) == "sql/_rendered/transformations/gold_rate_window_weekly.sql"


def test_app_read_never_computes_the_window_per_request() -> None:
    from backend.services.repositories.databricks_rate_window import RATE_WINDOW_SQL

    lowered = RATE_WINDOW_SQL.lower()
    assert ".gold.rate_window_weekly" in lowered
    assert "percentile" not in lowered
    assert "borrower_360" not in lowered
    assert "fn_in_the_money" not in lowered
    # And the module issues no other statement: one governed read, nothing else.
    module = REPOSITORY.read_text(encoding="utf-8")
    assert module.count("self._client.execute(") == 1

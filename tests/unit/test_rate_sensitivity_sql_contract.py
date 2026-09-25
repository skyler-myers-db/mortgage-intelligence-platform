"""SQL text contract for the Rate Lever grid (audit wow-stage-1).

Two SQL sites compute "in the money at par + step": the gold CTAS
(``sql/transformations/gold_rate_sensitivity_rollup.sql``, the addressable
grid) and the repository's live contactable aggregate. Both must carry the
SAME note gate and per-step rule, taken from ``backend/services/rate_scenario``,
or the contactable subset and its superset would be counted by different
rules. The live statement runs as the App, which holds no ``mip.silver``
grant (``docs/security/GRANTS.md`` section 5), so it reads the gated note from
``mip.gold.rate_sensitivity_book`` -- a third SQL site, built by the same
refresh from the same gate text over the same lien join. These pins keep the
three in step and keep the arithmetic honest:

* the rollup CTAS embeds ``NOTE_RATE_GATE_SQL`` and ``SCENARIO_ITM_SQL``, the
  book CTAS embeds ``NOTE_RATE_GATE_SQL`` over the rollup's own join, and the
  live statement embeds ``SCENARIO_ITM_SQL`` (all whitespace-normalized);
* the live statement reads gold only and LEFT JOINs the book (a gated
  borrower has no book row and must read as the rollup's NULL note, which
  still clears a ``min_spread_bps <= 0`` screen);
* the CTAS grid literal is ``RATE_SCENARIO_STEPS_BPS``;
* no DECIMAL ``10000.0`` literal, no bin shifting off ``rate_spread_bps``, no
  per-task ``CURRENT_TIMESTAMP`` in either CTAS;
* the base par is ``borrower_360.market_rate_fraction`` (never silver
  ``is_latest``), and the lien join is a LEFT JOIN (gated rows stay in);
* the live statement embeds ``eligible_sql_predicate()`` verbatim;
* the bundle wires both CTAS behind the freshness sentinel, through the
  rendered path, so ``./scripts/deploy.sh -t dev`` builds them.
"""

from __future__ import annotations

import re
from pathlib import Path

from backend.services.eligibility import eligible_sql_predicate
from backend.services.rate_scenario import (
    NOTE_RATE_GATE_SQL,
    RATE_SCENARIO_STEPS_BPS,
    SCENARIO_ITM_SQL,
    scenario_in_the_money,
)
from backend.services.repositories.databricks_rate_sensitivity import RATE_SENSITIVITY_SQL

REPO_ROOT = Path(__file__).resolve().parents[2]
TRANSFORM = REPO_ROOT / "sql" / "transformations" / "gold_rate_sensitivity_rollup.sql"
DDL = REPO_ROOT / "sql" / "ddl" / "gold_rate_sensitivity_rollup.sql"
BOOK_TRANSFORM = REPO_ROOT / "sql" / "transformations" / "gold_rate_sensitivity_book.sql"
BOOK_DDL = REPO_ROOT / "sql" / "ddl" / "gold_rate_sensitivity_book.sql"
MANIFEST = REPO_ROOT / "sql" / "ddl" / "003_gold_tables.sql"
BUNDLE = REPO_ROOT / "databricks.yml"
REPOSITORY = REPO_ROOT / "backend" / "services" / "repositories" / "databricks_rate_sensitivity.py"


def _strip_line_comments(sql_text: str) -> str:
    return "\n".join(line.split("--", 1)[0] for line in sql_text.splitlines())


def _normalize(sql_text: str) -> str:
    """Collapse whitespace and the padding inside parentheses."""
    text = " ".join(_strip_line_comments(sql_text).split())
    text = re.sub(r"\(\s+", "(", text)
    return re.sub(r"\s+\)", ")", text)


def _ctas(path: Path = TRANSFORM) -> str:
    """The executable CTAS statement (the COMMENT ON COLUMN tail excluded)."""
    body = _strip_line_comments(path.read_text(encoding="utf-8"))
    return body.split(";", 1)[0]


# The rollup's book join; the note book must read the gate over exactly it.
_LIEN_JOIN = "FROM mip.gold.borrower_360 AS b LEFT JOIN mip.silver.lien_current AS lc ON lc.clip = b.clip"
_BOOK_JOIN = "LEFT JOIN mip.gold.rate_sensitivity_book AS nb ON nb.clip = b.clip"
# A schema reference into the ETL-only layers, catalog-qualified or not.
_ETL_ONLY_SCHEMA = re.compile(r"(?i)(?<![\w$])(?:[\w`]+\.)?`?(?:silver|raw)`?\.`?[a-z_]")


def test_every_site_embeds_the_shared_note_gate_and_rule() -> None:
    gate = _normalize(NOTE_RATE_GATE_SQL)
    rule = _normalize(SCENARIO_ITM_SQL)
    rollup = _normalize(_ctas())
    book = _normalize(_ctas(BOOK_TRANSFORM))
    live = _normalize(RATE_SENSITIVITY_SQL)
    assert gate in rollup, "the rollup CTAS must embed NOTE_RATE_GATE_SQL verbatim"
    assert rule in rollup, "the rollup CTAS must embed SCENARIO_ITM_SQL verbatim"
    assert f"{gate} AS note_rate_fraction" in book, "the book CTAS must embed NOTE_RATE_GATE_SQL verbatim"
    assert rule in live, "the live statement must embed SCENARIO_ITM_SQL verbatim"
    # The live statement reads the gated note; it never re-derives it.
    assert gate not in live
    assert "nb.note_rate_fraction" in live


def test_book_ctas_gates_over_the_rollups_own_join() -> None:
    rollup = _normalize(_ctas())
    book = _normalize(_ctas(BOOK_TRANSFORM))
    assert _LIEN_JOIN in rollup
    assert _LIEN_JOIN in book, "the book must read the note over the rollup's own lien join"
    # Every borrower_360 CLIP is considered (no state or eligibility filter);
    # only the NULL-gated rows are left out, one row per CLIP.
    gated = re.search(r"\bgated AS \((.*?)\) SELECT", book)
    assert gated, "could not locate the gated CTE"
    assert " WHERE " not in gated.group(1)
    tail = book.split(") SELECT", 1)[1]
    assert tail.strip().endswith("WHERE g.note_rate_fraction IS NOT NULL")
    assert tail.strip().startswith("g.clip, g.note_rate_fraction,")
    assert "GROUP BY" not in book and "DISTINCT" not in book


def test_book_ctas_is_deterministic() -> None:
    body = _ctas(BOOK_TRANSFORM)
    assert not re.search(r"CURRENT_TIMESTAMP\s*\(", body, re.IGNORECASE)
    assert "mip.ref.refresh_run_state" in body
    assert re.search(r"AS\s+book_as_of\b", body)
    assert re.search(r"AS\s+refreshed_at\b", body)
    assert re.search(r"CREATE OR REPLACE TABLE mip\.gold\.rate_sensitivity_book\s+CLUSTER BY \(clip\)", body)


def test_ctas_grid_literal_is_the_python_grid() -> None:
    match = re.search(r"EXPLODE\(\s*ARRAY\(([^)]*)\)\s*\)\s*AS\s+step_bps", _ctas())
    assert match, "the CTAS must explode the step grid literal"
    steps = tuple(int(value) for value in match.group(1).split(","))
    assert steps == RATE_SCENARIO_STEPS_BPS


def test_ctas_arithmetic_is_double_and_never_bin_shifted() -> None:
    body = _ctas()
    assert "10000.0" not in body, "a DECIMAL literal changes the step arithmetic"
    assert "rate_spread_bps" not in body, "the scenario never shifts rounded spread bins"
    assert not re.search(r"CURRENT_TIMESTAMP\s*\(", body, re.IGNORECASE)
    assert "mip.ref.refresh_run_state" in body
    assert re.search(r"AS\s+book_as_of\b", body)
    assert re.search(r"AS\s+refreshed_at\b", body)
    # No literal threshold predicates: the refresh's own thresholds decide.
    assert not re.search(r">=\s*75\b", body)
    assert not re.search(r">=\s*15\b", body)


def test_ctas_base_par_is_the_scored_par_and_gated_rows_stay_in() -> None:
    normalized = _normalize(_ctas())
    assert "b.market_rate_fraction" in normalized
    assert "market_rates_weekly" not in normalized, "the base par is borrower_360's, not silver is_latest"
    assert "is_latest" not in normalized
    assert "LEFT JOIN mip.silver.lien_current AS lc ON lc.clip = b.clip" in normalized
    # The population is every borrower_360 row with a state, not only the gated ones.
    book = re.search(r"\bbook AS \((.*?)\), book_cells AS", normalized)
    assert book, "could not locate the book CTE"
    where = book.group(1).split(" WHERE ", 1)[1]
    assert where.strip() == "b.state IS NOT NULL", where
    # rate_movable counts only the rows a scenario can move.
    assert re.search(r"WHEN k\.note_rate_fraction IS NOT NULL THEN k\.borrower_count", normalized)


def test_live_statement_embeds_the_eligibility_predicate_verbatim() -> None:
    assert eligible_sql_predicate() in RATE_SENSITIVITY_SQL
    normalized = _normalize(RATE_SENSITIVITY_SQL)
    assert _BOOK_JOIN in normalized
    # The live grid is the gold grid, so the two align by construction.
    assert "SELECT DISTINCT step_bps FROM mip.gold.rate_sensitivity_rollup" in normalized
    assert "LEFT JOIN contactable AS c ON c.state = r.state AND c.step_bps = r.step_bps" in normalized
    assert "10000.0" not in normalized
    module = REPOSITORY.read_text(encoding="utf-8")
    assert module.count("self._client.execute(") == 1, "one statement per read"


def test_ddl_manifest_and_bundle_register_the_table() -> None:
    ddl = DDL.read_text(encoding="utf-8")
    manifest = MANIFEST.read_text(encoding="utf-8")
    for text in (ddl, manifest):
        assert "CREATE TABLE IF NOT EXISTS mip.gold.rate_sensitivity_rollup" in text
        assert re.search(r"^\s*step_bps\s+INT\s+NOT NULL", text, re.MULTILINE)
        assert re.search(r"^\s*in_the_money_borrowers\s+BIGINT\s+NOT NULL", text, re.MULTILINE)
        assert re.search(r"^\s*book_as_of\s+TIMESTAMP\s+NOT NULL", text, re.MULTILINE)
    cited = re.findall(r"\b((?:docs|sql|tests|backend)/[\w./-]+\.(?:md|sql|py|json))\b", ddl)
    assert cited
    for path in cited:
        assert (REPO_ROOT / path).exists(), f"{DDL.name} cites {path}, which does not exist"

    bundle = BUNDLE.read_text(encoding="utf-8")
    block = re.search(r"mip_refresh_scores:(.*?)(?:\n    [a-zA-Z_]+:|\Z)", bundle, re.DOTALL)
    assert block, "could not locate mip_refresh_scores block in databricks.yml."
    task = re.search(
        r"- task_key:\s*ctas_rate_sensitivity_rollup\s*.*?depends_on:\s*\n\s*- task_key:\s*([A-Za-z0-9_]+)"
        r".*?path:\s*(\S+)",
        block.group(1),
        re.DOTALL,
    )
    assert task, "ctas_rate_sensitivity_rollup is not wired into mip_refresh_scores."
    assert task.group(1) == "assert_borrower_360_fresh"
    assert task.group(2) == "sql/_rendered/transformations/gold_rate_sensitivity_rollup.sql"
    assert "15. rate_sensitivity_rollup" in bundle


def test_live_statement_reads_gold_only() -> None:
    """The App holds no mip.silver / mip.raw grant (GRANTS.md section 5)."""
    hit = _ETL_ONLY_SCHEMA.search(RATE_SENSITIVITY_SQL)
    assert hit is None, f"the live statement reads an ETL-only schema: {hit.group(0) if hit else ''}"
    assert "lien_current" not in RATE_SENSITIVITY_SQL
    assert "fn_bounded_mortgage_rate" not in RATE_SENSITIVITY_SQL


def test_live_note_join_is_a_left_join() -> None:
    normalized = _normalize(RATE_SENSITIVITY_SQL)
    joins = re.findall(r"(\w+ )?JOIN mip\.gold\.rate_sensitivity_book\b", normalized)
    assert joins == ["LEFT "], joins


def _contactable_counts(book_join: str, min_spread_bps: int) -> list[int]:
    """Python mirror of the live aggregate over a synthetic eligible book.

    ``book_join`` is ``"silver"`` (the pre-fix statement: the gate applied to
    the lien row), ``"left"`` (the book LEFT JOIN) or ``"inner"``. The book is
    built exactly as the CTAS builds it: the non-NULL gated notes only.
    """
    borrowers = [
        # (clip, current_rate, bounded lien note or None, equity_pct)
        ("c1", 6.5, 0.0725, 40),  # active, in bounds: movable
        ("c2", 0.0, 0.0650, 40),  # paid down: gated
        ("c3", 5.0, 0.15, 40),  # AT the 15% clamp: gated
        ("c4", 5.0, None, 40),  # no lien row: gated
        ("c5", 6.0, 0.0575, 10),  # movable, below the equity screen
    ]
    market = 0.0625

    def gate(current_rate: float, note: float | None) -> float | None:
        if note is not None and current_rate > 0 and 0.01 < note < 0.15:
            return note
        return None

    book = {clip: gate(rate, note) for clip, rate, note, _ in borrowers if gate(rate, note) is not None}
    counts = []
    for step in RATE_SCENARIO_STEPS_BPS:
        count = 0
        for clip, rate, note, equity in borrowers:
            if book_join == "silver":
                note_rate: float | None = gate(rate, note)
            elif book_join == "inner" and clip not in book:
                continue
            else:
                note_rate = book.get(clip)
            count += scenario_in_the_money(note_rate, market, step, equity, min_spread_bps, 15)
        counts.append(count)
    return counts


def test_book_left_join_counts_exactly_what_the_silver_join_counted() -> None:
    """The book LEFT JOIN is count-identical to the pre-fix silver join at
    every step and threshold; an INNER JOIN is not once a gated borrower can
    clear the screen (min_spread_bps <= 0 scores their no-signal 0 bps in)."""
    for min_spread_bps in (75, 1, 0, -25):
        assert _contactable_counts("left", min_spread_bps) == _contactable_counts("silver", min_spread_bps)
    assert _contactable_counts("inner", 75) == _contactable_counts("silver", 75)
    assert _contactable_counts("inner", 0) != _contactable_counts("silver", 0)
    # Non-vacuity: the scenario moves the book, and the gated rows matter at 0.
    assert len(set(_contactable_counts("silver", 75))) > 1
    assert min(_contactable_counts("silver", 0)) >= 3


def test_book_ddl_manifest_and_bundle_register_the_table() -> None:
    ddl = BOOK_DDL.read_text(encoding="utf-8")
    manifest = MANIFEST.read_text(encoding="utf-8")
    for text in (ddl, manifest):
        assert "CREATE TABLE IF NOT EXISTS mip.gold.rate_sensitivity_book" in text
        assert re.search(r"^\s*clip\s+STRING\s+NOT NULL", text, re.MULTILINE)
        assert re.search(r"^\s*note_rate_fraction\s+DOUBLE\s+NOT NULL", text, re.MULTILINE)
        assert re.search(r"^\s*book_as_of\s+TIMESTAMP\s+NOT NULL", text, re.MULTILINE)
        assert re.search(r"^\s*refreshed_at\s+TIMESTAMP\s+NOT NULL", text, re.MULTILINE)
    cited = re.findall(r"\b((?:docs|sql|tests|backend)/[\w./-]+\.(?:md|sql|py|json))\b", ddl)
    assert cited
    for path in cited:
        assert (REPO_ROOT / path).exists(), f"{BOOK_DDL.name} cites {path}, which does not exist"

    bundle = BUNDLE.read_text(encoding="utf-8")
    block = re.search(r"mip_refresh_scores:(.*?)(?:\n    [a-zA-Z_]+:|\Z)", bundle, re.DOTALL)
    assert block, "could not locate mip_refresh_scores block in databricks.yml."
    task = re.search(
        r"- task_key:\s*ctas_rate_sensitivity_book\s*.*?depends_on:\s*\n\s*- task_key:\s*([A-Za-z0-9_]+)"
        r".*?warehouse_id:\s*(\S+).*?path:\s*(\S+)",
        block.group(1),
        re.DOTALL,
    )
    assert task, "ctas_rate_sensitivity_book is not wired into mip_refresh_scores."
    assert task.group(1) == "assert_borrower_360_fresh"
    assert task.group(2) == "${resources.sql_warehouses.mip_serverless_sql.id}"
    assert task.group(3) == "sql/_rendered/transformations/gold_rate_sensitivity_book.sql"
    assert "16. rate_sensitivity_book" in bundle
